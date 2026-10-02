"""Generates events/sample_event.json - a CloudWatch Logs subscription
filter event (gzipped + base64-encoded, as Lambda actually receives it)
containing a few sample default-format VPC Flow Log records. Used with:

    sam local invoke FlowLogIngestionFunction -e events/sample_event.json
"""

import base64
import gzip
import json
import time
from pathlib import Path

now = int(time.time())

# version account_id interface_id srcaddr dstaddr srcport dstport protocol packets bytes start end action log_status
RECORDS = [
    f"2 123456789012 eni-0123456789abcdef0 10.0.1.5 10.0.2.10 443 51514 6 10 1500 {now - 60} {now} ACCEPT OK",
    f"2 123456789012 eni-0123456789abcdef0 10.0.2.10 10.0.1.5 51514 443 6 8 1200 {now - 60} {now} ACCEPT OK",
    f"2 123456789012 eni-0123456789abcdef0 10.0.1.5 10.0.3.20 80 60123 6 5 700 {now - 60} {now} ACCEPT OK",
]

payload = {
    "messageType": "DATA_MESSAGE",
    "owner": "123456789012",
    "logGroup": "/vpc/flow-logs/vpc-0123456789abcdef0",
    "logStream": "eni-0123456789abcdef0-all",
    "subscriptionFilters": ["flowlog-viz-to-lambda"],
    "logEvents": [
        {"id": str(i), "timestamp": now * 1000, "message": msg}
        for i, msg in enumerate(RECORDS)
    ],
}

compressed = gzip.compress(json.dumps(payload).encode("utf-8"))
encoded = base64.b64encode(compressed).decode("ascii")

event = {"awslogs": {"data": encoded}}

out_path = Path(__file__).parent.parent / "events" / "sample_event.json"
out_path.parent.mkdir(exist_ok=True)
out_path.write_text(json.dumps(event, indent=2))
print(f"Wrote {out_path}")
