"""FastAPI backend for the VPC Flow Log visualization web app."""

import os
from pathlib import Path

import boto3
from fastapi import FastAPI
from fastapi.responses import FileResponse

DYNAMODB_ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT_URL")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "vpc_flow_log_edges")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="VPC Flow Log Visualization")

_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION, endpoint_url=DYNAMODB_ENDPOINT)
_table = _dynamodb.Table(TABLE_NAME)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/graph")
def get_graph() -> dict:
    nodes_seen: dict[str, dict] = {}
    edges: list[dict] = []

    items = []
    response = _table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = _table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    for item in items:
        src_ip = item["src_ip"]
        dst_ip = item["dst_ip"]
        count = int(item["count"])
        direction = item["direction"]
        last_seen = int(item["last_seen"])
        protocol = item.get("protocol", "")

        nodes_seen.setdefault(src_ip, {"id": src_ip, "label": src_ip})
        nodes_seen.setdefault(dst_ip, {"id": dst_ip, "label": dst_ip})

        edges.append(
            {
                "from": src_ip,
                "to": dst_ip,
                "label": f"{count} pkts",
                "direction": direction,
                "count": count,
                "protocol": protocol,
                "last_seen": last_seen,
            }
        )

    return {"nodes": list(nodes_seen.values()), "edges": edges}
