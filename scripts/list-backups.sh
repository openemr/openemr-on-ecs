#!/bin/bash
# Script to list AWS Backup recovery points for OpenEMR infrastructure
# This is a helper script for viewing available backups

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

# Logging functions
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
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

# List recovery points by resource type
list_recovery_points() {
    local resource_type=$1
    
    log "Recovery points for $resource_type:"
    echo ""
    
    aws backup list-recovery-points-by-backup-vault \
        --backup-vault-name "$BACKUP_VAULT_NAME" \
        --region "$REGION" \
        --query "RecoveryPoints[?ResourceType=='$resource_type'].[RecoveryPointArn, CreationDate, Status, BackupSizeInBytes]" \
        --output table 2>&1 | head -20
    
    local count
    count=$(aws backup list-recovery-points-by-backup-vault \
        --backup-vault-name "$BACKUP_VAULT_NAME" \
        --region "$REGION" \
        --query "RecoveryPoints[?ResourceType=='$resource_type] | length(@)" \
        --output text 2>/dev/null || echo "0")
    
    if [ "$count" -gt 0 ]; then
        echo ""
        log "Total $resource_type recovery points: $count"
    else
        log "No $resource_type recovery points found"
    fi
    echo ""
}

# Main function
main() {
    log "Listing backups for stack: $STACK_NAME"
    echo ""
    
    discover_backup_vault
    
    # List RDS recovery points
    list_recovery_points "RDS"
    
    # List EFS recovery points
    list_recovery_points "EFS"
    
    log_success "Backup listing complete"
}

# CLI usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

List AWS Backup recovery points for OpenEMR infrastructure.

Options:
  -s, --stack-name NAME  CloudFormation stack name (default: OpenemrEcsStack)
  -v, --vault-name NAME  Backup vault name (auto-discovered if not provided)
  -r, --region REGION    AWS region (default: us-west-2)
  -h, --help             Show this help message

Examples:
  # List backups for default stack
  $0

  # List backups for specific stack
  $0 -s MyStackName

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
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

main

