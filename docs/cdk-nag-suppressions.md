# CDK Nag Suppressions Rationale

This document explains why certain CDK Nag findings are suppressed in the OpenEMR on ECS deployment.

## Wildcards in IAM Policies (AwsSolutions-IAM5, HIPAA.Security-IAMNoInlinePolicy)

### Lambda Functions
Lambda functions require wildcard permissions for:
- **S3 Bucket Operations** (`s3:GetBucket*`, `s3:GetObject*`, `s3:List*`): These are AWS SDK standard patterns for bucket operations
- **S3 Object Operations** (`<bucket>/*`): Required to access all objects in a bucket
- **KMS Operations** (`kms:GenerateDataKey*`, `kms:ReEncrypt*`): Required for S3 encryption operations
- **ECS Task Execution** (`ecs:RunTask`, `ecs:DescribeTasks`): Required for triggering tasks
- **Lambda Invocation** (`lambda:InvokeFunction` with `:*` suffix): Required for versioned functions

These are necessary for service functionality and follow AWS best practices.

### SageMaker Execution Role
- Uses AWS managed policies (`AmazonSageMakerFullAccess`, etc.) which are required for SageMaker functionality
- Wildcard S3 permissions needed for data science workflows accessing multiple buckets
- Lambda invocations needed for data export pipelines

### EMR Serverless and Glue
- Wildcard permissions for EMR applications (`arn:aws:emr-serverless:*:*/applications/*`)
- ECR access (`arn:aws:ecr:*:*/*`) for custom Spark containers
- Required for big data processing workflows

## AWS Managed Policies (AwsSolutions-IAM4)

The following AWS managed policies are intentionally used:
- `AWSLambdaBasicExecutionRole`: Standard CloudWatch Logs permissions for Lambda
- `AWSBackupServiceRolePolicyForBackup/Restores`: Required for AWS Backup service
- `AmazonSageMakerFullAccess`: Required for SageMaker Studio domain
- `AWSGlueServiceRole`: Required for AWS Glue catalog integration

These policies are maintained by AWS and updated with new service features.

## Lambda Configuration (HIPAA.Security-Lambda*)

### Lambda Not in VPC
Many Lambda functions don't need VPC access:
- **SMTPSetup**: Configures SES credentials (AWS API calls only)
- **MakeRuleSetActive**: Manages SES rule sets (AWS API calls only)
- **OneTimeSSLSetup**: Triggers ECS task for SSL certificate setup
- **MaintainSSLMaterialsLambda**: Schedules SSL renewal tasks
- **EmailForwardingLambda**: Processes S3-stored emails
- **Cleanup Lambda**: Cleans up resources on stack deletion

VPC-enabled Lambdas have cold start penalties and require VPC endpoint costs without providing security benefits for API-only operations.

### Lambda Concurrency and DLQ
- Concurrency limits not set: Allows autoscaling based on demand
- Dead Letter Queue (DLQ) not configured: These are simple synchronous operations that fail fast
- For production, consider adding DLQ for async operations

## IAM User with Inline Policy (HIPAA.Security-IAMUserNoPolicies)

### SMTP User
The SMTP user requires:
- Inline policy with `ses:SendRawEmail` permission
- Cannot be moved to a group as this is a service account for SES SMTP authentication
- IAM user is required (not role) because SMTP authentication requires long-lived credentials

## Environment Variables in ECS Task (AwsSolutions-ECS2)

OpenEMR container uses environment variables for:
- Non-sensitive configuration (timeouts, feature flags)
- Secrets are properly injected from Secrets Manager (database password, SMTP credentials)
- This is standard practice for container configuration

## S3 Bucket Configuration

### Replication Not Enabled
- Most buckets don't require cross-region replication
- **ELB Logs**: Generated continuously, not critical
- **CloudTrail Logs**: Immutable audit logs with 7-year retention
- **Export Buckets**: Temporary storage for data exports
- Access logs buckets: Supporting infrastructure only

### Buckets Without Server Access Logging
- Access log buckets themselves don't have logging (circular dependency)
- Suppressed with clear rationale

## VPC and Network

### Public Subnet IGW Routes (HIPAA.Security-VPCNoUnrestrictedRouteToIGW)
- Required for Application Load Balancer internet connectivity
- ALB is protected by security groups with IP allowlisting
- No compute resources in public subnets

### Default Security Group (HIPAA.Security-VPCDefaultSecurityGroupClosed)
- Default SG is not used - all resources use explicitly created SGs
- Cannot be deleted (AWS limitation) but documented as closed

### VPC Endpoint Security Groups (CdkNagValidationFailure)
- VPC CIDR blocks resolved at deploy time via CloudFormation intrinsic functions
- Cannot be validated at synth time
- Security group rules are correctly configured at runtime

## RDS Configuration

### Default Port (AwsSolutions-RDS11)
- Using standard MySQL port 3306 for compatibility
- Security enforced through: VPC isolation, security groups, SSL/TLS, IAM authentication

### Backtrack Not Enabled (AwsSolutions-RDS14)
- Not supported for Aurora Serverless v2
- Using AWS Backup with point-in-time recovery instead

### Deletion Protection (AwsSolutions-RDS10)
- Enabled by default
- Can be temporarily disabled via context flag for stack destruction

## Secrets Manager

### Rotation Not Enabled (AwsSolutions-SMG4, HIPAA.Security-SecretsManagerRotationEnabled)
- Database secret: Managed by Aurora with automatic rotation
- SMTP secret: SMTP credentials don't support automatic rotation (IAM user based)
- Application password: Manually rotated by administrators

## CloudWatch Logs

All CloudWatch Log Groups are now encrypted with KMS customer-managed keys for HIPAA compliance.

## Summary

All suppressions are intentional and documented with clear rationale. The infrastructure follows AWS best practices and HIPAA security requirements while maintaining operational simplicity.

