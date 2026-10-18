import base64
import json
import os
import re
import secrets
import traceback
import uuid
from datetime import datetime, timezone

import api_logic as logic
import cc_session

TABLE_NAME = os.environ.get("TABLE_NAME", "")
QUEUE_URL = os.environ.get("QUEUE_URL", "")
SCANNER_FUNCTION = os.environ.get("SCANNER_FUNCTION", "")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "3"))

_clients = {}


def _table():
    if "table" not in _clients:
        _clients["table"] = cc_session.resource("dynamodb").Table(TABLE_NAME)
    return _clients["table"]


def _sqs():
    if "sqs" not in _clients:
        _clients["sqs"] = cc_session.client("sqs")
    return _clients["sqs"]


def _secrets():
    if "secrets" not in _clients:
        _clients["secrets"] = cc_session.client("secretsmanager")
    return _clients["secrets"]


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, default=str),
    }


def _user(event):
    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    raw_groups = str(claims.get("cognito:groups", ""))
    groups = re.sub(r"[\[\],]", " ", raw_groups).split()
    return {
        "sub": claims["sub"],
        "email": claims.get("email", ""),
        "is_admin": "admins" in groups,
    }


def _body(event):
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if len(raw) > logic.MAX_BODY_CHARS:
        raise logic.ApiError(413, "Request body is too large")
    try:
        return json.loads(raw)
    except ValueError:
        raise logic.ApiError(400, "Request body must be valid JSON")


def _query(pk, prefix):
    from boto3.dynamodb.conditions import Key

    kwargs = {"KeyConditionExpression": Key(
        "pk").eq(pk) & Key("sk").begins_with(prefix)}
    items = []
    while True:
        resp = _table().query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def list_accounts(user):
    items = _query(f"USER#{user['sub']}", "ACCOUNT#")
    items.sort(key=lambda i: i.get("created_at", ""))
    return 200, {"accounts": [logic.account_public_view(i) for i in items]}


def create_account(user, body):
    cleaned = logic.validate_account(body, user["is_admin"])
    pk = f"USER#{user['sub']}"
    if len(_query(pk, "ACCOUNT#")) >= logic.MAX_ACCOUNTS:
        raise logic.ApiError(
            409, f"You can connect up to {logic.MAX_ACCOUNTS} accounts")

    account_id = uuid.uuid4().hex[:12]
    item = {
        "pk": pk,
        "sk": f"ACCOUNT#{account_id}",
        "kind": "ACCOUNT",
        "account_id": account_id,
        "provider": cleaned["provider"],
        "label": cleaned["label"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_status": "never scanned",
    }
    secret_name = None
    if cleaned["provider"] == "aws":
        item["regions"] = cleaned["regions"]
        if cleaned["role_arn"]:
            item["role_arn"] = cleaned["role_arn"]
            item["external_id"] = secrets.token_hex(16)
        else:
            item["host_account"] = True
    else:
        secret_name = f"clearcloud/{user['sub']}/{account_id}"
        _secrets().create_secret(
            Name=secret_name,
            SecretString=json.dumps(cleaned["credentials"]),
            Description="ClearCloud read-only cloud credentials",
        )
        item["secret_name"] = secret_name

    try:
        _table().put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    except Exception:
        if secret_name:
            _secrets().delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
        raise
    return 201, {"account": logic.account_public_view(item)}


def delete_account(user, account_id):
    if not re.fullmatch(r"[0-9a-f]{12}", account_id or ""):
        raise logic.ApiError(404, "Account not found")
    pk = f"USER#{user['sub']}"
    key = {"pk": pk, "sk": f"ACCOUNT#{account_id}"}
    item = _table().get_item(Key=key).get("Item")
    if not item:
        raise logic.ApiError(404, "Account not found")
    if item.get("secret_name"):
        try:
            _secrets().delete_secret(
                SecretId=item["secret_name"], ForceDeleteWithoutRecovery=True
            )
        except Exception:
            traceback.print_exc()
    _table().delete_item(Key=key)
    _table().delete_item(Key={"pk": pk, "sk": f"REPORT#{account_id}"})
    return 200, {"deleted": account_id}


def get_report(user):
    pk = f"USER#{user['sub']}"
    report = logic.merge_reports(_query(pk, "REPORT#"))
    report["accounts_count"] = len(_query(pk, "ACCOUNT#"))
    return 200, report


def request_scan(user):
    if not QUEUE_URL:
        raise logic.ApiError(500, "Scan queue is not configured")
    accounts = _query(f"USER#{user['sub']}", "ACCOUNT#")
    if not accounts:
        raise logic.ApiError(400, "Add a cloud account first")
    now = datetime.now(timezone.utc)
    due = [a for a in accounts if logic.should_enqueue(a, now)]
    entries = [
        {"Id": str(i), "MessageBody": json.dumps(
            {"pk": a["pk"], "sk": a["sk"]})}
        for i, a in enumerate(due)
    ]
    if entries:
        resp = _sqs().send_message_batch(QueueUrl=QUEUE_URL, Entries=entries)
        if resp.get("Failed"):
            raise logic.ApiError(502, "Could not queue every scan. Try again.")
        cc_session.start_workers(
            SCANNER_FUNCTION, cc_session.worker_count(
                len(entries), MAX_WORKERS)
        )
    for account in due:
        _table().update_item(
            Key={"pk": account["pk"], "sk": account["sk"]},
            UpdateExpression="SET scan_requested_at = :t",
            ExpressionAttributeValues={":t": now.isoformat()},
        )
    return 202, {"queued": len(due), "skipped": len(accounts) - len(due)}


def lambda_handler(event, context):
    try:
        user = _user(event)
        route = event.get("routeKey", "")
        if route == "GET /api/report":
            status, body = get_report(user)
        elif route == "GET /api/accounts":
            status, body = list_accounts(user)
        elif route == "POST /api/accounts":
            status, body = create_account(user, _body(event))
        elif route == "DELETE /api/accounts/{account_id}":
            status, body = delete_account(
                user, (event.get("pathParameters") or {}).get("account_id")
            )
        elif route == "POST /api/scan":
            status, body = request_scan(user)
        else:
            raise logic.ApiError(404, "Not found")
        return _response(status, body)
    except logic.ApiError as err:
        return _response(err.status, {"error": err.message})
    except Exception:
        traceback.print_exc()
        return _response(500, {"error": "Internal error"})
