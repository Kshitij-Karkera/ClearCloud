import json
import os

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "clearcloud-reports")
HERE = os.path.dirname(os.path.abspath(__file__))


def _json(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def load_report():
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    latest = table.get_item(Key={"pk": "latest"}).get("Item")
    if not latest:
        return None

    history = []
    scan = table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr(
            "pk").begins_with("history#")
    )
    for it in scan.get("Items", []):
        history.append(
            {"scanned_at": it["scanned_at"],
                "total": json.loads(it["total_json"])}
        )
    history.sort(key=lambda h: h["scanned_at"])

    return {
        "scanned_at": latest["scanned_at"],
        "providers": list(latest.get("providers", [])),
        "scanned": list(latest.get("scanned", [])),
        "summary": json.loads(latest["summary_json"]),
        "findings": json.loads(latest["findings_json"]),
        "spend": json.loads(latest["spend_json"]),
        "cost_explorer_status": latest.get("cost_explorer_status", "unknown"),
        "errors": json.loads(latest.get("errors_json", "[]")),
        "history": history[-30:],
    }


def lambda_handler(event, context):
    path = event.get("rawPath", "/")
    if path == "/api/report":
        report = load_report()
        if report is None:
            return _json(404, {"error": "No scan has run yet. Invoke the scanner first."})
        return _json(200, report)
    if path in ("/", "/index.html"):
        with open(os.path.join(HERE, "dashboard.html"), encoding="utf-8") as f:
            html = f.read()
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": html,
        }
    return _json(404, {"error": "Not found"})
