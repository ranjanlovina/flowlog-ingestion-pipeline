# Build it by hand in the AWS Console

This builds the same system Terraform would, one resource at a time. Use **us-east-1** everywhere (top-right region selector) so the names below match. Build in this order, because later pieces refer to earlier ones.

```
Step 1  DynamoDB table
Step 2  Lambda (IAM role + function)
Step 3  CloudWatch log group + subscription filter
Step 4  VPC Flow Log (IAM role + flow log)
Step 5  Test the ingest half with a fake log event
Step 6  Network for the web app (VPC, subnet, IGW, route, security group)
Step 7  ECR repo + push the Docker image
Step 8  ECS (IAM roles, cluster, task definition, service)
Step 9  Open the app, generate traffic
Step 10 Delete everything
```

Keep a notepad of every resource you create. You need it for Step 10.

Names used below: table `vpc_flow_log_edges`, function `flowlog-viz-flow-log-ingestion`, log group `/vpc/flow-logs/<vpc-id>`.

---

## Step 1: DynamoDB table

Console: **DynamoDB -> Tables -> Create table**

1. Table name: `vpc_flow_log_edges`
2. Partition key: `connection_id`, type **String**. No sort key.
3. Table settings: **Customize settings**, Capacity mode **On-demand**.
4. Create table.
5. Open the table -> **Additional settings** tab -> **Time to Live (TTL) -> Turn on**, attribute name `ttl_expiry`.

Why: each row is one edge (`ip_a|ip_b|protocol`). TTL deletes edges nobody has touched for 30 days.

---

## Step 2: Lambda

### 2a. Create the function

Console: **Lambda -> Create function -> Author from scratch**

- Name: `flowlog-viz-flow-log-ingestion`
- Runtime: **Python 3.12**
- Architecture: x86_64
- Permissions: leave "Create a new role with basic Lambda permissions". This role already gets CloudWatch Logs write access.

### 2b. Paste the code

In the **Code** tab, open `lambda_function.py`. Replace its contents with the whole of `lambda/index.py` from this repo. Then **rename the file to `index.py`**: right-click it in the left file tree -> Rename. (The handler setting below expects `index.handler`.) Click **Deploy**.

### 2c. Settings

**Configuration -> General configuration -> Edit**: Memory 256 MB, Timeout 30 sec.

**Configuration -> Environment variables -> Edit**:

| Key | Value |
|---|---|
| `DYNAMODB_TABLE` | `vpc_flow_log_edges` |

**Code tab -> Runtime settings -> Edit**: Handler = `index.handler`.

### 2d. Let the Lambda write to DynamoDB

**Configuration -> Permissions** -> click the role name (opens IAM) -> **Add permissions -> Create inline policy -> JSON**:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem"],
    "Resource": "arn:aws:dynamodb:us-east-1:<YOUR_ACCOUNT_ID>:table/vpc_flow_log_edges"
  }]
}
```

Replace `<YOUR_ACCOUNT_ID>` (12 digits, top-right menu in the console). Name it `dynamodb-access`.

Why least privilege: the Lambda only writes, so it gets no Scan or Delete.

---

## Step 3: CloudWatch log group and subscription filter

### 3a. Log group

**CloudWatch -> Logs -> Log groups -> Create log group**

- Name: `/vpc/flow-logs/<your-vpc-id>`  (for example `/vpc/flow-logs/vpc-0abc123`)
- Retention: 1 month

### 3b. Allow CloudWatch Logs to call the Lambda

If you create the filter through the console UI (3c), AWS adds this permission for you. If it complains about permissions, add it by hand:

**Lambda -> your function -> Configuration -> Permissions -> Resource-based policy statements -> Add permissions -> AWS service**

- Service: **Other**, Statement ID: `AllowCloudWatchLogs`
- Principal: `logs.us-east-1.amazonaws.com`
- Source ARN: `arn:aws:logs:us-east-1:<YOUR_ACCOUNT_ID>:log-group:/vpc/flow-logs/<your-vpc-id>:*`
- Action: `lambda:InvokeFunction`

### 3c. Subscription filter

Open the log group -> **Subscription filters** tab -> **Create -> Create Lambda subscription filter**

- Lambda function: `flowlog-viz-flow-log-ingestion`
- Log format: **Other**
- Subscription filter pattern: leave **empty** (send every line)
- Subscription filter name: `to-lambda`
- **Start streaming**

Why: this is the "push" connection. CloudWatch batches new log lines and invokes your Lambda with them.

---

## Step 4: VPC Flow Log

### 4a. IAM role so Flow Logs can write into CloudWatch

**IAM -> Roles -> Create role -> Custom trust policy**:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "vpc-flow-logs.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

Next, skip attaching managed policies, name the role `flowlog-viz-flow-log-publish`, create it. Then open it -> **Add permissions -> Create inline policy -> JSON**:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
      "logs:DescribeLogGroups", "logs:DescribeLogStreams"
    ],
    "Resource": "*"
  }]
}
```

