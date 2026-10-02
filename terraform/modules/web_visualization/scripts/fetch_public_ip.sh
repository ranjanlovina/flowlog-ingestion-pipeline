#!/usr/bin/env bash
# Looks up the current public IP of the running ECS Fargate task for the
# given cluster/service and writes it to $OUTPUT_FILE. Used both by
# Terraform (local-exec, right after `apply`) and documented standalone in
# CLAUDE.md/README.md for re-use whenever the task is later replaced and
# the Terraform output has gone stale.
#
# Usage: fetch_public_ip.sh <cluster> <service> <region> <output_file>

set -euo pipefail

CLUSTER="$1"
SERVICE="$2"
REGION="$3"
OUTPUT_FILE="$4"

for attempt in $(seq 1 20); do
  TASK_ARN=$(aws ecs list-tasks \
    --cluster "$CLUSTER" \
    --service-name "$SERVICE" \
    --desired-status RUNNING \
    --region "$REGION" \
    --query 'taskArns[0]' \
    --output text 2>/dev/null || true)

  if [ -n "$TASK_ARN" ] && [ "$TASK_ARN" != "None" ]; then
    break
  fi
  sleep 5
done

if [ -z "${TASK_ARN:-}" ] || [ "$TASK_ARN" = "None" ]; then
  echo "ERROR: no RUNNING task found for service $SERVICE in cluster $CLUSTER after waiting" >&2
  echo "unavailable" > "$OUTPUT_FILE"
  exit 0
fi

ENI_ID=$(aws ecs describe-tasks \
  --cluster "$CLUSTER" \
  --tasks "$TASK_ARN" \
  --region "$REGION" \
  --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' \
  --output text)

PUBLIC_IP=$(aws ec2 describe-network-interfaces \
  --network-interface-ids "$ENI_ID" \
  --region "$REGION" \
  --query 'NetworkInterfaces[0].Association.PublicIp' \
  --output text)

echo "$PUBLIC_IP" > "$OUTPUT_FILE"
