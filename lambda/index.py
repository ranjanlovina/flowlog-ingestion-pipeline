"""VPC Flow Log ingestion Lambda.

Receives gzipped, base64-encoded CloudWatch Logs subscription filter events
containing default-format VPC Flow Log records, deduplicates each IP-pair
connection, and maintains a rolling edge count/direction in DynamoDB.
"""

import base64
import gzip
import json
import os
import time
from typing import Any

import boto3

DYNAMODB_ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT_URL")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "vpc_flow_log_edges")
TTL_SECONDS = int(os.environ.get("TTL_SECONDS", str(30 * 24 * 60 * 60)))

_dynamodb = boto3.resource("dynamodb", endpoint_url=DYNAMODB_ENDPOINT)
_table = _dynamodb.Table(TABLE_NAME)

# Default VPC Flow Log record field order (space-delimited).
FIELDS = [
    "version", "account_id", "interface_id", "srcaddr", "dstaddr",
    "srcport", "dstport", "protocol", "packets", "bytes", "start",
    "end", "action", "log_status",
]


def decode_payload(event: dict) -> dict:
    """Decode the gzipped, base64-encoded CloudWatch Logs payload."""
    compressed = base64.b64decode(event["awslogs"]["data"])
    return json.loads(gzip.decompress(compressed))


def parse_record(message: str) -> dict | None:
    """Parse one space-delimited flow log line into a dict of fields.

    Returns None if the record should be filtered out (non-IP traffic,
    NODATA/SKIPDATA status, or malformed).
    """
    parts = message.split()
    if len(parts) != len(FIELDS):
        return None

    record = dict(zip(FIELDS, parts))

    if record["log_status"] != "OK":
        return None
    if record["srcaddr"] == "-" or record["dstaddr"] == "-":
        return None

    return record


def build_connection_id(srcaddr: str, dstaddr: str, protocol: str) -> tuple[str, str, str]:
    """Build the (connection_id, primary, secondary) tuple for an IP pair.

    IPs are sorted lexicographically so both directions of a connection map
    to the same connection_id. `|` is used as the delimiter (not `:`)
    because IPv6 addresses themselves contain colons, which would make a
    `:`-delimited key ambiguous.

    dstport is deliberately NOT part of the key: for a normal client<->
    server session the two directions have different dstport values (the
    client's request has dstport=<service port>, the server's reply has
    dstport=<client's ephemeral port>), so keying on dstport would put the
    forward and reverse packets of the same connection under two different
    connection_ids and "bidirectional" could never be detected. Dropping
    the port also matches the spec's own /api/graph example, which shows
    one edge per host pair with no port field at all.
    """
    primary, secondary = sorted((srcaddr, dstaddr))
    connection_id = f"{primary}|{secondary}|{protocol}"
    return connection_id, primary, secondary


def aggregate_records(records: list[dict]) -> dict[str, dict]:
    """Aggregate parsed flow log records sharing a connection_id in memory.

    Multiple records for the same connection routinely arrive in a single
    CloudWatch Logs batch; aggregating here avoids redundant DynamoDB writes.

    Packets are tallied against the connection_id's canonical primary/
    secondary IPs (not "whichever record arrived first in this batch") so
    the result is independent of record arrival order within a batch - a
    batch processed in a different order must still produce the same
    direction/count outcome.

    Returns a dict keyed by connection_id with the fields needed to apply
    a single atomic update per connection.
    """
    aggregated: dict[str, dict] = {}

    for record in records:
        connection_id, primary, secondary = build_connection_id(
            record["srcaddr"], record["dstaddr"], record["protocol"],
        )
        packets = int(record["packets"])
        end_ts = int(record["end"])

        bucket = aggregated.get(connection_id)
        if bucket is None:
            bucket = {
                "primary": primary,
                "secondary": secondary,
                "protocol": record["protocol"],
                "packets_primary_to_secondary": 0,
                "packets_secondary_to_primary": 0,
                "last_seen": end_ts,
            }
            aggregated[connection_id] = bucket

        if record["srcaddr"] == primary:
            bucket["packets_primary_to_secondary"] += packets
        else:
            bucket["packets_secondary_to_primary"] += packets

        bucket["last_seen"] = max(bucket["last_seen"], end_ts)

    return aggregated


def apply_update(connection_id: str, agg: dict) -> None:
    """Apply one atomic, conditional update for a single connection_id.

    src_ip/dst_ip are always stored canonically as (primary, secondary) -
    the lexicographically sorted IPs baked into connection_id - so the
    stored item is independent of which direction's record happened to
    create it. Direction is "src-to-dest" when only primary->secondary
    traffic has been observed, "bidirectional" once secondary->primary
    traffic is also seen (sticky; never reverts once bidirectional).

    last_seen only ever advances (never regresses), so out-of-order
    delivery across concurrent invocations can't roll it backwards.
    """
    total_packets = agg["packets_primary_to_secondary"] + agg["packets_secondary_to_primary"]
    is_bidirectional = agg["packets_secondary_to_primary"] > 0
    ttl_expiry = agg["last_seen"] + TTL_SECONDS

    try:
        _table.put_item(
            Item={
                "connection_id": connection_id,
                "src_ip": agg["primary"],
                "dst_ip": agg["secondary"],
                "protocol": agg["protocol"],
                "direction": "bidirectional" if is_bidirectional else "src-to-dest",
                "count": total_packets,
                "last_seen": agg["last_seen"],
                "ttl_expiry": ttl_expiry,
            },
            ConditionExpression="attribute_not_exists(connection_id)",
        )
        return
    except _dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        pass

    # Item already exists - increment count unconditionally, and flip
    # direction to bidirectional if this batch observed reverse traffic.
    direction_clause = ", direction = :bidir" if is_bidirectional else ""
    direction_values = {":bidir": "bidirectional"} if is_bidirectional else {}

    try:
        _table.update_item(
            Key={"connection_id": connection_id},
            UpdateExpression=(
                "SET #count = #count + :packets, last_seen = :last_seen, "
                "ttl_expiry = :ttl_expiry" + direction_clause
            ),
            ConditionExpression="last_seen < :last_seen",
            ExpressionAttributeNames={"#count": "count"},
            ExpressionAttributeValues={
                ":packets": total_packets,
                ":last_seen": agg["last_seen"],
                ":ttl_expiry": ttl_expiry,
                **direction_values,
            },
        )
    except _dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        # Stored last_seen is already >= this batch's last_seen (an
        # out-of-order batch from a concurrent invocation) - still apply
        # the count/direction change, just leave last_seen/ttl untouched.
        _table.update_item(
            Key={"connection_id": connection_id},
            UpdateExpression="SET #count = #count + :packets" + direction_clause,
            ExpressionAttributeNames={"#count": "count"},
            ExpressionAttributeValues={
                ":packets": total_packets,
                **direction_values,
            },
        )


def handler(event: dict, context: Any) -> dict:
    payload = decode_payload(event)

    if payload.get("messageType") != "DATA_MESSAGE":
        # CONTROL_MESSAGE events (e.g. subscription confirmation) carry no
        # flow log data and must be skipped.
        return {"processed": 0}

    records = []
    for log_event in payload.get("logEvents", []):
        record = parse_record(log_event["message"])
        if record is not None:
            records.append(record)

    aggregated = aggregate_records(records)
    for connection_id, agg in aggregated.items():
        apply_update(connection_id, agg)

    return {"processed": len(aggregated)}