### 4b. Create the flow log

**VPC -> Your VPCs -> select the VPC -> Flow logs tab -> Create flow log**

- Name: `flowlog-viz`
- Filter: **All**
- Maximum aggregation interval: **1 minute**
- Destination: **Send to CloudWatch Logs**
- Destination log group: `/vpc/flow-logs/<your-vpc-id>`
- Service access: **Use an existing service role** -> `flowlog-viz-flow-log-publish`
- Log record format: **AWS default format**. This matters: the Lambda expects exactly the 14 default fields.
- Create flow log.

Which VPC to monitor: pick one that has an EC2 instance you can generate traffic from, for example the default VPC.

---

## Step 5: Test the ingest half before building the web app

### 5a. Fake event (works instantly)

The repo has a prepared payload at `events/sample_event.json`. In **Lambda -> Test tab -> Create new event**, paste its contents, name it `sample`, click **Test**.

Expect a green result like `{"processed": N}`. Then **DynamoDB -> Tables -> vpc_flow_log_edges -> Explore table items** and you should see rows.

If it fails, read the error in the result box. Common causes:
- `No module named 'index'`: file not renamed, or handler not `index.handler`.
- `AccessDeniedException`: the inline policy in 2d has a wrong ARN.
- `ResourceNotFoundException`: table name or region mismatch.

### 5b. Real event

Wait 5-10 minutes after Step 4. CloudWatch -> your log group -> **Log streams** should show one stream per network interface. Then check DynamoDB again, and the Lambda's **Monitor -> View CloudWatch logs** for invocations.

---

## Step 6: Network for the web app

A new VPC, separate from the monitored one.

### 6a. VPC

**VPC -> Create VPC -> VPC only**: name `flowlog-viz-app`, IPv4 CIDR `10.100.0.0/16`. After creating: **Actions -> Edit VPC settings -> Enable DNS hostnames**.

### 6b. Subnet

**Subnets -> Create subnet**: VPC `flowlog-viz-app`, name `flowlog-viz-public`, any AZ, CIDR `10.100.1.0/24`. After creating: **Actions -> Edit subnet settings -> Enable auto-assign public IPv4**.

### 6c. Internet gateway

**Internet gateways -> Create** (`flowlog-viz-igw`) -> **Actions -> Attach to VPC** -> `flowlog-viz-app`.

### 6d. Route to the internet

**Route tables** -> find the one for `flowlog-viz-app` (Main) -> **Routes -> Edit routes -> Add route**: `0.0.0.0/0` -> Target **Internet Gateway** -> `flowlog-viz-igw`. Then **Subnet associations -> Edit** -> tick `flowlog-viz-public`.

Why: no NAT Gateway. The task gets a public IP and talks to ECR, DynamoDB and the internet directly through the IGW.

### 6e. Security group

**Security groups -> Create**: name `flowlog-viz-app`, VPC `flowlog-viz-app`.

- Inbound: **Custom TCP, port 80, source `<your-public-ip>/32`**. Get your IP with `curl https://checkip.amazonaws.com`. **Never use 0.0.0.0/0**, since the app has no login.
- Outbound: leave the default (all).

---

## Step 7: ECR repository and the image

### 7a. Repository

**ECR -> Private registry -> Repositories -> Create**: name `flowlog-viz-app`, mutable tags.

### 7b. Build and push (Git Bash, from the repo root)

Open the repo in ECR and click **View push commands**; it shows these. Doing it yourself:

```bash
ACCOUNT=<YOUR_ACCOUNT_ID>
REGION=us-east-1
REPO=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/flowlog-viz-app

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
docker build -t $REPO:latest app
docker push $REPO:latest
```

Docker Desktop must be running. Refresh the ECR repo page and you should see the `latest` tag.

---

## Step 8: ECS

### 8a. Two IAM roles

Both have the trust policy "AWS service -> **Elastic Container Service Task**" (`ecs-tasks.amazonaws.com`). In IAM -> Create role -> AWS service -> use case **Elastic Container Service Task**.

