#!/bin/bash
# Pre-deployment validation script
# Validates AWS account limits, prerequisites, and configuration before CDK deployment

set -e

echo "========================================="
echo "CDK Deployment Pre-Flight Validation"
echo "========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERROR_COUNT=0
WARNING_COUNT=0

# Function to print error
error() {
    echo -e "${RED}✗ ERROR:${NC} $1"
    ((ERROR_COUNT++))
}

# Function to print warning
warning() {
    echo -e "${YELLOW}⚠ WARNING:${NC} $1"
    ((WARNING_COUNT++))
}

# Function to print success
success() {
    echo -e "${GREEN}✓${NC} $1"
}

# Check AWS CLI is installed
echo "1. Checking AWS CLI installation..."
if ! command -v aws &> /dev/null; then
    error "AWS CLI is not installed. Install it from https://aws.amazon.com/cli/"
    exit 1
fi
success "AWS CLI is installed"

# Check AWS credentials are configured
echo ""
echo "2. Checking AWS credentials..."
if ! aws sts get-caller-identity &> /dev/null; then
    error "AWS credentials are not configured or invalid. Run 'aws configure'"
    exit 1
fi
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region || echo "us-east-1")
success "AWS credentials are valid (Account: $ACCOUNT_ID, Region: $REGION)"

# Check CDK is installed
echo ""
echo "3. Checking AWS CDK installation..."
if ! command -v cdk &> /dev/null; then
    error "AWS CDK CLI is not installed. Install it with: npm install -g aws-cdk"
    exit 1
fi
CDK_VERSION=$(cdk --version 2>&1 | head -n 1)
success "CDK is installed: $CDK_VERSION"

# Check Python dependencies
echo ""
echo "4. Checking Python dependencies..."
if ! python3 -c "import aws_cdk" 2>/dev/null; then
    error "AWS CDK Python library is not installed. Run: pip install -r requirements.txt"
    exit 1
fi
success "Python dependencies are installed"

# Check CDK bootstrap status
echo ""
echo "5. Checking CDK bootstrap status..."
if aws cloudformation describe-stacks --stack-name "CDKToolkit" --region "$REGION" &> /dev/null; then
    success "CDK is bootstrapped in $REGION"
else
    error "CDK is not bootstrapped in $REGION. Run: cdk bootstrap aws://$ACCOUNT_ID/$REGION"
    exit 1
fi

# Check if stack already exists (informational)
echo ""
echo "6. Checking for existing stack..."
STACK_NAME="OpenemrEcsStack"
if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" &> /dev/null; then
    STACK_STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" --query 'Stacks[0].StackStatus' --output text)
    if [ "$STACK_STATUS" == "ROLLBACK_COMPLETE" ] || [ "$STACK_STATUS" == "ROLLBACK_FAILED" ]; then
        error "Stack exists in ROLLBACK state. You may need to delete it first: cdk destroy"
    else
        success "Stack exists with status: $STACK_STATUS (this will be an update, not a new deployment)"
    fi
else
    success "Stack does not exist (this will be a new deployment)"
fi

# Find project root (directory containing cdk.json)
echo ""
echo "7. Checking CDK configuration..."
PROJECT_ROOT=""
SEARCH_DIR="$(pwd)"

# Search for cdk.json in current directory and parent directories
while [ "$SEARCH_DIR" != "/" ]; do
    if [ -f "$SEARCH_DIR/cdk.json" ]; then
        PROJECT_ROOT="$SEARCH_DIR"
        break
    fi
    SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done

if [ -z "$PROJECT_ROOT" ]; then
    error "cdk.json not found. Please run this script from the project directory or a subdirectory."
    exit 1
fi

# Change to project root if we're not already there
if [ "$PROJECT_ROOT" != "$(pwd)" ]; then
    log "Found cdk.json in: $PROJECT_ROOT"
    cd "$PROJECT_ROOT"
fi

success "cdk.json found"

# Try to synthesize (catches Python and CDK errors early)
echo ""
echo "8. Validating CDK stack synthesis..."
if cdk synth --quiet &> /tmp/cdk-synth-output.log 2>&1; then
    success "CDK synthesis successful - stack definition is valid"
else
    error "CDK synthesis failed. Check the error messages below:"
    cat /tmp/cdk-synth-output.log
    rm -f /tmp/cdk-synth-output.log
    exit 1
fi
rm -f /tmp/cdk-synth-output.log

# Check for required context values (basic validation)
echo ""
echo "9. Validating context configuration..."
if [ -f "cdk.json" ]; then
    # Check if route53_domain is set
    ROUTE53_DOMAIN=$(python3 -c "import json; print(json.load(open('cdk.json')).get('context', {}).get('route53_domain', '') or '')" 2>/dev/null || echo "")
    if [ -n "$ROUTE53_DOMAIN" ]; then
        # Check if hosted zone exists
        if aws route53 list-hosted-zones --query "HostedZones[?Name=='${ROUTE53_DOMAIN%.}.'].Id" --output text 2>/dev/null | grep -q .; then
            success "Route53 hosted zone exists for domain: $ROUTE53_DOMAIN"
        else
            warning "Route53 domain is configured ($ROUTE53_DOMAIN) but hosted zone may not exist. Ensure it exists before deployment."
        fi
    else
        success "No Route53 domain configured (using ALB DNS or Global Accelerator)"
    fi
fi

# Summary
echo ""
echo "========================================="
echo "Validation Summary"
echo "========================================="
if [ $ERROR_COUNT -eq 0 ] && [ $WARNING_COUNT -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed!${NC}"
    echo ""
    echo "You're ready to deploy. Run:"
    echo "  cdk deploy"
    exit 0
elif [ $ERROR_COUNT -eq 0 ]; then
    echo -e "${YELLOW}⚠ Validation passed with $WARNING_COUNT warning(s)${NC}"
    echo ""
    echo "You can proceed with deployment, but review the warnings above."
    echo "Run: cdk deploy"
    exit 0
else
    echo -e "${RED}✗ Validation failed with $ERROR_COUNT error(s) and $WARNING_COUNT warning(s)${NC}"
    echo ""
    echo "Please fix the errors above before deploying."
    exit 1
fi

