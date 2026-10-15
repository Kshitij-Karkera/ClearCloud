import json
import os

from dispatch_logic import build_entries, chunks


def _all_accounts(table):
    from boto3.dynamodb.conditions import Key

    kwargs = {
        "IndexName": "kind-index",
        "KeyConditionExpression": Key("kind").eq("ACCOUNT"),
    }
    items = []
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def lambda_handler(event, context):
    import boto3

    table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    sqs = boto3.client("sqs")
    queue_url = os.environ["QUEUE_URL"]

    accounts = _all_accounts(table)
    failed = 0
    for batch in chunks(accounts, 10):
        resp = sqs.send_message_batch(
            QueueUrl=queue_url, Entries=build_entries(batch))
        failed += len(resp.get("Failed", []))

    print(json.dumps({"accounts": len(accounts), "failed": failed}))
    if failed:
        raise RuntimeError(f"{failed} scan messages could not be queued")
    return {"queued": len(accounts)}
