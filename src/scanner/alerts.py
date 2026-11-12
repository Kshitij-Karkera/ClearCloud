"""Pure logic for "new waste found" email alerts (no AWS calls, easy to unit test)."""
import json

MAX_LISTED = 10
SUBJECT_LIMIT = 100  # SNS subject limit


def parse_findings(findings_json):
    """Findings stored on a REPORT item as a JSON string -> list of dicts (never raises)."""
    if not findings_json:
        return []
    try:
        data = json.loads(findings_json) if isinstance(findings_json, str) else findings_json
    except (TypeError, ValueError):
        return []
    return [f for f in data if isinstance(f, dict)] if isinstance(data, list) else []


def new_findings(previous, current):
    """Findings in `current` whose id was not in `previous`, highest savings first."""
    seen = {f.get("id") for f in previous}
    fresh = [f for f in current if f.get("id") not in seen]
    return sorted(fresh, key=lambda f: float(f.get("est_monthly_savings") or 0), reverse=True)


def build_alert(account, fresh, summary):
    """Return (subject, body) for the SNS message."""
    label = account.get("account_label") or account.get("label") or account.get("account_id", "account")
    provider = str(account.get("provider", "")).upper()
    new_savings = round(sum(float(f.get("est_monthly_savings") or 0) for f in fresh), 2)
    noun = "issue" if len(fresh) == 1 else "issues"
    subject = f"ClearCloud: {len(fresh)} new {noun} in {label} (${new_savings:.2f}/mo)"
    if len(subject) > SUBJECT_LIMIT:
        subject = subject[: SUBJECT_LIMIT - 3] + "..."
    lines = [
        f"ClearCloud found {len(fresh)} new cost-waste {noun} in {label} ({provider}).",
        f"Estimated extra waste: ${new_savings:.2f} per month.",
        f"All open issues on this account: {summary.get('count', 0)} "
        f"(${float(summary.get('total_est_monthly_savings', 0)):.2f} per month).",
        "",
    ]
    for f in fresh[:MAX_LISTED]:
        lines.append(f"- {f.get('title', f.get('id'))}  (~${float(f.get('est_monthly_savings') or 0):.2f}/mo)")
        if f.get("recommendation"):
            lines.append(f"    {f['recommendation']}")
    if len(fresh) > MAX_LISTED:
        lines.append(f"... and {len(fresh) - MAX_LISTED} more. Open the dashboard to see all.")
    lines += ["", "Savings are estimates from approximate on-demand prices."]
    return subject, "\n".join(lines)