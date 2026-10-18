import json
import os


def credentials_from_env(env=None):
    env = os.environ if env is None else env
    key = env.get("CC_ACCESS_KEY_ID", "")
    if not key:
        return {}
    return {
        "aws_access_key_id": key,
        "aws_secret_access_key": env.get("CC_SECRET_ACCESS_KEY", ""),
        "aws_session_token": env.get("CC_SESSION_TOKEN") or None,
    }


def session():
    import boto3

    return boto3.Session(**credentials_from_env())


def client(name, **kwargs):
    return session().client(name, **kwargs)


def resource(name, **kwargs):
    return session().resource(name, **kwargs)


def worker_count(queued, max_workers):
    if queued <= 0 or max_workers <= 0:
        return 0
    return min(queued, max_workers)


def start_workers(function_name, count):
    if not function_name or count <= 0:
        return 0
    lam = client("lambda")
    for _ in range(count):
        lam.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"mode": "drain"}).encode(),
        )
    return count
