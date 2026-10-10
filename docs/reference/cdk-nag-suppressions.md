# cdk-nag suppressions

This page lists every cdk-nag finding that the stack deliberately acknowledges
and explains why each one is safe for this architecture. It is for reviewers,
auditors, and contributors who add or change infrastructure. For how cdk-nag
fits into the project's overall security checks, see
[Security and compliance](security-and-compliance.md#cdk-nag-rule-packs).

## Contents

- [How acknowledgments work in this project](#how-acknowledgments-work-in-this-project)
- [All acknowledged rules at a glance](#all-acknowledged-rules-at-a-glance)
- [Wildcards in IAM policies (AwsSolutions-IAM5, HIPAA.Security-IAMNoInlinePolicy)](#wildcards-in-iam-policies-awssolutions-iam5-hipaasecurity-iamnoinlinepolicy)
  - [Lambda functions](#lambda-functions)
  - [ECS tasks](#ecs-tasks)
  - [SageMaker execution role](#sagemaker-execution-role)
  - [EMR Serverless and Glue](#emr-serverless-and-glue)
  - [Other roles](#other-roles)
- [AWS managed policies (AwsSolutions-IAM4)](#aws-managed-policies-awssolutions-iam4)
- [Lambda configuration (HIPAA.Security-Lambda*)](#lambda-configuration-hipaasecurity-lambda)
  - [Lambda not in VPC](#lambda-not-in-vpc)
  - [Lambda concurrency and DLQ](#lambda-concurrency-and-dlq)
  - [Lambda runtime (AwsSolutions-L1)](#lambda-runtime-awssolutions-l1)
- [IAM user with inline policy (HIPAA.Security-IAMUserNoPolicies)](#iam-user-with-inline-policy-hipaasecurity-iamusernopolicies)
  - [SMTP user](#smtp-user)
- [Environment variables in ECS tasks (AwsSolutions-ECS2)](#environment-variables-in-ecs-tasks-awssolutions-ecs2)
- [S3 bucket configuration](#s3-bucket-configuration)
  - [Replication not enabled](#replication-not-enabled)
  - [Buckets without server access logging](#buckets-without-server-access-logging)
  - [Buckets that must use S3-managed encryption](#buckets-that-must-use-s3-managed-encryption)
- [VPC and network](#vpc-and-network)
  - [Public subnet IGW routes (HIPAA.Security-VPCNoUnrestrictedRouteToIGW)](#public-subnet-igw-routes-hipaasecurity-vpcnounrestrictedroutetoigw)
  - [Default security group (HIPAA.Security-VPCDefaultSecurityGroupClosed)](#default-security-group-hipaasecurity-vpcdefaultsecuritygroupclosed)
  - [Load balancer security group (AwsSolutions-EC23)](#load-balancer-security-group-awssolutions-ec23)
  - [Security groups flagged because of intrinsic functions](#security-groups-flagged-because-of-intrinsic-functions)
- [RDS configuration](#rds-configuration)
  - [IAM database authentication (AwsSolutions-RDS6)](#iam-database-authentication-awssolutions-rds6)
  - [Default port (AwsSolutions-RDS11)](#default-port-awssolutions-rds11)
  - [Backtrack not enabled (AwsSolutions-RDS14)](#backtrack-not-enabled-awssolutions-rds14)
  - [Deletion protection (AwsSolutions-RDS10)](#deletion-protection-awssolutions-rds10)
  - [Backup plan and enhanced monitoring](#backup-plan-and-enhanced-monitoring)
- [Secrets Manager](#secrets-manager)
  - [Rotation not enabled (AwsSolutions-SMG4, HIPAA.Security-SecretsManagerRotationEnabled)](#rotation-not-enabled-awssolutions-smg4-hipaasecurity-secretsmanagerrotationenabled)
- [CloudWatch Logs](#cloudwatch-logs)
- [Test-only acknowledgments](#test-only-acknowledgments)
- [Summary](#summary)

## How acknowledgments work in this project

The `AwsSolutionsChecks` and `HIPAASecurityChecks` rule packs run on every
synthesis (see [`app.py`](../../app.py)). This project uses **cdk-nag v3**,
which removed the old `NagSuppressions` API in favor of CDK's native
validations ("acknowledgments").

All acknowledgments go through `acknowledge_findings()` in
[`openemr_ecs/nag_suppressions.py`](../../openemr_ecs/nag_suppressions.py),
plus a few helpers built on it:

| Helper | Applies |
|---|---|
| `acknowledge_findings(construct, findings)` | One or more rule IDs, each with a reason, to a construct and everything inside it |
| `suppress_lambda_common_findings()` | Lambda concurrency, DLQ, and (unless the function needs a VPC) Lambda-in-VPC findings |
| `suppress_lambda_role_common_findings()` | `AWSLambdaBasicExecutionRole`, inline policy, and S3/KMS/ECS wildcards for a Lambda role, depending on `role_type` |
| `suppress_sagemaker_role_findings()` | SageMaker managed policies and data-science wildcards |
| `suppress_vpc_endpoint_security_group_findings()` | Intrinsic-function false positives on VPC endpoint security groups |

Two details matter when you add an acknowledgment:

- **Granular IDs.** v3 has no bulk suppression. When a finding is limited to
  specific actions or resources (`appliesTo`), each one becomes its own
  `RuleId[suffix]` acknowledgment, for example
  `AwsSolutions-IAM5[Action::s3:GetObject*]`.
- **Metadata is written directly.** The helper writes the CDK "acknowledged
  rules" metadata itself instead of calling
  `Validations.of(construct).acknowledge()`, because of an upstream bug
  ([cdklabs/cdk-nag#2351](https://github.com/cdklabs/cdk-nag/issues/2351))
  where ID validation rejects IDs containing more than one `::`, which
  includes AWS managed policy ARNs.

Any new finding that appears during `cdk synth` must be fixed or acknowledged
with a reason before deployment.

## All acknowledged rules at a glance

| Rule | Where it is acknowledged |
|---|---|
| `AwsSolutions-IAM5` | Lambda roles, ECS task and execution roles, SageMaker, EMR/Glue, AWS Backup, cleanup, SES, termination protection, Bedrock |
| `HIPAA.Security-IAMNoInlinePolicy` | Most CDK-generated roles and policies |
| `AwsSolutions-IAM4` | Lambda basic execution, AWS Backup, SageMaker, Glue, RDS export |
| `HIPAA.Security-LambdaConcurrency`, `HIPAA.Security-LambdaDLQ`, `HIPAA.Security-LambdaInsideVPC` | Every Lambda function |
| `AwsSolutions-L1` | Stack cleanup Lambda |
| `HIPAA.Security-IAMUserNoPolicies`, `HIPAA.Security-IAMUserGroupMembership` | SES SMTP IAM user |
| `AwsSolutions-ECS2` | OpenEMR, credential rotation, and import task definitions |
| `HIPAA.Security-S3BucketReplicationEnabled` | Log, export, email, ECS Exec, and import staging buckets |
| `AwsSolutions-S1`, `HIPAA.Security-S3BucketLoggingEnabled` | Access-log, export, email, and ECS Exec buckets |
| `AwsSolutions-S3`, `HIPAA.Security-S3DefaultEncryptionKMS`, `HIPAA.Security-S3BucketSSEEnabled` | ALB log buckets and the SES email bucket |
| `HIPAA.Security-VPCNoUnrestrictedRouteToIGW` | Public subnets |
| `HIPAA.Security-VPCDefaultSecurityGroupClosed` | VPC |
| `AwsSolutions-EC23` | Load balancer and VPC endpoint security groups |
| `HIPAA.Security-EC2RestrictedCommonPorts`, `HIPAA.Security-EC2RestrictedSSH` | Database and VPC endpoint security groups |
| `AwsSolutions-RDS6`, `AwsSolutions-RDS10`, `AwsSolutions-RDS11`, `AwsSolutions-RDS14` | Aurora cluster |
| `HIPAA.Security-RDSInBackupPlan`, `HIPAA.Security-RDSInstanceDeletionProtectionEnabled`, `HIPAA.Security-RDSEnhancedMonitoringEnabled` | Aurora cluster |
| `AwsSolutions-SMG4`, `HIPAA.Security-SecretsManagerRotationEnabled` | Database admin, OpenEMR admin, RDS slot, and SMTP secrets |
| `HIPAA.Security-CloudWatchLogGroupEncrypted` | WAF log group |
| `HIPAA.Security-ELBDeletionProtectionEnabled` | Load balancer, guarded live E2E stacks only |
| `HIPAA.Security-EFSInBackupPlan` | EFS file systems, Floci emulator stacks only |

## Wildcards in IAM policies (AwsSolutions-IAM5, HIPAA.Security-IAMNoInlinePolicy)

`AwsSolutions-IAM5` flags wildcards (`*`) in IAM policies.
`HIPAA.Security-IAMNoInlinePolicy` flags inline policies. Most inline policies
here are generated by CDK `grant_*()` calls and are scoped to the specific
resources each role needs.

### Lambda functions

Lambda roles need wildcard permissions for:

- **S3 bucket operations** (`s3:GetBucket*`, `s3:GetObject*`, `s3:List*`,
  `s3:Abort*`, `s3:DeleteObject*`): the standard AWS SDK patterns CDK grants
  generate.
- **S3 object operations** (`<bucket>/*`): required to access all objects in a
  bucket.
- **KMS operations** (`kms:GenerateDataKey*`, `kms:ReEncrypt*`): required for
  S3 encryption.
- **ECS task execution** (`ecs:RunTask`, `ecs:DescribeTasks`): the SSL
  certificate Lambdas start ECS tasks; these actions require wildcard resources
  and are limited with a cluster condition.
- **SES** (`SetActiveReceiptRuleSet`): requires a wildcard resource.
- **Secrets Manager** (`SecretAccessKey*`): used to generate SMTP credentials.
- **Stack cleanup**: the cleanup Lambda needs broad permissions across RDS,
  SageMaker, SES, EFS, ALB, EC2, and AWS Backup to clear blockers during stack
  deletion.
- **Termination protection**: the Lambda that turns on stack termination
  protection needs a wildcard on the stack ARN to cover change sets.

These are required for the functions to work and follow AWS practice.

### ECS tasks

- **ECS execution roles** need wildcard ECR authorization and CloudWatch Logs
  delivery permissions; CDK generates these.
- **ECS Exec** (when enabled) needs wildcard SSM message channel and log group
  describe permissions.
- **Credential rotation task**: ECS service update/describe and KMS grants use
  scoped wildcard resources resolved at runtime, and KMS
  `GenerateDataKey`/`ReEncrypt` are needed for Secrets Manager
  `PutSecretValue`.
- **OpenEMR import task** (only when `openemr_import_target` is `true`): the
  migration ID is chosen at runtime, so the staging bucket paths
  `migrations/*/source.tar` and `migrations/*/status.json` use a wildcard in
  that one path segment.

### SageMaker execution role

Only created with the analytics environment.

- Uses AWS managed policies (`AmazonSageMakerFullAccess` and others) that
  SageMaker Studio requires.
- Wildcard S3 permissions for data-science workflows that read multiple
  buckets and objects.
- Lambda invocation permissions with a version suffix (`<function>.Arn:*`) for
  the data export pipelines.

### EMR Serverless and Glue

Only created with the analytics environment.

- Wildcard permissions for EMR Serverless applications
  (`arn:aws:emr-serverless:*:*/applications/*`).
- ECR access (`arn:aws:ecr:*:*/*`) for custom Spark containers.
- Glue Data Catalog operations (`Resource::*`) and S3 access for big-data
  processing.

### Other roles

- **AWS Backup service role:** the managed backup and restore policies use
  wildcard resources.
- **Bedrock integration** (when enabled): foundation model ARNs follow the
  pattern `arn:aws:bedrock:*::foundation-model/*` by design.
- **RDS export role** (analytics): S3 and KMS wildcards for snapshot export.

## AWS managed policies (AwsSolutions-IAM4)

These AWS managed policies are used on purpose. AWS maintains them and updates
them as services add features.

| Policy | Used by |
|---|---|
| `AWSLambdaBasicExecutionRole` | Lambda functions (CloudWatch Logs access) |
| `AWSBackupServiceRolePolicyForBackup`, `AWSBackupServiceRolePolicyForRestores` | AWS Backup service role |
| `AmazonSageMakerFullAccess`, `AmazonSageMakerClusterInstanceRolePolicy`, `AmazonSageMakerFeatureStoreAccess`, `AmazonSageMakerModelGovernanceUseAccess`, `AmazonSageMakerModelRegistryFullAccess`, `AmazonSageMakerGroundTruthExecution`, `AmazonSageMakerPipelinesIntegrations`, `AmazonSageMakerCanvasFullAccess` | SageMaker Studio domain (analytics only) |
| `AWSGlueServiceRole` | Glue Data Catalog integration with EMR Serverless (analytics only) |
| `AmazonRDSDataFullAccess` | RDS export functionality (analytics only) |

## Lambda configuration (HIPAA.Security-Lambda*)

### Lambda not in VPC

These Lambda functions only call AWS APIs and don't need to be inside the VPC:

- **SMTPSetup**: creates SES SMTP credentials
- **MakeRuleSetActive**: manages SES receipt rule sets
- **EmailForwardingLambda**: processes emails stored in S3
- **OneTimeSSLSetup**: starts the ECS task that creates the internal TLS
  certificates
- **MaintainSSLMaterialsLambda**: starts the scheduled certificate renewal
  task
- **StackCleanupLambda**: cleans up resources during stack deletion
- **EnableTerminationProtectionLambda**: turns on stack termination protection
- **EFStoS3ExportLambda** and **RDStoS3ExportLambda** (analytics only): start
  the export jobs

Putting them in a VPC would add cold-start time and VPC endpoint costs without
a security benefit for API-only operations.

### Lambda concurrency and DLQ

- **Concurrency limits** aren't set, so functions can scale with demand.
- **Dead-letter queues (DLQs)** aren't configured. These are short
  synchronous operations that fail fast, and failures are logged to
  CloudWatch.
- For production, consider adding a DLQ to any function you invoke
  asynchronously.

### Lambda runtime (AwsSolutions-L1)

The stack cleanup Lambda is acknowledged for `AwsSolutions-L1` (latest
runtime). It uses Python 3.14, the latest runtime available when it was
written; `LAMBDA_PYTHON_RUNTIME` in
[`constants.py`](../../openemr_ecs/constants.py) is reviewed by the monthly
version audit.

## IAM user with inline policy (HIPAA.Security-IAMUserNoPolicies)

### SMTP user

Only created when `configure_ses` is `"true"`. The SMTP user:

- Needs an inline policy allowing SES email sending (generated by CDK's
  `grant_send_email()`).
- Is a service account, not a human, so it isn't placed in a group
  (`HIPAA.Security-IAMUserGroupMembership`).
- Must be an IAM user rather than a role, because SES SMTP authentication
  requires long-lived credentials.

## Environment variables in ECS tasks (AwsSolutions-ECS2)

The task definitions pass only non-sensitive values as environment variables:

- **OpenEMR task:** `MYSQL_DATABASE` and other non-sensitive configuration.
  Database, admin, and SMTP passwords are injected from Secrets Manager.
- **Credential rotation task:** resource IDs and mount paths; secrets are read
  from Secrets Manager at runtime.
- **Import task:** resource names, paths, and the public application version;
  database credentials use ECS secrets.

## S3 bucket configuration

### Replication not enabled

Cross-Region replication isn't needed for these buckets:

- **ALB access-log buckets:** logs are generated continuously and aren't
  needed for recovery.
- **Server access-log buckets:** supporting infrastructure for debugging.
- **CloudTrail log bucket:** immutable audit logs with 7-year retention;
  CloudTrail continuously writes new logs.
- **Analytics export buckets:** data can be exported again from the database
  or EFS.
- **SES email bucket:** incoming emails are forwarded right away.
- **ECS Exec bucket:** temporary command output.
- **Import staging bucket:** temporary, encrypted staging data with a one-day
  lifecycle; durable copies remain at the source and in verified backups.

### Buckets without server access logging

- Access-log buckets don't log to themselves; that would be a circular
  dependency.
- The analytics export buckets already have access logs through the analytics
  access-logs bucket.
- The SES email bucket and the ECS Exec bucket hold short-lived content.

### Buckets that must use S3-managed encryption

The ALB access-log buckets and the SES incoming-email bucket use S3-managed
encryption (SSE-S3) instead of KMS, because ALB access logging and SES
delivery to S3 require it. This is acknowledged for `AwsSolutions-S3`,
`HIPAA.Security-S3DefaultEncryptionKMS`, and (ALB buckets)
`HIPAA.Security-S3BucketSSEEnabled`.

## VPC and network

### Public subnet IGW routes (HIPAA.Security-VPCNoUnrestrictedRouteToIGW)

- The Application Load Balancer needs an internet gateway route to be reachable.
- The ALB is protected by security groups that allow only the IP ranges you
  configure.
- No compute resources run in the public subnets.

### Default security group (HIPAA.Security-VPCDefaultSecurityGroupClosed)

- The default security group isn't used; every resource uses an explicitly
  created, least-privilege security group.
- AWS doesn't allow the default security group to be deleted.

### Load balancer security group (AwsSolutions-EC23)

The ALB is public by design. Its security group allows HTTPS (443) only from
the CIDR ranges in `security_group_ip_range_ipv4` and
`security_group_ip_range_ipv6`.

### Security groups flagged because of intrinsic functions

The database security group and the VPC endpoint security groups (SMTP,
Bedrock Runtime, SageMaker API, and SageMaker Runtime) are flagged for
`HIPAA.Security-EC2RestrictedCommonPorts`, `HIPAA.Security-EC2RestrictedSSH`,
and (endpoints) `AwsSolutions-EC23`. Their rules refer to the VPC CIDR block
and the database port through CloudFormation intrinsic functions that are only
resolved at deploy time, so cdk-nag can't evaluate them at synthesis. The
rules are restricted to the VPC and don't open SSH.

## RDS configuration

### IAM database authentication (AwsSolutions-RDS6)

OpenEMR connects with standard MySQL usernames and passwords (mysqli/PDO) and
doesn't support IAM database authentication. Access is protected by VPC
isolation, security groups, required TLS, and Secrets Manager.

### Default port (AwsSolutions-RDS11)

- Uses the standard MySQL port 3306 for compatibility with OpenEMR and common
  tools.
- Access is controlled by VPC isolation, least-privilege security groups, and
  TLS.

### Backtrack not enabled (AwsSolutions-RDS14)

- Backtrack isn't supported for Aurora Serverless v2.
- AWS Backup is used for recovery instead.

### Deletion protection (AwsSolutions-RDS10)

Also acknowledged as `HIPAA.Security-RDSInstanceDeletionProtectionEnabled`.

- Controlled by `rds_deletion_protection`. The code defaults to `true` when the
  key is absent, but the shipped `cdk.json` sets it to `false` so `cdk destroy`
  works cleanly. Set it to `true` for production.
- It can be turned off for a destroy with
  `-c disable_rds_deletion_protection_on_destroy=true`, and the stack cleanup
  Lambda disables it during stack deletion.

> [!NOTE]
> The reason text recorded in code says deletion protection is "enabled by
> default". That is true of the code's fallback but not of the shipped
> `cdk.json`.

### Backup plan and enhanced monitoring

- `HIPAA.Security-RDSInBackupPlan`: the cluster is backed up by the AWS Backup
  plan defined in [`storage.py`](../../openemr_ecs/storage.py) (7-year
  retention), which cdk-nag doesn't detect.
- `HIPAA.Security-RDSEnhancedMonitoringEnabled`: Enhanced Monitoring isn't
  available for Aurora Serverless v2. CloudWatch Database Insights (Advanced
  mode, 15-month retention) and the exported `audit`, `error`, `general`, and
  `slowquery` logs are used instead.

## Secrets Manager

### Rotation not enabled (AwsSolutions-SMG4, HIPAA.Security-SecretsManagerRotationEnabled)

Secrets Manager's built-in rotation isn't configured on these secrets:

| Secret | How it is rotated |
|---|---|
| Database admin secret (`dbadmin`) | Not by Secrets Manager. The [credential rotation task](../guides/credential-rotation.md) rotates the admin password when you run it. |
| RDS slot secret | By the credential rotation task, which flips between two application users and can roll back. |
| OpenEMR admin password | Rotated manually by administrators; automatic rotation isn't appropriate for this credential. |
| SMTP credentials | Based on IAM access keys, which can't be rotated automatically; rotate them manually (see [HTTPS and DNS](../guides/https-and-dns.md)). |

> [!NOTE]
> The reason text recorded in code for the database admin secret says it is
> "managed by Aurora Serverless v2 with automatic rotation through RDS". The
> secret is actually a plain Secrets Manager secret created by the stack, with
> no RDS-managed rotation; rotation comes from the credential rotation task.

## CloudWatch Logs

The stack's CloudWatch log groups are encrypted with customer-managed KMS keys.
The WAF log group is acknowledged for `HIPAA.Security-CloudWatchLogGroupEncrypted`
because it is encrypted with the central KMS key but is still reported.

## Test-only acknowledgments

These apply only to special test stacks and never to normal deployments:

- `HIPAA.Security-ELBDeletionProtectionEnabled`: guarded live E2E stacks turn
  off ALB deletion protection so cleanup can always delete them. See
  [Live E2E](../maintainers/live-e2e.md).
- `HIPAA.Security-EFSInBackupPlan`: Floci emulator stacks omit AWS Backup
  because the emulator lacks the AWS Backup managed policies. See
  [Floci](../maintainers/floci.md).

## Summary

Every acknowledgment is intentional and has a recorded reason in code. The
infrastructure follows AWS practice and the HIPAA Security rule pack while
keeping operations simple.
