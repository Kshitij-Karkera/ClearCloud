#!/usr/bin/env python3
"""Load test for ClearCloud's scan fan-out.

Creates N synthetic AWS "host account" records in DynamoDB, publishes one scan message per
account to the scan topic (exactly what the dispatcher does), waits for the scanner Lambdas
to finish them all, prints timing statistics, and removes the synthetic data.

Run it in CloudShell, in the account that hosts the stack:

    python3 scripts/loadtest.py --accounts 50

Each synthetic account scans the real sandbox account, so make sure it has no waste first
(otherwise every account sends an alert email). The script refuses to run if it finds some,
unless you pass --force.
"""
import argparse
import collections
import json
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1,
                max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def summarize(rows, publish_seconds, wall_seconds):
    """rows: list of dicts with start (s after publish), finish (s after publish), status."""
    ok = [r for r in rows if r["status"].startswith("ok")]
    failed = [r for r in rows if not r["status"].startswith("ok")]
    finishes = [r["finish"] for r in rows]
    starts = [r["start"] for r in rows]
    durations = [max(0.0, r["finish"] - r["start"]) for r in rows]

    peak = 0
    if rows:
        for t in range(int(wall_seconds) + 2):
            peak = max(
                peak, sum(1 for r in rows if r["start"] <= t < r["finish"]))

    return {
        "accounts": len(rows),
        "ok": len(ok),
        "failed": len(failed),
        "publish_seconds": round(publish_seconds, 2),
        "wall_seconds": round(wall_seconds, 2),
        "throughput_per_second": round(len(rows) / wall_seconds, 2) if wall_seconds > 0 else 0,
        "start_delay_p50": round(percentile(starts, 50), 2),
        "start_delay_p95": round(percentile(starts, 95), 2),
        "start_delay_max": round(max(starts), 2) if starts else 0,
        "scan_seconds_mean": round(statistics.mean(durations), 2) if durations else 0,
        "scan_seconds_p95": round(percentile(durations, 95), 2),
        "finish_p50": round(percentile(finishes, 50), 2),
        "finish_p95": round(percentile(finishes, 95), 2),
        "peak_concurrent_observed": peak,
    }


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def preflight(ec2):
    """Return a list of reasons the account is not waste-free."""
    problems = []
    vols = ec2.describe_volumes(
        Filters=[{"Name": "status", "Values": ["available"]}]).get("Volumes", [])
    if vols:
        problems.append(f"{len(vols)} unattached volume(s)")
    addrs = ec2.describe_addresses().get("Addresses", [])
    free = [a for a in addrs if not a.get("AssociationId")]
    if free:
        problems.append(f"{len(free)} unassociated Elastic IP(s)")
    running = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )
    count = sum(len(r["Instances"]) for r in running.get("Reservations", []))
    if count:
        problems.append(f"{count} running instance(s) (may be reported idle)")
    return problems


def create_accounts(table, pk, count, region):
    items = []
    now = datetime.now(timezone.utc).isoformat()
    with table.batch_writer() as batch:
        for i in range(count):
            account_id = uuid.uuid4().hex[:12]
            item = {
                "pk": pk,
                "sk": f"ACCOUNT#{account_id}",
                "kind": "ACCOUNT",
                "account_id": account_id,
                "provider": "aws",
                "label": f"load-{i:03d}",
                "created_at": now,
                "last_status": "never scanned",
                "regions": [region],
                "host_account": True,
            }
            batch.put_item(Item=item)
            items.append(item)
    return items


def publish_all(sns, topic_arn, items):
    failed = 0
    for group in chunks(items, 10):
        entries = [
            {"Id": str(i), "Message": json.dumps(
                {"pk": a["pk"], "sk": a["sk"]})}
            for i, a in enumerate(group)
        ]
        resp = sns.publish_batch(
            TopicArn=topic_arn, PublishBatchRequestEntries=entries)
        failed += len(resp.get("Failed", []))
    return failed


def poll_until_done(ddb, table_name, items, t0, timeout, poll=1.0):
    pending = {a["sk"]: a for a in items}
    pk = items[0]["pk"]
    seen = {}
    deadline = t0 + timeout
    while pending and time.time() < deadline:
        keys = [{"pk": pk, "sk": sk} for sk in list(pending)]
        for group in chunks(keys, 100):
            resp = ddb.batch_get_item(RequestItems={table_name: {
                "Keys": group,
                "ProjectionExpression": "#s, #st, #t",
                "ExpressionAttributeNames": {"#s": "sk", "#st": "last_status", "#t": "last_scan_at"},
            }})
            now = time.time()
            for item in resp.get("Responses", {}).get(table_name, []):
                status = item.get("last_status", "")
                if status and status != "never scanned" and item["sk"] in pending:
                    started = datetime.fromisoformat(
                        item["last_scan_at"]).timestamp()
                    seen[item["sk"]] = {
                        "start": max(0.0, started - t0),
                        "finish": now - t0,
                        "status": status,
                    }
                    pending.pop(item["sk"])
        if pending:
            time.sleep(poll)
    return list(seen.values()), len(pending)


