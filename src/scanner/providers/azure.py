import datetime


def normalize_disk(d):
    return {
        "provider": "azure",
        "id": d.name,
        "region": d.location,
        "size_gb": d.disk_size_gb or 0,
        "disk_type": d.sku.name if d.sku else None,
        "attached": d.managed_by is not None,
    }


def normalize_public_ip(ip):
    is_standard = bool(ip.sku and ip.sku.name == "Standard")
    is_static = ip.public_ip_allocation_method == "Static"
    if not (is_standard or is_static):
        return None
    in_use = ip.ip_configuration is not None or getattr(
        ip, "nat_gateway", None) is not None
    return {
        "provider": "azure",
        "id": ip.name,
        "region": ip.location,
        "address": ip.ip_address,
        "in_use": in_use,
    }


def normalize_vm(vm, running, avg_cpu):
    return {
        "provider": "azure",
        "id": vm.name,
        "region": vm.location,
        "size": vm.hardware_profile.vm_size,
        "running": running,
        "avg_cpu": avg_cpu,
    }


def _avg_cpu(monitor, resource_id, now, lookback_days):
    start = now - datetime.timedelta(days=lookback_days)
    resp = monitor.metrics.list(
        resource_id,
        timespan=f"{start.isoformat()}/{now.isoformat()}",
        interval="P1D",
        metricnames="Percentage CPU",
        aggregation="Average",
    )
    values = []
    for metric in resp.value:
        for series in metric.timeseries:
            for point in series.data:
                if point.average is not None:
                    values.append(point.average)
    return sum(values) / len(values) if values else None


def collect(creds, now, lookback_days=7):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.compute import ComputeManagementClient
    from azure.mgmt.monitor import MonitorManagementClient
    from azure.mgmt.network import NetworkManagementClient

    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    sub = creds["subscription_id"]
    compute = ComputeManagementClient(credential, sub)
    network = NetworkManagementClient(credential, sub)
    monitor = MonitorManagementClient(credential, sub)

    disks = [normalize_disk(d) for d in compute.disks.list()]

    ips = []
    for ip in network.public_ip_addresses.list_all():
        item = normalize_public_ip(ip)
        if item:
            ips.append(item)

    vms = []
    for vm in compute.virtual_machines.list_all():
        resource_group = vm.id.split("/")[4]
        view = compute.virtual_machines.instance_view(resource_group, vm.name)
        running = any(
            s.code == "PowerState/running" for s in (view.statuses or []))
        cpu = _avg_cpu(monitor, vm.id, now, lookback_days) if running else None
        vms.append(normalize_vm(vm, running, cpu))

    return {"disks": disks, "public_ips": ips, "vms": vms}
