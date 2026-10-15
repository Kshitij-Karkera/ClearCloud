import datetime
import json
import logging
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src", "scanner"))

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

log = logging.getLogger("clearcloud")

ACCOUNT_LABELS = {
    "aws": "Demo AWS account",
    "azure": "Demo Azure subscription",
    "gcp": "Demo GCP project",
}


def sample_inventory():
    return {
        "disks": [
            {"provider": "aws", "id": "vol-0a1b2c3d4e5f60001", "region": "us-east-1", "size_gb": 100, "disk_type": "gp2", "attached": False},
            {"provider": "aws", "id": "vol-0a1b2c3d4e5f60003", "region": "us-east-1", "size_gb": 50, "disk_type": "gp3", "attached": True},
            {"provider": "azure", "id": "old-data-disk", "region": "eastus", "size_gb": 256, "disk_type": "Premium_LRS", "attached": False},
            {"provider": "gcp", "id": "scratch-disk-1", "region": "us-central1-a", "size_gb": 200, "disk_type": "pd-ssd", "attached": False},
        ],
        "public_ips": [
            {"provider": "aws", "id": "eipalloc-0123456789abcdef0", "region": "us-east-1", "address": "203.0.113.10", "in_use": False},
            {"provider": "azure", "id": "unused-static-ip", "region": "eastus", "address": "203.0.113.20", "in_use": False},
            {"provider": "gcp", "id": "reserved-ip-1", "region": "us-central1", "address": "203.0.113.30", "in_use": False},
            {"provider": "gcp", "id": "web-ip", "region": "us-central1", "address": "203.0.113.31", "in_use": True},
        ],
        "vms": [
            {"provider": "aws", "id": "i-0123456789abcdef0", "region": "us-east-1", "size": "m5.large", "running": True, "avg_cpu": 1.8},
            {"provider": "aws", "id": "i-0123456789abcdef1", "region": "us-east-1", "size": "t3.medium", "running": True, "avg_cpu": 42.0},
            {"provider": "azure", "id": "dev-vm-01", "region": "eastus", "size": "Standard_D2s_v3", "running": True, "avg_cpu": 2.4},
            {"provider": "gcp", "id": "batch-worker-3", "region": "us-central1-a", "size": "e2-standard-2", "running": True, "avg_cpu": 3.9},
        ],
    }


def build_sample_report():
    import rules

    inventory = sample_inventory()
    log.debug(
        "Inventory: %d disks, %d public IPs, %d VMs",
        len(inventory["disks"]), len(inventory["public_ips"]), len(inventory["vms"]),
    )
    findings = rules.analyze(inventory)
    for f in findings:
        f["account_label"] = ACCOUNT_LABELS[f["provider"]]
        log.debug(
            "Finding: %-5s %-18s %-26s save ~$%.2f/mo",
            f["provider"], f["type"], f["resource_id"], f["est_monthly_savings"],
        )
    summary = rules.summarize(findings)
    log.info(
        "Report built: %d findings, total est. savings $%.2f/mo, by provider %s",
        summary["count"], summary["total_est_monthly_savings"],
        {p: v["count"] for p, v in summary["by_provider"].items()},
    )
    return {
        "scanned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "providers": ["aws", "azure", "gcp"],
        "accounts_count": 3,
        "summary": summary,
        "findings": findings,
        "spend": [
            {"service": "Amazon Elastic Compute Cloud - Compute", "cost": 128.40},
            {"service": "Amazon Elastic Block Store", "cost": 22.15},
            {"service": "Amazon Relational Database Service", "cost": 18.90},
            {"service": "AWS Lambda", "cost": 0.42},
        ],
        "cost_explorer_status": "ok",
        "errors": [],
        "demo_note": "Demo mode: all data on this page is simulated sample data for AWS, Azure and GCP.",
    }


