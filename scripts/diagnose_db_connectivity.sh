#!/bin/bash
#
# RDS Aurora Serverless v2 Connectivity Diagnostic Script
#
# This script helps diagnose database connectivity issues between ECS and RDS
#

set -euo pipefail

echo "=================================="
echo "RDS Connectivity Diagnostic Tool"
echo "=================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get stack name from cdk.json or use default
STACK_NAME=$(jq -r '.context.stack_name // "OpenemrEcsStack"' cdk.json 2>/dev/null || echo "OpenemrEcsStack")

echo "Using stack name: $STACK_NAME"
echo ""

# 1. Check RDS Cluster Status
echo "[1/6] Checking RDS cluster status..."
CLUSTER_ID=$(aws cloudformation describe-stack-resources \
    --stack-name "$STACK_NAME" \
    --query "StackResources[?ResourceType=='AWS::RDS::DBCluster'].PhysicalResourceId" \
    --output text 2>/dev/null || echo "")

if [ -z "$CLUSTER_ID" ]; then
    echo -e "${RED}✗ Could not find RDS cluster${NC}"
    echo "  Stack may not be deployed yet"
    exit 1
fi

echo "  Cluster ID: $CLUSTER_ID"

CLUSTER_STATUS=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query "DBClusters[0].Status" \
    --output text 2>/dev/null || echo "unknown")

if [ "$CLUSTER_STATUS" = "available" ]; then
    echo -e "${GREEN}✓ Cluster status: $CLUSTER_STATUS${NC}"
else
    echo -e "${YELLOW}⚠ Cluster status: $CLUSTER_STATUS${NC}"
    echo "  Cluster may still be starting up or scaling"
fi

# 2. Check current capacity
echo ""
echo "[2/6] Checking Aurora Serverless v2 current capacity..."
CURRENT_CAPACITY=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query "DBClusters[0].ServerlessV2ScalingConfiguration.MinCapacity" \
    --output text 2>/dev/null || echo "unknown")

CURRENT_ACU=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query "DBClusters[0].ServerlessV2ScalingConfiguration" \
    --output json 2>/dev/null || echo "{}")

echo "  Min Capacity: $(echo "$CURRENT_ACU" | jq -r '.MinCapacity // "unknown"') ACU"
echo "  Max Capacity: $(echo "$CURRENT_ACU" | jq -r '.MaxCapacity // "unknown"') ACU"

MIN_CAP=$(echo "$CURRENT_ACU" | jq -r '.MinCapacity // 0')
if [ "$MIN_CAP" = "0" ] || [ "$MIN_CAP" = "0.0" ]; then
    echo -e "${RED}✗ PROBLEM FOUND: Min capacity is 0!${NC}"
    echo "  This allows the database to scale down to completely stopped"
    echo "  ECS containers will fail to connect until database scales up (takes minutes)"
    echo ""
    echo -e "${YELLOW}  RECOMMENDATION: Set serverless_v2_min_capacity to 0.5 or higher${NC}"
else
    echo -e "${GREEN}✓ Min capacity is set to $MIN_CAP ACU${NC}"
fi

# 3. Check DB instances status
echo ""
echo "[3/6] Checking DB instance status..."
INSTANCE_STATUSES=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query "DBClusters[0].DBClusterMembers[*].[DBInstanceIdentifier,IsClusterWriter]" \
    --output text 2>/dev/null || echo "")

if [ -z "$INSTANCE_STATUSES" ]; then
    echo -e "${YELLOW}⚠ No instances found or instances are scaling${NC}"
else
    while IFS=$'\t' read -r instance_id is_writer; do
        INST_STATUS=$(aws rds describe-db-instances \
            --db-instance-identifier "$instance_id" \
            --query "DBInstances[0].DBInstanceStatus" \
            --output text 2>/dev/null || echo "unknown")
        
        ROLE=$([ "$is_writer" = "True" ] && echo "writer" || echo "reader")
        
        if [ "$INST_STATUS" = "available" ]; then
            echo -e "${GREEN}✓ Instance $instance_id ($ROLE): $INST_STATUS${NC}"
        else
            echo -e "${YELLOW}⚠ Instance $instance_id ($ROLE): $INST_STATUS${NC}"
        fi
    done <<< "$INSTANCE_STATUSES"
fi

# 4. Check security group rules
echo ""
echo "[4/6] Checking security group rules..."
DB_SG=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query "DBClusters[0].VpcSecurityGroups[0].VpcSecurityGroupId" \
    --output text 2>/dev/null || echo "")

