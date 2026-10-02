import base64
import gzip
import json
import os
import time

import boto3
import pytest
from moto import mock_aws

import index  # lambda/index.py, see conftest.py for the sys.path setup

TABLE_NAME = os.environ["DYNAMODB_TABLE"]


@pytest.fixture
def dynamodb_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "connection_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "connection_id", "AttributeType": "S"}],
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(TABLE_NAME)


def make_record(srcaddr, dstaddr, srcport, dstport, protocol, packets, byte_count, end, action="ACCEPT", log_status="OK"):
    return {
        "version": "2", "account_id": "123456789012", "interface_id": "eni-abc",
        "srcaddr": srcaddr, "dstaddr": dstaddr, "srcport": str(srcport), "dstport": str(dstport),
        "protocol": str(protocol), "packets": str(packets), "bytes": str(byte_count),
        "start": str(end - 60), "end": str(end), "action": action, "log_status": log_status,
    }


def record_line(**kwargs) -> str:
    rec = make_record(**kwargs)
    return " ".join(rec[field] for field in index.FIELDS)


# --- parse_record ---

def test_parse_record_accepts_ok_record():
    line = record_line(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=443, dstport=51514, protocol=6, packets=10, byte_count=1500, end=1700000000)
    record = index.parse_record(line)
    assert record is not None
    assert record["srcaddr"] == "10.0.1.5"
    assert record["dstaddr"] == "10.0.2.10"


def test_parse_record_filters_dash_addresses():
    line = record_line(srcaddr="-", dstaddr="10.0.2.10", srcport=443, dstport=51514, protocol=6, packets=10, byte_count=1500, end=1700000000)
    assert index.parse_record(line) is None


def test_parse_record_filters_non_ok_log_status():
    line = record_line(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=443, dstport=51514, protocol=6, packets=10, byte_count=1500, end=1700000000, log_status="NODATA")
    assert index.parse_record(line) is None


def test_parse_record_filters_malformed_line():
    assert index.parse_record("not enough fields") is None


# --- build_connection_id ---

def test_connection_id_sorts_lexicographically_and_uses_pipe_delimiter():
    cid_a, primary_a, secondary_a = index.build_connection_id("10.0.2.10", "10.0.1.5", "6")
    cid_b, primary_b, secondary_b = index.build_connection_id("10.0.1.5", "10.0.2.10", "6")
    assert cid_a == cid_b
    assert cid_a == "10.0.1.5|10.0.2.10|6"
    assert primary_a == primary_b == "10.0.1.5"
    assert secondary_a == secondary_b == "10.0.2.10"


def test_connection_id_handles_ipv6_without_delimiter_collision():
    # Colons inside IPv6 addresses must not collide with the delimiter.
    cid, primary, secondary = index.build_connection_id("fe80::1", "fe80::2", "6")
    assert cid == "fe80::1|fe80::2|6"
    assert cid.count("|") == 2


def test_connection_id_ignores_dstport_so_both_directions_of_a_session_match():
    # Client->server has dstport=443; the server's reply has dstport=<the
    # client's ephemeral port>, NOT 443. connection_id must not depend on
    # dstport, or these would never be recognized as the same connection.
    forward, _, _ = index.build_connection_id("10.0.1.5", "10.0.2.10", "6")
    reverse, _, _ = index.build_connection_id("10.0.2.10", "10.0.1.5", "6")
    assert forward == reverse


# --- aggregate_records: must be order-independent ---

def test_aggregate_records_is_order_independent():
    r1 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=10, byte_count=100, end=1000)
    r2 = make_record(srcaddr="10.0.2.10", dstaddr="10.0.1.5", srcport=443, dstport=1, protocol=6, packets=7, byte_count=100, end=1005)

    forward_first = index.aggregate_records([r1, r2])
    reverse_first = index.aggregate_records([r2, r1])

    assert forward_first == reverse_first
    (connection_id, agg), = forward_first.items()
    assert agg["packets_primary_to_secondary"] == 10
    assert agg["packets_secondary_to_primary"] == 7
    assert agg["last_seen"] == 1005


def test_aggregate_records_sums_same_direction_records():
    r1 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=5, byte_count=100, end=1000)
    r2 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=3, byte_count=100, end=1010)

    aggregated = index.aggregate_records([r1, r2])
    (_, agg), = aggregated.items()
    assert agg["packets_primary_to_secondary"] == 8
    assert agg["packets_secondary_to_primary"] == 0
    assert agg["last_seen"] == 1010


# --- apply_update / full handler against a mocked DynamoDB table ---

def test_apply_update_creates_new_item_as_src_to_dest(dynamodb_table):
    r = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=10, byte_count=100, end=1000)
    aggregated = index.aggregate_records([r])
    connection_id, agg = next(iter(aggregated.items()))

    index.apply_update(connection_id, agg)

    item = dynamodb_table.get_item(Key={"connection_id": connection_id})["Item"]
    assert item["direction"] == "src-to-dest"
    assert int(item["count"]) == 10
    assert item["src_ip"] == "10.0.1.5"
    assert item["dst_ip"] == "10.0.2.10"


