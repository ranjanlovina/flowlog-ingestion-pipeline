# VPC Flow Log Ingestion & Network Visualization

Ingests AWS VPC Flow Logs in near real-time, deduplicates traffic between host pairs, and renders the result as an interactive, auto-refreshing network graph.

## How it works

1. **VPC Flow Logs** are enabled (traffic_type `ALL`) on every VPC ID you list, delivering to a per-VPC CloudWatch Log Group.
2. A **CloudWatch Logs Subscription Filter** on each log group invokes a **Lambda function** with every new batch of log events.
3. The Lambda (`lambda/index.py`) parses each flow log record, groups records by connection (`primary_ip|secondary_ip|protocol`, sorted so direction doesn't matter), and writes one aggregated item per connection into a **DynamoDB** table (`vpc_flow_log_edges`), tracking packet count, direction (`src-to-dest` or `bidirectional`), and last-seen time.
4. A **FastAPI** app (`app/main.py`) scans that table and serves it as graph JSON at `/api/graph`.
5. A static page (`app/static/index.html`) renders that graph with **Vis.js Network**, auto-refreshing every 10 seconds, with distinct arrow styling for one-way vs. bidirectional traffic.
6. The app runs as a single **ECS Fargate** task, reachable directly on port 80 (no load balancer) from whichever CIDR ranges you allow.

See [PLAN.md](PLAN.md) for the original spec and the full list of design decisions made while building this (including a real correctness bug found in the original `connection_id` design and how it was fixed).

## Prerequisites

- An AWS account and credentials configured locally, with permission to create the resources below.
- [Terraform](https://developer.hashicorp.com/terraform/downloads) >= 1.5
- [Docker](https://docs.docker.com/get-docker/) (to build the app image, and for local testing)
- [AWS CLI](https://aws.amazon.com/cli/) (used by Terraform's IP-lookup step and for pushing the image to ECR)
- Python 3.12+ (for the local test suite and helper scripts)
- At least one existing VPC ID you want to monitor

## Deploying

Full step-by-step instructions (variable setup, the two-pass ECR apply, verifying, redeploying, finding the app's IP, troubleshooting) are in **[DEPLOYMENT.md](DEPLOYMENT.md)**.

## Local development / testing (no AWS account needed)

Run the Lambda's unit tests (uses `moto` to mock DynamoDB):

```bash
pip install -r tests/requirements.txt
pytest tests/ -v
```

Run the web app against a local DynamoDB, with fake data so the graph has something to show:

```bash
docker-compose up --build
python scripts/seed_local_dynamodb.py
# open http://localhost:8080
```

Invoke the Lambda locally with SAM against a realistic sample event:

```bash
docker-compose up dynamodb-local
sam build
sam local invoke FlowLogIngestionFunction -e events/sample_event.json
```

Full command reference: [CLAUDE.md](CLAUDE.md).

## Teardown

```bash
cd terraform
terraform destroy
```

This removes everything, including the ECR repository and any images in it. See [DEPLOYMENT.md](DEPLOYMENT.md) for troubleshooting if destroy/apply doesn't go cleanly.

## Cost notes

- DynamoDB is on-demand billing — cost scales with actual traffic.
- No NAT Gateway and no ALB were deployed specifically to minimize idle cost for this project; see PLAN.md's decisions log for the reasoning and the security trade-offs that come with that choice (the task's public IP is reachable directly, gated only by the security group's CIDR allowlist).
- CloudWatch Logs ingestion/storage cost scales with flow log volume — `flow_log_aggregation_interval` (60s by default) is the main lever if that gets expensive.
