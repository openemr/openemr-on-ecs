#!/bin/bash
# Script to manually trigger AWS Backup jobs for OpenEMR infrastructure
# This creates on-demand backups that will appear as recovery points in the vault

# Use set -e but allow individual backup operations to fail without stopping the script
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
STACK_NAME="${STACK_NAME:-OpenemrEcsStack}"
REGION="${AWS_DEFAULT_REGION:-us-west-2}"
BACKUP_VAULT_NAME="${BACKUP_VAULT_NAME:-}"

# Logging functions (all output to stderr to prevent capture by command substitution)
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" >&2
}

log_success() {
    echo -e "${GREEN}✓${NC} $1" >&2
}

log_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1" >&2
}

# Discover backup vault name
discover_backup_vault() {
    if [ -z "$BACKUP_VAULT_NAME" ]; then
        log "Discovering backup vault for stack: $STACK_NAME"
        
        # Try to find vault by stack name pattern
        BACKUP_VAULT_NAME=$(aws backup list-backup-vaults \
            --region "$REGION" \
            --query "BackupVaultList[?contains(BackupVaultName, '${STACK_NAME}')].BackupVaultName" \
            --output text 2>&1 | head -1)
        
        if [ -z "$BACKUP_VAULT_NAME" ] || [[ "$BACKUP_VAULT_NAME" == *"error"* ]]; then
            log_error "Could not find backup vault for stack: $STACK_NAME"
            log "Available backup vaults:"
            aws backup list-backup-vaults --region "$REGION" --query "BackupVaultList[].BackupVaultName" --output table
            exit 1
        fi
        
        log_success "Found backup vault: $BACKUP_VAULT_NAME"
    fi
}

# Get the IAM role ARN for backup operations
get_backup_role_arn() {
    # Try to find the IAM role from the backup plan that uses this vault
    local plan_id role_arn
    
    # List all backup plans and find the one that uses our vault
    for plan_id in $(aws backup list-backup-plans --region "$REGION" --query 'BackupPlansList[].BackupPlanId' --output text 2>/dev/null); do
        local vault_name
        vault_name=$(aws backup get-backup-plan \
            --backup-plan-id "$plan_id" \
            --region "$REGION" \
            --query 'BackupPlan.Rules[0].TargetBackupVaultName' \
            --output text 2>/dev/null)
        
        if [ "$vault_name" = "$BACKUP_VAULT_NAME" ]; then
            # Found the plan that uses our vault, get its IAM role from the backup selection
            role_arn=$(aws backup list-backup-selections \
                --backup-plan-id "$plan_id" \
                --region "$REGION" \
                --query 'BackupSelectionsList[0].IamRoleArn' \
                --output text 2>/dev/null)
            
            if [ -n "$role_arn" ] && [[ ! "$role_arn" == *"error"* ]] && [[ ! "$role_arn" == "None" ]]; then
                echo "$role_arn"
                return 0
            fi
        fi
    done
    
    # Fallback to default service role if plan role not found
    local account_id
    account_id=$(aws sts get-caller-identity --region "$REGION" --query "Account" --output text)
    echo "arn:aws:iam::${account_id}:role/service-role/AWSBackupDefaultServiceRole"
}

# Get RDS cluster ARN from stack
get_rds_cluster_arn() {
    log "Getting RDS cluster ARN from stack outputs..."
    
    # Try DatabaseClusterArn first
    local cluster_arn
    cluster_arn=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='DatabaseClusterArn'].OutputValue" \
        --output text 2>&1)
    
    # If not found, extract cluster identifier from endpoint and query RDS
    if [ -z "$cluster_arn" ] || [[ "$cluster_arn" == *"error"* ]] || [[ "$cluster_arn" == "None" ]]; then
        log "DatabaseClusterArn not in outputs, trying to get from RDS endpoint..."
        
        local endpoint
        endpoint=$(aws cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --region "$REGION" \
            --query "Stacks[0].Outputs[?OutputKey=='DatabaseEndpoint'].OutputValue" \
            --output text 2>&1)
        
        if [ -n "$endpoint" ] && [[ ! "$endpoint" == *"error"* ]]; then
            # Extract cluster identifier from endpoint (format: cluster-name.cluster-xxx.region.rds.amazonaws.com)
            local cluster_id
            cluster_id=$(echo "$endpoint" | cut -d'.' -f1)
            
            if [ -n "$cluster_id" ]; then
                log "Found cluster identifier: $cluster_id"
                cluster_arn=$(aws rds describe-db-clusters \
                    --db-cluster-identifier "$cluster_id" \
                    --region "$REGION" \
                    --query "DBClusters[0].DBClusterArn" \
                    --output text 2>&1)
            fi
        fi
    fi
    
    if [ -z "$cluster_arn" ] || [[ "$cluster_arn" == *"error"* ]] || [[ "$cluster_arn" == "None" ]]; then
        log_error "Could not find RDS cluster ARN"
        return 1
    fi
    
    # Remove any log prefixes that might have been included
    cluster_arn=$(echo "$cluster_arn" | grep -o 'arn:aws:rds:[^[:space:]]*' | head -1)
    
    log "Found RDS cluster ARN: $cluster_arn"
    echo "$cluster_arn"
}

