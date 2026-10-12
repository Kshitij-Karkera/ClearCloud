import datetime


def _avg_cpu(cloudwatch, instance_id, now, lookback_days):
    resp = cloudwatch.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=now - datetime.timedelta(days=lookback_days),
        EndTime=now,
        Period=86400,
        Statistics=["Average"],
    )
    points = [p["Average"] for p in resp.get("Datapoints", [])]
    return sum(points) / len(points) if points else None


def normalize_volume(v, region):
    return {
        "provider": "aws",
        "id": v["VolumeId"],
        "region": region,
        "size_gb": v["Size"],
        "disk_type": v.get("VolumeType", "gp2"),
        "attached": v.get("State") != "available",
    }


def normalize_address(a, region):
    return {
        "provider": "aws",
        "id": a.get("AllocationId") or a.get("PublicIp", "unknown"),
        "region": region,
        "address": a.get("PublicIp"),
        "in_use": bool(a.get("AssociationId") or a.get("InstanceId") or a.get("NetworkInterfaceId")),
    }


def collect(region, now, lookback_days=7):
    import boto3

    ec2 = boto3.client("ec2", region_name=region)
    cw = boto3.client("cloudwatch", region_name=region)

    disks = []
    for page in ec2.get_paginator("describe_volumes").paginate():
        disks.extend(normalize_volume(v, region) for v in page["Volumes"])

    ips = [normalize_address(a, region)
           for a in ec2.describe_addresses().get("Addresses", [])]

    vms = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    ):
        for res in page["Reservations"]:
            for i in res["Instances"]:
                vms.append(
                    {
                        "provider": "aws",
                        "id": i["InstanceId"],
                        "region": region,
                        "size": i["InstanceType"],
                        "running": True,
                        "avg_cpu": _avg_cpu(cw, i["InstanceId"], now, lookback_days),
                    }
                )
    return {"disks": disks, "public_ips": ips, "vms": vms}


def collect_spend(now):
    import boto3

    try:
        ce = boto3.client("ce", region_name="us-east-1")
        end = now.date()
        start = end - datetime.timedelta(days=30)
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        totals = {}
        for period in resp.get("ResultsByTime", []):
            for g in period.get("Groups", []):
                svc = g["Keys"][0]
                amt = float(g["Metrics"]["UnblendedCost"]["Amount"])
                totals[svc] = totals.get(svc, 0.0) + amt
        rows = sorted(
            ({"service": k, "cost": round(v, 2)}
             for k, v in totals.items() if v > 0),
            key=lambda r: r["cost"],
            reverse=True,
        )
        return rows, "ok"
    except Exception as exc:
        return [], f"unavailable: {type(exc).__name__}"
