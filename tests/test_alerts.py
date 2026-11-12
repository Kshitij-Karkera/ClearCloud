import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "src", "scanner"))
import alerts  # noqa: E402


def f(i, sav):
    return {"id": f"x:{i}", "title": f"Volume {i}", "recommendation": "Delete it", "est_monthly_savings": sav}


class AlertsTest(unittest.TestCase):
    def test_parse_handles_bad_input(self):
        self.assertEqual(alerts.parse_findings(None), [])
        self.assertEqual(alerts.parse_findings("not json"), [])
        self.assertEqual(alerts.parse_findings('{"a":1}'), [])
        self.assertEqual(len(alerts.parse_findings(json.dumps([f(1, 1)]))), 1)

    def test_only_new_findings_sorted(self):
        fresh = alerts.new_findings([f(1, 5)], [f(1, 5), f(2, 1), f(3, 9)])
        self.assertEqual([x["id"] for x in fresh], ["x:3", "x:2"])

    def test_no_new_findings(self):
        self.assertEqual(alerts.new_findings([f(1, 5)], [f(1, 5)]), [])

    def test_first_scan_everything_is_new(self):
        self.assertEqual(len(alerts.new_findings([], [f(1, 1), f(2, 2)])), 2)

    def test_message(self):
        subject, body = alerts.build_alert(
            {"account_label": "Demo", "provider": "aws"}, [
                f(1, 3.5), f(2, 1.0)],
            {"count": 4, "total_est_monthly_savings": 9.0})
        self.assertIn("2 new issues", subject)
        self.assertIn("$4.50", subject)
        self.assertLessEqual(len(subject), 100)
        self.assertIn("Volume 1", body)

    def test_long_list_truncated(self):
        fresh = [f(i, 1) for i in range(15)]
        _, body = alerts.build_alert(
            {"account_id": "a", "provider": "aws"}, fresh, {})
        self.assertIn("and 5 more", body)


if __name__ == "__main__":
    unittest.main()
