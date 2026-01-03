# Lambda Functions

This directory contains AWS Lambda functions used by the OpenEMR CDK stack for automated operations.

## Table of Contents

- [Overview](#overview)
- [Functions](#functions)
- [Dependencies](#dependencies)
- [Development](#development)
- [Deployment](#deployment)

## Overview

The Lambda functions in this directory provide automation and orchestration capabilities for the OpenEMR deployment:

- **SSL Certificate Generation**: Automated TLS certificate creation and rotation
- **SES Configuration**: Email service setup and credential generation
- **Data Export**: Automated RDS and EFS data export to S3
- **Backup Automation**: Integration with AWS Backup services

These functions are automatically deployed as part of the CDK stack and do not require manual deployment.

## Functions

### `generate_ssl_materials`

**Purpose**: Generates self-signed SSL certificates for internal ALB-to-container communication.

**Trigger**: 
- One-time execution during stack creation
- Scheduled execution for certificate rotation (every 2 days by default)

**Runtime**: Python 3.14

**Key Operations**:
- Generates self-signed certificates
- Stores certificates on EFS file system
- Ensures proper file permissions
- Updates certificates before expiration

**Used By**:
- CDK stack for SSL certificate management
- ECS containers for secure internal communication

### `generate_smtp_credential`

**Purpose**: Generates SMTP credentials for Amazon SES email service.

**Trigger**: One-time execution during stack creation when SES is configured

**Runtime**: Python 3.14

**Key Operations**:
- Creates IAM user for SES SMTP access
- Generates SMTP credentials
- Stores credentials in Secrets Manager
- Configures SES sending authorization

**Used By**:
- CDK stack when `configure_ses` is enabled
- OpenEMR application for sending emails

### `make_ruleset_active`

**Purpose**: Activates an SES receipt rule set for email receiving.

**Trigger**: Executed via CDK trigger after SES rule set creation

**Runtime**: Python 3.14

**Key Operations**:
- Sets the specified rule set as active
- Ensures email routing is configured correctly

**Used By**:
- CDK stack for SES email forwarding configuration

### `export_from_rds_to_s3`

**Purpose**: Exports RDS Aurora snapshot data to S3 for analytics.

**Trigger**: Manual invocation or scheduled event

**Runtime**: Python 3.14

**Key Operations**:
- Initiates RDS snapshot export to S3
- Configures export parameters
- Monitors export progress

**Used By**:
- Analytics workflow for data processing
- Manual data export operations

### `sync_efs_to_s3`

**Purpose**: Synchronizes EFS file system contents to S3.

**Trigger**: Manual invocation or scheduled event

**Runtime**: Python 3.14

**Key Operations**:
- Recursively syncs EFS files to S3
- Preserves file structure and metadata
- Handles large file transfers

**Used By**:
- Backup and archival operations
- Data migration workflows

## Dependencies

Lambda functions use the following AWS SDK clients:

- `boto3` - AWS SDK for Python
  - `rds` - RDS database operations
  - `ses` - Simple Email Service
  - `s3` - S3 storage operations
  - `secretsmanager` - Secrets management
  - `iam` - Identity and Access Management
  - `backup` - AWS Backup service
  - `logs` - CloudWatch Logs

**Note**: `boto3` is provided by the Lambda runtime environment and does not need to be included in deployment packages.

## Development

### Local Testing

Lambda functions can be tested locally using the AWS SAM CLI or by creating test events:

```python
# Example test event
test_event = {
    "ResourceProperties": {
        "StackName": "OpenemrEcsStack",
        "RuleSetName": "my-rule-set"
    },
    "RequestType": "Create"
}

# Test handler
result = make_ruleset_active(test_event, mock_context)
```

### Code Structure

Each function is defined in `lambda_functions.py`:

```python
def function_name(event, context):
    """
    Function description.
    
    Args:
        event: Lambda event dictionary
        context: Lambda context object
        
    Returns:
        Response dictionary
    """
    # Function implementation
    pass
```

### Best Practices

1. **Error Handling**: Always wrap operations in try/except blocks
2. **Logging**: Use Python's `logging` module for structured logs
3. **Idempotency**: Ensure functions can be safely retried
4. **Timeouts**: Configure appropriate timeout values in CDK
5. **Permissions**: Follow principle of least privilege for IAM roles

## Deployment

Lambda functions are automatically deployed by the CDK stack:

1. **Code Packaging**: CDK packages the Lambda code automatically
2. **Role Creation**: IAM roles are created with appropriate permissions
3. **Function Creation**: Functions are created with specified runtime and configuration
4. **Trigger Configuration**: Event triggers and schedules are configured

### Manual Deployment (Not Recommended)

If you need to manually update Lambda functions:

```bash
# Package function
zip function.zip lambda_functions.py

# Update function code
aws lambda update-function-code \
    --function-name OpenemrEcsStack-FunctionName \
    --zip-file fileb://function.zip \
    --region us-west-2
```

**Note**: Manual updates will be overwritten on next `cdk deploy`.

## Configuration

Lambda function configuration is defined in the CDK stack:

- **Runtime**: Python 3.14
- **Architecture**: ARM64 (Graviton) for cost efficiency
- **Memory**: Varies by function (typically 256-512 MB)
- **Timeout**: Varies by function (typically 5-15 minutes)
- **VPC Configuration**: Some functions run in VPC for resource access

## Monitoring

Lambda functions log to CloudWatch Logs:

- **Log Group**: `/aws/lambda/{StackName}-{FunctionName}`
- **Log Retention**: 7 days (configurable)
- **Metrics**: Automatic metrics available in CloudWatch

View logs:
```bash
aws logs tail /aws/lambda/OpenemrEcsStack-FunctionName --follow
```

## Troubleshooting

### Function Timeout

**Issue**: Lambda function times out before completing.

**Solutions**:
- Increase timeout in CDK configuration
- Optimize function code for performance
- Check for network connectivity issues (VPC functions)

### Permission Errors

**Issue**: Function fails with "Access Denied" errors.

**Solutions**:
- Verify IAM role has required permissions
- Check resource ARNs are correct
- Ensure VPC configuration allows access to resources

### Import Errors

**Issue**: Function fails with module import errors.

**Solutions**:
- Verify all dependencies are available in Lambda runtime
- Use Lambda Layers for custom dependencies
- Check Python version compatibility

## Related Documentation

- [openemr_ecs/stack.py](../openemr_ecs/stack.py) - CDK stack that deploys these functions
- [openemr_ecs/security.py](../openemr_ecs/security.py) - Security-related Lambda usage
- [openemr_ecs/compute.py](../openemr_ecs/compute.py) - Compute-related Lambda usage
- [AWS Lambda Documentation](https://docs.aws.amazon.com/lambda/) - Official AWS Lambda documentation

