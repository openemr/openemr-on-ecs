#!/usr/bin/env bash
set -euo pipefail

STACK_NAME=""
DRY_RUN="false"
LOG_JSON="true"
SYNC_DB_USERS="false"

usage() {
  cat <<'EOF'
Run the OpenEMR credential rotation ECS task.

Usage:
  ./scripts/run-credential-rotation.sh [options]

Options:
  --stack-name NAME     CloudFormation stack name (default: auto-detect)
  --dry-run             Evaluate flow without mutating state
  --sync-db-users       Sync RDS users to match slot secrets (recovery from DB connection errors)
  --no-json             Disable --log-json output from task
  -h, --help            Show this help
EOF
}

# Auto-detect stack by finding one that has CredentialRotationTaskDefinitionArn output.
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
    if [[ -n "$out" && "$out" != "None" ]]; then
      echo "$stack"
      return
    fi
  done
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stack-name)
      STACK_NAME="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --sync-db-users)
      SYNC_DB_USERS="true"
      shift
      ;;
    --no-json)
      LOG_JSON="false"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$STACK_NAME" ]]; then
  STACK_NAME=$(detect_stack_name) || true
  if [[ -z "$STACK_NAME" ]]; then
    echo "Could not auto-detect OpenEMR ECS stack (no stack with CredentialRotationTaskDefinitionArn output). Use --stack-name NAME." >&2
    exit 1
  fi
fi

if [[ "$SYNC_DB_USERS" == "true" ]]; then
  :  # --sync-db-users overrides dry-run
fi

get_output() {
  local key="$1"
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue" \
    --output text
}

CLUSTER_NAME="$(get_output ECSClusterName)"
SERVICE_NAME="$(get_output ECSServiceName)"
TASK_DEF_ARN="$(get_output CredentialRotationTaskDefinitionArn)"
LOG_GROUP_NAME="$(get_output LogGroupName)"

if [[ -z "$CLUSTER_NAME" || -z "$SERVICE_NAME" || -z "$TASK_DEF_ARN" ]]; then
  echo "Required stack outputs not found. Did you deploy the latest stack?" >&2
  exit 1
fi

SUBNETS_RAW="$(aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" --query 'services[0].networkConfiguration.awsvpcConfiguration.subnets' --output text)"
SGS_RAW="$(aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" --query 'services[0].networkConfiguration.awsvpcConfiguration.securityGroups' --output text)"

if [[ -z "$SUBNETS_RAW" || -z "$SGS_RAW" ]]; then
  echo "Unable to discover subnet/security-group configuration from ECS service" >&2
  exit 1
fi

SUBNETS_CSV="$(echo "$SUBNETS_RAW" | tr '\t' ',')"
SGS_CSV="$(echo "$SGS_RAW" | tr '\t' ',')"

export DRY_RUN LOG_JSON SYNC_DB_USERS
echo "Running credential rotation: dry_run=$DRY_RUN sync_db_users=$SYNC_DB_USERS"
OVERRIDES_JSON="$(python3 - <<'PY'
import json
import os

if os.environ.get("SYNC_DB_USERS") == "true":
    cmd = ["--sync-db-users"]
else:
    cmd = []
    if os.environ["DRY_RUN"] == "true":
        cmd.append("--dry-run")
if os.environ["LOG_JSON"] == "true":
    cmd.append("--log-json")

print(json.dumps({"containerOverrides": [{"name": "credential-rotation", "command": cmd}]}))
PY
)"

TASK_ARN="$(aws ecs run-task \
  --cluster "$CLUSTER_NAME" \
  --task-definition "$TASK_DEF_ARN" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS_CSV],securityGroups=[$SGS_CSV],assignPublicIp=DISABLED}" \
  --overrides "$OVERRIDES_JSON" \
  --query 'tasks[0].taskArn' \
  --output text)"

if [[ "$TASK_ARN" == "None" || -z "$TASK_ARN" ]]; then
  echo "Failed to start credential rotation task" >&2
  exit 1
fi

TASK_ID="${TASK_ARN##*/}"

echo "Started credential rotation task: $TASK_ARN"
echo "Check task status: aws ecs describe-tasks --cluster $CLUSTER_NAME --tasks $TASK_ARN"
echo "Tail logs: aws logs tail $LOG_GROUP_NAME --follow --log-stream-name-prefix ecs/credential-rotation"
echo "Filter this task: aws logs filter-log-events --log-group-name $LOG_GROUP_NAME --log-stream-names \"ecs/credential-rotation/credential-rotation/$TASK_ID\""