# Get EFS file system IDs from stack
get_efs_file_systems() {
    log "Getting EFS file system IDs from stack outputs..." >&2
    
    local sites_efs_id ssl_efs_id
    
    sites_efs_id=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='EFSSitesFileSystemId'].OutputValue" \
        --output text 2>/dev/null)
    
    # Clean up any log messages that might have been captured
    sites_efs_id=$(echo "$sites_efs_id" | grep -o '^fs-[0-9a-f]\{8,40\}$' | head -1)
    
    ssl_efs_id=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='EFSSSLFileSystemId'].OutputValue" \
        --output text 2>/dev/null)
    
    # Clean up any log messages that might have been captured
    ssl_efs_id=$(echo "$ssl_efs_id" | grep -o '^fs-[0-9a-f]\{8,40\}$' | head -1)
    
    if [ -z "$sites_efs_id" ] || [[ "$sites_efs_id" == *"error"* ]]; then
        log_warning "Could not find Sites EFS file system ID in stack outputs"
        sites_efs_id=""
    fi
    
    if [ -z "$ssl_efs_id" ] || [[ "$ssl_efs_id" == *"error"* ]]; then
        log_warning "Could not find SSL EFS file system ID in stack outputs"
        ssl_efs_id=""
    fi
    
    echo "$sites_efs_id|$ssl_efs_id"
}

# Create backup job for a resource
create_backup_job() {
    local resource_arn=$1
    local resource_type=$2
    
    log "Creating backup job for $resource_type resource..."
    log "  Resource ARN: $resource_arn"
    log "  Backup Vault: $BACKUP_VAULT_NAME"
    
    local role_arn
    role_arn=$(get_backup_role_arn)
    
    local job_id error_output
    error_output=$(aws backup start-backup-job \
        --backup-vault-name "$BACKUP_VAULT_NAME" \
        --resource-arn "$resource_arn" \
        --iam-role-arn "$role_arn" \
        --region "$REGION" \
        --query "BackupJobId" \
        --output text 2>&1)
    local exit_code=$?
    job_id="$error_output"
    
    if [ $exit_code -ne 0 ] || [ -z "$job_id" ] || [[ "$job_id" == *"error"* ]] || [[ "$job_id" == *"An error occurred"* ]] || [[ "$job_id" == *"Exception"* ]]; then
        log_error "Failed to create backup job for $resource_type"
        if [[ "$job_id" == *"error"* ]] || [[ "$job_id" == *"Exception"* ]] || [[ "$job_id" == *"An error occurred"* ]]; then
            # Show the error message
            echo "$job_id" | grep -v "^$" | head -5
        fi
        return 1
    fi
    
    log_success "Backup job started: $job_id"
    return 0
}

# Create backup for RDS
backup_rds() {
    log "Starting RDS backup..."
    
    local cluster_arn
    if ! cluster_arn=$(get_rds_cluster_arn 2>&1) || [ -z "$cluster_arn" ] || [[ "$cluster_arn" == *"error"* ]]; then
        log_error "Could not get RDS cluster ARN"
        return 1
    fi
    
    # Clean up any log messages that might have been captured
    cluster_arn=$(echo "$cluster_arn" | grep -o 'arn:aws:rds:[^[:space:]]*' | head -1)
    
    if [ -z "$cluster_arn" ]; then
        log_error "Could not extract valid RDS cluster ARN"
        return 1
    fi
    
    create_backup_job "$cluster_arn" "RDS"
    return $?
}