def cleanup(table, pk):
    removed = 0
    kwargs = {
        "KeyConditionExpression": "pk = :p",
        "ExpressionAttributeValues": {":p": pk},
        "ProjectionExpression": "pk, sk",
    }
    while True:
        resp = table.query(**kwargs)
        with table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                removed += 1
        if "LastEvaluatedKey" not in resp:
            return removed
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def lambda_metrics(cw, function_name, start, end):
    out = {}
    for metric, stat in (("Invocations", "Sum"), ("Errors", "Sum"), ("Throttles", "Sum"),
                         ("ConcurrentExecutions", "Maximum"), ("Duration", "Average"),
                         ("Duration", "Maximum")):
        resp = cw.get_metric_statistics(
            Namespace="AWS/Lambda", MetricName=metric,
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start, EndTime=end, Period=60, Statistics=[stat],
        )
        points = [p[stat] for p in resp.get("Datapoints", [])]
        key = f"{metric}_{stat}"
        if not points:
            out[key] = None
        elif stat == "Sum":
            out[key] = round(sum(points), 2)
        elif stat == "Maximum":
            out[key] = round(max(points), 2)
        else:
            out[key] = round(sum(points) / len(points), 2)
    return out


def run(args, ddb, sns, cfn, ec2, cw, table_name=None, topic_arn=None):
    if table_name is None:
        table_name = cfn.describe_stack_resource(
            StackName=args.stack, LogicalResourceId="Table"
        )["StackResourceDetail"]["PhysicalResourceId"]
    if topic_arn is None:
        outputs = cfn.describe_stacks(StackName=args.stack)[
            "Stacks"][0]["Outputs"]
        topic_arn = next(o["OutputValue"]
                         for o in outputs if o["OutputKey"] == "ScanTopicArn")

    problems = preflight(ec2)
    if problems and not args.force:
        print("Refusing to run: the account has waste, so every synthetic account would send an alert email:")
        for p in problems:
            print("  -", p)
        print("Delete it first, or pass --force to run anyway.")
        return 2

    table = ddb.Table(table_name)
    run_id = uuid.uuid4().hex[:8]
    pk = f"USER#loadtest-{run_id}"
    print(f"Run {run_id}: creating {args.accounts} synthetic accounts ...")
    items = create_accounts(table, pk, args.accounts, args.region)

    window_start = datetime.now(timezone.utc)
    try:
        t0 = time.time()
        failed = publish_all(sns, topic_arn, items)
        publish_seconds = time.time() - t0
        print(
            f"Published {len(items) - failed}/{len(items)} scan messages in {publish_seconds:.2f}s; waiting ...")
        rows, still_pending = poll_until_done(
            ddb, table_name, items, t0, args.timeout, args.poll)
        wall = max((r["finish"] for r in rows), default=time.time() - t0)
        result = summarize(rows, publish_seconds, wall)
        result.update({"requested": args.accounts, "publish_failed": failed,
                       "timed_out": still_pending, "run_id": run_id})
        if args.metrics:
            print("Waiting for CloudWatch metrics to settle ...")
            time.sleep(150)
            result["cloudwatch_scanner"] = lambda_metrics(
                cw, "clearcloud-scanner", window_start, datetime.now(timezone.utc))
    finally:
        if args.keep:
            print(f"Keeping synthetic data under {pk}")
        else:
            print(f"Cleaned up {cleanup(table, pk)} items.")

    errors = [r["status"] for r in rows if not r["status"].startswith("ok")]
    if errors:
        breakdown = collections.Counter(e[:160] for e in errors)
        result["error_breakdown"] = dict(breakdown.most_common(5))
    print(json.dumps(result, indent=2))
    if errors:
        print("Sample error:", errors[0][:200])
    path = f"loadtest_{args.accounts}_{run_id}.json"
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2)
    print(f"Saved {path}")
    return 0 if not still_pending and not failed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--accounts", type=int, default=20)
    parser.add_argument("--stack", default="clearcloud")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--timeout", type=int, default=900,
                        help="give up waiting after this many seconds")
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--force", action="store_true",
                        help="run even if the account has waste")
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the synthetic data")
    parser.add_argument("--metrics", action="store_true",
                        help="also read CloudWatch metrics (adds ~2.5 min)")
    args = parser.parse_args(argv)
    if not 1 <= args.accounts <= 500:
        parser.error("--accounts must be between 1 and 500")

    import boto3

    return run(
        args,
        boto3.resource("dynamodb", region_name=args.region),
        boto3.client("sns", region_name=args.region),
        boto3.client("cloudformation", region_name=args.region),
        boto3.client("ec2", region_name=args.region),
        boto3.client("cloudwatch", region_name=args.region),
    )


if __name__ == "__main__":
    sys.exit(main())
