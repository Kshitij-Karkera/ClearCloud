import datetime


def _last(path):
    return path.rsplit("/", 1)[-1] if path else None


def normalize_disk(zone_key, d):
    return {
        "provider": "gcp",
        "id": d.name,
        "region": _last(zone_key),
        "size_gb": d.size_gb,
        "disk_type": _last(d.type_),
        "attached": len(list(d.users)) > 0,
    }


def normalize_address(scope_key, a):
    if a.address_type != "EXTERNAL":
        return None
    return {
        "provider": "gcp",
        "id": a.name,
        "region": _last(scope_key),
        "address": a.address,
        "in_use": a.status == "IN_USE",
    }


def normalize_instance(zone_key, inst, avg_cpu):
    return {
        "provider": "gcp",
        "id": inst.name,
        "region": _last(zone_key),
        "size": _last(inst.machine_type),
        "running": inst.status == "RUNNING",
        "avg_cpu": avg_cpu,
    }


def _avg_cpu(monitoring_client, monitoring_v3, project, instance_id, now, lookback_days):
    start = now - datetime.timedelta(days=lookback_days)
    interval = monitoring_v3.TimeInterval(
        {
            "start_time": {"seconds": int(start.timestamp())},
            "end_time": {"seconds": int(now.timestamp())},
        }
    )
    series = monitoring_client.list_time_series(
        request={
            "name": f"projects/{project}",
            "filter": (
                'metric.type = "compute.googleapis.com/instance/cpu/utilization" '
                f'AND resource.labels.instance_id = "{instance_id}"'
            ),
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
    )
    values = [p.value.double_value for ts in series for p in ts.points]
    return (sum(values) / len(values)) * 100 if values else None


def collect(service_account_info, now, lookback_days=7):
    from google.cloud import compute_v1, monitoring_v3
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    project = service_account_info["project_id"]

    disks = []
    disks_client = compute_v1.DisksClient(credentials=credentials)
    for zone_key, scoped in disks_client.aggregated_list(
        request=compute_v1.AggregatedListDisksRequest(project=project)
    ):
        disks.extend(normalize_disk(zone_key, d) for d in scoped.disks)

    ips = []
    addresses_client = compute_v1.AddressesClient(credentials=credentials)
    for scope_key, scoped in addresses_client.aggregated_list(
        request=compute_v1.AggregatedListAddressesRequest(project=project)
    ):
        for a in scoped.addresses:
            item = normalize_address(scope_key, a)
            if item:
                ips.append(item)

    vms = []
    monitoring_client = monitoring_v3.MetricServiceClient(
        credentials=credentials)
    instances_client = compute_v1.InstancesClient(credentials=credentials)
    for zone_key, scoped in instances_client.aggregated_list(
        request=compute_v1.AggregatedListInstancesRequest(project=project)
    ):
        for inst in scoped.instances:
            cpu = None
            if inst.status == "RUNNING":
                cpu = _avg_cpu(
                    monitoring_client, monitoring_v3, project, inst.id, now, lookback_days
                )
            vms.append(normalize_instance(zone_key, inst, cpu))

    return {"disks": disks, "public_ips": ips, "vms": vms}
