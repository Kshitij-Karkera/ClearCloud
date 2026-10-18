import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..", "src")


def _load_modules():
    for sub in ("scanner", "api", "dispatcher"):
        sys.path.insert(0, os.path.join(ROOT, sub))
    import api_logic
    import auth
    import cc_session
    import dispatch_logic
    import reporting

    return api_logic, dispatch_logic, reporting, cc_session, auth


def _load_app(folder, name):
    path = os.path.join(ROOT, folder, "app.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


os.environ["JWT_SECRET"] = "test-secret-" + "x" * 24
os.environ["ADMIN_EMAILS"] = "boss@example.com"
os.environ["TOPIC_ARN"] = "arn:aws:sns:us-east-1:123456789012:scan"
os.environ["TABLE_NAME"] = "table"

logic, dispatch_logic, reporting, cc_session, auth = _load_modules()
scanner_app = _load_app("scanner", "scanner_app")
api_app = _load_app("api", "api_app")

GUID = "11111111-2222-3333-4444-555555555555"
ROLE = "arn:aws:iam::123456789012:role/ClearCloudReadOnly"


def gcp_key(**overrides):
    key = {
        "type": "service_account",
        "project_id": "p",
        "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        "client_email": "sa@p.iam.gserviceaccount.com",
    }
    key.update(overrides)
    return key


class ValidateAccountTest(unittest.TestCase):
    def test_aws_with_role_is_allowed_for_anyone(self):
        out = logic.validate_account(
            {"provider": "aws", "label": "Team", "regions": "us-east-1, us-west-2", "role_arn": ROLE},
            is_admin=False,
        )
        self.assertEqual(out["regions"], ["us-east-1", "us-west-2"])
        self.assertEqual(out["role_arn"], ROLE)

    def test_host_account_scan_requires_admin(self):
        body = {"provider": "aws", "label": "Host"}
        with self.assertRaises(logic.ApiError) as ctx:
            logic.validate_account(body, is_admin=False)
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(logic.validate_account(body, is_admin=True)["role_arn"], "")

    def test_rejects_bad_inputs(self):
        bad = [
            {"provider": "oracle", "label": "x"},
            {"provider": "aws", "label": ""},
            {"provider": "aws", "label": "x", "role_arn": "not-an-arn"},
            {"provider": "aws", "label": "x", "role_arn": ROLE, "regions": ["../etc"]},
            {"provider": "aws", "label": "x" * 61, "role_arn": ROLE},
        ]
        for body in bad:
            with self.assertRaises(logic.ApiError, msg=str(body)) as ctx:
                logic.validate_account(body, is_admin=True)
            self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(logic.ApiError):
            logic.validate_account("nope", is_admin=True)

    def test_azure_credentials(self):
        creds = {"tenant_id": GUID, "client_id": GUID, "client_secret": "s3cret", "subscription_id": GUID}
        out = logic.validate_account({"provider": "azure", "label": "Az", "credentials": creds}, False)
        self.assertEqual(out["credentials"]["client_secret"], "s3cret")
        creds["tenant_id"] = "not-a-guid"
        with self.assertRaises(logic.ApiError):
            logic.validate_account({"provider": "azure", "label": "Az", "credentials": creds}, False)
        with self.assertRaises(logic.ApiError):
            logic.validate_account({"provider": "azure", "label": "Az"}, False)

    def test_gcp_credentials_accept_json_text_or_object(self):
        as_text = json.dumps(gcp_key())
        for creds in (as_text, gcp_key()):
            out = logic.validate_account({"provider": "gcp", "label": "G", "credentials": creds}, False)
            self.assertEqual(out["credentials"]["project_id"], "p")
        for creds in ("{not json", gcp_key(type="authorized_user"), {"type": "service_account"}):
            with self.assertRaises(logic.ApiError):
                logic.validate_account({"provider": "gcp", "label": "G", "credentials": creds}, False)


class AccountViewTest(unittest.TestCase):
    def test_public_view_never_exposes_secret_name(self):
        item = {
            "pk": "USER#u", "sk": "ACCOUNT#a", "account_id": "a", "provider": "azure",
            "label": "Az", "secret_name": "clearcloud/u/a", "created_at": "t", "last_status": "ok",
        }
        view = logic.account_public_view(item)
        self.assertNotIn("secret_name", view)
        self.assertNotIn("pk", view)
        self.assertEqual(view["label"], "Az")

    def test_role_accounts_show_external_id(self):
        item = {"account_id": "a", "provider": "aws", "label": "L", "role_arn": ROLE, "external_id": "abc"}
        self.assertEqual(logic.account_public_view(item)["external_id"], "abc")


class ScanCooldownTest(unittest.TestCase):
    def test_should_enqueue(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(logic.should_enqueue({}, now))
        recent = {"scan_requested_at": (now - timedelta(seconds=10)).isoformat()}
        old = {"scan_requested_at": (now - timedelta(seconds=120)).isoformat()}
        self.assertFalse(logic.should_enqueue(recent, now))
        self.assertTrue(logic.should_enqueue(old, now))
        self.assertTrue(logic.should_enqueue({"scan_requested_at": "garbage"}, now))


class MergeReportsTest(unittest.TestCase):
    def _report(self, provider, label, savings, spend=None, status="not requested", errors=None, at="2026-10-12T10:00:00+00:00"):
        finding = {
            "provider": provider, "type": "unattached_disk", "resource_id": label,
            "est_monthly_savings": savings, "account_label": label,
        }
        return {
            "provider": provider, "account_label": label, "scanned_at": at,
            "findings_json": json.dumps([finding]),
            "spend_json": json.dumps(spend or []),
            "cost_explorer_status": status,
            "errors_json": json.dumps(errors or []),
        }

    def test_merge_sorts_sums_and_prefixes_errors(self):
        reports = [
            self._report("aws", "A", 5.0, [{"service": "EC2", "cost": 10.0}], "ok", at="2026-10-12T09:00:00+00:00"),
            self._report("gcp", "G", 9.0, errors=["boom"], at="2026-10-12T11:00:00+00:00"),
            self._report("aws", "B", 1.0, [{"service": "EC2", "cost": 2.5}], "ok"),
        ]
        merged = logic.merge_reports(reports)
        self.assertEqual([f["est_monthly_savings"] for f in merged["findings"]], [9.0, 5.0, 1.0])
        self.assertEqual(merged["summary"]["total_est_monthly_savings"], 15.0)
        self.assertEqual(merged["summary"]["by_provider"]["aws"]["count"], 2)
        self.assertEqual(merged["spend"], [{"service": "EC2", "cost": 12.5}])
        self.assertEqual(merged["providers"], ["aws", "gcp"])
        self.assertEqual(merged["errors"], ["G: boom"])
        self.assertEqual(merged["scanned_at"], "2026-10-12T11:00:00+00:00")
        self.assertEqual(merged["cost_explorer_status"], "ok")

    def test_empty(self):
        merged = logic.merge_reports([])
        self.assertIsNone(merged["scanned_at"])
        self.assertEqual(merged["findings"], [])
        self.assertEqual(merged["summary"]["count"], 0)

    def test_cost_explorer_status_precedence(self):
        self.assertEqual(logic.cost_explorer_status(["not requested", "unavailable: X"]), "unavailable: X")
        self.assertEqual(logic.cost_explorer_status(["unavailable: X", "ok"]), "ok")
        self.assertEqual(logic.cost_explorer_status([]), "not requested")


class DispatchTest(unittest.TestCase):
    def test_chunks_and_entries(self):
        accounts = [{"pk": f"USER#{i}", "sk": "ACCOUNT#a"} for i in range(25)]
        batches = list(dispatch_logic.chunks(accounts, 10))
        self.assertEqual([len(b) for b in batches], [10, 10, 5])
        entries = dispatch_logic.build_entries(batches[2])
        self.assertEqual([e["Id"] for e in entries], ["0", "1", "2", "3", "4"])
        self.assertEqual(json.loads(entries[0]["Message"]), {"pk": "USER#20", "sk": "ACCOUNT#a"})


class ReportingTest(unittest.TestCase):
    ACCOUNT = {"pk": "USER#u", "account_id": "abc123abc123", "label": "Prod", "provider": "aws"}

    def test_report_item_tags_findings_and_truncates(self):
        findings = [{"id": str(i), "est_monthly_savings": 1.0} for i in range(reporting.MAX_FINDINGS_STORED + 5)]
        item = reporting.build_report_item(self.ACCOUNT, "t", findings, {"count": 1}, [], "ok", [])
        stored = json.loads(item["findings_json"])
        self.assertEqual(len(stored), reporting.MAX_FINDINGS_STORED)
        self.assertEqual(stored[0]["account_label"], "Prod")
        self.assertTrue(item["truncated"])
        self.assertEqual(item["sk"], "REPORT#abc123abc123")
        self.assertNotIn("account_label", findings[0])

    def test_history_item_has_ttl(self):
        item = reporting.build_history_item(self.ACCOUNT, "2026-10-12T10:00:00+00:00", 1000.7, 42.5)
        self.assertEqual(item["expires_at"], 1000 + reporting.HISTORY_TTL_DAYS * 86400)
        self.assertTrue(item["sk"].startswith("HIST#abc123abc123#"))
        self.assertEqual(json.loads(item["total_json"]), 42.5)


class SessionHelperTest(unittest.TestCase):
    def test_credentials_from_env(self):
        self.assertEqual(cc_session.credentials_from_env({}), {})
        env = {"CC_ACCESS_KEY_ID": "AK", "CC_SECRET_ACCESS_KEY": "SK", "CC_SESSION_TOKEN": "TK"}
        self.assertEqual(
            cc_session.credentials_from_env(env),
            {"aws_access_key_id": "AK", "aws_secret_access_key": "SK", "aws_session_token": "TK"},
        )
        env["CC_SESSION_TOKEN"] = ""
        self.assertIsNone(cc_session.credentials_from_env(env)["aws_session_token"])


class ScannerEventTest(unittest.TestCase):
    def setUp(self):
        self.original = scanner_app.process

    def tearDown(self):
        scanner_app.process = self.original

    def test_sns_event_is_unwrapped(self):
        seen = []
        scanner_app.process = lambda message, now: seen.append(message)
        event = {"Records": [{"Sns": {"Message": json.dumps({"pk": "a", "sk": "1"})}}]}
        self.assertEqual(scanner_app.lambda_handler(event, None), {"processed": 1})
        self.assertEqual(seen, [{"pk": "a", "sk": "1"}])

    def test_direct_invoke_and_empty_event(self):
        seen = []
        scanner_app.process = lambda message, now: seen.append(message)
        self.assertEqual(scanner_app.lambda_handler({"pk": "a", "sk": "1"}, None), {"processed": 1})
        self.assertEqual(scanner_app.lambda_handler({}, None), {"processed": 0})
        self.assertEqual(len(seen), 1)

    def test_failures_are_raised_so_lambda_retries(self):
        def boom(message, now):
            raise RuntimeError("boom")

        scanner_app.process = boom
        event = {"Records": [{"Sns": {"Message": json.dumps({"pk": "a", "sk": "1"})}}]}
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                scanner_app.lambda_handler(event, None)


class RouteTest(unittest.TestCase):
    def test_known_routes(self):
        self.assertEqual(logic.match_route("POST", "/api/auth/login"), ("login", (), False))
        self.assertEqual(logic.match_route("GET", "/api/report/"), ("report", (), True))
        name, params, needs_auth = logic.match_route("DELETE", "/api/accounts/0123456789ab")
        self.assertEqual((name, params, needs_auth), ("delete_account", ("0123456789ab",), True))

    def test_unknown_routes_are_404(self):
        for method, path in (("GET", "/api/nope"), ("PUT", "/api/accounts"), ("DELETE", "/api/accounts/../x")):
            with self.assertRaises(logic.ApiError) as ctx:
                logic.match_route(method, path)
            self.assertEqual(ctx.exception.status, 404)


class AuthTest(unittest.TestCase):
    def test_password_hash_round_trip(self):
        stored = auth.hash_password("Passw0rdOK")
        self.assertTrue(auth.verify_password("Passw0rdOK", stored))
        self.assertFalse(auth.verify_password("Passw0rdNO", stored))
        self.assertFalse(auth.verify_password("x", "garbage"))
        self.assertNotEqual(stored, auth.hash_password("Passw0rdOK"))

    def test_password_and_email_rules(self):
        for bad in ("short1A", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere", "", None):
            with self.assertRaises(auth.AuthError, msg=str(bad)):
                auth.validate_password(bad)
        self.assertEqual(auth.validate_password("Passw0rdOK"), "Passw0rdOK")
        self.assertEqual(auth.normalize_email("  Me@Example.COM "), "me@example.com")
        for bad in ("nope", "a@b", "a b@c.com", 5):
            with self.assertRaises(auth.AuthError):
                auth.normalize_email(bad)

    def test_token_round_trip_and_expiry(self):
        token = auth.sign_token({"sub": "u1", "email": "a@b.co"}, "k" * 32, now=1000, ttl=60)
        self.assertEqual(auth.verify_token(token, "k" * 32, now=1030)["sub"], "u1")
        with self.assertRaises(auth.AuthError):
            auth.verify_token(token, "k" * 32, now=1061)

    def test_tampered_tokens_are_rejected(self):
        token = auth.sign_token({"sub": "u1"}, "k" * 32, now=1000, ttl=600)
        head, body, sig = token.split(".")
        forged_body = auth._b64(json.dumps({"sub": "admin", "exp": 9999999999}).encode())
        none_head = auth._b64(json.dumps({"alg": "none"}).encode())
        for bad in (
            token[:-2] + "xx",
            f"{head}.{forged_body}.{sig}",
            f"{none_head}.{body}.",
            "not-a-token",
            "",
        ):
            with self.assertRaises(auth.AuthError, msg=bad):
                auth.verify_token(bad, "k" * 32, now=1100)
        with self.assertRaises(auth.AuthError):
            auth.verify_token(token, "other" * 8, now=1100)


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["pk"], Item["sk"])
        if ConditionExpression and key in self.items:
            err = Exception("exists")
            err.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise err
        self.items[key] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues=None, ReturnValues=None):
        item = self.items[(Key["pk"], Key["sk"])]
        if UpdateExpression.startswith("ADD fails"):
            item["fails"] = item.get("fails", 0) + 1
            return {"Attributes": {"fails": item["fails"]}}
        if UpdateExpression.startswith("SET locked_until"):
            item["locked_until"] = ExpressionAttributeValues[":until"]
            item.pop("fails", None)
        elif UpdateExpression.startswith("REMOVE"):
            item.pop("fails", None)
            item.pop("locked_until", None)
        elif UpdateExpression.startswith("SET scan_requested_at"):
            item["scan_requested_at"] = ExpressionAttributeValues[":t"]


class FakeSns:
    def __init__(self, failed=None):
        self.batches = []
        self.failed = failed or []

    def publish_batch(self, TopicArn, PublishBatchRequestEntries):
        self.batches.append((TopicArn, PublishBatchRequestEntries))
        return {"Failed": self.failed}


def call(method, path, body=None, token=None):
    event = {"requestContext": {"http": {"method": method}}, "rawPath": path, "headers": {}}
    if token:
        event["headers"]["authorization"] = "Bearer " + token
    if body is not None:
        event["body"] = json.dumps(body)
    result = api_app.lambda_handler(event, None)
    return result["statusCode"], json.loads(result["body"])


class ApiFlowTest(unittest.TestCase):
    def setUp(self):
        self.table = FakeTable()
        self.sns = FakeSns()
        self.saved = (api_app._table, api_app._sns, api_app._query)
        api_app._table = lambda: self.table
        api_app._sns = lambda: self.sns
        api_app._query = lambda pk, prefix: [
            dict(v) for (p, s), v in self.table.items.items() if p == pk and s.startswith(prefix)
        ]

    def tearDown(self):
        api_app._table, api_app._sns, api_app._query = self.saved

    def register(self, email="me@example.com", password="Passw0rdOK"):
        return call("POST", "/api/auth/register", {"email": email, "password": password})

    def test_register_then_login(self):
        status, body = self.register()
        self.assertEqual(status, 201)
        claims = auth.verify_token(body["token"], os.environ["JWT_SECRET"])
        self.assertEqual(claims["email"], "me@example.com")
        self.assertFalse(claims["adm"])
        self.assertNotIn("Passw0rdOK", json.dumps(list(self.table.items.values())))
        status, body = call("POST", "/api/auth/login", {"email": "ME@example.com", "password": "Passw0rdOK"})
        self.assertEqual(status, 200)
        self.assertIn("token", body)

    def test_duplicate_and_weak_registration(self):
        self.register()
        self.assertEqual(self.register()[0], 409)
        self.assertEqual(self.register("b@example.com", "weak")[0], 400)
        self.assertEqual(self.register("not-an-email")[0], 400)

    def test_admin_email_gets_admin_claim(self):
        _, body = self.register("boss@example.com")
        self.assertTrue(auth.verify_token(body["token"], os.environ["JWT_SECRET"])["adm"])

    def test_wrong_password_is_generic_and_locks_after_five_failures(self):
        self.register()
        for _ in range(auth.MAX_FAILED_LOGINS):
            status, body = call("POST", "/api/auth/login", {"email": "me@example.com", "password": "Wrong0Pass"})
            self.assertEqual((status, body["error"]), (401, "Incorrect email or password"))
        status, _ = call("POST", "/api/auth/login", {"email": "me@example.com", "password": "Passw0rdOK"})
        self.assertEqual(status, 429)

    def test_unknown_user_gets_same_error_as_wrong_password(self):
        status, body = call("POST", "/api/auth/login", {"email": "ghost@example.com", "password": "Passw0rdOK"})
        self.assertEqual((status, body["error"]), (401, "Incorrect email or password"))

    def test_protected_routes_need_a_valid_token(self):
        self.assertEqual(call("GET", "/api/report")[0], 401)
        self.assertEqual(call("GET", "/api/accounts", token="garbage")[0], 401)
        expired = auth.sign_token({"sub": "u"}, os.environ["JWT_SECRET"], now=1, ttl=1)
        self.assertEqual(call("GET", "/api/accounts", token=expired)[0], 401)
        self.assertEqual(call("GET", "/api/nope")[0], 404)

    def test_scan_publishes_one_message_per_due_account_and_honours_cooldown(self):
        _, body = self.register()
        token = body["token"]
        sub = auth.verify_token(token, os.environ["JWT_SECRET"])["sub"]
        for n in range(2):
            self.table.items[(f"USER#{sub}", f"ACCOUNT#{n}")] = {
                "pk": f"USER#{sub}", "sk": f"ACCOUNT#{n}", "account_id": str(n),
            }
        status, body = call("POST", "/api/scan", {}, token)
        self.assertEqual((status, body), (202, {"queued": 2, "skipped": 0}))
        topic, entries = self.sns.batches[0]
        self.assertEqual(topic, os.environ["TOPIC_ARN"])
        self.assertEqual(sorted(json.loads(e["Message"])["sk"] for e in entries), ["ACCOUNT#0", "ACCOUNT#1"])
        status, body = call("POST", "/api/scan", {}, token)
        self.assertEqual((status, body), (202, {"queued": 0, "skipped": 2}))
        self.assertEqual(len(self.sns.batches), 1)

    def test_scan_without_accounts_and_failed_publish(self):
        _, body = self.register()
        token = body["token"]
        self.assertEqual(call("POST", "/api/scan", {}, token)[0], 400)
        sub = auth.verify_token(token, os.environ["JWT_SECRET"])["sub"]
        self.table.items[(f"USER#{sub}", "ACCOUNT#0")] = {"pk": f"USER#{sub}", "sk": "ACCOUNT#0"}
        self.sns.failed = [{"Id": "0"}]
        self.assertEqual(call("POST", "/api/scan", {}, token)[0], 502)

    def test_host_account_is_admin_only(self):
        _, body = self.register()
        status, _ = call("POST", "/api/accounts", {"provider": "aws", "label": "Host"}, body["token"])
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
