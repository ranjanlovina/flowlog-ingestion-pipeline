# Build Log: manual AWS Console deployment

The repo ships Terraform, but I deployed every component by hand in the AWS Console (us-east-1) to learn how each piece connects. Step-by-step instructions: [MANUAL_CONSOLE_GUIDE.md](../MANUAL_CONSOLE_GUIDE.md).

## Architecture

```
Default VPC (monitored, 172.31.0.0/16)
  └─ VPC Flow Log (ALL traffic, 1-min aggregation)
        └─> CloudWatch Log Group /vpc/flow-logs/<vpc-id>
              └─ Subscription Filter ──> Lambda flowlog-viz-flow-log-ingestion (Python 3.12)
                                              └─> DynamoDB vpc_flow_log_edges (TTL 30 days)
                                                        ▲
App VPC (flowlog-viz-app, 10.100.0.0/16, public subnet, IGW, no NAT, no ALB)
  └─ ECS Fargate task (FastAPI + Vis.js) ── scans table ─┘
        reached on port 80 from my IP only (security group)
```

## Build order

| # | Component | Notes |
|---|---|---|
| 1 | DynamoDB table `vpc_flow_log_edges` | Partition key `connection_id` (String), on-demand, TTL attribute `ttl_expiry` |
| 2 | Lambda + IAM role | Handler `index.handler`, 256 MB, 30 s, env `DYNAMODB_TABLE`. Inline policy: PutItem/UpdateItem/GetItem on the one table only |
| 3 | Log group + subscription filter | Empty filter pattern. The console added the CloudWatch→Lambda invoke permission automatically |
| 4 | Flow log + IAM role | Role trusted by `vpc-flow-logs.amazonaws.com`, write-only log permissions |
| 5 | Pipeline test | Sample event first, then real traffic from test EC2 instances |
| 6 | App VPC | VPC, public subnet, internet gateway, route table, security group (port 80 from my IP /32) |
| 7 | ECR + image | Built locally with Docker, pushed to a private ECR repo |
| 8 | ECS Fargate | Cluster, task definition (task role = DynamoDB Scan/Query only), service with public IP |
| 9 | Verify | Opened the task's public IP and saw the live graph |

## Problems hit and how I solved them

| Problem | Cause | Fix / lesson |
|---|---|---|
| Inline policy rejected with "invalid account id" | Placeholder brackets left in the ARN | Use the plain 12-digit ID, or copy the table ARN from DynamoDB |
| Flow log group looked empty while the Lambda had log streams | I was comparing two different log groups | Flow records are in `/vpc/flow-logs/<vpc-id>`; the Lambda's own output is in `/aws/lambda/...`. Flow logs need real traffic and a few minutes of delay |
| CloudShell: "account verification in progress" | New account still being verified | Built the image locally with Docker instead |
| Docker Desktop: "Virtualization support not detected" | CPU virtualization disabled in BIOS | Enabled VT-x/SVM, installed WSL2, then Docker Desktop |
| `InvalidClientTokenId` from the CLI | No valid access key had been created | AWS discourages root access keys, so I used `aws login` (browser-based, short-lived session) |
| Image push failed with an expired session | CLI login and console login are separate sessions | Re-ran `aws login` before the ECR login |
| Graph nodes constantly moved and could not be clicked | `refresh()` did `clear()` + `add()` every 10 s, resetting positions and restarting physics | Diff-based dataset updates, physics runs once then freezes, added a legend and click-for-details panel |
| Large traffic numbers after terminating the instances | DynamoDB keeps edges for 30 days and counts are cumulative; ALL traffic includes rejected scans and DNS/metadata chatter | Checked the instances and network interfaces were gone and that the remaining ENI belonged to the app VPC's Fargate task |

## Design decisions

- **Least privilege:** the Lambda can only write one table; the app task role can only Scan/Query it; the flow log role can only write logs.
- **Low idle cost:** no NAT Gateway, no ALB. The task gets a public IP, gated by a security group to a single IP.
- **`connection_id` = `primary_ip|secondary_ip|protocol`:** sorted IPs, port omitted, so both directions merge and bidirectional traffic can be detected (see PLAN.md decision 12).
- **Short log retention** on the app's log group to avoid paying for logs nobody reads.

## Screenshots

Add to `docs/screenshots/`:

- `01-graph.png`: the live graph with legend
- `02-dynamodb-items.png`
- `03-lambda-and-subscription-filter.png`
- `04-flow-log-active.png`
- `05-ecs-service-running.png`
- `06-ecr-image.png`
- `07-iam-roles.png`

## Console vs Terraform

Doing it by hand showed me what Terraform automates: the IAM trust relationships, the Lambda permission for CloudWatch Logs, resource ordering (the role must exist before the flow log), and the teardown order (the reverse of creation).

## Cost

Everything was deleted after the demo (see the teardown step in the guide). Main cost drivers while running: EC2 test instances, flow log ingestion, and the Fargate task.
