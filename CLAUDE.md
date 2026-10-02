# CLAUDE.md

Build/test/deploy commands for this repo. See [PLAN.md](PLAN.md) for the original spec and the full decisions log (including a real bug found and fixed in the `connection_id` design during implementation).

## Architecture

```
VPC Flow Logs -> CloudWatch Log Group (per VPC) -> Subscription Filter -> Lambda (lambda/index.py)
                                                                              |
                                                                              v
                                                                    DynamoDB: vpc_flow_log_edges
                                                                              ^
                                                                              |
                                                            FastAPI app (app/main.py) <- Vis.js graph (app/static/index.html)
                                                            running on ECS Fargate, port 80, no ALB
```

- No ALB. The Fargate task has a public IP and a security group restricting inbound port 80 to `allowed_cidr_blocks`.
- No NAT Gateway. The app VPC is public-subnet-only; the Fargate task gets `assign_public_ip = true`.
- `connection_id` = `primary_ip|secondary_ip|protocol` (no port - see PLAN.md decision 12 for why).

## Python unit tests (Lambda logic)

No AWS account or installed tooling needed - uses `moto` to mock DynamoDB.

```bash
pip install -r tests/requirements.txt
pytest tests/ -v
```

## Local end-to-end testing (docker-compose)

Requires Docker. Runs DynamoDB Local + the FastAPI app together; does not involve the real Lambda.

```bash
docker-compose up --build
python scripts/seed_local_dynamodb.py   # populate fake edges so the graph renders something
# open http://localhost:8080
```

## Local Lambda testing (SAM)

Requires AWS SAM CLI + Docker. Run DynamoDB Local first (`docker-compose up dynamodb-local`), then:

```bash
sam build
sam local invoke FlowLogIngestionFunction -e events/sample_event.json
```

Regenerate the sample event (gzipped/base64-encoded CloudWatch Logs payload) with:

```bash
python scripts/make_sample_event.py
```

## Terraform deployment

Full step-by-step deployment, redeploy, IP-lookup, teardown, and troubleshooting instructions: see **[DEPLOYMENT.md](DEPLOYMENT.md)**.
