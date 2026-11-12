import argparse
import importlib.util
import os
import time
import unittest
from datetime import datetime, timezone

PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "loadtest.py")
spec = importlib.util.spec_from_file_location("loadtest", PATH)
loadtest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loadtest)


class SummaryTest(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(loadtest.percentile([], 95), 0.0)
        self.assertEqual(loadtest.percentile([5], 50), 5)
        self.assertEqual(loadtest.percentile(list(range(1, 101)), 95), 95)

    def test_summarize(self):
        rows = [
            {"start": 0.5, "finish": 4.5, "status": "ok: 0 findings"},
            {"start": 1.0, "finish": 5.0, "status": "ok: 2 findings"},
            {"start": 6.0, "finish": 8.0, "status": "error: boom"},
        ]
        s = loadtest.summarize(rows, 0.3, 8.0)
        self.assertEqual((s["ok"], s["failed"], s["accounts"]), (2, 1, 3))
        self.assertEqual(s["peak_concurrent_observed"], 2)
        self.assertEqual(s["start_delay_max"], 6.0)
        self.assertEqual(s["throughput_per_second"], 0.38)

    def test_chunks(self):
        self.assertEqual([len(c) for c in loadtest.chunks(
            list(range(25)), 10)], [10, 10, 5])


class FakeTable:
    def __init__(self):
        self.items = {}

    def batch_writer(self):
        table = self

        class W:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def put_item(self_inner, Item):
                table.items[(Item["pk"], Item["sk"])] = dict(Item)

            def delete_item(self_inner, Key):
                table.items.pop((Key["pk"], Key["sk"]), None)

        return W()

    def query(self, **kwargs):
        pk = kwargs["ExpressionAttributeValues"][":p"]
        return {"Items": [i for (p, _), i in self.items.items() if p == pk]}


class FakeDDB:
    def __init__(self, table, name):
        self.t, self.name, self.polls = table, name, 0

    def Table(self, name):
        return self.t

    def batch_get_item(self, RequestItems):
        self.polls += 1
        out = []
        for key in RequestItems[self.name]["Keys"]:
            item = self.t.items[(key["pk"], key["sk"])]
            if self.polls >= 2:  # the "scanner" finishes everything on the second poll
                item["last_status"] = "ok: 0 findings"
                item["last_scan_at"] = datetime.now(timezone.utc).isoformat()
            out.append({"sk": item["sk"], "last_status": item["last_status"],
                        "last_scan_at": item.get("last_scan_at", "")})
        return {"Responses": {self.name: out}}


class FakeSNS:
    def __init__(self):
        self.batches = []

    def publish_batch(self, TopicArn, PublishBatchRequestEntries):
        self.batches.append(len(PublishBatchRequestEntries))
        return {"Successful": PublishBatchRequestEntries, "Failed": []}


class FakeEC2:
    def __init__(self, volumes=0):
        self.volumes = volumes

    def describe_volumes(self, **kw):
        return {"Volumes": [{}] * self.volumes}

    def describe_addresses(self):
        return {"Addresses": []}

    def describe_instances(self, **kw):
        return {"Reservations": []}


class RunTest(unittest.TestCase):
    def args(self, **kw):
        base = dict(accounts=25, stack="s", region="us-east-1", timeout=5, poll=0.0,
                    force=False, keep=False, metrics=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_full_run_and_cleanup(self):
        table, sns = FakeTable(), FakeSNS()
        ddb = FakeDDB(table, "tbl")
        code = loadtest.run(self.args(), ddb, sns, None, FakeEC2(), None,
                            table_name="tbl", topic_arn="arn:x")
        self.assertEqual(code, 0)
        self.assertEqual(sns.batches, [10, 10, 5])
        self.assertEqual(table.items, {})  # synthetic data removed
        for name in os.listdir("."):
            if name.startswith("loadtest_25_"):
                os.remove(name)

    def test_refuses_when_account_has_waste(self):
        table, sns = FakeTable(), FakeSNS()
        code = loadtest.run(self.args(), FakeDDB(table, "tbl"), sns, None, FakeEC2(volumes=1), None,
                            table_name="tbl", topic_arn="arn:x")
        self.assertEqual(code, 2)
        self.assertEqual(sns.batches, [])
        self.assertEqual(table.items, {})


if __name__ == "__main__":
    unittest.main()
