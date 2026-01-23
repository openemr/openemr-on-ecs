"""Stack cleanup automation for reliable stack deletion.

This module provides automatic cleanup of resources that might block stack deletion,
such as RDS deletion protection, SES rule sets, and backup recovery points.
"""

from typing import Optional

from aws_cdk import (
    CustomResource,
    Duration,
)
from aws_cdk import aws_backup as backup
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_rds as rds
from aws_cdk import aws_ses as ses
from cdk_nag import NagSuppressions
from constructs import Construct


class CleanupComponents:
    """Automatic cleanup of resources during stack deletion.

    This class creates a custom resource that automatically handles cleanup
    of resources that might block stack deletion:
    - Disables RDS deletion protection
    - Deactivates SES rule sets
    - Deletes backup recovery points
    """

    def __init__(self, scope: Construct):
        """Initialize cleanup components.

        Args:
            scope: The CDK construct scope
        """
        self.scope = scope
        self.cleanup_lambda: Optional[_lambda.Function] = None
        self.cleanup_resource: Optional[CustomResource] = None

    def create_cleanup_resource(
        self,
        db_cluster: Optional[rds.DatabaseCluster],
        backup_vault: Optional[backup.BackupVault],
        ses_rule_set: Optional[ses.ReceiptRuleSet],
        stack_name: str,
        alb_arn: Optional[str] = None,
        sagemaker_domain_id: Optional[str] = None,
    ) -> CustomResource:
        """Create a custom resource that handles cleanup during stack deletion.

        This resource automatically:
        1. Disables RDS deletion protection (if enabled)
        2. Disables ALB deletion protection (if enabled)
        3. Deletes SES rule sets completely (deactivates, deletes rules, deletes rule set)
        4. Deletes all backup recovery points from the backup vault
        5. Cleans up SageMaker EFS file systems and ENIs

        Args:
            db_cluster: The RDS database cluster (optional)
            backup_vault: The backup vault (optional)
            ses_rule_set: The SES receipt rule set (optional, will be deleted by Lambda)
            stack_name: The CloudFormation stack name
            alb_arn: The ALB ARN (optional)
            sagemaker_domain_id: The SageMaker domain ID (optional)

        Returns:
            The cleanup custom resource
        """
        # Create Lambda function for cleanup operations
        self.cleanup_lambda = _lambda.Function(
            self.scope,
            "StackCleanupLambda",
            runtime=_lambda.Runtime.PYTHON_3_14,
            handler="index.handler",
            timeout=Duration.minutes(15),  # Allow time for backup deletion
            code=_lambda.Code.from_inline("""
import boto3
import logging
import time
import urllib.request
import urllib.error
import json

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def send_response(event, context, response_status, response_data={}, physical_resource_id=None, reason=None):
    \"\"\"Send response to CloudFormation.\"\"\"
    response_url = event['ResponseURL']

    response_body = {
        'Status': response_status,
        'Reason': reason or f"See CloudWatch Log Stream: {context.log_stream_name}",
        'PhysicalResourceId': physical_resource_id or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': response_data
    }

    json_response_body = json.dumps(response_body).encode('utf-8')

    try:
        req = urllib.request.Request(
            response_url,
            data=json_response_body,
            headers={'Content-Type': '', 'Content-Length': str(len(json_response_body))},
            method='PUT'
        )
        with urllib.request.urlopen(req) as response:
            logger.info(f"Response sent successfully: {response.status}")
    except urllib.error.URLError as e:
        logger.error(f"Failed to send response: {str(e)}")

def handler(event, context):
    \"\"\"Handle stack cleanup during deletion.\"\"\"
    request_type = event.get('RequestType')
    props = event.get('ResourceProperties', {})

    stack_name = props.get('StackName')
    db_cluster_identifier = props.get('DbClusterIdentifier')
    backup_vault_name = props.get('BackupVaultName')
    ses_rule_set_name = props.get('SesRuleSetName')
    sagemaker_domain_id = props.get('SageMakerDomainId')

    logger.info(f"Cleanup handler called: {request_type}")
    logger.info(f"Stack: {stack_name}")

    try:
        if request_type == 'Delete':
            logger.info("Starting cleanup operations for stack deletion...")

            # 1. Disable RDS deletion protection
            if db_cluster_identifier:
                try:
                    rds_client = boto3.client('rds')
                    logger.info(f"Disabling deletion protection for DB cluster: {db_cluster_identifier}")
                    rds_client.modify_db_cluster(
                        DBClusterIdentifier=db_cluster_identifier,
                        DeletionProtection=False
                    )
                    logger.info("RDS deletion protection disabled successfully")
                except Exception as e:
                    # If cluster doesn't exist or is already being deleted, that's okay
                    logger.warning(f"Could not disable RDS deletion protection: {str(e)}")

            # 2. Disable ALB deletion protection
            alb_arn = props.get('AlbArn')
            if alb_arn:
                try:
                    elbv2_client = boto3.client('elbv2')
                    logger.info(f"Disabling deletion protection for ALB: {alb_arn}")
                    elbv2_client.modify_load_balancer_attributes(
                        LoadBalancerArn=alb_arn,
                        Attributes=[
                            {
                                'Key': 'deletion_protection.enabled',
                                'Value': 'false'
                            }
                        ]
                    )
                    logger.info("ALB deletion protection disabled successfully")
                except Exception as e:
                    # If ALB doesn't exist or is already being deleted, that's okay
                    logger.warning(f"Could not disable ALB deletion protection: {str(e)}")

            # 3. Delete SES rule set completely (don't let CloudFormation do it)
            if ses_rule_set_name:
                try:
                    ses_client = boto3.client('ses')
                    logger.info(f"Processing SES rule set: {ses_rule_set_name}")

                    # First check if it's the active rule set and deactivate if needed
                    try:
                        active_rule_set = ses_client.describe_active_receipt_rule_set()
                        active_name = active_rule_set.get('Metadata', {}).get('Name')

                        if active_name == ses_rule_set_name:
                            logger.info(f"SES rule set {ses_rule_set_name} is active, deactivating...")
                            ses_client.set_active_receipt_rule_set(RuleSetName='')
                            logger.info("SES rule set deactivated, waiting for propagation...")

                            # Wait up to 60 seconds for deactivation to propagate
                            max_wait_attempts = 12  # 60 seconds total
                            for attempt in range(max_wait_attempts):
                                time.sleep(5)
                                try:
                                    check = ses_client.describe_active_receipt_rule_set()
                                    check_name = check.get('Metadata', {}).get('Name')
                                    if not check_name or check_name != ses_rule_set_name:
                                        logger.info("SES rule set successfully deactivated and verified")
                                        break
                                except ses_client.exceptions.RuleSetDoesNotExistException:
                                    logger.info("No active rule set found - deactivation successful")
                                    break

                                if attempt < max_wait_attempts - 1:
                                    logger.info(f"Rule set still active, waiting... (attempt {attempt + 1}/{max_wait_attempts})")

                            # Additional wait for eventual consistency
                            time.sleep(10)
                        else:
                            logger.info(f"SES rule set {ses_rule_set_name} is not active (active: {active_name})")

                    except ses_client.exceptions.RuleSetDoesNotExistException:
                        logger.info("No active SES rule set found")

                    # Now delete the rule set entirely
                    # First, we need to delete all rules in the rule set
                    try:
                        logger.info(f"Deleting all rules from rule set {ses_rule_set_name}...")
                        rule_set_details = ses_client.describe_receipt_rule_set(RuleSetName=ses_rule_set_name)
                        rules = rule_set_details.get('Rules', [])

                        for rule in rules:
                            rule_name = rule['Name']
                            try:
                                logger.info(f"Deleting rule: {rule_name}")
                                ses_client.delete_receipt_rule(RuleSetName=ses_rule_set_name, RuleName=rule_name)
                            except Exception as e:
                                logger.warning(f"Could not delete rule {rule_name}: {str(e)}")

                        # Wait a moment for rule deletions to complete
                        if rules:
                            time.sleep(5)

                        # Now delete the rule set itself
                        logger.info(f"Deleting SES rule set: {ses_rule_set_name}")
                        ses_client.delete_receipt_rule_set(RuleSetName=ses_rule_set_name)
                        logger.info(f"SES rule set {ses_rule_set_name} deleted successfully")

                    except ses_client.exceptions.RuleSetDoesNotExistException:
                        logger.info(f"SES rule set {ses_rule_set_name} does not exist - already deleted")
                    except ses_client.exceptions.CannotDeleteException as e:
                        logger.error(f"Cannot delete SES rule set {ses_rule_set_name}: {str(e)}")
                        logger.error("Rule set may still be active - deactivation may not have propagated")
                        raise

                except Exception as e:
                    logger.warning(f"Could not process SES rule set {ses_rule_set_name}: {str(e)}")
                    # Don't raise - allow other cleanup to continue

            # 4. Delete backup recovery points
            if backup_vault_name:
                try:
                    backup_client = boto3.client('backup')
                    logger.info(f"Deleting recovery points from backup vault: {backup_vault_name}")

                    # List all recovery points in the vault
                    paginator = backup_client.get_paginator('list_recovery_points_by_backup_vault')
                    deleted_count = 0

                    for page in paginator.paginate(BackupVaultName=backup_vault_name):
                        for recovery_point in page.get('RecoveryPoints', []):
                            recovery_point_arn = recovery_point['RecoveryPointArn']
                            try:
                                logger.info(f"Deleting recovery point: {recovery_point_arn}")
                                backup_client.delete_recovery_point(
                                    BackupVaultName=backup_vault_name,
                                    RecoveryPointArn=recovery_point_arn
                                )
                                deleted_count += 1
                            except Exception as e:
                                # Some recovery points may be protected or already deleted
                                logger.warning(f"Could not delete recovery point {recovery_point_arn}: {str(e)}")

                    # Wait a bit for deletions to propagate
                    if deleted_count > 0:
                        logger.info(f"Deleted {deleted_count} recovery points, waiting for propagation...")
                        time.sleep(10)

                    logger.info("Backup recovery point cleanup completed")
                except Exception as e:
                    logger.warning(f"Could not delete backup recovery points: {str(e)}")

            # 5. Clean up SageMaker domain EFS file systems and ENIs
            if sagemaker_domain_id:
                try:
                    sagemaker_client = boto3.client('sagemaker')
                    ec2_client = boto3.client('ec2')
                    efs_client = boto3.client('efs')

                    logger.info(f"Cleaning up EFS file systems for SageMaker domain: {sagemaker_domain_id}")

                    # Get domain details to find VPC (may fail if domain is already deleted)
                    vpc_id = None
                    try:
                        domain_response = sagemaker_client.describe_domain(DomainId=sagemaker_domain_id)
                        vpc_id = domain_response.get('VpcId')
                        subnet_ids = domain_response.get('SubnetIds', [])
                        logger.info(f"SageMaker domain VPC: {vpc_id}, Subnets: {subnet_ids}")
                    except Exception as e:
                        logger.warning(f"Could not describe SageMaker domain (may be deleted): {str(e)}")
                        logger.info("Will still attempt to find and clean up EFS file systems by tags")

                    # Find EFS file systems associated with SageMaker domain
                    # SageMaker creates EFS with ManagedByAmazonSageMakerResource tag
                    file_systems = efs_client.describe_file_systems()
                    deleted_fs_count = 0
                    logger.info(f"Scanning {len(file_systems.get('FileSystems', []))} EFS file systems for SageMaker resources...")

                    for fs in file_systems.get('FileSystems', []):
                        fs_id = fs['FileSystemId']
                        try:
                            # Check tags to see if this EFS belongs to our SageMaker domain
                            tags_response = efs_client.describe_tags(FileSystemId=fs_id)
                            tags = {tag['Key']: tag['Value'] for tag in tags_response.get('Tags', [])}

                            # Log EFS details for debugging
                            logger.info(f"Checking EFS {fs_id}: VPC={fs.get('VpcId')}, Tags={list(tags.keys())}")

                            # Check for SageMaker EFS by tag
                            is_sagemaker_efs = False
                            match_reason = None

                            if 'ManagedByAmazonSageMakerResource' in tags:
                                sagemaker_resource_arn = tags.get('ManagedByAmazonSageMakerResource', '')
                                logger.info(f"EFS {fs_id} has ManagedByAmazonSageMakerResource tag: {sagemaker_resource_arn}")

                                # Check if domain ID is in the ARN (e.g., "d-xyz" in "arn:aws:sagemaker:region:account:domain/d-xyz")
                                if sagemaker_domain_id and sagemaker_domain_id in sagemaker_resource_arn:
                                    is_sagemaker_efs = True
                                    match_reason = f"Exact domain ID match in ARN: {sagemaker_resource_arn}"
                                # Also check if this is ANY SageMaker domain in the same VPC (fallback)
                                elif vpc_id and fs.get('VpcId') == vpc_id and 'sagemaker' in sagemaker_resource_arn.lower():
                                    is_sagemaker_efs = True
                                    match_reason = f"SageMaker resource in same VPC: {sagemaker_resource_arn}"
                                # Even if domain ID doesn't match, if it's a SageMaker domain ARN, log it
                                elif 'sagemaker' in sagemaker_resource_arn.lower():
                                    logger.info(f"Found SageMaker EFS {fs_id} but domain ID doesn't match: {sagemaker_resource_arn}")

                            # Additional fallback: Check by VPC and any SageMaker-related tags
                            if not is_sagemaker_efs and vpc_id and fs.get('VpcId') == vpc_id:
                                if any('sagemaker' in str(v).lower() for v in tags.values()):
                                    is_sagemaker_efs = True
                                    match_reason = f"VPC match with SageMaker tags: VPC={vpc_id}"

                            if is_sagemaker_efs:
                                logger.info(f"✓ Identified SageMaker EFS {fs_id} for deletion. Reason: {match_reason}")

                                # Delete mount targets first
                                mount_targets = efs_client.describe_mount_targets(FileSystemId=fs_id)
                                mt_deleted_count = 0
                                for mt in mount_targets.get('MountTargets', []):
                                    mt_id = mt['MountTargetId']
                                    try:
                                        logger.info(f"Deleting mount target: {mt_id}")
                                        efs_client.delete_mount_target(MountTargetId=mt_id)
                                        mt_deleted_count += 1
                                    except Exception as e:
                                        logger.warning(f"Could not delete mount target {mt_id}: {str(e)}")

                                # Wait for mount targets to be deleted
                                if mt_deleted_count > 0:
                                    logger.info(f"Waiting 45s for {mt_deleted_count} mount targets to be deleted for {fs_id}...")
                                    time.sleep(45)  # Increased from 30s to 45s

                                # Verify mount targets are gone before deleting file system
                                max_retries = 3
                                for retry in range(max_retries):
                                    try:
                                        remaining_mts = efs_client.describe_mount_targets(FileSystemId=fs_id)
                                        if not remaining_mts.get('MountTargets'):
                                            logger.info(f"All mount targets deleted for {fs_id}")
                                            break
                                        else:
                                            logger.info(f"Still waiting for mount targets... (attempt {retry + 1}/{max_retries})")
                                            time.sleep(15)
                                    except Exception as e:
                                        logger.info(f"Mount target check failed (may be deleted): {str(e)}")
                                        break

                                # Now delete the file system
                                try:
                                    logger.info(f"Deleting EFS file system: {fs_id}")

                                    # First, try to disable replication overwrite protection if enabled
                                    try:
                                        efs_client.put_file_system_protection(
                                            FileSystemId=fs_id,
                                            ReplicationOverwriteProtection='DISABLED'
                                        )
                                        logger.info(f"Disabled replication overwrite protection for {fs_id}")
                                    except Exception as e:
                                        # May not be enabled, or API may not be available
                                        logger.info(f"Could not disable replication protection (may not be enabled): {str(e)}")

                                    # Now attempt deletion
                                    efs_client.delete_file_system(FileSystemId=fs_id)
                                    logger.info(f"✓ EFS file system {fs_id} deletion initiated successfully")
                                    deleted_fs_count += 1
                                except Exception as e:
                                    logger.error(f"✗ Could not delete EFS {fs_id}: {str(e)}")

                        except Exception as e:
                            logger.warning(f"Error processing EFS {fs_id}: {str(e)}")

                    if deleted_fs_count > 0:
                        logger.info(f"Successfully initiated deletion of {deleted_fs_count} SageMaker EFS file systems")
                    else:
                        logger.info("No SageMaker EFS file systems found to delete")

                    # Clean up ENIs associated with SageMaker in the VPC
                    if vpc_id:
                        logger.info(f"Cleaning up SageMaker ENIs in VPC {vpc_id}...")
                        enis = ec2_client.describe_network_interfaces(
                            Filters=[
                                {'Name': 'vpc-id', 'Values': [vpc_id]},
                                {'Name': 'description', 'Values': ['*SageMaker*']},
                                {'Name': 'status', 'Values': ['available']}  # Only delete available ENIs
                            ]
                        )

                        for eni in enis.get('NetworkInterfaces', []):
                            eni_id = eni['NetworkInterfaceId']
                            try:
                                logger.info(f"Deleting ENI: {eni_id}")
                                ec2_client.delete_network_interface(NetworkInterfaceId=eni_id)
                            except Exception as e:
                                logger.warning(f"Could not delete ENI {eni_id}: {str(e)}")

                    logger.info("SageMaker EFS and ENI cleanup completed")

                except Exception as e:
                    logger.warning(f"Could not clean up SageMaker EFS: {str(e)}")

            logger.info("Cleanup operations completed successfully")

        # For Create and Update, just acknowledge success
        send_response(event, context, 'SUCCESS', {
            'Message': f'Cleanup resource {request_type}d successfully'
        })

    except Exception as e:
        logger.error(f"Cleanup failed: {str(e)}", exc_info=True)
        # On Delete, we still want to signal success to allow stack deletion to continue
        # Other resources will be cleaned up by CloudFormation itself
        if request_type == 'Delete':
            send_response(event, context, 'SUCCESS', {
                'Message': f'Cleanup attempted (some operations may have failed): {str(e)}'
            })
        else:
            send_response(event, context, 'FAILED', {}, reason=str(e))
"""),
        )

        # Add suppressions for cleanup Lambda (custom resource for stack deletion)
        # This Lambda requires broad permissions as it cleans up multiple resource types
        NagSuppressions.add_resource_suppressions(
            self.cleanup_lambda,
            [
                {
                    "id": "AwsSolutions-L1",
                    "reason": "Custom resource Lambda uses Python 3.14 runtime - latest available at time of writing",
                },
                {
                    "id": "HIPAA.Security-LambdaConcurrency",
                    "reason": "Cleanup Lambda is a one-time custom resource triggered only during stack deletion - concurrency limits not needed",
                },
                {
                    "id": "HIPAA.Security-LambdaDLQ",
                    "reason": "Cleanup Lambda is a one-time custom resource for stack deletion - failure is logged to CloudWatch, DLQ not needed",
                },
                {
                    "id": "HIPAA.Security-LambdaInsideVPC",
                    "reason": "Cleanup Lambda accesses AWS APIs (RDS, SageMaker, SES, EFS, ALB, EC2) - does not require VPC access",
                },
            ],
        )

        # Add suppressions for cleanup Lambda's IAM role
        NagSuppressions.add_resource_suppressions(
            self.cleanup_lambda.role,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWSLambdaBasicExecutionRole is an AWS managed policy and is acceptable for basic Lambda execution",
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
                    ],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Cleanup Lambda requires broad wildcard permissions (Resource::*) for multi-service cleanup operations (RDS, SageMaker, SES, EFS, ALB, EC2) during stack deletion",
                    "appliesTo": ["Resource::*"],
                },
            ],
            apply_to_children=True,
        )

        # Grant necessary permissions (creates DefaultPolicy)
        cleanup_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                # RDS permissions
                "rds:ModifyDBCluster",
                "rds:DescribeDBClusters",
                # ELB permissions (ALBv2)
                "elasticloadbalancing:ModifyLoadBalancerAttributes",
                "elasticloadbalancing:DescribeLoadBalancers",
                "elasticloadbalancingv2:ModifyLoadBalancerAttributes",
                "elasticloadbalancingv2:DescribeLoadBalancers",
                # SES permissions - need full CRUD for cleanup
                "ses:SetActiveReceiptRuleSet",
                "ses:DescribeActiveReceiptRuleSet",
                "ses:DescribeReceiptRuleSet",
                "ses:DeleteReceiptRule",
                "ses:DeleteReceiptRuleSet",
                # Backup permissions
                "backup:ListRecoveryPointsByBackupVault",
                "backup:DeleteRecoveryPoint",
                "backup:DescribeBackupVault",
                # SageMaker permissions
                "sagemaker:DescribeDomain",
                # EFS permissions
                "elasticfilesystem:DescribeFileSystems",
                "elasticfilesystem:DescribeTags",
                "elasticfilesystem:DescribeMountTargets",
                "elasticfilesystem:DeleteMountTarget",
                "elasticfilesystem:DeleteFileSystem",
                "elasticfilesystem:PutFileSystemProtection",
                # EC2 permissions for ENI cleanup
                "ec2:DescribeNetworkInterfaces",
                "ec2:DeleteNetworkInterface",
            ],
            resources=["*"],  # Required for SES and some RDS operations
        )
        self.cleanup_lambda.add_to_role_policy(cleanup_policy)

        # Suppress inline policy for DefaultPolicy (after grant creates it)
        NagSuppressions.add_resource_suppressions(
            self.cleanup_lambda.role.node.find_child("DefaultPolicy").node.find_child("Resource"),
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Cleanup Lambda requires broad wildcard permissions (Resource::*) for multi-service cleanup operations (RDS, SageMaker, SES, EFS, ALB, EC2) during stack deletion",
                    "appliesTo": ["Resource::*"],
                },
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is generated by CDK for cleanup Lambda permissions - provides least-privilege access for stack deletion operations",
                },
            ],
        )

        # Build properties for the custom resource
        properties = {"StackName": stack_name}

        if db_cluster:
            properties["DbClusterIdentifier"] = db_cluster.cluster_identifier
        if backup_vault:
            properties["BackupVaultName"] = backup_vault.backup_vault_name
        if ses_rule_set:
            properties["SesRuleSetName"] = ses_rule_set.receipt_rule_set_name
        if alb_arn:
            properties["AlbArn"] = alb_arn
        if sagemaker_domain_id:
            properties["SageMakerDomainId"] = sagemaker_domain_id

        # Create the custom resource
        self.cleanup_resource = CustomResource(
            self.scope, "StackCleanupResource", service_token=self.cleanup_lambda.function_arn, properties=properties
        )

        return self.cleanup_resource
