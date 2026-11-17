import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "src", "scanner"))
os.environ.setdefault("TABLE_NAME", "table")
import app as scanner  # noqa: E402


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, **kwargs):
        self.published.append(kwargs)


class AlertOwnerTest(unittest.TestCase):
    def test_alert_carries_the_owner_so_each_user_only_gets_their_own(self):
        sns = FakeSns()
        saved = (scanner.ALERT_TOPIC_ARN, scanner.cc_session.client)
        scanner.ALERT_TOPIC_ARN = "arn:aws:sns:us-east-2:1:alerts"
        scanner.cc_session.client = lambda name: sns
        try:
            finding = {"id": "x:1", "title": "Volume 1",
                       "recommendation": "Delete it", "est_monthly_savings": 1.0}
            account = {"pk": "USER#abc123", "account_id": "a1",
                       "label": "Demo", "provider": "aws"}
            scanner._send_alert(account, None, [finding], {
                                "count": 1, "total_est_monthly_savings": 1.0})
        finally:
            scanner.ALERT_TOPIC_ARN, scanner.cc_session.client = saved
        self.assertEqual(len(sns.published), 1)
        self.assertEqual(sns.published[0]["MessageAttributes"],
                         {"owner": {"DataType": "String", "StringValue": "abc123"}})


if __name__ == "__main__":
    unittest.main()
