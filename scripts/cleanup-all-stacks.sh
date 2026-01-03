#!/bin/bash
# Cleanup script to delete all OpenEMR CDK stacks across regions
# This script finds and deletes all stacks with "OpenemrEcs" in the name

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "OpenEMR Stack Cleanup Script"
echo "========================================="
echo ""

# Regions to check
REGIONS=("us-east-1" "us-east-2" "us-west-1" "us-west-2" "eu-west-1" "eu-west-2" "eu-central-1")

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo -e "${RED}ERROR: AWS CLI is not installed${NC}"
    exit 1
fi

# Check if AWS credentials are configured
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}ERROR: AWS credentials not configured${NC}"
    echo "Please configure AWS credentials using:"
    echo "  aws configure"
    echo "  or"
    echo "  export AWS_ACCESS_KEY_ID=..."
    echo "  export AWS_SECRET_ACCESS_KEY=..."
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "AWS Account: $ACCOUNT_ID"
echo ""

# Confirm deletion
echo -e "${YELLOW}WARNING: This will delete ALL OpenEMR stacks in the following regions:${NC}"
for region in "${REGIONS[@]}"; do
    echo "  - $region"
done
echo ""
read -r -p "Are you sure you want to continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "========================================="
echo "Starting stack deletion..."
echo "========================================="
echo ""

TOTAL_DELETED=0
TOTAL_FAILED=0

for region in "${REGIONS[@]}"; do
    echo "Checking region: $region"
    
    # Get all stacks with OpenemrEcs in the name
    STACKS=$(aws cloudformation list-stacks \
        --region "$region" \
        --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE CREATE_FAILED UPDATE_FAILED ROLLBACK_COMPLETE \
        --query "StackSummaries[?contains(StackName, 'OpenemrEcs') || contains(StackName, 'TestStack')].StackName" \
        --output text 2>/dev/null || echo "")
    
    if [ -z "$STACKS" ] || [ "$STACKS" == "None" ]; then
        echo "  No stacks found"
        continue
    fi
    
    for stack in $STACKS; do
        echo ""
        echo -e "${YELLOW}Deleting stack: $stack in $region${NC}"
        
        # Check if stack has termination protection
        TERMINATION_PROTECTION=$(aws cloudformation describe-stacks \
            --region "$region" \
            --stack-name "$stack" \
            --query "Stacks[0].EnableTerminationProtection" \
            --output text 2>/dev/null || echo "False")
        
        if [ "$TERMINATION_PROTECTION" == "True" ]; then
            echo "  Disabling termination protection..."
            aws cloudformation update-termination-protection \
                --region "$region" \
                --stack-name "$stack" \
                --no-enable-termination-protection 2>&1 || true
        fi
        
        # Delete the stack
        if aws cloudformation delete-stack \
            --region "$region" \
            --stack-name "$stack" 2>&1; then
            echo -e "  ${GREEN}✅ Delete initiated for: $stack${NC}"
            TOTAL_DELETED=$((TOTAL_DELETED + 1))
        else
            echo -e "  ${RED}❌ Failed to initiate delete for: $stack${NC}"
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
        fi
    done
done

echo ""
echo "========================================="
echo "Deletion Summary"
echo "========================================="
echo -e "${GREEN}Stacks deletion initiated: $TOTAL_DELETED${NC}"
if [ $TOTAL_FAILED -gt 0 ]; then
    echo -e "${RED}Failed: $TOTAL_FAILED${NC}"
fi

if [ $TOTAL_DELETED -gt 0 ]; then
    echo ""
    echo "Waiting for stacks to be deleted (this may take 10-20 minutes)..."
    echo "You can monitor progress with:"
    echo "  aws cloudformation list-stacks --region <region> --stack-status-filter DELETE_IN_PROGRESS"
    echo ""
    echo "Or check the AWS Console:"
    echo "  https://console.aws.amazon.com/cloudformation/"
fi

echo ""
echo "Done!"

