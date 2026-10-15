import json

MAX_FINDINGS_STORED = 500
HISTORY_TTL_DAYS = 90


def build_report_item(account, scanned_at, findings, summary, spend, ce_status, errors):
    stored = [
        {**f, "account_id": account["account_id"],
            "account_label": account["label"]}
        for f in findings[:MAX_FINDINGS_STORED]
    ]
    return {
        "pk": account["pk"],
        "sk": f"REPORT#{account['account_id']}",
        "scanned_at": scanned_at,
        "provider": account["provider"],
        "account_label": account["label"],
        "findings_json": json.dumps(stored),
        "summary_json": json.dumps(summary),
        "spend_json": json.dumps(spend),
        "cost_explorer_status": ce_status,
        "errors_json": json.dumps(errors),
        "truncated": len(findings) > len(stored),
    }


def build_history_item(account, scanned_at, now_epoch, total):
    return {
        "pk": account["pk"],
        "sk": f"HIST#{account['account_id']}#{scanned_at}",
        "scanned_at": scanned_at,
        "total_json": json.dumps(total),
        "expires_at": int(now_epoch) + HISTORY_TTL_DAYS * 86400,
    }
