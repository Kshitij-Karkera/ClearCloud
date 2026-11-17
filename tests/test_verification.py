import json
import os
import sys
import unittest

import test_backend as tb

sys.path.insert(0, os.path.join(tb.ROOT, "api"))
import verify  # noqa: E402

GUID = "11111111-2222-3333-4444-555555555555"
AZURE = {"tenant_id": GUID, "client_id": GUID,
         "client_secret": "s3cret", "subscription_id": GUID}


class AzureCheckTest(unittest.TestCase):
    def test_good_credentials_pass(self):
        calls = []

        def http(url, data=None, token=None):
            calls.append(url)
            if "login.microsoftonline.com" in url:
                return 200, {"access_token": "tok"}
            self.assertEqual(token, "tok")
            return 200, {}

        verify.check_azure(AZURE, http)
        self.assertEqual(len(calls), 2)

    def test_wrong_secret_is_explained(self):
        def http(url, data=None, token=None): return (
            401, {"error_description": "AADSTS7000215: Invalid client secret"})
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.check_azure(AZURE, http)
        self.assertIn("client secret", ctx.exception.message)
        self.assertNotIn("AADSTS", ctx.exception.message)

    def test_unknown_tenant_and_client_are_explained(self):
        for code, word in (("AADSTS90002", "Tenant ID"), ("AADSTS700016", "Client ID")):
            def http(url, data=None, token=None, c=code): return (
                400, {"error_description": c + ": x"})
            with self.assertRaises(verify.VerifyError) as ctx:
                verify.check_azure(AZURE, http)
            self.assertIn(word, ctx.exception.message)

    def test_subscription_the_app_cannot_see(self):
        def http(url, data=None, token=None):
            return (200, {"access_token": "t"}) if "login" in url else (404, {})

        with self.assertRaises(verify.VerifyError) as ctx:
            verify.check_azure(AZURE, http)
        self.assertIn("Subscription ID", ctx.exception.message)


def _gcp_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    return {
        "type": "service_account", "project_id": "p", "private_key_id": "k1",
        "private_key": pem, "client_email": "sa@p.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.headers = status, {}
        self.data = json.dumps(body).encode()


class GcpCheckTest(unittest.TestCase):
    def test_accepted_key_passes(self):
        request = lambda *a, **k: FakeResponse(200,
                                               {"access_token": "t", "expires_in": 3600})
        verify.check_gcp(_gcp_key(), request)

    def test_rejected_key_is_explained(self):
        request = lambda *a, **k: FakeResponse(
            400, {"error": "invalid_grant", "error_description": "Invalid JWT"})
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.check_gcp(_gcp_key(), request)
        self.assertIn("rejected", ctx.exception.message)

    def test_garbage_key_is_explained(self):
        bad = _gcp_key()
        bad["private_key"] = "not a key"
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.check_gcp(bad, lambda *a, **k: None)
        self.assertIn("not valid", ctx.exception.message)


class FakeSts:
    def __init__(self, code=None):
        self.code, self.calls = code, []

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        if self.code:
            err = Exception("denied")
            err.response = {"Error": {"Code": self.code}}
            raise err
        return {"Credentials": {}}


class VerificationFlowTest(unittest.TestCase):
    def setUp(self):
        self.table = tb.FakeTable()
        self.saved = (tb.api_app._table, tb.api_app._query, tb.api_app._ssm)
        tb.api_app._table = lambda: self.table
        tb.api_app._query = lambda pk, prefix: [
            dict(v) for (p, s), v in self.table.items.items() if p == pk and s.startswith(prefix)
        ]
        self.saved_checks = (tb.api_app.verify.check_azure,
                             tb.api_app.verify.check_gcp, tb.api_app._sts)
        self.sts = FakeSts()
        tb.api_app._sts = lambda: self.sts
        ssm = type("Ssm", (), {"put_parameter": lambda s, **
                   k: None, "delete_parameter": lambda s, **k: None})()
        tb.api_app._ssm = lambda: ssm

    def tearDown(self):
        tb.api_app.verify.check_azure, tb.api_app.verify.check_gcp, tb.api_app._sts = self.saved_checks
        tb.api_app._table, tb.api_app._query, tb.api_app._ssm = self.saved

    def register(self, email="me@example.com"):
        return tb.call("POST", "/api/auth/register", {"email": email, "password": "Passw0rdOK"})

    def token(self):
        return self.register()[1]["token"]

    def connect_role(self, token):
        return tb.call("POST", "/api/accounts", {
            "provider": "aws", "label": "Mine", "regions": "us-east-2",
            "role_arn": "arn:aws:iam::123456789012:role/ClearCloudReadOnly"}, token)

    def test_role_account_starts_unverified(self):
        status, body = self.connect_role(self.token())
        self.assertEqual(status, 201)
        self.assertFalse(body["account"]["verified"])
        self.assertEqual(body["account"]["last_status"],
                         "waiting for verification")

    def test_verify_assumes_the_role_with_its_external_id(self):
        token = self.token()
        _, body = self.connect_role(token)
        acc = body["account"]
        status, done = tb.call(
            "POST", f"/api/accounts/{acc['account_id']}/verify", None, token)
        self.assertEqual(status, 200)
        self.assertTrue(done["account"]["verified"])
        self.assertEqual(self.sts.calls[0]["ExternalId"], acc["external_id"])
        _, listed = tb.call("GET", "/api/accounts", None, token)
        self.assertTrue(listed["accounts"][0]["verified"])

    def test_failed_verify_explains_and_stays_unverified(self):
        token = self.token()
        _, body = self.connect_role(token)
        self.sts.code = "AccessDenied"
        status, err = tb.call(
            "POST", f"/api/accounts/{body['account']['account_id']}/verify", None, token)
        self.assertEqual(status, 400)
        self.assertIn("trust policy", err["error"])
        _, listed = tb.call("GET", "/api/accounts", None, token)
        self.assertFalse(listed["accounts"][0]["verified"])

    def test_cannot_verify_someone_elses_account(self):
        _, body = self.connect_role(self.token())
        other = self.register("other@example.com")[1]["token"]
        status, _ = tb.call(
            "POST", f"/api/accounts/{body['account']['account_id']}/verify", None, other)
        self.assertEqual(status, 404)

    def test_azure_with_bad_credentials_is_refused_and_not_saved(self):
        def refuse(creds):
            raise verify.VerifyError("Azure rejected the client secret.")

        tb.api_app.verify.check_azure = refuse
        token = self.token()
        status, err = tb.call("POST", "/api/accounts", {
            "provider": "azure", "label": "Az", "credentials": AZURE}, token)
        self.assertEqual(status, 400)
        self.assertIn("client secret", err["error"])
        self.assertEqual(tb.call("GET", "/api/accounts",
                         None, token)[1]["accounts"], [])

    def test_azure_with_good_credentials_is_saved_as_verified(self):
        tb.api_app.verify.check_azure = lambda creds: None
        token = self.token()
        status, body = tb.call("POST", "/api/accounts", {
            "provider": "azure", "label": "Az", "credentials": AZURE}, token)
        self.assertEqual(status, 201)
        self.assertTrue(body["account"]["verified"])

    def test_signups_are_capped_per_hour(self):
        old = tb.api_app.MAX_SIGNUPS_PER_HOUR
        tb.api_app.MAX_SIGNUPS_PER_HOUR = 2
        try:
            codes = [self.register(f"u{i}@example.com")[0] for i in range(3)]
        finally:
            tb.api_app.MAX_SIGNUPS_PER_HOUR = old
        self.assertEqual(codes, [201, 201, 429])


if __name__ == "__main__":
    unittest.main()
