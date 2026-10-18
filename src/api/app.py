import base64
import json
import os
import re
import secrets
import time
import traceback
import uuid
from datetime import datetime, timezone

import api_logic as logic
import auth
import cc_session

TABLE_NAME = os.environ.get("TABLE_NAME", "")
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "")
ADMIN_EMAILS = {
    e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
}

_clients = {}


def _table():
    if "table" not in _clients:
        _clients["table"] = cc_session.resource("dynamodb").Table(TABLE_NAME)
    return _clients["table"]


def _sns():
    if "sns" not in _clients:
        _clients["sns"] = cc_session.client("sns")
    return _clients["sns"]


def _ssm():
    if "ssm" not in _clients:
        _clients["ssm"] = cc_session.client("ssm")
    return _clients["ssm"]


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, default=str),
    }


def _secret():
    if len(JWT_SECRET) < 16:
        raise logic.ApiError(500, "Authentication is not configured")
    return JWT_SECRET


def _user(event):
    header = (event.get("headers") or {}).get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise logic.ApiError(401, "Sign in required")
    try:
        claims = auth.verify_token(header[7:].strip(), _secret())
    except auth.AuthError:
        raise logic.ApiError(401, "Your session expired. Please sign in again.")
    return {
        "sub": claims["sub"],
        "email": claims.get("email", ""),
        "is_admin": bool(claims.get("adm")),
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
        body = json.loads(raw)
    except ValueError:
        raise logic.ApiError(400, "Request body must be valid JSON")
    if not isinstance(body, dict):
        raise logic.ApiError(400, "Request body must be a JSON object")
    return body


def _query(pk, prefix):
    from boto3.dynamodb.conditions import Key

    kwargs = {"KeyConditionExpression": Key("pk").eq(pk) & Key("sk").begins_with(prefix)}
    items = []
    while True:
        resp = _table().query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _issue_token(profile):
    claims = {
        "sub": profile["sub"],
        "email": profile["email"],
        "adm": profile["email"] in ADMIN_EMAILS,
    }
    return auth.sign_token(claims, _secret())


def register(body):
    try:
        email = auth.normalize_email(body.get("email"))
        auth.validate_password(body.get("password"))
    except auth.AuthError as err:
        raise logic.ApiError(400, str(err))
    _secret()
    profile = {
        "pk": f"AUTH#{email}",
        "sk": "PROFILE",
        "sub": uuid.uuid4().hex,
        "email": email,
        "password_hash": auth.hash_password(body["password"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _table().put_item(Item=profile, ConditionExpression="attribute_not_exists(pk)")
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            raise logic.ApiError(409, "An account with that email already exists")
        raise
    return 201, {"token": _issue_token(profile), "email": email}


def login(body):
    try:
        email = auth.normalize_email(body.get("email"))
    except auth.AuthError:
        raise logic.ApiError(401, "Incorrect email or password")
    password = body.get("password")
    if not isinstance(password, str) or not password or len(password) > 128:
        raise logic.ApiError(401, "Incorrect email or password")
    _secret()

    key = {"pk": f"AUTH#{email}", "sk": "PROFILE"}
    profile = _table().get_item(Key=key).get("Item")
    if profile and int(profile.get("locked_until", 0)) > time.time():
        raise logic.ApiError(429, "Too many failed attempts. Try again in a few minutes.")
    if not profile:
        auth.waste_time(password)
        raise logic.ApiError(401, "Incorrect email or password")
    if not auth.verify_password(password, profile["password_hash"]):
        resp = _table().update_item(
            Key=key,
            UpdateExpression="ADD fails :one",
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        if int(resp["Attributes"]["fails"]) >= auth.MAX_FAILED_LOGINS:
            _table().update_item(
                Key=key,
                UpdateExpression="SET locked_until = :until REMOVE fails",
                ExpressionAttributeValues={":until": int(time.time()) + auth.LOCKOUT_SECONDS},
            )
        raise logic.ApiError(401, "Incorrect email or password")
    if profile.get("fails") or profile.get("locked_until"):
        _table().update_item(Key=key, UpdateExpression="REMOVE fails, locked_until")
    return 200, {"token": _issue_token(profile), "email": email}


def list_accounts(user):
    items = _query(f"USER#{user['sub']}", "ACCOUNT#")
    items.sort(key=lambda i: i.get("created_at", ""))
    return 200, {"accounts": [logic.account_public_view(i) for i in items]}


def create_account(user, body):
    cleaned = logic.validate_account(body, user["is_admin"])
    pk = f"USER#{user['sub']}"
    if len(_query(pk, "ACCOUNT#")) >= logic.MAX_ACCOUNTS:
        raise logic.ApiError(409, f"You can connect up to {logic.MAX_ACCOUNTS} accounts")

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
        secret_name = f"/clearcloud/{user['sub']}/{account_id}"
        _ssm().put_parameter(
            Name=secret_name,
            Value=json.dumps(cleaned["credentials"]),
            Type="SecureString",
            Tier="Intelligent-Tiering",
            Description="ClearCloud read-only cloud credentials",
        )
        item["secret_name"] = secret_name

    try:
        _table().put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    except Exception:
        if secret_name:
            _ssm().delete_parameter(Name=secret_name)
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
            _ssm().delete_parameter(Name=item["secret_name"])
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
    if not TOPIC_ARN:
        raise logic.ApiError(500, "Scan topic is not configured")
    accounts = _query(f"USER#{user['sub']}", "ACCOUNT#")
    if not accounts:
        raise logic.ApiError(400, "Add a cloud account first")
    now = datetime.now(timezone.utc)
    due = [a for a in accounts if logic.should_enqueue(a, now)]
    entries = [
        {"Id": str(i), "Message": json.dumps({"pk": a["pk"], "sk": a["sk"]})}
        for i, a in enumerate(due)
    ]
    if entries:
        resp = _sns().publish_batch(TopicArn=TOPIC_ARN, PublishBatchRequestEntries=entries)
        if resp.get("Failed"):
            raise logic.ApiError(502, "Could not queue every scan. Try again.")
    for account in due:
        _table().update_item(
            Key={"pk": account["pk"], "sk": account["sk"]},
            UpdateExpression="SET scan_requested_at = :t",
            ExpressionAttributeValues={":t": now.isoformat()},
        )
    return 202, {"queued": len(due), "skipped": len(accounts) - len(due)}


def lambda_handler(event, context):
    try:
        http = (event.get("requestContext") or {}).get("http") or {}
        method = http.get("method", "")
        path = event.get("rawPath") or http.get("path", "")
        name, params, needs_auth = logic.match_route(method, path)
        if name == "register":
            status, body = register(_body(event))
        elif name == "login":
            status, body = login(_body(event))
        else:
            user = _user(event)
            if name == "report":
                status, body = get_report(user)
            elif name == "list_accounts":
                status, body = list_accounts(user)
            elif name == "create_account":
                status, body = create_account(user, _body(event))
            elif name == "delete_account":
                status, body = delete_account(user, params[0])
            else:
                status, body = request_scan(user)
        return _response(status, body)
    except logic.ApiError as err:
        return _response(err.status, {"error": err.message})
    except Exception:
        traceback.print_exc()
        return _response(500, {"error": "Internal error"})
