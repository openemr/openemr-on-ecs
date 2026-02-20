#!/usr/bin/env bash
# Fix sqlconf.php permissions on EFS (chmod 644) so Apache can read it.
# Use this when Apache logs show "Permission denied" for sqlconf.php.
# No redeploy required - runs a one-off task with the same networking as the rotation task.
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
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE \
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
    echo "Could not auto-detect stack. Use --stack-name NAME." >&2
    exit 1
  fi
fi

get_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true
}

# Prefer stack outputs; fall back to resource discovery during rollback
CLUSTER_NAME="$(get_output ECSClusterName)"
if [ -z "$CLUSTER_NAME" ] || [ "$CLUSTER_NAME" = "None" ]; then
  CLUSTER_NAME="$(aws ecs list-clusters --query 'clusterArns[0]' --output text 2>/dev/null | awk -F/ '{print $NF}')"
fi
TASK_DEF_ARN="$(get_output CredentialRotationTaskDefinitionArn)"
if [ -z "$TASK_DEF_ARN" ] || [ "$TASK_DEF_ARN" = "None" ]; then
  TASK_DEF_ARN="$(aws cloudformation list-stack-resources --stack-name "$STACK_NAME" \
    --query "StackResourceSummaries[?ResourceType=='AWS::ECS::TaskDefinition' && contains(LogicalResourceId,'CredentialRotation')].PhysicalResourceId" \
    --output text 2>/dev/null | head -1)"
fi
SERVICE_NAME="$(get_output ECSServiceName)"
if [ -z "$SERVICE_NAME" ] || [ "$SERVICE_NAME" = "None" ]; then
  SERVICE_NAME="$(aws ecs list-services --cluster "$CLUSTER_NAME" --query 'serviceArns[0]' --output text 2>/dev/null | awk -F/ '{print $NF}')"
fi
SUBNETS_RAW="$(aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" --query 'services[0].networkConfiguration.awsvpcConfiguration.subnets' --output text 2>/dev/null)"
SGS_RAW="$(aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" --query 'services[0].networkConfiguration.awsvpcConfiguration.securityGroups' --output text 2>/dev/null)"
SUBNETS_CSV="$(echo "$SUBNETS_RAW" | tr '\t' ',')"
SGS_CSV="$(echo "$SGS_RAW" | tr '\t' ',')"

if [ -z "$CLUSTER_NAME" ] || [ -z "$TASK_DEF_ARN" ] || [ -z "$SUBNETS_CSV" ] || [ -z "$SGS_CSV" ]; then
  echo "Could not discover cluster, task definition, or network config." >&2
  exit 1
fi

# Use Alpine task (no redeploy required); rotation task with --fix-permissions needs redeploy
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALPINE_TEMPLATE="${SCRIPT_DIR}/fix-sqlconf-permissions-alpine.template.json"

if [ ! -f "$ALPINE_TEMPLATE" ]; then
  echo "Alpine task template not found at $ALPINE_TEMPLATE" >&2
  exit 1
fi

# Get role ARNs from the credential rotation task definition
TASK_DEF_JSON="$(aws ecs describe-task-definition --task-definition "$TASK_DEF_ARN" --query 'taskDefinition' --output json 2>/dev/null)"
EXEC_ROLE_ARN="$(echo "$TASK_DEF_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('executionRoleArn',''))" 2>/dev/null)"
TASK_ROLE_ARN="$(echo "$TASK_DEF_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('taskRoleArn',''))" 2>/dev/null)"
EFS_ID="$(get_output EFSSitesFileSystemId)"
LOG_GROUP="$(get_output LogGroupName)"
AWS_REGION="${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo "us-west-2")}"

if [ -z "$EXEC_ROLE_ARN" ] || [ -z "$TASK_ROLE_ARN" ] || [ -z "$EFS_ID" ] || [ -z "$LOG_GROUP" ]; then
  echo "Could not resolve execution role, task role, EFS ID, or log group from stack." >&2
  exit 1
fi

# Generate task definition from template
ALPINE_TASK_JSON="$(mktemp)"
trap 'rm -f "$ALPINE_TASK_JSON"' EXIT
sed -e "s|PLACEHOLDER_EXECUTION_ROLE_ARN|$EXEC_ROLE_ARN|g" \
    -e "s|PLACEHOLDER_TASK_ROLE_ARN|$TASK_ROLE_ARN|g" \
    -e "s|PLACEHOLDER_EFS_FILE_SYSTEM_ID|$EFS_ID|g" \
    -e "s|PLACEHOLDER_LOG_GROUP_NAME|$LOG_GROUP|g" \
    -e "s|PLACEHOLDER_AWS_REGION|$AWS_REGION|g" \
    "$ALPINE_TEMPLATE" > "$ALPINE_TASK_JSON"

echo "Registering one-off task definition..."
aws ecs register-task-definition --cli-input-json "file://$ALPINE_TASK_JSON" --query 'taskDefinition.taskDefinitionArn' --output text >/dev/null 2>&1 || true

echo "Running one-off task to fix sqlconf.php permissions..."
TASK_ARN="$(aws ecs run-task \
  --cluster "$CLUSTER_NAME" \
  --task-definition "openemr-fix-sqlconf-permissions" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS_CSV],securityGroups=[$SGS_CSV],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' \
  --output text)"

if [ -z "$TASK_ARN" ] || [ "$TASK_ARN" = "None" ]; then
  echo "Failed to start task" >&2
  exit 1
fi

echo "Task started: $TASK_ARN"
echo "Waiting for task to stop..."
aws ecs wait tasks-stopped --cluster "$CLUSTER_NAME" --tasks "$TASK_ARN"

EXIT=$(aws ecs describe-tasks --cluster "$CLUSTER_NAME" --tasks "$TASK_ARN" \
  --query "tasks[0].containers[0].exitCode" --output text)
if [ "$EXIT" = "0" ]; then
  echo "Permissions fixed. ECS tasks should recover on next health check."
else
  echo "Task exited with code $EXIT. Check CloudWatch logs." >&2
  exit 1
fi
