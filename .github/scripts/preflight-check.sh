#!/bin/bash
#
# Pre-flight validation script for deploy-staging.yml
# Checks if required infrastructure exists before attempting deployment
#
# Usage: ./preflight-check.sh <environment>
# Example: ./preflight-check.sh staging
#
# Exit codes:
#   0 - All checks passed
#   1 - Infrastructure missing or validation failed
#   2 - Invalid arguments or configuration error

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ENVIRONMENT="${1:-staging}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# Environment-specific configuration
case "$ENVIRONMENT" in
    staging)
        AWS_ACCOUNT_ID="737549531759"
        CLUSTER_NAME="rubrum-staging"
        DB_CLUSTER="rubrum-staging-aurora"
        ALB_NAME="rubrum-staging-alb"
        ;;
    production)
        AWS_ACCOUNT_ID="963884676865"
        CLUSTER_NAME="rubrum-prod"
        DB_CLUSTER="rubrum-prod-aurora"
        ALB_NAME="rubrum-prod-alb"
        ;;
    *)
        echo -e "${RED}❌ ERROR: Invalid environment '$ENVIRONMENT'. Must be 'staging' or 'production'${NC}"
        exit 2
        ;;
esac

echo "🔍 Running pre-flight checks for $ENVIRONMENT environment..."
echo ""

CHECKS_PASSED=0
CHECKS_FAILED=0

# Function to check a resource
check_resource() {
    local resource_name=$1
    local check_command=$2
    
    echo -n "Checking $resource_name... "
    
    if eval "$check_command" &> /dev/null; then
        echo -e "${GREEN}✅ EXISTS${NC}"
        ((CHECKS_PASSED++))
        return 0
    else
        echo -e "${RED}❌ MISSING${NC}"
        ((CHECKS_FAILED++))
        return 1
    fi
}

# 1. Check ECS Cluster
check_resource "ECS Cluster ($CLUSTER_NAME)" \
    "aws ecs describe-clusters --clusters $CLUSTER_NAME --region $AWS_REGION --query 'clusters[?status==\`ACTIVE\`]' --output text | grep -q $CLUSTER_NAME"

# 2. Check RDS Aurora Cluster
check_resource "RDS Aurora Cluster ($DB_CLUSTER)" \
    "aws rds describe-db-clusters --db-cluster-identifier $DB_CLUSTER --region $AWS_REGION --query 'DBClusters[?Status==\`available\`]' --output text | grep -q $DB_CLUSTER"

# 3. Check Application Load Balancer
check_resource "Application Load Balancer ($ALB_NAME)" \
    "aws elbv2 describe-load-balancers --region $AWS_REGION --query 'LoadBalancers[?LoadBalancerName==\`$ALB_NAME\` && State.Code==\`active\`]' --output text | grep -q $ALB_NAME"

# 4. Check ECR Repositories
check_resource "ECR Repository (hiive-pa-api)" \
    "aws ecr describe-repositories --repository-names hiive-pa-api --region $AWS_REGION"

check_resource "ECR Repository (hiive-pa-frontend)" \
    "aws ecr describe-repositories --repository-names hiive-pa-frontend --region $AWS_REGION"

# 5. Check VPC (indirectly through ECS cluster network configuration)
echo -n "Checking VPC configuration... "
VPC_ID=$(aws ecs describe-clusters --clusters $CLUSTER_NAME --region $AWS_REGION --query 'clusters[0].tags[?key==`VpcId`].value' --output text 2>/dev/null || echo "")
if [ -n "$VPC_ID" ]; then
    echo -e "${GREEN}✅ CONFIGURED${NC}"
    ((CHECKS_PASSED++))
else
    echo -e "${YELLOW}⚠️  Cannot verify (cluster might not have VPC tags)${NC}"
fi

# 6. Check ECS Services (if they exist - not required for first deployment)
echo -n "Checking ECS Services... "
SERVICE_COUNT=$(aws ecs list-services --cluster $CLUSTER_NAME --region $AWS_REGION --query 'length(serviceArns)' --output text 2>/dev/null || echo "0")
if [ "$SERVICE_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✅ FOUND ($SERVICE_COUNT services)${NC}"
    ((CHECKS_PASSED++))
else
    echo -e "${YELLOW}⚠️  NO SERVICES (expected for first deployment)${NC}"
fi

# 7. Check Secrets Manager (database credentials)
echo -n "Checking Secrets Manager... "
SECRET_COUNT=$(aws secretsmanager list-secrets --region $AWS_REGION --query "SecretList[?contains(Name, '$ENVIRONMENT')].Name | length(@)" --output text 2>/dev/null || echo "0")
if [ "$SECRET_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✅ FOUND ($SECRET_COUNT secrets)${NC}"
    ((CHECKS_PASSED++))
else
    echo -e "${YELLOW}⚠️  NO SECRETS (might need manual creation)${NC}"
fi

# 8. Check S3 Terraform State Backend
TERRAFORM_STATE_BUCKET="rubrum-$ENVIRONMENT-hiivehealth-com-terraform-state"
check_resource "S3 Terraform State Bucket ($TERRAFORM_STATE_BUCKET)" \
    "aws s3 ls s3://$TERRAFORM_STATE_BUCKET --region $AWS_REGION"

# Summary
echo ""
echo "=========================================="
echo "Pre-flight Check Summary"
echo "=========================================="
echo -e "Checks Passed: ${GREEN}$CHECKS_PASSED${NC}"
echo -e "Checks Failed: ${RED}$CHECKS_FAILED${NC}"
echo ""

if [ $CHECKS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ All critical infrastructure exists. Deployment can proceed.${NC}"
    exit 0
else
    echo -e "${RED}❌ Infrastructure validation failed. Cannot proceed with deployment.${NC}"
    echo ""
    echo "Required actions:"
    echo "1. Run 'cdk deploy' to create missing infrastructure"
    echo "2. If ECR repositories missing, ensure CDK stack includes ECR resources"
    echo "3. Verify AWS credentials and permissions"
    echo ""
    echo "Documentation: docs/ and README.md"
    exit 1
fi
