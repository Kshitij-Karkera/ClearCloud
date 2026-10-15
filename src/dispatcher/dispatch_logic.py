import json


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def build_entries(accounts):
    return [
        {"Id": str(index), "MessageBody": json.dumps(
            {"pk": a["pk"], "sk": a["sk"]})}
        for index, a in enumerate(accounts)
    ]