if [ -n "$DB_SG" ]; then
    echo "  Database Security Group: $DB_SG"
    
    INGRESS_RULES=$(aws ec2 describe-security-groups \
        --group-ids "$DB_SG" \
        --query "SecurityGroups[0].IpPermissions[?ToPort==\`3306\`]" \
        --output json 2>/dev/null || echo "[]")
    
    RULE_COUNT=$(echo "$INGRESS_RULES" | jq 'length')
    
    if [ "$RULE_COUNT" -gt 0 ]; then
        echo -e "${GREEN}✓ Found $RULE_COUNT ingress rule(s) for port 3306${NC}"
        echo "$INGRESS_RULES" | jq -r '.[] | "  From: " + (.UserIdGroupPairs[0].GroupId // .IpRanges[0].CidrIp // "unknown")'
    else
        echo -e "${RED}✗ No ingress rules found for port 3306${NC}"
        echo "  ECS tasks cannot connect to database!"
    fi
else
    echo -e "${YELLOW}⚠ Could not find database security group${NC}"
fi

# 5. Check ECS task security group
echo ""
echo "[5/6] Checking ECS task security group..."
ECS_SG=$(aws cloudformation describe-stack-resources \
    --stack-name "$STACK_NAME" \
    --query "StackResources[?LogicalResourceId=='EcsTaskSecurityGroup'].PhysicalResourceId" \
    --output text 2>/dev/null || echo "")

if [ -n "$ECS_SG" ]; then
    echo "  ECS Task Security Group: $ECS_SG"
    
    EGRESS_RULES=$(aws ec2 describe-security-groups \
        --group-ids "$ECS_SG" \
        --query "SecurityGroups[0].IpPermissionsEgress[?ToPort==\`3306\`]" \
        --output json 2>/dev/null || echo "[]")
    
    EGRESS_COUNT=$(echo "$EGRESS_RULES" | jq 'length')
    
    if [ "$EGRESS_COUNT" -gt 0 ]; then
        echo -e "${GREEN}✓ Found $EGRESS_COUNT egress rule(s) for port 3306${NC}"
    else
        echo -e "${RED}✗ No egress rules found for port 3306${NC}"
        echo "  ECS tasks cannot initiate connections to database!"
    fi
else
    echo -e "${YELLOW}⚠ Could not find ECS task security group${NC}"
fi

# 6. Check recent CloudWatch Logs for connection errors
echo ""
echo "[6/6] Checking recent ECS container logs..."
LOG_GROUP=$(aws cloudformation describe-stack-resources \
    --stack-name "$STACK_NAME" \
    --query "StackResources[?ResourceType=='AWS::Logs::LogGroup' && contains(PhysicalResourceId, 'openemr')].PhysicalResourceId" \
    --output text 2>/dev/null | head -1 || echo "")

if [ -n "$LOG_GROUP" ]; then
    echo "  Log Group: $LOG_GROUP"
    echo ""
    echo "  Recent database connection attempts (last 5 minutes):"
    
    # Get logs from last 5 minutes
    START_TIME=$(($(date +%s) - 300))000
    
    aws logs filter-log-events \
        --log-group-name "$LOG_GROUP" \
        --start-time "$START_TIME" \
        --filter-pattern "database" \
        --query "events[*].message" \
        --output text \
        --max-items 10 2>/dev/null | head -20 || echo "  (No recent logs found)"
else
    echo "  Could not find CloudWatch log group"
fi

# Summary
echo ""
echo "=================================="
echo "Summary & Recommendations"
echo "=================================="
echo ""

if [ "$MIN_CAP" = "0" ] || [ "$MIN_CAP" = "0.0" ]; then
    echo -e "${RED}CRITICAL ISSUE DETECTED:${NC}"
    echo ""
    echo "Your Aurora Serverless v2 cluster has min_capacity=0, which allows it to"
    echo "scale down to completely stopped when idle. This causes connection failures"
    echo "because the database needs several minutes to scale back up."
    echo ""
    echo -e "${YELLOW}SOLUTION:${NC}"
    echo ""
    echo "1. Edit openemr_ecs/database.py"
    echo "2. Find 'serverless_v2_min_capacity=0'"
    echo "3. Change to 'serverless_v2_min_capacity=0.5' (recommended minimum)"
    echo "4. Run: cdk deploy"
    echo ""
    echo "Recommended capacity settings:"
    echo "  - Development: min=0.5, max=2"
    echo "  - Production:  min=1, max=16+ (based on load)"
    echo ""
else
    echo -e "${GREEN}No critical issues detected${NC}"
    echo ""
    if [ "$CLUSTER_STATUS" != "available" ]; then
        echo "Database cluster status is: $CLUSTER_STATUS"
        echo "Wait for cluster to become 'available' before testing connectivity"
    fi
fi

