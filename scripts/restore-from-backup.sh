#!/bin/bash
# Script to restore OpenEMR infrastructure from AWS Backup recovery points
# This script helps restore RDS databases and EFS file systems from backup

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STACK_NAME="${STACK_NAME:-OpenemrEcsStack}"
REGION="${AWS_DEFAULT_REGION:-us-west-2}"
BACKUP_VAULT_NAME="${BACKUP_VAULT_NAME:-}"

# Logging functions (output to stderr so they're not captured by command substitution)
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

# Check prerequisites
check_prerequisites() {
    log "Checking prerequisites..."
    
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI not found. Please install AWS CLI."
        exit 1
    fi
    
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "AWS credentials not configured or invalid."
        exit 1
    fi
    
    log_success "Prerequisites check passed"
}

# Get stack outputs
get_stack_output() {
    local output_key=$1
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='$output_key'].OutputValue" \
        --output text 2>/dev/null || echo ""
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

# List available recovery points
list_recovery_points() {
    local resource_type=$1
    
    log "Listing recovery points for resource type: $resource_type"
    
    local recovery_points
    recovery_points=$(aws backup list-recovery-points-by-backup-vault \
        --backup-vault-name "$BACKUP_VAULT_NAME" \
        --region "$REGION" \
        --query "RecoveryPoints[?ResourceType=='$resource_type'].[RecoveryPointArn, CreationDate, Status]" \
        --output text 2>&1)
    
    if [ -z "$recovery_points" ] || [[ "$recovery_points" == *"error"* ]]; then
        log_warning "No recovery points found for resource type: $resource_type"
        return 1
    fi
    
    echo "$recovery_points"
    return 0
}

# Restore RDS database from backup
restore_rds() {
    local recovery_point_arn=$1
    local db_cluster_identifier=$2
    local subnet_group_name=$3
    local security_group_ids=$4
    
    log "Initiating RDS restore from recovery point..."
    log "  Recovery Point: $recovery_point_arn"
    log "  Target Cluster: $db_cluster_identifier"
    
    # Start restore job
    local restore_job_id
    restore_job_id=$(aws backup start-restore-job \
        --recovery-point-arn "$recovery_point_arn" \
        --iam-role-arn "$(get_restore_role_arn)" \
        --metadata "{\"DBClusterIdentifier\":\"$db_cluster_identifier\",\"DBSubnetGroupName\":\"$subnet_group_name\",\"VpcSecurityGroupIds\":\"$security_group_ids\"}" \
        --region "$REGION" \
        --query "RestoreJobId" \
        --output text)
    
    if [ -z "$restore_job_id" ] || [[ "$restore_job_id" == *"error"* ]]; then
        log_error "Failed to start restore job"
        return 1
    fi
    
    log_success "Restore job started: $restore_job_id"
    log "Monitoring restore progress..."
    
    # Monitor restore job for RDS
    while true; do
        local status
        status=$(aws backup describe-restore-job \
            --restore-job-id "$restore_job_id" \
            --region "$REGION" \
            --query "Status" \
            --output text 2>/dev/null || echo "UNKNOWN")
        
        case "$status" in
            "COMPLETED")
                log_success "Restore job completed successfully"
                return 0
                ;;
            "ABORTED"|"FAILED")
                log_error "Restore job failed with status: $status"
                aws backup describe-restore-job \
                    --restore-job-id "$restore_job_id" \
                    --region "$REGION" \
                    --query "StatusMessage" \
                    --output text
                return 1
                ;;
            "RUNNING"|"PENDING")
                log "Restore job status: $status (checking again in 30 seconds...)"
                sleep 30
                ;;
            *)
                log_warning "Unknown restore job status: $status"
                sleep 30
                ;;
        esac
    done
}