# Create backup for EFS
backup_efs() {
    local file_system_id=$1
    local description=$2
    
    if [ -z "$file_system_id" ]; then
        log_warning "No file system ID provided for $description"
        return 1
    fi
    
    log "Starting EFS backup for $description..."
    
    # Get file system ARN
    local fs_arn
    fs_arn=$(aws efs describe-file-systems \
        --file-system-id "$file_system_id" \
        --region "$REGION" \
        --query "FileSystems[0].FileSystemArn" \
        --output text 2>&1)
    
    if [ -z "$fs_arn" ] || [[ "$fs_arn" == *"error"* ]] || [[ "$fs_arn" == "None" ]]; then
        log_error "Could not get EFS file system ARN for $file_system_id"
        if [[ "$fs_arn" == *"error"* ]]; then
            echo "$fs_arn" | head -3
        fi
        return 1
    fi
    
    create_backup_job "$fs_arn" "EFS ($description)"
    local result=$?
    if [ $result -ne 0 ]; then
        log_error "Failed to create EFS backup for $description"
    fi
    return $result
}

# Main function
main() {
    log "Creating backup jobs for stack: $STACK_NAME"
    echo ""
    
    discover_backup_vault
    
    log "Creating backups for all resources..."
    echo ""
    
    local backup_count=0
    local error_count=0
    
    # Backup RDS
    log "Backing up RDS database..."
    if backup_rds; then
        backup_count=$((backup_count + 1))
        echo ""
    else
        error_count=$((error_count + 1))
        echo ""
    fi
    
    # Backup EFS file systems
    local efs_info
    efs_info=$(get_efs_file_systems)
    IFS='|' read -r sites_efs ssl_efs <<< "$efs_info"
    
            if [ -n "$sites_efs" ] && [[ "$sites_efs" =~ ^fs-[0-9a-f]{8,40}$ ]]; then
                if backup_efs "$sites_efs" "Sites"; then
                    backup_count=$((backup_count + 1))
                else
                    error_count=$((error_count + 1))
                fi
                echo ""
            elif [ -z "$sites_efs" ]; then
                log_warning "Skipping Sites EFS backup (file system ID not found)"
                error_count=$((error_count + 1))
                echo ""
            else
                log_error "Invalid Sites EFS file system ID format: $sites_efs"
                error_count=$((error_count + 1))
                echo ""
            fi
            
            if [ -n "$ssl_efs" ] && [[ "$ssl_efs" =~ ^fs-[0-9a-f]{8,40}$ ]]; then
                if backup_efs "$ssl_efs" "SSL"; then
                    backup_count=$((backup_count + 1))
                else
                    error_count=$((error_count + 1))
                fi
                echo ""
            elif [ -z "$ssl_efs" ]; then
                log_warning "Skipping SSL EFS backup (file system ID not found)"
                error_count=$((error_count + 1))
                echo ""
            else
                log_error "Invalid SSL EFS file system ID format: $ssl_efs"
                error_count=$((error_count + 1))
                echo ""
            fi
    
    # Summary
    if [ $backup_count -gt 0 ]; then
        log_success "Successfully created $backup_count backup job(s)"
    fi
    
    if [ $error_count -gt 0 ]; then
        log_warning "$error_count backup job(s) failed to start"
    fi
    
    if [ $backup_count -eq 0 ] && [ $error_count -gt 0 ]; then
        log_error "All backup jobs failed"
        exit 1
    fi
    
    log ""
    log "Note: Backup jobs run asynchronously. Recovery points will appear in the vault once jobs complete."
    log "Check backup status with: ./scripts/list-backups.sh -s $STACK_NAME"
}

# CLI usage
usage() {
    cat << EOF
Usage: $0

Manually trigger AWS Backup jobs for all OpenEMR infrastructure resources (RDS and EFS).

This script creates on-demand backups for:
  - RDS database cluster
  - EFS file systems (Sites and SSL)

Options (via environment variables):
  STACK_NAME             CloudFormation stack name (default: OpenemrEcsStack)
  BACKUP_VAULT_NAME      Backup vault name (auto-discovered if not provided)
  AWS_DEFAULT_REGION     AWS region (default: us-west-2)

Examples:
  # Create backups for all resources
  $0

  # With custom stack name and region
  STACK_NAME=MyStack REGION=us-east-1 $0

Environment Variables:
  STACK_NAME             CloudFormation stack name
  BACKUP_VAULT_NAME      Backup vault name
  AWS_DEFAULT_REGION     AWS region

EOF
}

# Parse arguments
if [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]] || [[ "${1:-}" != "" ]]; then
    usage
    exit 0
fi

# Run main function (always backs up all resources)
main

