#!/bin/bash
# Stress test script for CDK deployment/destruction with various configurations

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test configurations
declare -a TEST_CONFIGS=(
    "minimal:route53_domain=null:certificate_arn=null:enable_global_accelerator=false:enable_bedrock_integration=false:enable_data_api=false:create_serverless_analytics_environment=false"
    "standard:route53_domain=null:certificate_arn=null:enable_global_accelerator=false:enable_bedrock_integration=true:enable_data_api=true:create_serverless_analytics_environment=false"
    "full-featured:route53_domain=null:certificate_arn=null:enable_global_accelerator=true:enable_bedrock_integration=true:enable_data_api=true:create_serverless_analytics_environment=true"
)

PASSED=0
FAILED=0
SKIPPED=0

log() {
    echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $1"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
    ((PASSED++))
}

error() {
    echo -e "${RED}✗${NC} $1"
    ((FAILED++))
}

warning() {
    echo -e "${YELLOW}⚠${NC} $1"
    ((SKIPPED++))
}

test_config() {
    local config_name=$1
    local config_vars=$2
    
    log "Testing configuration: $config_name"
    log "Config vars: $config_vars"
    
    # Parse and apply configuration
    IFS=':' read -ra VARS <<< "$config_vars"
    local cdk_args=""
    
    for var in "${VARS[@]}"; do
        if [[ -n "$var" ]]; then
            IFS='=' read -ra PARTS <<< "$var"
            if [ ${#PARTS[@]} -eq 2 ]; then
                local key="${PARTS[0]}"
                local value="${PARTS[1]}"
                
                if [[ "$value" == "null" ]]; then
                    cdk_args="$cdk_args -c $key=null"
                elif [[ "$value" == "true" ]] || [[ "$value" == "false" ]]; then
                    cdk_args="$cdk_args -c $key=$value"
                else
                    cdk_args="$cdk_args -c $key=$value"
                fi
            fi
        fi
    done
    
    log "CDK args: $cdk_args"
    
    # Test synthesis first
    log "Testing synthesis..."
    if cdk synth "$cdk_args" > /tmp/cdk-synth-"${config_name}".log 2>&1; then
        success "Synthesis successful for $config_name"
    else
        error "Synthesis failed for $config_name"
        cat /tmp/cdk-synth-"${config_name}".log
        return 1
    fi
    
    return 0
}

# Check if we should actually deploy (since deployments take ~40 minutes)
DEPLOY_ACTUAL=${DEPLOY_ACTUAL:-"false"}

echo "========================================="
echo "CDK Stack Stress Test"
echo "========================================="
echo ""
echo "This script tests CDK stack synthesis and optionally deployment/destruction"
echo "with various configurations."
echo ""
echo "To actually deploy (takes ~40 minutes per config), set:"
echo "  export DEPLOY_ACTUAL=true"
echo ""
echo "Current mode: ${DEPLOY_ACTUAL}"
echo ""

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate 2>/dev/null || true
fi

# Run tests
for config in "${TEST_CONFIGS[@]}"; do
    IFS=':' read -ra PARTS <<< "$config"
    config_name="${PARTS[0]}"
    config_vars="${config#*:}"
    
    echo ""
    echo "----------------------------------------"
    echo "Test: $config_name"
    echo "----------------------------------------"
    
    if test_config "$config_name" "$config_vars"; then
        if [[ "$DEPLOY_ACTUAL" == "true" ]]; then
            log "Deploying $config_name (this will take ~40 minutes)..."
            
            # Deploy
            if cdk deploy "$cdk_args" --require-approval never > /tmp/cdk-deploy-"${config_name}".log 2>&1; then
                success "Deployment successful for $config_name"
                
                # Wait a bit for stack to stabilize
                sleep 30
                
                # Destroy
                log "Destroying $config_name..."
                if cdk destroy "$cdk_args" --force > /tmp/cdk-destroy-"${config_name}".log 2>&1; then
                    success "Destruction successful for $config_name"
                else
                    error "Destruction failed for $config_name"
                    cat /tmp/cdk-destroy-"${config_name}".log
                fi
            else
                error "Deployment failed for $config_name"
                cat /tmp/cdk-deploy-"${config_name}".log
            fi
        else
            log "Skipping actual deployment (synthesis only mode)"
        fi
    fi
    
    echo ""
done

# Summary
echo "========================================="
echo "Test Summary"
echo "========================================="
echo -e "${GREEN}Passed:${NC} $PASSED"
echo -e "${RED}Failed:${NC} $FAILED"
echo -e "${YELLOW}Skipped:${NC} $SKIPPED"
echo ""

if [ $FAILED -eq 0 ]; then
    success "All tests passed!"
    exit 0
else
    error "Some tests failed"
    exit 1
fi

