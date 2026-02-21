#!/usr/bin/env bash
# Run credential rotation dry-run, wait for task, and verify exit code and logs.
# Usage: ./scripts/verify-credential-rotation.sh [--stack-name NAME]
# Stack name is auto-detected if not provided.
set -euo pipefail

STACK_NAME="${STACK_NAME:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

detect_stack_name() {
  local candidates
  candidates=$(aws cloudformation list-stacks \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --query "StackSummaries[*].StackName" \
    --output text 2>/dev/null || true)
  for stack in $candidates; do
    local out
    out=$(aws cloudformation describe-stacks --stack-name "$stack" \
      --query "Stacks[0].Outputs[?OutputKey=='CredentialRotationTaskDefinitionArn'].OutputValue" \
      --output text 2>/dev/null || true)
    if [ -n "$out" ] && [ "$out" != "None" ]; then
      echo "$stack"
      return 0
    fi
  done
  return 1
}

if [ -z "$STACK_NAME" ]; then
  STACK_NAME=$(detect_stack_name) || true
  if [ -z "$STACK_NAME" ]; then
    echo "Could not auto-detect OpenEMR ECS stack. Use --stack-name NAME." >&2
    exit 1
  fi
fi

get_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

CLUSTER_NAME=$(get_output ECSClusterName)
LOG_GROUP=$(get_output LogGroupName)

echo "Starting dry-run rotation task..."
OUT=$(./scripts/run-credential-rotation.sh --stack-name "$STACK_NAME" --target both --dry-run 2>&1)
echo "$OUT"
TASK_ARN=$(echo "$OUT" | grep -o 'arn:aws:ecs:[^ ]*task/[^ ]*' | head -1)
if [ -z "$TASK_ARN" ]; then
  echo "Could not parse TASK_ARN from script output."
  exit 1
fi

echo "Waiting for task to stop..."
aws ecs wait tasks-stopped --cluster "$CLUSTER_NAME" --tasks "$TASK_ARN"

echo "Task result:"
aws ecs describe-tasks --cluster "$CLUSTER_NAME" --tasks "$TASK_ARN" \
  --query "tasks[0].containers[0].[name,lastStatus,exitCode,reason]" --output table

EXIT=$(aws ecs describe-tasks --cluster "$CLUSTER_NAME" --tasks "$TASK_ARN" \
  --query "tasks[0].containers[0].exitCode" --output text)
if [ "$EXIT" != "0" ]; then
  echo "Task exited with code $EXIT. Recent logs:"
  TASK_ID="${TASK_ARN##*/}"
  aws logs filter-log-events --log-group-name "$LOG_GROUP" \
    --log-stream-name-prefix "ecs/credential-rotation/credential-rotation/$TASK_ID" \
    --query 'events[*].message' --output text 2>/dev/null | tail -30
  exit 1
fi

echo "Dry-run verification passed (exit 0)."
