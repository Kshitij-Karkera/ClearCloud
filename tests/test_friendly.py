import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "src", "scanner"))
import friendly  # noqa: E402


class FakeClientError(Exception):
    def __init__(self, code, message="An error occurred"):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class FriendlyTests(unittest.TestCase):
    def test_access_denied_says_what_to_do(self):
        text = friendly.explain(FakeClientError("UnauthorizedOperation"))
        self.assertIn("read-only access", text)
        self.assertTrue(text.endswith("[UnauthorizedOperation]"))

    def test_bad_or_expired_credentials(self):
        self.assertIn("expired", friendly.explain(
            FakeClientError("ExpiredToken")))
        self.assertIn("rejected", friendly.explain(
            FakeClientError("AuthFailure")))

    def test_throttling_and_region_problems(self):
        self.assertIn("Wait a minute", friendly.explain(
            FakeClientError("RequestLimitExceeded")))
        self.assertIn("not enabled", friendly.explain(
            FakeClientError("OptInRequired")))
        self.assertIn("region name", friendly.explain(
            FakeClientError("InvalidParameterValue")))

    def test_unapproved_host_account(self):
        text = friendly.explain(PermissionError(
            "AWS account has no role_arn and is not an approved host account"))
        self.assertIn("role ARN", text)

    def test_unknown_error_is_generic_and_keeps_the_type(self):
        text = friendly.explain(ValueError("boom"))
        self.assertIn("unexpected reason", text)
        self.assertTrue(text.endswith("[ValueError]"))

    def test_messages_start_with_a_capital_and_fit_the_status_field(self):
        for code in ("UnauthorizedOperation", "ExpiredToken", "RequestLimitExceeded", "OptInRequired", "Weird"):
            text = friendly.explain(FakeClientError(code))
            self.assertTrue(text[0].isupper())
            self.assertLessEqual(len(text), 300)


if __name__ == "__main__":
    unittest.main()
