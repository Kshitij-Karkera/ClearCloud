import datetime
import json
import os
import traceback

import boto3

import reporting
import rules
from providers import aws as aws_provider

TABLE_NAME = os.environ.get("TABLE_NAME", "")
DEFAULT_AWS_REGION = os.environ.get(
    "DEFAULT_AWS_REGION") or os.environ.get("AWS_REGION", "us-east-1")
CPU_LOOKBACK_DAYS = int(os.environ.get("CPU_LOOKBACK_DAYS", "7"))

_clients = {}


def _table():
    if "table" not in _clients:
        _clients["table"] = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _clients["table"]


def _get_secret(name):
    if "secrets" not in _clients:
        _clients["secrets"] = boto3.client("secretsmanager")
    return json.loads(_clients["secrets"].get_secret_value(SecretId=name)["SecretString"])


def _merge(target, part):
    for key in ("disks", "public_ips", "vms"):
        target[key].extend(part.get(key, []))


def collect_for_account(account, now):
    provider = account["provider"]
    inventory = {"disks": [], "public_ips": [], "vms": []}
    spend, ce_status, errors = [], "not requested", []

    if provider == "aws":
        role_arn = account.get("role_arn")
        if role_arn:
            session = aws_provider.make_session(
                role_arn, account.get("external_id"), account["account_id"]
            )
        elif account.get("host_account"):
            session = None
        else:
            raise PermissionError(
                "AWS account has no role_arn and is not an approved host account")
        regions = list(account.get("regions") or []) or [DEFAULT_AWS_REGION]
        for region in regions:
            try:
                _merge(inventory, aws_provider.collect(
                    region, now, CPU_LOOKBACK_DAYS, session))
            except Exception as exc:
                errors.append(f"{region}: {type(exc).__name__}: {exc}")
        spend, ce_status = aws_provider.collect_spend(now, session)
    elif provider == "azure":
        from providers import azure as azure_provider

        inventory = azure_provider.collect(
            _get_secret(account["secret_name"]), now, CPU_LOOKBACK_DAYS
        )
    elif provider == "gcp":
        from providers import gcp as gcp_provider

        inventory = gcp_provider.collect(
            _get_secret(account["secret_name"]), now, CPU_LOOKBACK_DAYS
        )
    else:
        raise ValueError(f"Unknown provider: {provider}")
    return inventory, spend, ce_status, errors


def _record_status(key, now, status):
    _table().update_item(
        Key=key,
        UpdateExpression="SET last_status = :s, last_scan_at = :t",
        ExpressionAttributeValues={":s": status[:300], ":t": now.isoformat()},
    )


def process(message, now):
    key = {"pk": message["pk"], "sk": message["sk"]}
    account = _table().get_item(Key=key).get("Item")
    if not account:
        return

    try:
        inventory, spend, ce_status, errors = collect_for_account(account, now)
    except Exception as exc:
        _record_status(key, now, f"error: {type(exc).__name__}: {exc}")
        return
    if errors and not any(inventory.values()):
        _record_status(key, now, "error: " + "; ".join(errors))
        return

    findings = rules.analyze(inventory)
    summary = rules.summarize(findings)
    scanned_at = now.isoformat()
    _table().put_item(
        Item=reporting.build_report_item(
            account, scanned_at, findings, summary, spend, ce_status, errors
        )
    )
    _table().put_item(
        Item=reporting.build_history_item(
            account, scanned_at, now.timestamp(
            ), summary["total_est_monthly_savings"]
        )
    )
    _record_status(key, now, f"ok: {len(findings)} findings")


def lambda_handler(event, context):
    now = datetime.datetime.now(datetime.timezone.utc)
    failures = []
    for record in event.get("Records", []):
        try:
            process(json.loads(record["body"]), now)
        except Exception:
            traceback.print_exc()
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
