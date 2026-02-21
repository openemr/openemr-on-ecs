"""Storage infrastructure: EFS file systems, S3 buckets, and backup configuration."""

from typing import Optional

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_backup as backup
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from cdk_nag import NagSuppressions
from constructs import Construct

from .utils import get_resource_suffix


class StorageComponents:
    """Creates and manages storage infrastructure.

    This class handles:
    - EFS file systems for shared storage
    - S3 buckets for logs and backups
    - AWS Backup configuration
    - CloudTrail logging (optional)
    """

    def __init__(self, scope: Construct):
        """Initialize storage components.

        Args:
            scope: The CDK construct scope
        """
        self.scope = scope
        self.elb_log_bucket: Optional[s3.Bucket] = None
        self.cloudtrail_log_bucket: Optional[s3.Bucket] = None
        self.cloudtrail_kms_key: Optional[kms.Key] = None
        self.trail: Optional[cloudtrail.Trail] = None
        self.file_system_for_sites_folder: Optional[efs.FileSystem] = None
        self.file_system_for_ssl_folder: Optional[efs.FileSystem] = None
        self.efs_volume_configuration_for_sites_folder: Optional[ecs.EfsVolumeConfiguration] = None
        self.efs_volume_configuration_for_ssl_folder: Optional[ecs.EfsVolumeConfiguration] = None
        self.backup_vault: Optional[backup.BackupVault] = None

    def create_elb_log_bucket(self) -> s3.Bucket:
        """Create S3 bucket for Application Load Balancer access logs.

        Note: ALB access logging does not support KMS encryption, only SSE-S3.
        This is an AWS service limitation.

        Returns:
            The created S3 bucket
        """
        # Create server access log bucket first (also SSE-S3 since it's for ALB logs)
        elb_access_log_bucket = s3.Bucket(
            self.scope,
            "elb-access-logs-bucket",
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,  # ALB requires SSE-S3, not KMS
            enforce_ssl=True,
            versioned=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,  # Modern alternative to AccessControl
        )

        # Suppress replication and KMS requirements for access log bucket
        NagSuppressions.add_resource_suppressions(
            elb_access_log_bucket,
            [
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": "Server access logs bucket does not require replication - logs are for debugging only",
                },
                {
                    "id": "AwsSolutions-S1",
                    "reason": "This is the access logs bucket - enabling logs on the logs bucket would create circular dependency",
                },
                {
                    "id": "HIPAA.Security-S3BucketLoggingEnabled",
                    "reason": "This is the access logs bucket - enabling logs on the logs bucket would create circular dependency",
                },
                {
                    "id": "HIPAA.Security-S3BucketSSEEnabled",
                    "reason": "ALB access logging requires S3-managed encryption (SSE-S3), not KMS - this is an AWS service limitation",
                },
                {
                    "id": "HIPAA.Security-S3DefaultEncryptionKMS",
                    "reason": "ALB access logging requires S3-managed encryption (SSE-S3), not KMS - this is an AWS service limitation",
                },
                {
                    "id": "AwsSolutions-S3",
                    "reason": "ALB access logging requires S3-managed encryption (SSE-S3), not KMS - this is an AWS service limitation",
                },
            ],
        )

        # Now create the main ELB logs bucket with server access logging (also SSE-S3)
        self.elb_log_bucket = s3.Bucket(
            self.scope,
            "elb-logs-bucket",
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,  # ALB requires SSE-S3, not KMS
            enforce_ssl=True,
            versioned=True,
            server_access_logs_bucket=elb_access_log_bucket,
            server_access_logs_prefix="elb-logs-access/",
        )

        # Suppress replication and KMS requirements for ELB logs (ALB limitation)
        NagSuppressions.add_resource_suppressions(
            self.elb_log_bucket,
            [
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": "ELB logs bucket does not require replication - ALB logs are generated continuously and not critical for recovery",
                },
                {
                    "id": "HIPAA.Security-S3BucketSSEEnabled",
                    "reason": "ALB access logging requires S3-managed encryption (SSE-S3), not KMS - this is an AWS service limitation",
                },
                {
                    "id": "HIPAA.Security-S3DefaultEncryptionKMS",
                    "reason": "ALB access logging requires S3-managed encryption (SSE-S3), not KMS - this is an AWS service limitation",
                },
                {
                    "id": "AwsSolutions-S3",
                    "reason": "ALB access logging requires S3-managed encryption (SSE-S3), not KMS - this is an AWS service limitation",
                },
            ],
        )

        policy_statement = iam.PolicyStatement(
            actions=["s3:PutObject"],
            resources=[f"{self.elb_log_bucket.bucket_arn}/*"],
            principals=[iam.ArnPrincipal(f"arn:aws:iam::{Stack.of(self.scope).account}:root")],
        )

        self.elb_log_bucket.add_to_resource_policy(policy_statement)

        return self.elb_log_bucket

    def create_cloudtrail_logging(self, region: str) -> tuple:
        """Create CloudTrail logging infrastructure (optional).

        Args:
            region: AWS region for service principal

        Returns:
            Tuple of (cloudtrail_log_bucket, cloudtrail_kms_key, trail) or None if disabled
        """
        # CloudTrail is optional based on context
        # This will be called conditionally from the main stack

        # Create KMS key for CloudTrail encryption
        # Set removal policy to DESTROY to schedule key deletion when stack is deleted
        # KMS keys have a mandatory 7-30 day waiting period before actual deletion
        self.cloudtrail_kms_key = kms.Key(
            self.scope,
            "CloudtrailKmsKey",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
            pending_window=Duration.days(7),  # Minimum waiting period before key deletion
        )
        self.cloudtrail_kms_key.grant_encrypt_decrypt(iam.ServicePrincipal(f"logs.{region}.amazonaws.com"))
        self.cloudtrail_kms_key.grant_encrypt_decrypt(iam.ServicePrincipal("s3.amazonaws.com"))
        self.cloudtrail_kms_key.grant_encrypt_decrypt(iam.ServicePrincipal("cloudtrail.amazonaws.com"))

        # Create access logs bucket first
        cloudtrail_access_log_bucket = s3.Bucket(
            self.scope,
            "CloudTrailAccessLogBucket",
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.cloudtrail_kms_key,
            enforce_ssl=True,
            versioned=True,
        )

        # Suppress findings for access log bucket
        NagSuppressions.add_resource_suppressions(
            cloudtrail_access_log_bucket,
            [
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": "Server access logs bucket does not require replication",
                },
                {
                    "id": "AwsSolutions-S1",
                    "reason": "This is the access logs bucket - circular dependency",
                },
                {
                    "id": "HIPAA.Security-S3BucketLoggingEnabled",
                    "reason": "This is the access logs bucket - circular dependency",
                },
            ],
        )

        # Create S3 bucket for CloudTrail logs
        self.cloudtrail_log_bucket = s3.Bucket(
            self.scope,
            "CloudTrailLogBucket",
            versioned=True,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.cloudtrail_kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            server_access_logs_bucket=cloudtrail_access_log_bucket,
            server_access_logs_prefix="cloudtrail-access/",
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Suppress replication for CloudTrail (data is immutable audit log)
        NagSuppressions.add_resource_suppressions(
            self.cloudtrail_log_bucket,
            [
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": "CloudTrail logs are immutable audit logs with 7-year retention - replication not required as CloudTrail continuously generates new logs",
                },
            ],
        )

        # Add lifecycle policy to retain logs for 7 years
        self.cloudtrail_log_bucket.add_lifecycle_rule(
            id="Retain7Years",
            enabled=True,
            expiration=Duration.days(7 * 365),  # Expire objects after 7 years
            transitions=[
                s3.Transition(
                    storage_class=s3.StorageClass.GLACIER,
                    transition_after=Duration.days(90),  # Move to Glacier after 90 days
                )
            ],
        )

        # Get KMS key for CloudWatch Logs encryption
        logs_kms_key = self.scope.kms_keys.central_key

        # Create CloudTrail trail with CloudWatch Logs
        self.trail = cloudtrail.Trail(
            self.scope,
            "OpenEMRCloudTrail",
            bucket=self.cloudtrail_log_bucket,
            encryption_key=self.cloudtrail_kms_key,
            include_global_service_events=True,
            send_to_cloud_watch_logs=True,
            cloud_watch_logs_retention=logs.RetentionDays.NINE_YEARS,
            cloud_watch_log_group=logs.LogGroup(
                self.scope,
                "CloudTrailLogGroup",
                encryption_key=logs_kms_key,
                retention=logs.RetentionDays.NINE_YEARS,
            ),
            management_events=cloudtrail.ReadWriteType.ALL,
        )

        # Suppress inline policy warning for CloudTrail LogsRole (CDK-generated)
        cloudtrail_log_role = self.trail.node.find_child("LogsRole")
        if cloudtrail_log_role:
            logs_role_policy = cloudtrail_log_role.node.try_find_child("DefaultPolicy")
            if logs_role_policy:
                NagSuppressions.add_resource_suppressions(
                    logs_role_policy,
                    [
                        {
                            "id": "HIPAA.Security-IAMNoInlinePolicy",
                            "reason": "Inline policy is generated by CDK for CloudTrail CloudWatch Logs integration - required for trail functionality",
                        }
                    ],
                )

        # Grant CloudTrail permissions to write to the bucket
        self.cloudtrail_log_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:GetBucketAcl"],
                resources=[
                    self.cloudtrail_log_bucket.arn_for_objects("*"),
                    self.cloudtrail_log_bucket.bucket_arn,
                ],
                principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
            )
        )

        return (self.cloudtrail_log_bucket, self.cloudtrail_kms_key, self.trail)

    def create_efs_volumes(self, vpc: ec2.Vpc, context: dict) -> tuple:
        """Create EFS file systems for sites and SSL certificates.

        Args:
            vpc: The VPC to create EFS in
            context: CDK context dictionary

        Returns:
            Tuple of (sites_efs, ssl_efs, sites_volume_config, ssl_volume_config)
        """
        suffix = get_resource_suffix(context)

        # Create EFS for sites folder (shared OpenEMR data)
        # Create security group first with allow_all_outbound=False for custom egress rules
        sites_efs_sg = ec2.SecurityGroup(
            self.scope,
            f"EfsForSitesSecurityGroup-{suffix}",
            vpc=vpc,
            description="Security group for sites EFS",
            allow_all_outbound=False,
        )
        self.file_system_for_sites_folder = efs.FileSystem(
            self.scope,
            f"EfsForSites-{suffix}",
            vpc=vpc,
            encrypted=True,
            removal_policy=RemovalPolicy.DESTROY,
            security_group=sites_efs_sg,
        )

        # Create EFS volume configuration for sites folder
        self.efs_volume_configuration_for_sites_folder = ecs.EfsVolumeConfiguration(
            file_system_id=self.file_system_for_sites_folder.file_system_id, transit_encryption="ENABLED"
        )

        # Create EFS for SSL folder (shared SSL certificates)
        # Create security group first with allow_all_outbound=False for custom egress rules
        ssl_efs_sg = ec2.SecurityGroup(
            self.scope,
            f"EfsForSslSecurityGroup-{suffix}",
            vpc=vpc,
            description="Security group for SSL EFS",
            allow_all_outbound=False,
        )
        self.file_system_for_ssl_folder = efs.FileSystem(
            self.scope,
            f"EfsForSsl-{suffix}",
            vpc=vpc,
            encrypted=True,
            removal_policy=RemovalPolicy.DESTROY,
            security_group=ssl_efs_sg,
        )

        # Create EFS volume configuration for SSL folder
        self.efs_volume_configuration_for_ssl_folder = ecs.EfsVolumeConfiguration(
            file_system_id=self.file_system_for_ssl_folder.file_system_id, transit_encryption="ENABLED"
        )

        return (
            self.file_system_for_sites_folder,
            self.file_system_for_ssl_folder,
            self.efs_volume_configuration_for_sites_folder,
            self.efs_volume_configuration_for_ssl_folder,
        )

    def create_backup_plan(self, db_instance, sites_efs, ssl_efs, context: dict) -> backup.BackupPlan:
        """Create AWS Backup plan for RDS and EFS resources.

        Creates a backup vault with DESTROY removal policy to ensure clean stack deletion.
        The vault will be deleted when the stack is destroyed (after recovery points are removed).

        Note: The backup vault can only be deleted if it's empty (no recovery points).
        Recovery points are automatically deleted when their retention period expires.

        Args:
            db_instance: The RDS database cluster
            sites_efs: EFS file system for sites
            ssl_efs: EFS file system for SSL certificates
            context: CDK context dictionary

        Returns:
            The created backup plan
        """
        suffix = get_resource_suffix(context)

        # Create a backup vault with DESTROY removal policy for clean stack deletion
        # Using a fixed name based on stack name ensures consistent naming across deployments
        self.backup_vault = backup.BackupVault(
            self.scope,
            f"BackupVault-{suffix}",
            backup_vault_name=f"{Stack.of(self.scope).stack_name}-vault-{suffix}",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Use the convenience method which creates daily, weekly, and monthly backups
        # with 7-year retention, but specify our custom vault
        plan = backup.BackupPlan.daily_weekly_monthly7_year_retention(
            self.scope, "Plan", backup_vault=self.backup_vault
        )

        # Apply DESTROY removal policy to the plan
        plan.apply_removal_policy(RemovalPolicy.DESTROY)

        # Add resources to backup
        # Enable restore permissions by setting allow_restores=True
        # Create IAM role for AWS Backup service
        backup_role = iam.Role(
            self.scope,
            "BackupServiceRole",
            assumed_by=iam.ServicePrincipal("backup.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSBackupServiceRolePolicyForBackup"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSBackupServiceRolePolicyForRestores"),
            ],
        )

        # Suppress AWS managed policy warnings for backup service role
        NagSuppressions.add_resource_suppressions(
            backup_role,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWS Backup service requires AWS managed policies (AWSBackupServiceRolePolicyForBackup and AWSBackupServiceRolePolicyForRestores) to function correctly",
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores",
                    ],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "AWS Backup service managed policies require wildcard permissions to backup and restore resources across services",
                    "appliesTo": ["Resource::*"],
                },
            ],
        )

        # Add resources to backup plan with the custom role
        plan.add_selection(
            "Resources",
            resources=[
                backup.BackupResource.from_rds_database_cluster(db_instance),
                backup.BackupResource.from_efs_file_system(ssl_efs),
                backup.BackupResource.from_efs_file_system(sites_efs),
            ],
            role=backup_role,  # Pass the role explicitly
            allow_restores=True,  # Enable restore permissions for the backup plan role
        )

        return plan
