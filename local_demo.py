import datetime
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src", "scanner"))


def sample_inventory():
    return {
        "disks": [
            {"provider": "aws", "id": "vol-0a1b2c3d4e5f60001", "region": "us-east-1",
                "size_gb": 100, "disk_type": "gp2", "attached": False},
            {"provider": "aws", "id": "vol-0a1b2c3d4e5f60003", "region": "us-east-1",
                "size_gb": 50, "disk_type": "gp3", "attached": True},
            {"provider": "azure", "id": "old-data-disk", "region": "eastus",
                "size_gb": 256, "disk_type": "Premium_LRS", "attached": False},
            {"provider": "gcp", "id": "scratch-disk-1", "region": "us-central1-a",
                "size_gb": 200, "disk_type": "pd-ssd", "attached": False},
        ],
        "public_ips": [
            {"provider": "aws", "id": "eipalloc-0123456789abcdef0",
                "region": "us-east-1", "address": "203.0.113.10", "in_use": False},
            {"provider": "azure", "id": "unused-static-ip",
                "region": "eastus", "address": "203.0.113.20", "in_use": False},
            {"provider": "gcp", "id": "reserved-ip-1", "region": "us-central1",
                "address": "203.0.113.30", "in_use": False},
            {"provider": "gcp", "id": "web-ip", "region": "us-central1",
                "address": "203.0.113.31", "in_use": True},
        ],
        "vms": [
            {"provider": "aws", "id": "i-0123456789abcdef0", "region": "us-east-1",
                "size": "m5.large", "running": True, "avg_cpu": 1.8},
            {"provider": "aws", "id": "i-0123456789abcdef1", "region": "us-east-1",
                "size": "t3.medium", "running": True, "avg_cpu": 42.0},
            {"provider": "azure", "id": "dev-vm-01", "region": "eastus",
                "size": "Standard_D2s_v3", "running": True, "avg_cpu": 2.4},
            {"provider": "gcp", "id": "batch-worker-3", "region": "us-central1-a",
                "size": "e2-standard-2", "running": True, "avg_cpu": 3.9},
        ],
    }


def build_sample_report():
    import rules

    findings = rules.analyze(sample_inventory())
    return {
        "scanned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "providers": ["aws", "azure", "gcp"],
        "scanned": ["aws:us-east-1", "azure", "gcp"],
        "summary": rules.summarize(findings),
        "findings": findings,
        "spend": [
            {"service": "Amazon Elastic Compute Cloud - Compute", "cost": 128.40},
            {"service": "Amazon Elastic Block Store", "cost": 22.15},
            {"service": "Amazon Relational Database Service", "cost": 18.90},
            {"service": "AWS Lambda", "cost": 0.42},
        ],
        "cost_explorer_status": "ok",
        "errors": [],
        "history": [],
        "demo_note": "Demo mode: all data on this page is simulated sample data for AWS, Azure and GCP.",
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/report":
            body = json.dumps(build_sample_report()).encode()
            ctype = "application/json"
        elif self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "src", "api", "dashboard.html"), "rb") as f:
                body = f.read()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(
        f"ClearCloud demo (simulated multi-cloud data) at http://localhost:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
