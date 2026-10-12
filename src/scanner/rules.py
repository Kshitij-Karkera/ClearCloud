HOURS_PER_MONTH = 730
IDLE_CPU_THRESHOLD = 5.0

DEFAULT_DISK_PRICE_PER_GB = 0.10

DISK_PRICE_PER_GB = {
    "aws": {
        "gp3": 0.08,
        "gp2": 0.10,
        "io1": 0.125,
        "io2": 0.125,
        "st1": 0.045,
        "sc1": 0.015,
        "standard": 0.05,
    },
    "azure": {
        "Standard_LRS": 0.045,
        "StandardSSD_LRS": 0.075,
        "Premium_LRS": 0.15,
        "UltraSSD_LRS": 0.12,
    },
    "gcp": {
        "pd-standard": 0.04,
        "pd-balanced": 0.10,
        "pd-ssd": 0.17,
        "pd-extreme": 0.125,
    },
}

PUBLIC_IP_IDLE_MONTHLY = {
    "aws": round(0.005 * HOURS_PER_MONTH, 2),
    "azure": round(0.005 * HOURS_PER_MONTH, 2),
    "gcp": round(0.01 * HOURS_PER_MONTH, 2),
}

VM_HOURLY = {
    "aws": {
        "t2.micro": 0.0116,
        "t2.small": 0.023,
        "t2.medium": 0.0464,
        "t2.large": 0.0928,
        "t3.micro": 0.0104,
        "t3.small": 0.0208,
        "t3.medium": 0.0416,
        "t3.large": 0.0832,
        "m5.large": 0.096,
        "m5.xlarge": 0.192,
        "c5.large": 0.085,
        "c5.xlarge": 0.17,
    },
    "azure": {
        "Standard_B1s": 0.0104,
        "Standard_B2s": 0.0416,
        "Standard_D2s_v3": 0.096,
        "Standard_D4s_v3": 0.192,
    },
    "gcp": {
        "e2-micro": 0.0084,
        "e2-small": 0.0168,
        "e2-medium": 0.0335,
        "e2-standard-2": 0.067,
        "e2-standard-4": 0.134,
        "n1-standard-1": 0.0475,
    },
}


def disk_monthly_cost(provider, size_gb, disk_type):
    price = DISK_PRICE_PER_GB.get(provider, {}).get(
        disk_type, DEFAULT_DISK_PRICE_PER_GB)
    return round(size_gb * price, 2)


def vm_monthly_cost(provider, size):
    hourly = VM_HOURLY.get(provider, {}).get(size)
    if hourly is None:
        return None
    return round(hourly * HOURS_PER_MONTH, 2)


def _finding(provider, ftype, resource_id, region, title, recommendation, savings, details=None):
    return {
        "id": f"{provider}:{ftype}:{region}:{resource_id}",
        "provider": provider,
        "type": ftype,
        "resource_id": resource_id,
        "region": region,
        "title": title,
        "recommendation": recommendation,
        "est_monthly_savings": round(float(savings), 2),
        "details": details or {},
    }


def detect_unattached_disks(disks):
    out = []
    for d in disks:
        if d["attached"]:
            continue
        cost = disk_monthly_cost(
            d["provider"], d["size_gb"], d.get("disk_type"))
        out.append(
            _finding(
                d["provider"],
                "unattached_disk",
                d["id"],
                d["region"],
                f"Unattached {d['size_gb']} GB {d.get('disk_type') or 'disk'}",
                "This disk is not attached to any VM but is still billed. "
                "Snapshot it if you might need the data, then delete it.",
                cost,
                {"size_gb": d["size_gb"], "disk_type": d.get("disk_type")},
            )
        )
    return out


def detect_unused_public_ips(ips):
    out = []
    for ip in ips:
        if ip["in_use"]:
            continue
        out.append(
            _finding(
                ip["provider"],
                "unused_public_ip",
                ip["id"],
                ip["region"],
                f"Public IP {ip.get('address') or ip['id']} is not in use",
                "Reserved public IPs that are not attached to anything are charged hourly. "
                "Release it if nothing needs it.",
                PUBLIC_IP_IDLE_MONTHLY.get(ip["provider"], 0.0),
                {"address": ip.get("address")},
            )
        )
    return out


def detect_idle_vms(vms, threshold=IDLE_CPU_THRESHOLD):
    out = []
    for vm in vms:
        if not vm["running"]:
            continue
        avg = vm.get("avg_cpu")
        if avg is None or avg >= threshold:
            continue
        cost = vm_monthly_cost(vm["provider"], vm["size"])
        if cost is None:
            cost = 0.0
        out.append(
            _finding(
                vm["provider"],
                "idle_vm",
                vm["id"],
                vm["region"],
                f"{vm['size']} VM averaging {avg:.1f}% CPU",
                "Average CPU is very low. Stop it if it is not needed, or resize to a "
                "smaller VM size.",
                cost,
                {"size": vm["size"], "avg_cpu_percent": round(avg, 2)},
            )
        )
    return out


def analyze(inventory):
    findings = []
    findings += detect_unattached_disks(inventory.get("disks", []))
    findings += detect_unused_public_ips(inventory.get("public_ips", []))
    findings += detect_idle_vms(inventory.get("vms", []))
    findings.sort(key=lambda f: f["est_monthly_savings"], reverse=True)
    return findings


def summarize(findings):
    total = round(sum(f["est_monthly_savings"] for f in findings), 2)
    by_type = {}
    by_provider = {}
    for f in findings:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
        p = by_provider.setdefault(
            f["provider"], {"count": 0, "est_monthly_savings": 0.0})
        p["count"] += 1
        p["est_monthly_savings"] = round(
            p["est_monthly_savings"] + f["est_monthly_savings"], 2)
    return {
        "total_est_monthly_savings": total,
        "count": len(findings),
        "by_type": by_type,
        "by_provider": by_provider,
    }