# Restore EFS file system from backup
restore_efs() {
    local recovery_point_arn=$1
    local file_system_id=$2
    
    log "Initiating EFS restore from recovery point..."
    log "  Recovery Point: $recovery_point_arn"
    log "  Target File System: $file_system_id"
    
    # Start restore job
    local restore_job_id
    restore_job_id=$(aws backup start-restore-job \
        --recovery-point-arn "$recovery_point_arn" \
        --iam-role-arn "$(get_restore_role_arn)" \
        --metadata "{\"file-system-id\":\"$file_system_id\",\"newFileSystem\":\"false\",\"Encrypted\":\"true\"}" \
        --region "$REGION" \
        --query "RestoreJobId" \
        --output text)
    
    if [ -z "$restore_job_id" ] || [[ "$restore_job_id" == *"error"* ]]; then
        log_error "Failed to start restore job"
        return 1
    fi
    
    log_success "Restore job started: $restore_job_id"
    log "Monitoring restore progress..."
    
    # Monitor restore job for EFS
    while true; do
        local status
        status=$(aws backup describe-restore-job \
            --restore-job-id "$restore_job_id" \
            --region "$REGION" \
            --query "Status" \
            --output text 2>/dev/null || echo "UNKNOWN")
        
        case "$status" in
            "COMPLETED")
                log_success "Restore job completed successfully"
                return 0
                ;;
            "ABORTED"|"FAILED")
                log_error "Restore job failed with status: $status"
                aws backup describe-restore-job \
                    --restore-job-id "$restore_job_id" \
                    --region "$REGION" \
                    --query "StatusMessage" \
                    --output text
                return 1
                ;;
            "RUNNING"|"PENDING")
                log "Restore job status: $status (checking again in 30 seconds...)"
                sleep 30
                ;;
            *)
                log_warning "Unknown restore job status: $status"
                sleep 30
                ;;
        esac
    done
}

# Get restore IAM role ARN from backup plan
get_restore_role_arn() {
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
    log_warning "Could not find backup plan role, using default service role"
    local account_id
    account_id=$(aws sts get-caller-identity --region "$REGION" --query "Account" --output text)
    echo "arn:aws:iam::${account_id}:role/service-role/AWSBackupDefaultServiceRole"
}

# Interactive recovery point selection
select_recovery_point() {
    local resource_type=$1
    local resource_id=$2
    
    log "Selecting recovery point for $resource_type: $resource_id"
    
    # List available recovery points
    local recovery_points
    if ! recovery_points=$(list_recovery_points "$resource_type"); then
        return 1
    fi
    
    # Filter by resource ID if provided
    if [ -n "$resource_id" ]; then
        recovery_points=$(echo "$recovery_points" | grep "$resource_id" || echo "")
    fi
    
    # Display recovery points
    echo ""
    log "Available recovery points:"
    echo "$recovery_points" | nl -w2 -s'. '
    echo ""
    
    # Prompt for selection
    local count
    count=$(echo "$recovery_points" | wc -l | tr -d ' ')
    if [ "$count" -eq 1 ]; then
        local selected_arn
        selected_arn=$(echo "$recovery_points" | awk '{print $1}')
        log_success "Auto-selected only available recovery point: $selected_arn"
        echo "$selected_arn"
        return 0
    fi
    
    read -r -p "Enter recovery point number (1-$count): " selection
    
    if ! [[ "$selection" =~ ^[0-9]+$ ]] || [ "$selection" -lt 1 ] || [ "$selection" -gt "$count" ]; then
        log_error "Invalid selection"
        return 1
    fi
    
    local selected_arn
    selected_arn=$(echo "$recovery_points" | sed -n "${selection}p" | awk '{print $1}')
    echo "$selected_arn"
}

