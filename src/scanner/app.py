import datetime
import json
import os

import boto3

import rules
from providers import aws as aws_provider

TABLE_NAME = os.environ.get("TABLE_NAME", "clearcloud-reports")
PROVIDERS = [p.strip().lower() for p in os.environ.get(
    "PROVIDERS", "aws").split(",") if p.strip()]
REGIONS = [r.strip() for r in os.environ.get(
    "REGIONS", "").split(",") if r.strip()]
CPU_LOOKBACK_DAYS = int(os.environ.get("CPU_LOOKBACK_DAYS", "7"))
AZURE_SECRET_NAME = os.environ.get("AZURE_SECRET_NAME", "clearcloud/azure")
GCP_SECRET_NAME = os.environ.get("GCP_SECRET_NAME", "clearcloud/gcp")


def _get_secret(name):
    client = boto3.client("secretsmanager")
    return json.loads(client.get_secret_value(SecretId=name)["SecretString"])


def _merge(target, part):
    for key in ("disks", "public_ips", "vms"):
        target[key].extend(part.get(key, []))


def collect_all(now):
    inventory = {"disks": [], "public_ips": [], "vms": []}
    errors = []
    scanned = []

    if "aws" in PROVIDERS:
        regions = REGIONS or [os.environ.get("AWS_REGION", "us-east-1")]
        for region in regions:
            try:
                _merge(inventory, aws_provider.collect(
                    region, now, CPU_LOOKBACK_DAYS))
                scanned.append(f"aws:{region}")
            except Exception as exc:
                errors.append(f"aws:{region}: {type(exc).__name__}: {exc}")

    if "azure" in PROVIDERS:
        try:
            from providers import azure as azure_provider

            _merge(
                inventory,
                azure_provider.collect(_get_secret(
                    AZURE_SECRET_NAME), now, CPU_LOOKBACK_DAYS),
            )
            scanned.append("azure")
        except Exception as exc:
            errors.append(f"azure: {type(exc).__name__}: {exc}")

    if "gcp" in PROVIDERS:
        try:
            from providers import gcp as gcp_provider

            _merge(
                inventory,
                gcp_provider.collect(_get_secret(
                    GCP_SECRET_NAME), now, CPU_LOOKBACK_DAYS),
            )
            scanned.append("gcp")
        except Exception as exc:
            errors.append(f"gcp: {type(exc).__name__}: {exc}")

    return inventory, scanned, errors


def lambda_handler(event, context):
    now = datetime.datetime.now(datetime.timezone.utc)

    inventory, scanned, errors = collect_all(now)
    findings = rules.analyze(inventory)
    summary = rules.summarize(findings)

    spend, ce_status = ([], "not requested")
    if "aws" in PROVIDERS:
        spend, ce_status = aws_provider.collect_spend(now)

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    ts = now.isoformat()
    item = {
        "scanned_at": ts,
        "providers": PROVIDERS,
        "scanned": scanned,
        "summary_json": json.dumps(summary),
        "findings_json": json.dumps(findings),
        "spend_json": json.dumps(spend),
        "cost_explorer_status": ce_status,
        "errors_json": json.dumps(errors),
    }
    table.put_item(Item={"pk": "latest", **item})
    table.put_item(
        Item={
            "pk": f"history#{ts}",
            "scanned_at": ts,
            "total_json": json.dumps(summary["total_est_monthly_savings"]),
        }
    )
    return {"scanned_at": ts, "scanned": scanned, "findings": len(findings), "errors": errors}
