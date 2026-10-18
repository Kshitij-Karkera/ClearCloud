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
    import cc_session
    import dispatch_logic
    import reporting

    return api_logic, dispatch_logic, reporting, cc_session


def _load_scanner_app():
    path = os.path.join(ROOT, "scanner", "app.py")
    spec = importlib.util.spec_from_file_location("scanner_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


logic, dispatch_logic, reporting, cc_session = _load_modules()
scanner_app = _load_scanner_app()

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
        self.assertEqual(json.loads(entries[0]["MessageBody"]), {"pk": "USER#20", "sk": "ACCOUNT#a"})


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

    def test_worker_count(self):
        self.assertEqual(cc_session.worker_count(0, 3), 0)
        self.assertEqual(cc_session.worker_count(2, 3), 2)
        self.assertEqual(cc_session.worker_count(50, 3), 3)
        self.assertEqual(cc_session.worker_count(5, 0), 0)

    def test_start_workers_skips_when_not_configured(self):
        self.assertEqual(cc_session.start_workers("", 3), 0)
        self.assertEqual(cc_session.start_workers("fn", 0), 0)


class FakeContext:
    def __init__(self, remaining):
        self.remaining = list(remaining)

    def get_remaining_time_in_millis(self):
        return self.remaining.pop(0) if len(self.remaining) > 1 else self.remaining[0]


class FakeSqs:
    def __init__(self, bodies):
        self.queue = [
            {"Body": json.dumps(b), "ReceiptHandle": f"r{i}"} for i, b in enumerate(bodies)
        ]
        self.deleted = []
        self.delayed = []

    def receive_message(self, **kwargs):
        return {"Messages": [self.queue.pop(0)]} if self.queue else {}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)

    def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):
        self.delayed.append((ReceiptHandle, VisibilityTimeout))


class ScannerDrainTest(unittest.TestCase):
    def setUp(self):
        self.original = (scanner_app._sqs, scanner_app.process)

    def tearDown(self):
        scanner_app._sqs, scanner_app.process = self.original

    def test_drain_deletes_successes_and_retries_failures(self):
        sqs = FakeSqs([{"pk": "a", "sk": "1"}, {"pk": "b", "sk": "2"}, {"pk": "c", "sk": "3"}])
        seen = []

        def fake_process(message, now):
            seen.append(message["pk"])
            if message["pk"] == "b":
                raise RuntimeError("boom")

        scanner_app._sqs = lambda: sqs
        scanner_app.process = fake_process
        with contextlib.redirect_stderr(io.StringIO()):
            result = scanner_app.lambda_handler({"mode": "drain"}, FakeContext([600000]))
        self.assertEqual(result, {"processed": 2, "failed": 1})
        self.assertEqual(seen, ["a", "b", "c"])
        self.assertEqual(sqs.deleted, ["r0", "r2"])
        self.assertEqual(sqs.delayed, [("r1", scanner_app.RETRY_DELAY_SECONDS)])

    def test_drain_stops_when_time_is_nearly_up(self):
        sqs = FakeSqs([{"pk": "a", "sk": "1"}, {"pk": "b", "sk": "2"}])
        scanner_app._sqs = lambda: sqs
        scanner_app.process = lambda message, now: None
        context = FakeContext([600000, scanner_app.MIN_REMAINING_MS - 1])
        result = scanner_app.lambda_handler({"mode": "drain"}, context)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(len(sqs.queue), 1)

    def test_drain_on_empty_queue(self):
        scanner_app._sqs = lambda: FakeSqs([])
        result = scanner_app.lambda_handler({"mode": "drain"}, FakeContext([600000]))
        self.assertEqual(result, {"processed": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()