# Main restore function
main_restore() {
    local resource_type=$1
    local resource_id=$2
    local recovery_point_arn=$3
    
    check_prerequisites
    discover_backup_vault
    
    # Get recovery point if not provided
    if [ -z "$recovery_point_arn" ]; then
        if ! recovery_point_arn=$(select_recovery_point "$resource_type" "$resource_id"); then
            # select_recovery_point already logged the reason (no recovery points found)
            log_error "Cannot proceed without a recovery point"
            log ""
            log "Tips:"
            log "  - Check if backups have been created: bash scripts/list-backups.sh -s $STACK_NAME"
            log "  - AWS Backup typically creates backups on a schedule (check your backup plan)"
            log "  - If backups exist, they may be in a different backup vault"
            log "  - You can also provide a recovery point ARN directly as an argument"
            exit 1
        fi
        if [ -z "$recovery_point_arn" ]; then
            log_error "No recovery point selected"
            exit 1
        fi
    fi
    
    case "$resource_type" in
        "RDS")
            # Get RDS cluster details from stack
            local db_cluster_id
            db_cluster_id=$(get_stack_output "DatabaseEndpoint" | cut -d'.' -f1 || echo "")
            if [ -z "$db_cluster_id" ]; then
                log_error "Could not determine RDS cluster identifier from stack outputs"
                exit 1
            fi
            
            # Get subnet group and security groups (simplified - may need adjustment)
            local subnet_group
            subnet_group=$(aws rds describe-db-clusters \
                --db-cluster-identifier "$db_cluster_id" \
                --region "$REGION" \
                --query "DBClusters[0].DBSubnetGroup" \
                --output text 2>/dev/null || echo "")
            
            local security_groups
            security_groups=$(aws rds describe-db-clusters \
                --db-cluster-identifier "$db_cluster_id" \
                --region "$REGION" \
                --query "DBClusters[0].VpcSecurityGroups[].VpcSecurityGroupId" \
                --output text 2>/dev/null | tr '\t' ',' || echo "")
            
            restore_rds "$recovery_point_arn" "$db_cluster_id" "$subnet_group" "$security_groups"
            ;;
        "EFS")
            # Get EFS file system ID
            local efs_id=$resource_id
            if [ -z "$efs_id" ]; then
                efs_id=$(get_stack_output "EFSSitesFileSystemId")
                if [ -z "$efs_id" ]; then
                    log_error "EFS file system ID required for EFS restore"
                    exit 1
                fi
            fi
            
            restore_efs "$recovery_point_arn" "$efs_id"
            ;;
        *)
            log_error "Unsupported resource type: $resource_type"
            log "Supported types: RDS, EFS"
            exit 1
            ;;
    esac
}

# CLI usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS] RESOURCE_TYPE [RESOURCE_ID] [RECOVERY_POINT_ARN]

Restore OpenEMR infrastructure from AWS Backup recovery points.

Arguments:
  RESOURCE_TYPE          Resource type to restore (RDS or EFS)
  RESOURCE_ID            Optional resource ID (required for EFS if not in stack)
  RECOVERY_POINT_ARN     Optional recovery point ARN (will prompt if not provided)

Options:
  -s, --stack-name NAME  CloudFormation stack name (default: OpenemrEcsStack)
  -v, --vault-name NAME  Backup vault name (auto-discovered if not provided)
  -r, --region REGION    AWS region (default: us-west-2)
  -h, --help             Show this help message

Examples:
  # Restore RDS database (interactive)
  $0 RDS

  # Restore specific EFS file system
  $0 EFS fs-12345678

  # Restore from specific recovery point
  $0 RDS "" arn:aws:backup:us-west-2:123456789012:recovery-point:...

Environment Variables:
  STACK_NAME             CloudFormation stack name
  BACKUP_VAULT_NAME      Backup vault name
  AWS_DEFAULT_REGION     AWS region

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -s|--stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        -v|--vault-name)
            BACKUP_VAULT_NAME="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            export AWS_DEFAULT_REGION="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        RDS|EFS)
            RESOURCE_TYPE="$1"
            shift
            if [ $# -gt 0 ] && [[ ! "$1" =~ ^- ]]; then
                RESOURCE_ID="$1"
                shift
            fi
            if [ $# -gt 0 ] && [[ ! "$1" =~ ^- ]]; then
                RECOVERY_POINT_ARN="$1"
                shift
            fi
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Execute restore
if [ -z "$RESOURCE_TYPE" ]; then
    log_error "Resource type (RDS or EFS) is required"
    usage
    exit 1
fi

main_restore "$RESOURCE_TYPE" "$RESOURCE_ID" "$RECOVERY_POINT_ARN"

