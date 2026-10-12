import rules
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "src", "scanner"))


class RulesTest(unittest.TestCase):
    def test_unattached_volume_flagged_and_priced(self):
        vols = [
            {"VolumeId": "vol-1", "Size": 100,
                "VolumeType": "gp3", "State": "available"},
            {"VolumeId": "vol-2", "Size": 100,
                "VolumeType": "gp3", "State": "in-use"},
        ]
        out = rules.detect_unattached_volumes(vols, "us-east-1")
        self.assertEqual([f["resource_id"] for f in out], ["vol-1"])
        self.assertEqual(out[0]["est_monthly_savings"], 8.0)

    def test_unassociated_eip_only(self):
        addrs = [
            {"PublicIp": "1.1.1.1", "AllocationId": "eipalloc-a"},
            {"PublicIp": "2.2.2.2", "AllocationId": "eipalloc-b", "AssociationId": "x"},
        ]
        out = rules.detect_unassociated_eips(addrs, "us-east-1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["resource_id"], "eipalloc-a")

    def test_idle_instance_threshold(self):
        inst = [
            {"InstanceId": "i-low", "InstanceType": "t3.medium", "State": "running"},
            {"InstanceId": "i-busy", "InstanceType": "t3.medium", "State": "running"},
            {"InstanceId": "i-nodata", "InstanceType": "t3.medium", "State": "running"},
            {"InstanceId": "i-stopped", "InstanceType": "t3.medium", "State": "stopped"},
        ]
        cpu = {"i-low": 2.0, "i-busy": 40.0,
               "i-nodata": None, "i-stopped": 1.0}
        out = rules.detect_idle_instances(inst, cpu, "us-east-1")
        self.assertEqual([f["resource_id"] for f in out], ["i-low"])

    def test_unknown_instance_type_does_not_crash(self):
        inst = [{"InstanceId": "i-x", "InstanceType": "z9.mega", "State": "running"}]
        out = rules.detect_idle_instances(inst, {"i-x": 1.0}, "us-east-1")
        self.assertEqual(out[0]["est_monthly_savings"], 0.0)

    def test_summarize(self):
        f = [
            {"type": "a", "est_monthly_savings": 1.5},
            {"type": "a", "est_monthly_savings": 2.5},
            {"type": "b", "est_monthly_savings": 3.0},
        ]
        s = rules.summarize(f)
        self.assertEqual(s["total_est_monthly_savings"], 7.0)
        self.assertEqual(s["by_type"], {"a": 2, "b": 1})


if __name__ == "__main__":
    unittest.main()
