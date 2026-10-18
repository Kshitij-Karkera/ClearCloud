import json
import re
from datetime import datetime

PROVIDERS = ("aws", "azure", "gcp")
MAX_ACCOUNTS = 10
SCAN_COOLDOWN_SECONDS = 60
MAX_BODY_CHARS = 30000

REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
ROLE_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:role/[A-Za-z0-9+=,.@_/-]{1,512}$")
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
AZURE_FIELDS = ("tenant_id", "client_id", "client_secret", "subscription_id")
AZURE_GUID_FIELDS = ("tenant_id", "client_id", "subscription_id")
GCP_REQUIRED = ("type", "project_id", "private_key", "client_email")
MAX_CREDENTIAL_CHARS = 8000

ROUTES = (
    ("POST", r"/api/auth/register", "register", False),
    ("POST", r"/api/auth/login", "login", False),
    ("GET", r"/api/report", "report", True),
    ("GET", r"/api/accounts", "list_accounts", True),
    ("POST", r"/api/accounts", "create_account", True),
    ("DELETE", r"/api/accounts/([0-9a-f]{12})", "delete_account", True),
    ("POST", r"/api/scan", "scan", True),
)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def match_route(method, path):
    clean = (path or "").rstrip("/") or "/"
    for route_method, pattern, name, needs_auth in ROUTES:
        if route_method == method:
            hit = re.fullmatch(pattern, clean)
            if hit:
                return name, hit.groups(), needs_auth
    raise ApiError(404, "Not found")


def _text(value, name, max_len=200, required=True):
    if value is None or value == "":
        if required:
            raise ApiError(400, f"{name} is required")
        return ""
    if not isinstance(value, str):
        raise ApiError(400, f"{name} must be text")
    value = value.strip()
    if required and not value:
        raise ApiError(400, f"{name} is required")
    if len(value) > max_len:
        raise ApiError(400, f"{name} is too long")
    return value


def _validate_aws(body, is_admin):
    regions = body.get("regions") or []
    if isinstance(regions, str):
        regions = [r for r in re.split(r"[,\s]+", regions) if r]
    if not isinstance(regions, list) or len(regions) > 5:
        raise ApiError(400, "regions must be a list of up to 5 AWS regions")
    for region in regions:
        if not isinstance(region, str) or not REGION_RE.match(region):
            raise ApiError(400, f"Invalid region: {region!r}")
    role_arn = _text(body.get("role_arn"), "role_arn", 600, required=False)
    if role_arn:
        if not ROLE_ARN_RE.match(role_arn):
            raise ApiError(
                400, "role_arn must look like arn:aws:iam::<12-digit account id>:role/<name>"
            )
    elif not is_admin:
        raise ApiError(
            403,
            "Scanning the account that hosts ClearCloud is restricted to admins. "
            "Provide a role_arn for your own AWS account.",
        )
    return {"regions": regions, "role_arn": role_arn}


def _validate_azure(body):
    raw = body.get("credentials")
    if not isinstance(raw, dict):
        raise ApiError(400, "credentials must be an object with the Azure service principal fields")
    creds = {field: _text(raw.get(field), field, 300) for field in AZURE_FIELDS}
    for field in AZURE_GUID_FIELDS:
        if not GUID_RE.match(creds[field]):
            raise ApiError(400, f"{field} must be a GUID")
    return {"credentials": creds}


def _validate_gcp(body):
    raw = body.get("credentials")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ApiError(400, "credentials must be valid service account key JSON")
    if not isinstance(raw, dict):
        raise ApiError(400, "credentials must be service account key JSON")
    for key in GCP_REQUIRED:
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise ApiError(400, f"service account key is missing {key}")
    if raw["type"] != "service_account":
        raise ApiError(400, "credentials must be a service account key")
    if len(json.dumps(raw)) > MAX_CREDENTIAL_CHARS:
        raise ApiError(400, "service account key is too large")
    return {"credentials": raw}


def validate_account(body, is_admin):
    if not isinstance(body, dict):
        raise ApiError(400, "Request body must be a JSON object")
    provider = _text(body.get("provider"), "provider").lower()
    if provider not in PROVIDERS:
        raise ApiError(400, "provider must be aws, azure or gcp")
    cleaned = {"provider": provider, "label": _text(body.get("label"), "label", 60)}
    if provider == "aws":
        cleaned.update(_validate_aws(body, is_admin))
    elif provider == "azure":
        cleaned.update(_validate_azure(body))
    else:
        cleaned.update(_validate_gcp(body))
    return cleaned


def account_public_view(item):
    view = {
        key: item.get(key)
        for key in ("account_id", "provider", "label", "created_at", "last_status", "last_scan_at")
    }
    view["regions"] = list(item.get("regions") or [])
    view["host_account"] = bool(item.get("host_account"))
    if item.get("role_arn"):
        view["role_arn"] = item["role_arn"]
        view["external_id"] = item.get("external_id")
    return view


def should_enqueue(account, now, cooldown=SCAN_COOLDOWN_SECONDS):
    last = account.get("scan_requested_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now - last_dt).total_seconds() >= cooldown


def summarize_findings(findings):
    total = round(sum(f["est_monthly_savings"] for f in findings), 2)
    by_type = {}
    by_provider = {}
    for f in findings:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
        entry = by_provider.setdefault(f["provider"], {"count": 0, "est_monthly_savings": 0.0})
        entry["count"] += 1
        entry["est_monthly_savings"] = round(
            entry["est_monthly_savings"] + f["est_monthly_savings"], 2
        )
    return {
        "total_est_monthly_savings": total,
        "count": len(findings),
        "by_type": by_type,
        "by_provider": by_provider,
    }


def cost_explorer_status(statuses):
    if "ok" in statuses:
        return "ok"
    for status in statuses:
        if status.startswith("unavailable"):
            return status
    return "not requested"


def merge_reports(report_items):
    findings = []
    spend = {}
    errors = []
    statuses = []
    providers = set()
    scanned = []
    for item in report_items:
        findings.extend(json.loads(item.get("findings_json", "[]")))
        for row in json.loads(item.get("spend_json", "[]")):
            spend[row["service"]] = round(spend.get(row["service"], 0.0) + row["cost"], 2)
        label = item.get("account_label", "account")
        for message in json.loads(item.get("errors_json", "[]")):
            errors.append(f"{label}: {message}")
        statuses.append(item.get("cost_explorer_status", "not requested"))
        if item.get("provider"):
            providers.add(item["provider"])
        if item.get("scanned_at"):
            scanned.append(item["scanned_at"])
    findings.sort(key=lambda f: f["est_monthly_savings"], reverse=True)
    spend_rows = sorted(
        ({"service": s, "cost": c} for s, c in spend.items()),
        key=lambda r: r["cost"],
        reverse=True,
    )
    return {
        "scanned_at": max(scanned) if scanned else None,
        "providers": sorted(providers),
        "summary": summarize_findings(findings),
        "findings": findings,
        "spend": spend_rows,
        "cost_explorer_status": cost_explorer_status(statuses),
        "errors": errors,
    }