1. **Execution role** `flowlog-viz-ecs-task-execution`: attach managed policy `AmazonECSTaskExecutionRolePolicy`. ECS itself uses it to pull the image and write container logs.
2. **Task role** `flowlog-viz-ecs-task`: no managed policy. Add an inline policy:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["dynamodb:Scan", "dynamodb:Query"],
       "Resource": "arn:aws:dynamodb:us-east-1:<YOUR_ACCOUNT_ID>:table/vpc_flow_log_edges"
     }]
   }
   ```
   Your app code uses this one.

### 8b. Log group for the container

**CloudWatch -> Log groups -> Create**: `/ecs/flowlog-viz-app`.

### 8c. Cluster

**ECS -> Clusters -> Create cluster**: name `flowlog-viz-cluster`, infrastructure **AWS Fargate** only.

### 8d. Task definition

**ECS -> Task definitions -> Create new task definition**

- Family: `flowlog-viz-app`
- Launch type: **AWS Fargate**, OS Linux/X86_64
- CPU 0.25 vCPU, Memory 0.5 GB
- Task role: `flowlog-viz-ecs-task`
- Task execution role: `flowlog-viz-ecs-task-execution`
- Container:
  - Name `app`
  - Image URI: `<ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/flowlog-viz-app:latest`
  - Container port **80**, protocol TCP
  - Environment variables: `DYNAMODB_TABLE` = `vpc_flow_log_edges`, `AWS_REGION` = `us-east-1`
  - Logging: awslogs, group `/ecs/flowlog-viz-app`, region `us-east-1`, stream prefix `app`

### 8e. Service

Open the cluster -> **Services -> Create**

- Compute: **Launch type -> FARGATE**
- Task definition family `flowlog-viz-app`, latest revision
- Service name `flowlog-viz-app`, desired tasks **1**
- Networking: VPC `flowlog-viz-app`, subnet `flowlog-viz-public` only, security group `flowlog-viz-app` (remove the default one), **Public IP: ON**
- No load balancer.

Wait until the service shows 1 running task.

---

## Step 9: Open it

1. ECS -> cluster -> service -> **Tasks** tab -> click the task -> **Networking** section -> copy the **Public IP**.
2. Open `http://<public-ip>` in your browser (it is http, not https).
3. Generate traffic in the monitored VPC: SSH into an EC2 there and `ping` another instance or `curl` something. Wait 2-5 minutes (flow log 60 s aggregation plus delivery) and the graph appears; it auto-refreshes every 10 s.

If the page does not load: security group inbound IP (your IP may have changed), task not running, or the task stopped (check **Logs** tab and **Stopped tasks** for `CannotPullContainerError`, which usually means the image tag is missing or the task lacks a public IP).

If the page loads but the graph is empty: DynamoDB has no items (go back to Step 5), or the task role (8a.2) is missing.

The IP changes every time the task is replaced. Repeat the lookup above.

---

## Step 10: Delete everything (reverse order)

1. **ECS**: service -> Update desired tasks to 0 -> Delete service (force). Then delete the cluster. Task definition -> Deregister all revisions.
2. **ECR**: delete repository `flowlog-viz-app`.
3. **VPC (app)**: delete the security group, then the subnet, then detach and delete the IGW, then delete the VPC `flowlog-viz-app`.
4. **VPC Flow Log**: monitored VPC -> Flow logs tab -> Delete flow log. (This does not delete the VPC itself; do not delete that one.)
5. **CloudWatch**: delete the subscription filter, then the log groups `/vpc/flow-logs/<vpc-id>`, `/ecs/flowlog-viz-app`, and the Lambda's `/aws/lambda/flowlog-viz-flow-log-ingestion`.
6. **Lambda**: delete the function.
7. **DynamoDB**: delete table `vpc_flow_log_edges`.
8. **IAM**: delete roles `flowlog-viz-flow-log-publish`, `flowlog-viz-ecs-task-execution`, `flowlog-viz-ecs-task`, and the Lambda's auto-created role (`flowlog-viz-flow-log-ingestion-role-xxxx`).
9. Any test EC2 instance: terminate it.
10. Check **Billing -> Bills** the next day to confirm nothing is still running.

---

## Console vs Terraform (what you just learned)

| You did by hand | Terraform equivalent |
|---|---|
| Steps 1-4 | `modules/flow_log_ingestion` |
| Steps 6-8 | `modules/web_visualization` |
| Pasting code into Lambda | `archive_file` zips `lambda/index.py` for you |
| Clicking public IP in ECS | the `fetch_public_ip.sh` local-exec |
| Step 10 | `terraform destroy` |
