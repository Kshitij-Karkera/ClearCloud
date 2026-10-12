import rules
import datetime
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src", "scanner"))

REGION = "us-east-1"


def build_sample_report():
    volumes = [
        {"VolumeId": "vol-0a1b2c3d4e5f60001", "Size": 100,
            "VolumeType": "gp2", "State": "available"},
        {"VolumeId": "vol-0a1b2c3d4e5f60002", "Size": 30,
            "VolumeType": "gp3", "State": "available"},
        {"VolumeId": "vol-0a1b2c3d4e5f60003", "Size": 50,
            "VolumeType": "gp3", "State": "in-use"},
    ]
    addresses = [
        {"PublicIp": "203.0.113.10", "AllocationId": "eipalloc-0123456789abcdef0"},
        {"PublicIp": "203.0.113.11", "AllocationId": "eipalloc-0123456789abcdef1",
            "AssociationId": "eipassoc-1"},
    ]
    instances = [
        {"InstanceId": "i-0123456789abcdef0",
            "InstanceType": "m5.large", "State": "running"},
        {"InstanceId": "i-0123456789abcdef1",
            "InstanceType": "t3.medium", "State": "running"},
        {"InstanceId": "i-0123456789abcdef2",
            "InstanceType": "t3.small", "State": "running"},
    ]
    cpu = {"i-0123456789abcdef0": 1.8,
           "i-0123456789abcdef1": 42.0, "i-0123456789abcdef2": 3.2}

    findings = []
    findings += rules.detect_unattached_volumes(volumes, REGION)
    findings += rules.detect_unassociated_eips(addresses, REGION)
    findings += rules.detect_idle_instances(instances, cpu, REGION)
    findings.sort(key=lambda f: f["est_monthly_savings"], reverse=True)

    return {
        "scanned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "regions": [REGION],
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
    print(f"ClearCloud demo (sample data) at http://localhost:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