def build_sample_accounts():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "accounts": [
            {"account_id": "demo00000001", "provider": "aws", "label": ACCOUNT_LABELS["aws"], "regions": ["us-east-1"], "host_account": True, "created_at": now, "last_status": "ok: 3 findings", "last_scan_at": now},
            {"account_id": "demo00000002", "provider": "azure", "label": ACCOUNT_LABELS["azure"], "regions": [], "host_account": False, "created_at": now, "last_status": "ok: 3 findings", "last_scan_at": now},
            {"account_id": "demo00000003", "provider": "gcp", "label": ACCOUNT_LABELS["gcp"], "regions": [], "host_account": False, "created_at": now, "last_status": "ok: 3 findings", "last_scan_at": now},
        ]
    }


class Handler(BaseHTTPRequestHandler):
    def _begin(self):
        self._t0 = time.perf_counter()
        self._status = None
        self._size = 0
        length = int(self.headers.get("Content-Length") or 0)
        self._body = self.rfile.read(length) if length else b""
        log.debug(
            "-> %s %s from %s | User-Agent: %s",
            self.command, self.path, self.client_address[0],
            (self.headers.get("User-Agent") or "-")[:80],
        )
        if self._body:
            log.debug("   request body (%d bytes): %s", len(self._body), self._body[:300].decode("utf-8", "replace"))

    def _end(self):
        ms = (time.perf_counter() - self._t0) * 1000
        level = logging.INFO if (self._status or 500) < 400 else logging.WARNING
        log.log(level, "%s %s -> %s (%d bytes, %.1f ms)", self.command, self.path, self._status, self._size, ms)

    def _send(self, status, body, ctype):
        self._status = status
        self._size = len(body)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _handle(self, routes):
        self._begin()
        try:
            routes()
        except Exception:
            log.error("Unhandled error while serving %s %s\n%s", self.command, self.path, traceback.format_exc())
            if self._status is None:
                self._json(500, {"error": "Internal error"})
        self._end()

    def do_GET(self):
        self._handle(self._route_get)

    def do_POST(self):
        self._handle(self._route_post)

    def _route_get(self):
        if self.path == "/config.json":
            self._json(200, {"demo": True})
        elif self.path == "/api/report":
            self._json(200, build_sample_report())
        elif self.path == "/api/accounts":
            accounts = build_sample_accounts()
            log.debug("Returning %d demo accounts", len(accounts["accounts"]))
            self._json(200, accounts)
        elif self.path in STATIC:
            name, ctype = STATIC[self.path]
            log.debug("Serving static file src/frontend/%s", name)
            with open(os.path.join(HERE, "src", "frontend", name), "rb") as f:
                self._send(200, f.read(), ctype)
        else:
            log.warning("No route for GET %s", self.path)
            self._json(404, {"error": "Not found"})

    def _route_post(self):
        if self.path == "/api/scan":
            log.info("Scan requested: pretending to queue 3 accounts (demo mode, nothing is scanned)")
            self._json(202, {"queued": 3, "skipped": 0})
        else:
            log.warning("Blocked POST %s: demo mode is read-only", self.path)
            self._json(403, {"error": "Demo mode is read-only"})

    def log_message(self, *args):
        pass


def setup_logging():
    level = logging.DEBUG if os.environ.get("LOG_LEVEL", "DEBUG").upper() == "DEBUG" else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    log.setLevel(level)
    log.addHandler(handler)
    log.propagate = False


if __name__ == "__main__":
    setup_logging()
    port = int(os.environ.get("PORT", "8000"))
    log.info("ClearCloud demo (simulated multi-cloud data) at http://localhost:%d", port)
    log.info("Python %s | project root: %s", sys.version.split()[0], HERE)
    log.info("Log level: %s (set LOG_LEVEL=INFO for less detail)", logging.getLevelName(log.level))
    for name in sorted({n for n, _ in STATIC.values()}):
        path = os.path.join(HERE, "src", "frontend", name)
        log.info("Frontend file %-10s %s", name, "found" if os.path.exists(path) else "MISSING")
    log.info("API routes: GET /config.json, GET /api/report, GET /api/accounts, POST /api/scan")
    log.info("Press Ctrl+C to stop")
    server = HTTPServer(("127.0.0.1", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        server.server_close()