You are acting as a Principal Cloud & Infrastructure Engineer. Build a production-ready, fully functional AWS VPC Flow Log ingestion and network visualization system using Terraform, Python, and HTML/JavaScript.

### PROJECT REQUIREMENTS & SPECIFICATIONS

1. PROJECT STRUCTURE
Create the following file directory layout:
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── terraform.tfvars.example
│   ├── modules/
│   │   ├── flow_log_ingestion/
│   │   │   ├── main.tf
│   │   │   ├── lambda.tf
│   │   │   ├── dynamodb.tf
│   │   │   └── variables.tf
│   │   └── web_visualization/
│   │       ├── main.tf
│   │       ├── ecs.tf
│   │       ├── alb.tf
│   │       └── variables.tf
├── lambda/
│   └── index.py
├── app/
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   └── static/
│       └── index.html
├── CLAUDE.md
└── README.md

2. TERRAFORM & INFRASTRUCTURE REQUIREMENTS
- Create a input variable `vpc_ids` in `terraform/variables.tf` as `list(string)` that accepts VPC IDs.
- For EACH VPC ID provided in the list:
  * Create a CloudWatch Log Group for VPC Flow Logs.
  * Create an AWS VPC Flow Log resource delivering ALL traffic (ACCEPT and REJECT) to that Log Group.
  * Attach a CloudWatch Logs Subscription Filter targeting the log processing Lambda function.
- Create a DynamoDB table named `vpc_flow_log_edges` with On-Demand billing (`PAY_PER_REQUEST`):
  * Partition Key: `connection_id` (String)
- Create a Lambda function with appropriate IAM roles (CloudWatch logs access, DynamoDB read/write access).
- Create an Amazon ECS Fargate cluster, Task Definition, and Service to run the visualization web app.
- Attach an Application Load Balancer (ALB) or exposing task port (8000) so the web app HTML can be accessed publicly.

3. LAMBDA INGESTION ENGINE (`lambda/index.py`)
- Receives gzipped, base64-encoded CloudWatch Log events containing default VPC Flow Log format fields (srcaddr, dstaddr, srcport, dstport, protocol, packets, bytes, end timestamp).
- For each log record:
  * Extract `srcaddr`, `dstaddr`, `srcport`, `dstport`, `protocol`, `packets`, `bytes`, and `end` (timestamp).
  * Filter out records where IP is `-` or non-IP traffic.
  * Deduplication & Direction Logic:
    1. Lexicographically sort IP pairs: `primary = min(srcaddr, dstaddr)`, `secondary = max(srcaddr, dstaddr)`.
    2. Construct `connection_id = hash(primary + ":" + secondary + ":" + protocol + ":" + dstport)`.
    3. Query DynamoDB or execute an atomic `UpdateItem`:
       - If item does NOT exist: insert item with `src_ip = srcaddr`, `dst_ip = dstaddr`, `direction = "src-to-dest"`, `count = packets`, `last_seen = end`.
       - If item exists and current record's `srcaddr` matches existing `dst_ip` (reverse direction observed): update `direction` to `"bidirectional"`, increment `count` by `packets`, and update `last_seen = max(existing.last_seen, end)`.
       - If item exists and same direction observed: increment `count` by `packets` and update `last_seen`.
- Use `boto3` and handle batching efficiently.

4. WEB VISUALIZATION APP (`app/`)
- `app/main.py`: A FastAPI web backend with endpoints:
  * `GET /`: Serves `static/index.html`.
  * `GET /api/graph`: Queries DynamoDB (`vpc_flow_log_edges`), parses items, and returns JSON in graph format:
    ```json
    {
      "nodes": [{"id": "10.0.1.5", "label": "10.0.1.5"}],
      "edges": [
        {
          "from": "10.0.1.5",
          "to": "10.0.2.10",
          "label": "150 pkts",
          "direction": "bidirectional",
          "count": 150,
          "last_seen": 1700000000
        }
      ]
    }
    ```
