# Deployment Guide

Step-by-step instructions for deploying this stack to a real AWS account. For architecture/local-testing/teardown, see [README.md](README.md); for the full command reference, see [CLAUDE.md](CLAUDE.md).

## Prerequisites

| Tool | Needed for |
|---|---|
| [Terraform](https://developer.hashicorp.com/terraform/downloads) >= 1.5 | Provisioning all infrastructure |
| [Docker](https://docs.docker.com/get-docker/) | Building the app container image |
| [AWS CLI](https://aws.amazon.com/cli/) | Pushing the image to ECR, and the apply-time IP lookup Terraform shells out to |
| AWS credentials | Configured locally (`aws configure`, env vars, or an SSO profile) with permission to create VPCs, Lambda, DynamoDB, ECS/ECR, IAM roles, CloudWatch Logs |
| At least one existing VPC ID | The VPC(s) you want flow-logged — this stack does not create them |

This stack deliberately has no remote Terraform backend — state is a local `terraform.tfstate` file in `terraform/`. Run all `terraform` commands from the same machine/directory every time, and don't delete that state file between runs.

## Step 1 — Configure variables

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

- **`vpc_ids`** — the VPC ID(s) to enable flow logs on.
- **`allowed_cidr_blocks`** — **required, no default.** `terraform apply` will fail until this is set. This is the only thing standing between the web app and the public internet (there's no login), so scope it tight — e.g. your current IP as a `/32`.
- Everything else has a sensible default (region, table name, TTL, aggregation interval, etc.) — see `terraform/variables.tf` for the full list if you want to override more.

## Step 2 — Initialize and validate

```bash
terraform init
terraform validate
terraform plan
```

Review the plan. You're creating: a dedicated VPC for the app, a DynamoDB table, a Lambda function + IAM roles, per-VPC CloudWatch Log Groups + Flow Logs + Subscription Filters, an ECR repository, an ECS cluster/task/service, and a security group gating the app.

## Step 3 — Create the ECR repository first

The ECS task definition references `<repo>:latest`, which doesn't exist until you build and push an image — so the full `apply` would fail on a fresh account. Create just the repository first:

```bash
terraform apply -target=module.web_visualization.aws_ecr_repository.app
```

## Step 4 — Build and push the app image

```bash
REPO_URL=$(terraform output -raw ecr_repository_url)
REGION=$(terraform output -raw -state=terraform.tfstate 2>/dev/null || echo "<your-region>")  # or just hardcode the region you used

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO_URL%/*}"
docker build -t "$REPO_URL:latest" ../app
docker push "$REPO_URL:latest"
```

## Step 5 — Deploy everything else

```bash
terraform apply
```

This creates the Lambda, flow logs/subscriptions, and the ECS service. The ECS service won't come up healthy until the image from Step 4 exists in ECR — if you skipped straight to this step, go back to Step 3-4 first.

Terraform's `local-exec` provisioner polls the ECS API for the running task's public IP once the service is up (up to ~100s) and writes it to the `app_public_ip` / `app_url` outputs.

## Step 6 — Verify

```bash
terraform output app_url
```

Open that URL. The graph will be empty at first — it only populates once real traffic generates VPC Flow Log records and those get processed by the Lambda (allow a few minutes, since `flow_log_aggregation_interval` defaults to 60s and CloudWatch Logs delivery adds its own small delay).

## Finding the app's IP later

There's no load balancer, so there's no stable DNS name. If the Fargate task is ever replaced (a redeploy, a crash, a new image push followed by a service restart), it gets a new public IP and the `app_public_ip`/`app_url` outputs from Step 5 go stale. Look it up again with:

```bash
bash terraform/modules/web_visualization/scripts/fetch_public_ip.sh \
  "$(terraform output -raw ecs_cluster_name)" \
  "$(terraform output -raw ecs_service_name)" \
  "<your-region>" \
  /dev/stdout
```

## Redeploying a new image

Pushing a new image tag to ECR doesn't automatically restart the running task. Force a new deployment to pick it up:

```bash
aws ecs update-service \
  --cluster "$(terraform output -raw ecs_cluster_name)" \
  --service "$(terraform output -raw ecs_service_name)" \
  --force-new-deployment \
  --region <your-region>
```

The task gets a new public IP when this happens — re-run the IP lookup above afterward.

## Teardown

```bash
cd terraform
terraform destroy
```

The ECR repository has `force_delete = true`, so it's removed even if it still holds images — no manual cleanup step needed.

## Troubleshooting

- **`apply` fails with a missing image / ECS service stuck in PENDING**: you skipped Step 3/4, or pushed to the wrong repository URL. Confirm with `aws ecr describe-images --repository-name <repo>`.
- **`allowed_cidr_blocks` validation error**: this variable has no default by design — set it in `terraform.tfvars`.
- **`app_public_ip` output is `unavailable`**: the IP-lookup script (`fetch_public_ip.sh`) timed out waiting for a RUNNING task — check the ECS service's events in the console (often an image pull failure or a crash loop), then re-run the lookup command once the task is healthy.
- **Graph page loads but stays empty**: check the Lambda's CloudWatch Logs (`/aws/lambda/<project_name>-flow-log-ingestion`) for errors, and confirm the Subscription Filter exists on the VPC's flow log group.
