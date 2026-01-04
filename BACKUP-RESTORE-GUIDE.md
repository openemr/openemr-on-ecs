# Backup and Restore Guide

This guide explains how to use AWS Backup to create and restore backups of your OpenEMR infrastructure.

## Table of Contents
- [Overview](#overview)
- [Backup Strategy](#backup-strategy)
- [Understanding Recovery Points](#understanding-recovery-points)
- [Restoring from Backup](#restoring-from-backup)
- [Restore Scenarios](#restore-scenarios)
- [Cross-Region Backup](#cross-region-backup)
- [Cross-Account Backup](#cross-account-backup)
- [Troubleshooting](#troubleshooting)

## Overview

The OpenEMR CDK stack automatically configures AWS Backup with a comprehensive backup plan that includes:

- **RDS Aurora MySQL**: Database cluster backups
- **EFS File Systems**: Both sites and SSL certificate file systems
- **Retention**: 7-year retention with daily, weekly, and monthly backups

### Automatic Backup Plan

The stack creates:
- **Daily backups**: Retained for 7 days
- **Weekly backups**: Retained for 4 weeks
- **Monthly backups**: Retained for 84 months (7 years)

All backups are stored in a backup vault named: `{StackName}-vault-{suffix}`

## Backup Strategy

### What Gets Backed Up

1. **RDS Aurora MySQL Cluster**
   - Complete database cluster snapshots
   - Includes all data, schema, and configuration
   - Cross-AZ backups for high availability

2. **EFS Sites File System**
   - All OpenEMR site data
   - Patient documents
   - Configuration files
   - Custom code and templates

3. **EFS SSL File System**
   - SSL/TLS certificates
   - Certificate configurations

### Backup Frequency

- **Daily**: Automated backups run daily at the configured time
- **Weekly**: Weekly backups are created from daily backups
- **Monthly**: Monthly backups are created from weekly backups

### Backup Vault Location

Backups are stored in the same region as your stack. To view your backup vault:

```bash
aws backup list-backup-vaults --region us-west-2
```

## Understanding Recovery Points

Recovery points are immutable snapshots of your resources at specific points in time.

### Viewing Recovery Points

List all recovery points in your backup vault:

```bash
# Get your backup vault name from stack outputs
BACKUP_VAULT=$(aws cloudformation describe-stacks \
    --stack-name OpenemrEcsStack \
    --query "Stacks[0].Outputs[?OutputKey=='BackupVaultName'].OutputValue" \
    --output text)

# List RDS recovery points
aws backup list-recovery-points-by-backup-vault \
    --backup-vault-name "$BACKUP_VAULT" \
    --resource-type RDS \
    --region us-west-2

# List EFS recovery points
aws backup list-recovery-points-by-backup-vault \
    --backup-vault-name "$BACKUP_VAULT" \
    --resource-type EFS \
    --region us-west-2
```

### Recovery Point Information

Each recovery point includes:
- **Recovery Point ARN**: Unique identifier for the recovery point
- **Creation Date**: When the backup was created
- **Status**: Current status (COMPLETED, DELETING, EXPIRED, etc.)
- **Resource Type**: RDS or EFS
- **Resource ID**: The original resource identifier

## Restoring from Backup

### Using the Restore Script

The `scripts/restore-from-backup.sh` script provides an easy way to restore resources.

#### Prerequisites

1. AWS CLI configured with appropriate permissions
2. Stack name (default: `OpenemrEcsStack`)
3. AWS Backup service role (created automatically by AWS)

#### Basic Usage

**Restore RDS Database:**
```bash
# Interactive mode (will list available recovery points)
./scripts/restore-from-backup.sh RDS

# Restore from specific recovery point
./scripts/restore-from-backup.sh RDS "" \
    "arn:aws:backup:us-west-2:123456789012:recovery-point:..."
```

**Restore EFS File System:**
```bash
# Restore sites file system (will use stack output)
./scripts/restore-from-backup.sh EFS

# Restore specific EFS file system
./scripts/restore-from-backup.sh EFS fs-12345678
```

#### Script Options

```bash
./scripts/restore-from-backup.sh [OPTIONS] RESOURCE_TYPE [RESOURCE_ID] [RECOVERY_POINT_ARN]

Options:
  -s, --stack-name NAME   CloudFormation stack name
  -v, --vault-name NAME   Backup vault name
  -r, --region REGION     AWS region
  -h, --help              Show help
```

### Manual Restore Process

#### Restore RDS Database

1. **Identify the recovery point:**
   ```bash
   aws backup list-recovery-points-by-backup-vault \
       --backup-vault-name "OpenemrEcsStack-vault-xxxxxx" \
       --resource-type RDS \
       --region us-west-2
   ```

2. **Get restore metadata:**
   ```bash
   RECOVERY_POINT_ARN="arn:aws:backup:..."
   
   aws backup get-recovery-point-restore-metadata \
       --backup-vault-name "OpenemrEcsStack-vault-xxxxxx" \
       --recovery-point-arn "$RECOVERY_POINT_ARN" \
       --region us-west-2
   ```

3. **Start restore job:**
   ```bash
   aws backup start-restore-job \
       --recovery-point-arn "$RECOVERY_POINT_ARN" \
       --iam-role-arn "arn:aws:iam::ACCOUNT:role/service-role/AWSBackupDefaultServiceRole" \
       --metadata '{
           "DBClusterIdentifier": "restored-cluster-name",
           "DBSubnetGroupName": "subnet-group-name",
           "VpcSecurityGroupIds": "sg-xxxxx"
       }' \
       --region us-west-2
   ```

4. **Monitor restore progress:**
   ```bash
   RESTORE_JOB_ID="..."
   
   aws backup describe-restore-job \
       --restore-job-id "$RESTORE_JOB_ID" \
       --region us-west-2
   ```

#### Restore EFS File System

EFS restores can target either a new file system or an existing file system. When restoring to an existing file system, **files are restored to a directory named `aws-backup-restore_<timestamp>`** rather than overwriting existing content.

1. **Identify the recovery point:**
   ```bash
   aws backup list-recovery-points-by-backup-vault \
       --backup-vault-name "OpenemrEcsStack-vault-xxxxxx" \
       --resource-type EFS \
       --region us-west-2
   ```

2. **Start restore job (to existing file system):**
   ```bash
   RECOVERY_POINT_ARN="arn:aws:backup:..."
   FILE_SYSTEM_ID="fs-xxxxx"  # Target file system ID
   
   aws backup start-restore-job \
       --recovery-point-arn "$RECOVERY_POINT_ARN" \
       --iam-role-arn "arn:aws:iam::ACCOUNT:role/service-role/AWSBackupDefaultServiceRole" \
       --metadata "{
           \"file-system-id\": \"$FILE_SYSTEM_ID\",
           \"newFileSystem\": \"false\",
           \"Encrypted\": \"true\"
       }" \
       --region us-west-2
   ```

3. **Start restore job (to new file system):**
   ```bash
   aws backup start-restore-job \
       --recovery-point-arn "$RECOVERY_POINT_ARN" \
       --iam-role-arn "arn:aws:iam::ACCOUNT:role/service-role/AWSBackupDefaultServiceRole" \
       --metadata "{
           \"newFileSystem\": \"true\",
           \"Encrypted\": \"true\",
           \"PerformanceMode\": \"generalPurpose\",
           \"CreationToken\": \"restored-efs-$(date +%s)\"
       }" \
       --region us-west-2
   ```

4. **Monitor restore progress** (same as RDS)

5. **After restore to existing file system**: Move restored files from `aws-backup-restore_<timestamp>/` to their original locations as needed.

## Restore Scenarios

### Scenario 1: Restore RDS After Data Corruption

**Situation**: Database corruption detected, need to restore from yesterday's backup.

**Steps**:
1. Stop application traffic (scale down ECS service to 0)
2. Identify recovery point from before corruption:
   ```bash
   ./scripts/restore-from-backup.sh RDS
   ```
3. Select recovery point from before corruption occurred
4. Wait for restore to complete (time varies significantly based on database size—expect 30-60 minutes for small databases, several hours for databases over 100GB)
5. Verify restored database
6. Update application to point to restored database
7. Restart application traffic

**Important**: RDS restore creates a NEW cluster. Update connection strings accordingly.

### Scenario 2: Restore EFS After Accidental Deletion

**Situation**: Critical files accidentally deleted from EFS.

**Steps**:
1. Identify recovery point from before deletion:
   ```bash
   ./scripts/restore-from-backup.sh EFS fs-xxxxx
   ```
2. Select recovery point from before deletion
3. Wait for restore to complete
4. Locate restored files in the `aws-backup-restore_<timestamp>/` directory
5. Copy or move restored files to their original locations
6. Remove the restore directory after verification
7. Resume normal operations

**Note**: When restoring to an existing file system, AWS Backup creates a restore directory rather than overwriting existing files. This allows you to selectively restore specific files.

### Scenario 3: Complete Disaster Recovery

**Situation**: Entire infrastructure needs to be restored.

**Steps**:
1. **Restore RDS first:**
   ```bash
   ./scripts/restore-from-backup.sh RDS
   ```
   - Note the new cluster endpoint
   - Update stack configuration or connection strings

2. **Restore EFS file systems:**
   ```bash
   # Restore sites file system
   SITES_EFS=$(aws cloudformation describe-stacks \
       --stack-name OpenemrEcsStack \
       --query "Stacks[0].Outputs[?OutputKey=='EFSSitesFileSystemId'].OutputValue" \
       --output text)
   ./scripts/restore-from-backup.sh EFS "$SITES_EFS"
   
   # Restore SSL file system
   SSL_EFS=$(aws cloudformation describe-stacks \
       --stack-name OpenemrEcsStack \
       --query "Stacks[0].Outputs[?OutputKey=='EFSSSLFileSystemId'].OutputValue" \
       --output text)
   ./scripts/restore-from-backup.sh EFS "$SSL_EFS"
   ```

3. **Move restored files to correct locations** (if restoring to existing file systems):
   ```bash
   # Example: Move restored files from restore directory
   # Adjust paths based on your mount points
   mv /mnt/efs/sites/aws-backup-restore_*/* /mnt/efs/sites/
   ```

4. **Update application configuration:**
   - Update database endpoint in ECS task definition
   - Verify EFS mount points
   - Restart ECS service

5. **Verify application:**
   - Check application health
   - Verify data integrity
   - Test critical workflows

### Scenario 4: Point-in-Time Recovery Testing

**Situation**: Regular disaster recovery testing.

**Steps**:
1. Schedule maintenance window
2. Create a test environment (or use existing)
3. Restore backups to test environment:
   ```bash
   # Use different stack name for test environment
   ./scripts/restore-from-backup.sh \
       -s OpenemrEcsStackTest \
       RDS
   ```
4. Verify restored data
5. Test application functionality
6. Document test results
7. Clean up test environment

## Troubleshooting

### Common Issues

#### 1. Backup Vault Not Found

**Error**: "Could not find backup vault"

**Solution**:
- Verify stack name is correct
- Check if backup plan was created successfully
- List all backup vaults:
  ```bash
  aws backup list-backup-vaults --region us-west-2
  ```

#### 2. No Recovery Points Available

**Error**: "No recovery points found"

**Solution**:
- Verify backup jobs have completed successfully
- Check backup plan status:
  ```bash
  aws backup list-backup-jobs \
      --by-state COMPLETED \
      --region us-west-2
  ```
- Ensure backups have been running for at least 24 hours

#### 3. Restore Job Fails

**Error**: Restore job status is "FAILED"

**Solution**:
- Check restore job details:
  ```bash
  aws backup describe-restore-job \
      --restore-job-id "RESTORE_JOB_ID" \
      --region us-west-2
  ```
- Verify IAM permissions for AWS Backup service role
- Check that target resources exist and are accessible
- Ensure subnet groups and security groups are correct (for RDS)

#### 4. Restore Takes Too Long

**Issue**: Restore job is running but taking hours

**Solution**:
- This is normal for large databases (100GB+ can take 2-4 hours or more)
- Monitor progress:
  ```bash
  watch -n 30 'aws backup describe-restore-job \
      --restore-job-id "RESTORE_JOB_ID" \
      --region us-west-2 \
      --query "[Status, StatusMessage]" \
      --output text'
  ```
- Check CloudWatch metrics for AWS Backup

#### 5. Cannot Find Restored EFS Files

**Issue**: After EFS restore, files appear to be missing

**Solution**:
- When restoring to an existing file system, files are placed in `aws-backup-restore_<timestamp>/`
- List the restore directory:
  ```bash
  ls -la /mnt/efs/aws-backup-restore_*/
  ```
- Move files to their intended locations after verification

### Restore Job Status Values

- **PENDING**: Job has been created, waiting to start
- **RUNNING**: Restore is in progress
- **COMPLETED**: Restore completed successfully
- **ABORTED**: Job was manually cancelled
- **FAILED**: Job failed (check StatusMessage for details)

### Best Practices

1. **Regular Testing**: Test restore procedures quarterly
2. **Documentation**: Document recovery procedures specific to your environment
3. **Monitoring**: Set up CloudWatch alarms for backup job failures
4. **Retention**: Ensure backup retention meets compliance requirements
5. **Cross-Region Backups**: Implement cross-region backups for disaster recovery and compliance
6. **Cross-Account Backups**: Use cross-account backups for security isolation and protection against account compromise
7. **Automation**: Automate restore testing in non-production environments
8. **Encryption**: Properly manage KMS keys for cross-region and cross-account scenarios
9. **Access Control**: Implement least-privilege policies for destination vaults
10. **Verification**: Regularly verify backup copies are being created successfully

### Recovery Time Objectives (RTO)

Actual recovery times vary based on data size:

- **RDS Restore**: 30-60 minutes for small databases (<50GB), 2-4+ hours for large databases (100GB+)
- **EFS Restore**: 15-30 minutes for typical file systems, longer for large datasets
- **Full Stack Restore**: 1-2 hours minimum (including verification), longer for large datasets

### Recovery Point Objectives (RPO)

- **Standard RPO**: Up to 24 hours (time between daily backups)
- **With Continuous Backup**: Minutes (for supported services)

#### Enabling Continuous Backup for RDS

For critical systems requiring lower RPO, AWS Backup supports continuous backup for RDS, enabling point-in-time recovery (PITR) with RPO of minutes rather than hours:

1. Open the [AWS Backup console](https://console.aws.amazon.com/backup)
2. Navigate to **Backup plans**
3. Edit or create a backup plan
4. In the backup rule, enable **Continuous backup for supported resources**
5. Set the PITR window (1-35 days)

**Note**: Continuous backup is supported for RDS but not for EFS. EFS backups are always snapshot-based.

### Enhanced Backup Strategy with Cross-Region/Cross-Account

For comprehensive backup protection:

1. **Local Backups**: Daily/weekly/monthly in same region (current setup)
2. **Cross-Region Backups**: Copy critical backups to different region for disaster recovery
3. **Cross-Account Backups**: Copy backups to separate account for security isolation

**Recommended Setup**:
- Primary backups in production region (current)
- Cross-region copies in secondary region (disaster recovery)
- Cross-account copies in dedicated backup account (security isolation)

## Cross-Region Backup

Cross-region backup copies your backups to different AWS Regions, providing:
- **Disaster Recovery**: Protection against regional outages
- **Compliance**: Meet requirements for geographic data separation
- **Business Continuity**: Maintain backups at a minimum distance from production

### Requirements

- Most AWS Backup-supported resources support cross-region backup (RDS and EFS are supported)
- Most AWS Regions support cross-region backup
- Cross-region copies are not supported for storage in cold tiers
- Backups will be re-encrypted using the destination vault's customer managed key

Reference: [AWS Backup Cross-Region Backup Documentation](https://docs.aws.amazon.com/aws-backup/latest/devguide/cross-region-backup.html)

### Performing On-Demand Cross-Region Backup

#### Using AWS Console

1. Open the [AWS Backup console](https://console.aws.amazon.com/backup)
2. Navigate to **Backup vaults**
3. Select the vault containing the recovery point to copy
4. In the **Backups** section, select a recovery point
5. Choose **Copy** from the **Actions** dropdown
6. Configure copy settings:
   - **Copy to destination**: Select destination AWS Region
   - **Destination Backup vault**: Choose or create destination vault
   - **Transition to cold storage**: Optional lifecycle transition (minimum 1 day before transition; backup must remain in cold storage for at least 90 days once transitioned)
   - **Retention period**: Days until copy deletion
   - **IAM role**: Choose role for copy operation (default role created if needed)
7. Choose **Copy**

#### Using AWS CLI

```bash
# List recovery points in source region
aws backup list-recovery-points-by-backup-vault \
    --backup-vault-name "OpenemrEcsStack-vault-xxxxxx" \
    --region us-west-2

# Start cross-region copy
RECOVERY_POINT_ARN="arn:aws:backup:us-west-2:123456789012:recovery-point:..."

aws backup start-copy-job \
    --recovery-point-arn "$RECOVERY_POINT_ARN" \
    --source-backup-vault-name "OpenemrEcsStack-vault-xxxxxx" \
    --destination-backup-vault-arn "arn:aws:backup:us-east-1:123456789012:backup-vault:OpenemrEcsStack-vault-xxxxxx" \
    --iam-role-arn "arn:aws:iam::123456789012:role/service-role/AWSBackupDefaultServiceRole" \
    --region us-west-2
```

### Scheduling Cross-Region Backup

Add cross-region copy rules to your backup plan:

1. Open the [AWS Backup console](https://console.aws.amazon.com/backup)
2. Navigate to **Backup plans** → **Create Backup plan**
3. Choose **Build a new plan**
4. Configure backup rules as usual
5. In **Copy to destination** section:
   - Select destination AWS Region
   - Choose destination backup vault (or create new)
   - Configure lifecycle and retention settings
6. Create the plan

#### Example: Adding Cross-Region Copy to Existing Plan

You can add cross-region copy rules to an existing backup plan using AWS CLI:

```bash
# Get backup plan details
aws backup get-backup-plan \
    --backup-plan-id "<plan-id>" \
    --region us-west-2

# Update backup plan with cross-region copy rule
aws backup update-backup-plan \
    --backup-plan-id "<plan-id>" \
    --backup-plan file://updated-plan.json \
    --region us-west-2
```

### Cross-Region Backup Considerations

#### Encryption

- Backups are automatically re-encrypted using the destination vault's KMS key
- Source encryption keys are not transferred to destination
- Ensure destination vault has appropriate KMS key configured

#### Incremental vs. Full Copies

Copy behavior varies by service:

| Service | First Copy | Subsequent Copies |
|---------|-----------|-------------------|
| RDS | Full | Incremental |
| EFS | Full | **Full** (incremental not supported for cross-region) |
| EBS | Full | Incremental (unless encryption changes) |

**Important for EFS**: Each cross-region copy of an EFS backup is a full copy, which can result in higher storage costs and longer copy times compared to RDS.

#### Amazon RDS Considerations

**Important**: You cannot copy option groups to another AWS Region. If you attempt a cross-region restore, you may encounter errors like:
```
"The snapshot requires a target option group with the following options: ..."
```

**Solution**: When restoring to a different region, you must:
1. Create matching option groups in the destination region
2. Specify the option group during restore
3. Ensure all options are available in the destination region

### Monitoring Cross-Region Copies

```bash
# List copy jobs
aws backup list-copy-jobs \
    --region us-west-2

# Describe specific copy job
aws backup describe-copy-job \
    --copy-job-id "<job-id>" \
    --region us-west-2

# Monitor copy progress
watch -n 30 'aws backup describe-copy-job \
    --copy-job-id "<job-id>" \
    --region us-west-2 \
    --query "[State, StateMessage]" \
    --output text'
```

## Cross-Account Backup

Cross-account backup copies your backups to different AWS accounts, providing:
- **Security Isolation**: Separate production and backup accounts
- **Compliance**: Meet requirements for account separation
- **Data Protection**: Protect against account-level compromise

### Requirements

- **AWS Organizations**: Source and destination accounts **must** be in the same AWS Organizations (this is mandatory)
- **Service Trust**: Cross-account backup must be enabled in AWS Organizations
- **Access Policy**: Destination vault must allow `backup:CopyIntoBackupVault` permission
- Most AWS Backup-supported resources support cross-account backup (RDS and EFS are supported)
- Cross-account copies are not supported for storage in cold tiers

Reference: [AWS Backup Cross-Account Backup Documentation](https://docs.aws.amazon.com/aws-backup/latest/devguide/create-cross-account-backup.html)

### Setting Up Cross-Account Backup

#### Step 1: Enable Cross-Account Backup

**Must be done by AWS Organizations management account**:

1. Log in using AWS Organizations management account credentials
2. Open the [AWS Backup console](https://console.aws.amazon.com/backup)
3. Navigate to **Settings**
4. Under **Cross-account backup**, choose **Enable**

#### Step 2: Configure Destination Account

**In the destination account**:

1. Create or select a backup vault (cannot be the default vault)
2. Configure vault access policy to allow `backup:CopyIntoBackupVault`

**Example Access Policy** (Allow specific source account):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::SOURCE_ACCOUNT_ID:root"
      },
      "Action": "backup:CopyIntoBackupVault",
      "Resource": "*"
    }
  ]
}
```

**Example Access Policy** (Allow entire organization):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": "*",
      "Action": "backup:CopyIntoBackupVault",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:PrincipalOrgID": "o-xxxxxxxxxx"
        }
      }
    }
  ]
}
```

**Apply policy using AWS CLI**:

```bash
# Set destination vault policy
aws backup put-backup-vault-access-policy \
    --backup-vault-name "DestinationBackupVault" \
    --policy file://vault-policy.json \
    --region us-east-1
```

#### Step 3: Share Encryption Keys (If Using Customer Managed Keys)

If your backups use customer managed KMS keys, share them with the destination account:

```bash
# Share KMS key with destination account
aws kms create-grant \
    --key-id "<source-key-id>" \
    --grantee-principal "arn:aws:iam::DESTINATION_ACCOUNT_ID:root" \
    --operations "Decrypt" "DescribeKey" \
    --region us-west-2
```

### Performing On-Demand Cross-Account Backup

#### Using AWS Console

1. Open the [AWS Backup console](https://console.aws.amazon.com/backup) in the **source account**
2. Navigate to **Backup vaults**
3. Select the vault containing the recovery point to copy
4. Select a recovery point
5. Choose **Copy** from the **Actions** dropdown
6. Configure copy settings:
   - **Copy to destination**: Select destination AWS Region
   - **Copy to another account's vault**: Toggle **ON**
   - **Destination AWS account ID**: Enter destination account ID
   - **Destination Backup vault**: Choose destination vault
   - Configure retention and lifecycle settings
   - **IAM role**: Choose role with cross-account permissions
7. Choose **Copy**

#### Using AWS CLI

```bash
# In source account
RECOVERY_POINT_ARN="arn:aws:backup:us-west-2:SOURCE_ACCOUNT:recovery-point:..."
DESTINATION_ACCOUNT="DESTINATION_ACCOUNT_ID"
DESTINATION_REGION="us-east-1"

aws backup start-copy-job \
    --recovery-point-arn "$RECOVERY_POINT_ARN" \
    --source-backup-vault-name "OpenemrEcsStack-vault-xxxxxx" \
    --destination-backup-vault-arn "arn:aws:backup:${DESTINATION_REGION}:${DESTINATION_ACCOUNT}:backup-vault:DestinationVault" \
    --iam-role-arn "arn:aws:iam::SOURCE_ACCOUNT:role/AWSBackupCrossAccountRole" \
    --region us-west-2
```

### Scheduling Cross-Account Backup

Add cross-account copy rules to your backup plan:

1. Open the [AWS Backup console](https://console.aws.amazon.com/backup) in the **source account**
2. Navigate to **Backup plans** → Create or edit plan
3. In backup rule configuration, expand **Copy to destination**
4. Toggle **Copy to another account's vault** to **ON**
5. Enter **Destination AWS account ID**
6. Select **Destination Backup vault**
7. Configure retention and lifecycle settings
8. Save the plan

### Cross-Account Backup Considerations

#### Security Considerations

1. **Default Vault Limitation**: Default backup vault cannot be used as destination (uses unshareable encryption keys)
2. **Account Separation**: If destination account leaves organization, backups are retained (consider SCPs to prevent account leaving)
3. **Eventual Consistency**: Cross-account operations may continue for ~15 minutes after disabling
4. **Snapshot Unsharing**: If copy job role is deleted during copy, snapshots may not be unshared automatically

#### IAM Role Configuration

The IAM role used for cross-account copies needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "backup:StartCopyJob",
        "backup:DescribeCopyJob"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:ModifySnapshotAttribute",
        "ec2:DescribeSnapshots"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "kms:CreateGrant",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "backup.amazonaws.com"
        }
      }
    }
  ]
}
```

#### Limiting Destination Accounts

Use AWS Organizations Service Control Policies (SCPs) to restrict which accounts can receive backups:

**Example SCP** (Limit to tagged vaults):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Deny",
      "Action": "backup:CopyIntoBackupVault",
      "Resource": "*",
      "Condition": {
        "Null": {
          "aws:ResourceTag/DestinationBackupVault": "true"
        }
      }
    }
  ]
}
```

Tag approved destination vaults:

```bash
aws backup tag-resource \
    --resource-arn "arn:aws:backup:region:account:backup-vault:VaultName" \
    --tags Key=DestinationBackupVault,Value=true \
    --region us-east-1
```

### Restoring from Cross-Account Backup

To restore a backup copied to another account:

1. **In destination account**: Locate the recovery point
2. **Copy back to source account**: Use cross-account copy to copy back to source account
3. **Restore in source account**: Use normal restore procedures

**Note**: You may need to share encryption keys back to source account if customer managed keys were used.

### Monitoring Cross-Account Copies

```bash
# List copy jobs in source account
aws backup list-copy-jobs \
    --region us-west-2

# Describe specific copy job
aws backup describe-copy-job \
    --copy-job-id "<job-id>" \
    --region us-west-2

# Check copy job status
aws backup describe-copy-job \
    --copy-job-id "<job-id>" \
    --region us-west-2 \
    --query "[State, StateMessage, ResourceType]" \
    --output table
```

### Cross-Account Best Practices

1. **Organizational Structure**: Use AWS Organizations to manage account relationships
2. **Access Control**: Implement least-privilege access policies on destination vaults
3. **Key Management**: Properly share and rotate KMS keys for encrypted backups
4. **Monitoring**: Set up CloudWatch alarms for copy job failures
5. **Testing**: Regularly test cross-account restore procedures
6. **Documentation**: Document which accounts receive backups and why

## Additional Resources

- [AWS Backup Documentation](https://docs.aws.amazon.com/aws-backup/)
- [Cross-Region Backup Guide](https://docs.aws.amazon.com/aws-backup/latest/devguide/cross-region-backup.html)
- [Cross-Account Backup Guide](https://docs.aws.amazon.com/aws-backup/latest/devguide/create-cross-account-backup.html)
- [RDS Restore Documentation](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-backup-restore.html)
- [EFS Backup Documentation](https://docs.aws.amazon.com/efs/latest/ug/awsbackup.html)
