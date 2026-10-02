"""Creates the vpc_flow_log_edges table against DynamoDB Local (if it
doesn't already exist) and seeds it with a handful of fake connection
edges, so the web app's graph can be visually verified without a real
AWS deployment or real flow log traffic.

Usage (from repo root, with docker-compose's dynamodb-local running):
    python scripts/seed_local_dynamodb.py
"""

import time

import boto3

ENDPOINT_URL = "http://localhost:8000"
TABLE_NAME = "vpc_flow_log_edges"
REGION = "us-east-1"

FAKE_EDGES = [
    {"src_ip": "10.0.1.5", "dst_ip": "10.0.2.10", "protocol": "6", "direction": "bidirectional", "count": 1520},
    {"src_ip": "10.0.1.5", "dst_ip": "10.0.3.20", "protocol": "6", "direction": "src-to-dest", "count": 340},
    {"src_ip": "10.0.2.10", "dst_ip": "10.0.4.8", "protocol": "17", "direction": "bidirectional", "count": 88},
    {"src_ip": "10.0.3.20", "dst_ip": "10.0.4.8", "protocol": "6", "direction": "src-to-dest", "count": 12},
    {"src_ip": "10.0.5.1", "dst_ip": "10.0.1.5", "protocol": "6", "direction": "bidirectional", "count": 604},
]


def main() -> None:
    client = boto3.client(
        "dynamodb",
        endpoint_url=ENDPOINT_URL,
        region_name=REGION,
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )

    existing_tables = client.list_tables().get("TableNames", [])
    if TABLE_NAME not in existing_tables:
        client.create_table(
            TableName=TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "connection_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "connection_id", "AttributeType": "S"}],
        )
        client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        print(f"Created table {TABLE_NAME}")

    table = boto3.resource(
        "dynamodb",
        endpoint_url=ENDPOINT_URL,
        region_name=REGION,
        aws_access_key_id="local",
        aws_secret_access_key="local",
    ).Table(TABLE_NAME)

    now = int(time.time())
    for edge in FAKE_EDGES:
        primary, secondary = sorted((edge["src_ip"], edge["dst_ip"]))
        connection_id = f"{primary}|{secondary}|{edge['protocol']}"
        table.put_item(
            Item={
                "connection_id": connection_id,
                "src_ip": edge["src_ip"],
                "dst_ip": edge["dst_ip"],
                "protocol": edge["protocol"],
                "direction": edge["direction"],
                "count": edge["count"],
                "last_seen": now,
                "ttl_expiry": now + 30 * 24 * 60 * 60,
            }
        )

    print(f"Seeded {len(FAKE_EDGES)} fake edges into {TABLE_NAME}")


if __name__ == "__main__":
    main()
