from providers import aws, azure, gcp
import rules
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "src", "scanner"))


class RulesTest(unittest.TestCase):
    def test_unattached_disk_priced_per_provider(self):
        disks = [
            {"provider": "aws", "id": "a", "region": "r",
                "size_gb": 100, "disk_type": "gp3", "attached": False},
            {"provider": "azure", "id": "b", "region": "r", "size_gb": 100,
                "disk_type": "Premium_LRS", "attached": False},
            {"provider": "gcp", "id": "c", "region": "r", "size_gb": 100,
                "disk_type": "pd-ssd", "attached": False},
            {"provider": "aws", "id": "d", "region": "r",
                "size_gb": 100, "disk_type": "gp3", "attached": True},
        ]
        out = rules.detect_unattached_disks(disks)
        self.assertEqual([f["resource_id"] for f in out], ["a", "b", "c"])
        self.assertEqual([f["est_monthly_savings"]
                         for f in out], [8.0, 15.0, 17.0])
        self.assertEqual([f["provider"] for f in out], ["aws", "azure", "gcp"])

    def test_unused_public_ip_only(self):
        ips = [
            {"provider": "aws", "id": "x", "region": "r",
                "address": "1.1.1.1", "in_use": False},
            {"provider": "gcp", "id": "y", "region": "r",
                "address": "2.2.2.2", "in_use": True},
        ]
        out = rules.detect_unused_public_ips(ips)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["resource_id"], "x")

    def test_idle_vm_threshold(self):
        vms = [
            {"provider": "azure", "id": "low", "region": "r",
                "size": "Standard_B2s", "running": True, "avg_cpu": 2.0},
            {"provider": "azure", "id": "busy", "region": "r",
                "size": "Standard_B2s", "running": True, "avg_cpu": 40.0},
            {"provider": "gcp", "id": "nodata", "region": "r",
                "size": "e2-small", "running": True, "avg_cpu": None},
            {"provider": "aws", "id": "stopped", "region": "r",
                "size": "t3.small", "running": False, "avg_cpu": 1.0},
        ]
        out = rules.detect_idle_vms(vms)
        self.assertEqual([f["resource_id"] for f in out], ["low"])

    def test_unknown_vm_size_does_not_crash(self):
        vms = [{"provider": "gcp", "id": "v", "region": "r",
                "size": "z9-mega", "running": True, "avg_cpu": 1.0}]
        self.assertEqual(rules.detect_idle_vms(
            vms)[0]["est_monthly_savings"], 0.0)

    def test_summarize_by_provider(self):
        findings = [
            {"provider": "aws", "type": "a", "est_monthly_savings": 1.5},
            {"provider": "aws", "type": "a", "est_monthly_savings": 2.5},
            {"provider": "gcp", "type": "b", "est_monthly_savings": 3.0},
        ]
        s = rules.summarize(findings)
        self.assertEqual(s["total_est_monthly_savings"], 7.0)
        self.assertEqual(s["by_type"], {"a": 2, "b": 1})
        self.assertEqual(s["by_provider"]["aws"], {
                         "count": 2, "est_monthly_savings": 4.0})

    def test_analyze_across_providers(self):
        inv = {
            "disks": [{"provider": "azure", "id": "d", "region": "r", "size_gb": 10, "disk_type": "Standard_LRS", "attached": False}],
            "public_ips": [{"provider": "gcp", "id": "i", "region": "r", "address": "9.9.9.9", "in_use": False}],
            "vms": [],
        }
        findings = rules.analyze(inv)
        self.assertEqual({f["provider"] for f in findings}, {"azure", "gcp"})
        self.assertEqual(findings[0]["est_monthly_savings"], 7.3)


class ProviderNormalizerTest(unittest.TestCase):
    def test_aws_volume_and_address(self):
        v = aws.normalize_volume(
            {"VolumeId": "vol-1", "Size": 20, "VolumeType": "gp3", "State": "available"}, "us-east-1")
        self.assertFalse(v["attached"])
        used = aws.normalize_address(
            {"PublicIp": "1.1.1.1", "AllocationId": "e", "AssociationId": "x"}, "us-east-1")
        self.assertTrue(used["in_use"])

    def test_azure_disk_and_ip(self):
        disk = NS(name="d1", location="eastus", disk_size_gb=128,
                  sku=NS(name="Premium_LRS"), managed_by=None)
        d = azure.normalize_disk(disk)
        self.assertFalse(d["attached"])
        self.assertEqual(d["disk_type"], "Premium_LRS")

        static_unused = NS(name="ip1", location="eastus", ip_address="1.2.3.4", sku=NS(name="Standard"),
                           public_ip_allocation_method="Static", ip_configuration=None)
        self.assertFalse(azure.normalize_public_ip(static_unused)["in_use"])

        basic_dynamic = NS(name="ip2", location="eastus", ip_address=None, sku=NS(name="Basic"),
                           public_ip_allocation_method="Dynamic", ip_configuration=None)
        self.assertIsNone(azure.normalize_public_ip(basic_dynamic))

    def test_azure_vm(self):
        vm = NS(name="vm1", location="eastus",
                hardware_profile=NS(vm_size="Standard_B2s"))
        out = azure.normalize_vm(vm, True, 3.0)
        self.assertEqual((out["size"], out["running"],
                         out["avg_cpu"]), ("Standard_B2s", True, 3.0))

    def test_gcp_disk_address_instance(self):
        disk = NS(name="pd1", size_gb=50,
                  type_="https://x/zones/z/diskTypes/pd-ssd", users=[])
        d = gcp.normalize_disk("zones/us-central1-a", disk)
        self.assertEqual((d["region"], d["disk_type"],
                         d["attached"]), ("us-central1-a", "pd-ssd", False))

        reserved = NS(name="a1", address="5.5.5.5",
                      address_type="EXTERNAL", status="RESERVED")
        self.assertFalse(gcp.normalize_address(
            "regions/us-central1", reserved)["in_use"])
        internal = NS(name="a2", address="10.0.0.1",
                      address_type="INTERNAL", status="RESERVED")
        self.assertIsNone(gcp.normalize_address(
            "regions/us-central1", internal))

        inst = NS(name="vm", status="RUNNING",
                  machine_type="https://x/machineTypes/e2-small")
        out = gcp.normalize_instance("zones/us-central1-a", inst, 2.5)
        self.assertEqual((out["size"], out["running"]), ("e2-small", True))


if __name__ == "__main__":
    unittest.main()