def test_apply_update_detects_bidirectional_on_first_batch(dynamodb_table):
    r1 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=10, byte_count=100, end=1000)
    r2 = make_record(srcaddr="10.0.2.10", dstaddr="10.0.1.5", srcport=443, dstport=1, protocol=6, packets=4, byte_count=100, end=1001)

    aggregated = index.aggregate_records([r1, r2])
    connection_id, agg = next(iter(aggregated.items()))
    index.apply_update(connection_id, agg)

    item = dynamodb_table.get_item(Key={"connection_id": connection_id})["Item"]
    assert item["direction"] == "bidirectional"
    assert int(item["count"]) == 14


def test_apply_update_flips_existing_item_to_bidirectional(dynamodb_table):
    r1 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=10, byte_count=100, end=1000)
    aggregated = index.aggregate_records([r1])
    connection_id, agg = next(iter(aggregated.items()))
    index.apply_update(connection_id, agg)

    # second batch, later in time, reverse direction observed
    r2 = make_record(srcaddr="10.0.2.10", dstaddr="10.0.1.5", srcport=443, dstport=1, protocol=6, packets=6, byte_count=100, end=1050)
    aggregated_2 = index.aggregate_records([r2])
    connection_id_2, agg_2 = next(iter(aggregated_2.items()))
    assert connection_id_2 == connection_id
    index.apply_update(connection_id_2, agg_2)

    item = dynamodb_table.get_item(Key={"connection_id": connection_id})["Item"]
    assert item["direction"] == "bidirectional"
    assert int(item["count"]) == 16
    assert int(item["last_seen"]) == 1050


def test_apply_update_same_direction_increments_without_changing_direction(dynamodb_table):
    r1 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=10, byte_count=100, end=1000)
    aggregated = index.aggregate_records([r1])
    connection_id, agg = next(iter(aggregated.items()))
    index.apply_update(connection_id, agg)

    r2 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=5, byte_count=100, end=1020)
    aggregated_2 = index.aggregate_records([r2])
    connection_id_2, agg_2 = next(iter(aggregated_2.items()))
    index.apply_update(connection_id_2, agg_2)

    item = dynamodb_table.get_item(Key={"connection_id": connection_id})["Item"]
    assert item["direction"] == "src-to-dest"
    assert int(item["count"]) == 15
    assert int(item["last_seen"]) == 1020


def test_apply_update_does_not_regress_last_seen_on_out_of_order_batch(dynamodb_table):
    r1 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=10, byte_count=100, end=2000)
    aggregated = index.aggregate_records([r1])
    connection_id, agg = next(iter(aggregated.items()))
    index.apply_update(connection_id, agg)

    # an out-of-order batch arrives with an OLDER end timestamp
    r2 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=3, byte_count=100, end=1500)
    aggregated_2 = index.aggregate_records([r2])
    connection_id_2, agg_2 = next(iter(aggregated_2.items()))
    index.apply_update(connection_id_2, agg_2)

    item = dynamodb_table.get_item(Key={"connection_id": connection_id})["Item"]
    assert int(item["last_seen"]) == 2000  # unchanged, not regressed to 1500
    assert int(item["count"]) == 13  # count still incremented


def test_apply_update_sets_ttl_expiry(dynamodb_table):
    r1 = make_record(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=10, byte_count=100, end=1000)
    aggregated = index.aggregate_records([r1])
    connection_id, agg = next(iter(aggregated.items()))
    index.apply_update(connection_id, agg)

    item = dynamodb_table.get_item(Key={"connection_id": connection_id})["Item"]
    assert int(item["ttl_expiry"]) == 1000 + index.TTL_SECONDS


# --- handler (full event decode path) ---

def make_cwlogs_event(records: list[str], message_type: str = "DATA_MESSAGE") -> dict:
    payload = {
        "messageType": message_type,
        "owner": "123456789012",
        "logGroup": "/vpc/flow-logs/vpc-abc",
        "logStream": "eni-abc-all",
        "subscriptionFilters": ["filter"],
        "logEvents": [{"id": str(i), "timestamp": 0, "message": m} for i, m in enumerate(records)],
    }
    compressed = gzip.compress(json.dumps(payload).encode("utf-8"))
    return {"awslogs": {"data": base64.b64encode(compressed).decode("ascii")}}


def test_handler_skips_control_messages(dynamodb_table):
    event = make_cwlogs_event([], message_type="CONTROL_MESSAGE")
    result = index.handler(event, None)
    assert result == {"processed": 0}


def test_handler_processes_data_message_end_to_end(dynamodb_table):
    now = int(time.time())
    lines = [
        record_line(srcaddr="10.0.1.5", dstaddr="10.0.2.10", srcport=1, dstport=443, protocol=6, packets=10, byte_count=100, end=now),
        record_line(srcaddr="10.0.2.10", dstaddr="10.0.1.5", srcport=443, dstport=1, protocol=6, packets=4, byte_count=100, end=now),
    ]
    event = make_cwlogs_event(lines)

    result = index.handler(event, None)
    assert result == {"processed": 1}

    items = dynamodb_table.scan()["Items"]
    assert len(items) == 1
    assert items[0]["direction"] == "bidirectional"
    assert int(items[0]["count"]) == 14