- `app/static/index.html`:
  * Embeds **Vis.js Network** (via CDN) to render an interactive force-directed graph.
  * Renders edges with arrows: directed arrow for `src-to-dest`, double-headed arrow or distinct styling for `bidirectional`.
  * Shows edge tooltips on hover displaying: Packet Count, Protocol, and Last Seen timestamp (human-readable date).
  * Includes an auto-refresh toggle (e.g., refresh every 10 seconds).
- `app/Dockerfile`: Multi-stage Python 3.11 container serving FastAPI via Uvicorn.

5. CLAUDE.md & DOCUMENTATION
- Include a root `CLAUDE.md` specifying build commands, local testing instructions with `sam local` or `docker-compose`, and Terraform deployment steps.
- Create a comprehensive `README.md` with step-by-step setup guides, architecture descriptions, and teardown instructions.

Deliver clean, production-ready, modular code for all files specified.

---

### DECISIONS (resolved via grill-me session, 2026-10-02)

These decisions override/clarify ambiguous or underspecified parts of the spec above.

1. **Delivery scope**: Produce the complete codebase only. No live `terraform apply`, `docker build`, or AWS deployment performed by the assistant — the user runs those themselves after installing Terraform/Docker/SAM CLI (none are installed in this environment as of this session). Python unit tests for the Lambda logic (using `moto` to mock AWS) ARE written and run locally, since Python is already available.

2. **Terraform state**: Local `terraform.tfstate` only. No S3/remote backend.

3. **App networking**: A new, dedicated VPC for the visualization stack (separate from the VPCs named in `vpc_ids`, which are only flow-logged, not used to host app infra). Public subnets only — **no NAT Gateway** (cost). ECS Fargate task gets `assign_public_ip = true`.

4. **No ALB.** Single ECS Fargate service, single task (`desired_count = 1`), container listens directly on **port 80** (not 8000). Security group on the task's ENI allows inbound 80 only from `var.allowed_cidr_blocks` — **no default value** (fails closed; `apply` requires the user to set it explicitly).

5. **Container runs as root** to bind port 80 (Fargate `awsvpc` mode requires the process itself to bind the exposed port; no `CAP_NET_BIND_SERVICE`/non-root wiring, since this is a single-purpose container with no untrusted code).

6. **IP discovery**: No ALB means no stable DNS endpoint. A Terraform output surfaces the task's public IP right after `apply`. The README/CLAUDE.md additionally document the `aws ecs describe-tasks` → `describe-network-interfaces` CLI lookup for finding the current IP later if the task is ever replaced (the Terraform output goes stale at that point).

7. **`connection_id` construction**: Raw delimited string, **not** a hash — e.g. `10.0.1.5|10.0.2.10|6|443`. Uses `|` as the delimiter (not `:`) specifically because IPv6 addresses contain colons, which would make a `:`-delimited key ambiguous/collision-prone. (Python's builtin `hash()` was rejected outright — it's randomized per-process via `PYTHONHASHSEED` and would break deduplication across Lambda cold starts.)

8. **Lambda write semantics**: Within a single invocation, aggregate records sharing the same `connection_id` in memory first (sum packets, resolve direction), then issue one atomic, conditional `UpdateItem` per unique connection — correct even when concurrent invocations touch the same connection.

9. **DynamoDB lifecycle**: TTL attribute (e.g. `ttl_expiry`) set to 30 days from `last_seen`, refreshed on every update, so stale connections expire automatically.

10. **Local testing harness**: Real, working `docker-compose.yml` (FastAPI app + DynamoDB Local) and a `template.yaml` for `sam local invoke` on the Lambda — not just documentation. Includes a seed script that populates DynamoDB Local with fake edges so the Vis.js graph can be visually verified without a real AWS deployment.

11. **Flow log → Lambda pipeline**: CloudWatch Logs Subscription Filter invokes Lambda directly per batch (CloudWatch-managed batching, not user-configurable). `aws_flow_log.max_aggregation_interval = 60` (seconds) — chosen over the 600s option to keep data reasonably fresh, matching the web UI's 10s auto-refresh.