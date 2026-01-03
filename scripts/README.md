# Helper Scripts

This directory contains various helper scripts for deployment, testing, and validation.

## Table of Contents

- [Deployment Scripts](#deployment-scripts)
  - [`validate-deployment-prerequisites.sh`](#validate-deployment-prerequisitessh)
  - [`stress-test.sh`](#stress-testsh)
  - [`load-test.sh`](#load-testsh)
- [Cleanup Scripts](#cleanup-scripts)
  - [`cleanup-all-stacks.sh`](#cleanup-all-stackssh)
- [Backup and Restore Scripts](#backup-and-restore-scripts)
  - [`restore-from-backup.sh`](#restore-from-backupsh)
- [Testing Scripts](#testing-scripts)
  - [`test-startup.sh`](#test-startupsh)
  - [`test-startup-ssl.sh`](#test-startup-sslsh)
- [SSL Certificate Scripts](#ssl-certificate-scripts)
  - [`mysql-entrypoint-wrapper.sh`](#mysql-entrypoint-wrappersh)
  - [`mysql-ssl-setup.sh` and `redis-ssl-setup.sh`](#mysql-ssl-setupsh-and-redis-ssl-setupsh)
  - [`setup-mysql-ssl-ca.sh`](#setup-mysql-ssl-cash)
- [Database Access Scripts](#database-access-scripts)
  - [`port_forward_to_rds.sh`](#port_forward_to_rdssh)
- [API Testing Scripts](#api-testing-scripts)
  - [`api_endpoint_test.py`](#api_endpoint_testpy)
  - [`test_data_api.py`](#test_data_apipy)
- [General Notes](#general-notes)
- [Troubleshooting](#troubleshooting)

## Deployment Scripts

### `validate-deployment-prerequisites.sh`

**Purpose:** Pre-flight validation to ensure your environment is ready for deployment.

**What it checks:**
- AWS CLI installation and credentials
- CDK installation and bootstrap status
- Python dependencies
- Existing stack status
- CDK stack synthesis
- Route53 hosted zone existence (if configured)

**Usage:**
```bash
# From project root:
./scripts/validate-deployment-prerequisites.sh

# From scripts directory:
cd scripts && ./validate-deployment-prerequisites.sh

# From any subdirectory:
cd some/subdirectory && ../../scripts/validate-deployment-prerequisites.sh
```

**Features:**
- Automatically detects project root by searching for `cdk.json`
- Works from any directory within the project
- Color-coded output for easy reading
- Provides actionable error messages

**Example Output:**
```
=========================================
CDK Deployment Pre-Flight Validation
=========================================

1. Checking AWS CLI installation...
✓ AWS CLI is installed

2. Checking AWS credentials...
✓ AWS credentials are valid (Account: 123456789012, Region: us-east-1)

...

=========================================
Validation Summary
=========================================
✓ All checks passed!

You're ready to deploy. Run:
  cdk deploy
```

---

### `stress-test.sh`

**Purpose:** Stress test CDK stack synthesis and optionally deployment/destruction with various configurations.

**Usage:**

**Synthesis Testing (Default - Fast, Safe):**
```bash
./scripts/stress-test.sh
```
This tests that the stack can be synthesized (validated) with different configurations:
- Minimal configuration (basic features only)
- Standard configuration (with Bedrock and Data API)
- Full-featured configuration (with Global Accelerator and Analytics)

**Full Deployment Testing (Slow, Creates/Destroys Resources):**
```bash
export DEPLOY_ACTUAL=true
./scripts/stress-test.sh
```
⚠️ **Warning:** This will actually deploy and destroy stacks, taking ~40 minutes per configuration. Only use this if you want to verify actual deployments work correctly.

**What it tests:**
- Stack synthesis with different feature combinations
- (Optional) Actual deployment and destruction cycles
- Configuration variations (with/without Route53, certificates, etc.)

**Customizing Test Configurations:**
Edit the `TEST_CONFIGS` array in the script to add your own test configurations.

**Example Output:**
```
=========================================
CDK Stack Stress Test
=========================================

----------------------------------------
Test: minimal
----------------------------------------
[14:00:00] Testing configuration: minimal
[14:00:00] Testing synthesis...
✓ Synthesis successful for minimal

----------------------------------------
Test: standard
----------------------------------------
...
=========================================
Test Summary
=========================================
✓ Passed: 3
✗ Failed: 0
⚠ Skipped: 0
```

---

### `load-test.sh`

**Purpose:** Load test the deployed OpenEMR application to verify it can handle concurrent requests and measure performance metrics.

**What it does:**
- Automatically retrieves the application URL from CloudFormation stack outputs
- Waits for the application to be ready (health checks)
- Runs concurrent load test with configurable parameters
- Measures response times, success rates, and requests per second
- Provides detailed performance statistics

**Usage:**
```bash
# Basic usage (uses defaults: 60s duration, 50 concurrent users, 100 RPS)
./scripts/load-test.sh [stack-name]

# With custom parameters
export DURATION=120                    # Test duration in seconds
export CONCURRENT_USERS=100            # Number of concurrent users
export REQUESTS_PER_SECOND=200         # Target requests per second
./scripts/load-test.sh OpenemrEcsStack
```

**Configuration via Environment Variables:**
- `DURATION`: Test duration in seconds (default: 60)
- `CONCURRENT_USERS`: Number of concurrent users/threads (default: 50)
- `REQUESTS_PER_SECOND`: Target requests per second (default: 100)
- `WARMUP_TIME`: Warmup period before actual test (default: 10 seconds)
- `AWS_REGION`: AWS region (default: from AWS CLI config)

**Example Output:**
```
=========================================
OpenEMR Load Testing Script
=========================================

[14:00:00] Checking dependencies...
✓ Dependencies check passed
[14:00:01] Getting application URL from stack: OpenemrEcsStack
✓ Application URL: https://openemr-alb-123456789.us-east-1.elb.amazonaws.com
[14:00:02] Waiting for application to be ready...
✓ Application is ready
[14:00:05] Starting load test...
[14:00:05] Target URL: https://openemr-alb-123456789.us-east-1.elb.amazonaws.com
[14:00:05] Duration: 60s
[14:00:05] Concurrent users: 50
[14:00:05] Target RPS: 100
Warming up for 10 seconds...
Starting 50 concurrent workers...

============================================================
LOAD TEST RESULTS
============================================================
Test Duration:        60.12s
Total Requests:       6012
Successful Requests:  5998
Failed Requests:      14
Success Rate:         99.77%
Actual RPS:           99.93
Target RPS:           100

Response Times (ms):
  Average:            245.32
  Median:             198.45
  P95:                512.67
  P99:                892.34
  Min:                89.23
  Max:                1234.56
============================================================
✓ Load test PASSED
```

**Requirements:**
- Python 3 with `requests` library (automatically installed if missing)
- AWS CLI configured with credentials
- Deployed OpenEMR stack with `ApplicationURL` or `LoadBalancerDNS` output
- Network access to the deployed application

**Success Criteria:**
- Success rate ≥ 95%
- Actual RPS ≥ 80% of target RPS

---

## Cleanup Scripts

### `cleanup-all-stacks.sh`

**Purpose:** Deletes all OpenEMR CDK stacks across multiple AWS regions.

**⚠️ Warning**: This will delete ALL OpenEMR stacks in the checked regions!

**What it does:**
- Finds all stacks with "OpenemrEcs" or "TestStack" in the name
- Checks multiple regions (us-east-1, us-east-2, us-west-1, us-west-2, eu-west-1, eu-west-2, eu-central-1)
- Disables termination protection if enabled
- Initiates stack deletion
- Provides summary of deletions initiated

**Usage:**
```bash
# Make sure AWS credentials are configured
aws configure

# Run the cleanup script
./scripts/cleanup-all-stacks.sh
```

**Requirements:**
- AWS CLI installed and configured
- Appropriate AWS credentials with CloudFormation permissions
- Confirmation prompt (type "yes" to proceed)

**Example Output:**
```
=========================================
OpenEMR Stack Cleanup Script
=========================================

AWS Account: 123456789012

WARNING: This will delete ALL OpenEMR stacks in the following regions:
  - us-east-1
  - us-east-2
  - us-west-1
  - us-west-2
  - eu-west-1
  - eu-west-2
  - eu-central-1

Are you sure you want to continue? (yes/no): yes

=========================================
Starting stack deletion...
=========================================

Checking region: us-west-2
Deleting stack: OpenemrEcsStack in us-west-2
  ✅ Delete initiated for: OpenemrEcsStack

=========================================
Deletion Summary
=========================================
Stacks deletion initiated: 1
Failed: 0

Waiting for stacks to be deleted (this may take 10-20 minutes)...
```

**Note**: 
- Stack deletion can take 10-20 minutes. The script initiates deletion but doesn't wait for completion.
- Monitor progress in the AWS Console or with:
  ```bash
  aws cloudformation list-stacks --region <region> --stack-status-filter DELETE_IN_PROGRESS
  ```
- The stack has automated cleanup for problematic resources (SES rules, backup recovery points, RDS deletion protection)

**See Also:**
- [UPGRADES-COMPLETE.md](../UPGRADES-COMPLETE.md) for infrastructure cleanup checklist

---

## Backup and Restore Scripts

### `create-backup.sh`

**Purpose:** Manually trigger AWS Backup jobs to create on-demand backups of RDS and EFS resources.

**Usage:**
```bash
# Create backups for all resources (RDS + EFS)
./scripts/create-backup.sh

# Create backup for RDS only
./scripts/create-backup.sh RDS

# Create backup for EFS only
./scripts/create-backup.sh EFS

# With custom stack name and region
STACK_NAME=MyStack REGION=us-east-1 ./scripts/create-backup.sh all
```

**Options:**
- `RESOURCE_TYPE`: Type of resource to backup (`RDS`, `EFS`, or `all` for everything)
- Environment variables: `STACK_NAME`, `BACKUP_VAULT_NAME`, `AWS_DEFAULT_REGION`

**What it does:**
1. Discovers the backup vault associated with the CloudFormation stack
2. Retrieves resource ARNs (RDS cluster, EFS file systems) from stack outputs
3. Creates on-demand backup jobs using AWS Backup API
4. Returns backup job IDs that can be monitored

**Example Output:**
```
[2024-01-15 10:00:00] Creating backup jobs for stack: OpenemrEcsStack
[2024-01-15 10:00:01] Discovering backup vault for stack: OpenemrEcsStack
✓ Found backup vault: OpenemrEcsStack-vault-abc123
[2024-01-15 10:00:02] Creating backups for all resources...
[2024-01-15 10:00:03] Starting RDS backup...
[2024-01-15 10:00:04] Creating backup job for RDS resource...
✓ Backup job started: BACKUP_JOB_ID_12345
[2024-01-15 10:00:05] Starting EFS backup for Sites...
✓ Backup job started: BACKUP_JOB_ID_12346
✓ Backup job(s) created successfully
```

**Important Notes:**
- Backup jobs run asynchronously - recovery points will appear once jobs complete
- Check backup status with `./scripts/list-backups.sh` or monitor via AWS Console
- RDS backups typically take 10-30 minutes depending on database size
- EFS backups typically take 15-30 minutes depending on data size

---

### `restore-from-backup.sh`

**Purpose:** Restore OpenEMR infrastructure (RDS databases and EFS file systems) from AWS Backup recovery points.

**What it does:**
- Lists available recovery points in the backup vault
- Provides interactive selection of recovery points
- Initiates restore jobs for RDS or EFS resources
- Monitors restore progress and reports completion status
- Automatically discovers backup vault and stack resources

**Usage:**
```bash
# Restore RDS database (interactive mode)
./scripts/restore-from-backup.sh RDS

# Restore specific EFS file system
./scripts/restore-from-backup.sh EFS fs-12345678

# Restore from specific recovery point
./scripts/restore-from-backup.sh RDS "" \
    "arn:aws:backup:us-west-2:123456789012:recovery-point:..."

# With custom stack name and vault
./scripts/restore-from-backup.sh \
    -s MyStackName \
    -v MyBackupVault \
    RDS
```

**Options:**
- `-s, --stack-name NAME`: CloudFormation stack name (default: `OpenemrEcsStack`)
- `-v, --vault-name NAME`: Backup vault name (auto-discovered if not provided)
- `-r, --region REGION`: AWS region (default: `us-west-2`)
- `-h, --help`: Show help message

**Environment Variables:**
- `STACK_NAME`: CloudFormation stack name
- `BACKUP_VAULT_NAME`: Backup vault name
- `AWS_DEFAULT_REGION`: AWS region

**Example Output:**
```
[2024-01-15 10:00:00] Checking prerequisites...
✓ Prerequisites check passed
[2024-01-15 10:00:01] Discovering backup vault for stack: OpenemrEcsStack
✓ Found backup vault: OpenemrEcsStack-vault-abc123
[2024-01-15 10:00:02] Listing recovery points for resource type: RDS

Available recovery points:
 1. arn:aws:backup:us-west-2:123456789012:recovery-point:...  2024-01-14T02:00:00Z  COMPLETED
 2. arn:aws:backup:us-west-2:123456789012:recovery-point:...  2024-01-13T02:00:00Z  COMPLETED
 3. arn:aws:backup:us-west-2:123456789012:recovery-point:...  2024-01-12T02:00:00Z  COMPLETED

Enter recovery point number (1-3): 1
[2024-01-15 10:00:10] Initiating RDS restore from recovery point...
✓ Restore job started: RESTORE_JOB_ID_12345
[2024-01-15 10:00:11] Monitoring restore progress...
[2024-01-15 10:00:41] Restore job status: RUNNING (checking again in 30 seconds...)
...
[2024-01-15 10:45:30] ✓ Restore job completed successfully
```

**Restore Times:**
- **RDS**: Typically 30-60 minutes for moderate-sized databases (10-100GB)
- **EFS**: Typically 15-30 minutes depending on data size

**Requirements:**
- AWS CLI configured with appropriate IAM permissions
- AWS Backup service role must exist (`AWSBackupDefaultServiceRole`)
- Stack must be deployed with AWS Backup plan configured
- Recovery points must exist in the backup vault

**Important Notes:**
- **RDS Restore**: Creates a NEW database cluster. Update application connection strings after restore.
- **EFS Restore**: Overwrites existing file system data. Ensure you have a recent backup if needed.
- Restore operations are asynchronous and can take significant time for large datasets.
- Monitor restore progress using CloudWatch or the script's built-in monitoring.

**See Also:**
- [BACKUP-RESTORE-GUIDE.md](../BACKUP-RESTORE-GUIDE.md) for comprehensive backup and restore documentation
- [AWS Backup Documentation](https://docs.aws.amazon.com/aws-backup/)

---

## Testing Scripts

### `test-startup.sh`

**Purpose:** Test OpenEMR container startup locally without SSL certificates.

**Usage:**
```bash
./scripts/test-startup.sh
```

See [README-TESTING.md](../README-TESTING.md) for detailed usage.

---

### `test-startup-ssl.sh`

**Purpose:** Test OpenEMR container startup locally with SSL certificates (simulates production environment).

**Usage:**
```bash
./scripts/test-startup-ssl.sh
```

See [README-TESTING.md](../README-TESTING.md) for detailed usage.

---

## SSL Certificate Scripts

### `mysql-entrypoint-wrapper.sh`

**Purpose:** Wrapper script for MySQL containers that generates SSL certificates before MySQL starts.

**Used by:** Docker Compose SSL test configuration.

---

### `mysql-ssl-setup.sh` and `redis-ssl-setup.sh`

**Purpose:** Generate SSL certificates for MySQL and Redis/Valkey containers.

**Used by:** Local Docker Compose testing.

---

### `setup-mysql-ssl-ca.sh`

**Purpose:** Downloads and sets up MySQL CA certificate for SSL connections.

**Used by:** Container startup scripts.

---

## Database Access Scripts

### `port_forward_to_rds.sh`

**Purpose:** Port forwards to RDS database through ECS Exec for secure database access.

**Usage:**
```bash
./scripts/port_forward_to_rds.sh <cluster-name> <db-hostname>
```

See [DETAILS.md](../DETAILS.md#using-ecs-exec) for detailed usage.

---

## Development/Utility Scripts

### `extract-startup-script.py`

**Purpose:** Extract the container startup script from `compute.py` for shellcheck analysis and testing.

**What it does:**
- Parses `openemr_ecs/compute.py` to extract the `startup_commands` list
- Generates a standalone shell script for shellcheck validation
- Helps ensure the startup script syntax is correct and follows best practices

**Usage:**
```bash
# Extract the startup script to /tmp/startup_script.sh
python3 scripts/extract-startup-script.py

# Run shellcheck on the extracted script
shellcheck /tmp/startup_script.sh
```

**Output:**
- Creates `/tmp/startup_script.sh` with the extracted startup commands
- Prints the number of commands extracted

**Use Cases:**
- Validating shell script syntax before deployment
- Ensuring shellcheck compliance
- Debugging startup script issues
- Updating docker-compose test files to match production script

**Example Output:**
```
Extracted 244 commands to /tmp/startup_script.sh
```

---

## API Testing Scripts

### `api_endpoint_test.py`

**Purpose:** Test OpenEMR REST API endpoints.

**Usage:**
```bash
python3 scripts/api_endpoint_test.py <openemr-url> <username> <password>
```

---

### `test_data_api.py`

**Purpose:** Test OpenEMR Data API endpoints.

**Usage:**
```bash
python3 scripts/test_data_api.py <openemr-url> <username> <password>
```

---

## General Notes

- All scripts use bash shebang (`#!/bin/bash`) and require bash 4.0+
- Scripts check for required tools and provide helpful error messages if missing
- Most scripts are designed to be run from the project root, but some (like `validate-deployment-prerequisites.sh`) work from any directory
- Scripts follow bash best practices with error handling (`set -e`) and clear output

## Troubleshooting

**Script won't run:**
```bash
chmod +x scripts/script-name.sh
```

**Script says "cdk.json not found":**
- Make sure you're in the project directory
- The `validate-deployment-prerequisites.sh` script automatically searches parent directories

**Permission denied:**
- Make sure the script has execute permissions: `chmod +x scripts/script-name.sh`
- On some systems, you may need to use `bash scripts/script-name.sh` instead

