"""Analytics infrastructure: SageMaker Studio, EMR Serverless, and data export functions."""

import hashlib
from typing import Optional

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_emrserverless as emrserverless
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sagemaker as sagemaker
from cdk_nag import NagSuppressions
from constructs import Construct

from .nag_suppressions import (
    suppress_lambda_common_findings,
    suppress_lambda_role_common_findings,
    suppress_sagemaker_role_findings,
    suppress_vpc_endpoint_security_group_findings,
)


class AnalyticsComponents:
    """Creates and manages serverless analytics infrastructure.

    This class handles:
    - SageMaker Studio domain and user profiles
    - EMR Serverless applications
    - S3 buckets for data exports
    - Lambda functions for RDS and EFS exports
    - IAM roles and policies for analytics
    - VPC endpoints for SageMaker
    """

    def __init__(self, scope: Construct):
        """Initialize analytics components.

        Args:
            scope: The CDK construct scope
        """
        self.scope = scope
        self.analytics_kms_key: Optional[kms.Key] = None
        self.export_bucket_rds: Optional[s3.Bucket] = None
        self.export_bucket_efs: Optional[s3.Bucket] = None
        self.sagemaker_api_interface_endpoint: Optional[ec2.InterfaceVpcEndpoint] = None
        self.sagemaker_runtime_interface_endpoint: Optional[ec2.InterfaceVpcEndpoint] = None

    def create_serverless_analytics_environment(
        self,
        vpc: ec2.Vpc,
        db_instance,
        ecs_cluster: ecs.Cluster,
        log_group,
        file_system_for_sites_folder: efs.FileSystem,
        efs_volume_configuration_for_sites_folder: ecs.EfsVolumeConfiguration,
        efs_only_security_group: ec2.SecurityGroup,
        openemr_version: str,
        container_port: int,
        emr_serverless_release_label: str,
        lambda_python_runtime: _lambda.Runtime,
        account: str,
        region: str,
        node_addr: str,
    ) -> dict:
        """Provision optional analytics tooling (SageMaker Studio, EMR Serverless, and exports).

        Args:
            vpc: The VPC for analytics resources
            db_instance: RDS database cluster
            ecs_cluster: ECS cluster for export tasks
            log_group: CloudWatch log group
            file_system_for_sites_folder: EFS file system for sites
            efs_volume_configuration_for_sites_folder: EFS volume configuration
            efs_only_security_group: Security group for EFS access
            openemr_version: OpenEMR container version
            container_port: Container port
            emr_serverless_release_label: EMR Serverless release label
            lambda_python_runtime: Lambda Python runtime
            account: AWS account ID
            region: AWS region
            node_addr: CDK node address for unique ID generation

        Returns:
            Dictionary with analytics resources
        """
        # Generate unique ID for naming (SageMaker requires "SageMaker" in names)
        # Note: MD5 is used only for non-cryptographic purposes (naming), not security
        unique_id = hashlib.md5(bytes(f"{node_addr}", "utf-8"), usedforsecurity=False).hexdigest().lower()[:18]

        # Create KMS key for analytics environment encryption
        # Set removal policy to DESTROY to schedule key deletion when stack is deleted
        # KMS keys have a mandatory 7-30 day waiting period before actual deletion
        self.analytics_kms_key = kms.Key(
            self.scope,
            "AnalyticsKmsKey",
            alias=f"AmazonSageMakerSMKMS{unique_id}{account}{region}",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
            pending_window=Duration.days(7),  # Minimum waiting period before key deletion
        )
        self.analytics_kms_key.grant_encrypt_decrypt(iam.ServicePrincipal(f"logs.{region}.amazonaws.com"))
        self.analytics_kms_key.grant_encrypt_decrypt(iam.ServicePrincipal("export.rds.amazonaws.com"))
        self.analytics_kms_key.grant_encrypt_decrypt(iam.ServicePrincipal("rds.amazonaws.com"))
        self.analytics_kms_key.grant_encrypt_decrypt(iam.ServicePrincipal("sagemaker.amazonaws.com"))
        self.analytics_kms_key.grant_encrypt_decrypt(iam.ServicePrincipal("elasticfilesystem.amazonaws.com"))

        # Create KMS policy statement for integration
        kms_policy_statement = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "kms:CreateGrant",
                "kms:ListGrants",
                "kms:RevokeGrant",
                "kms:GenerateDataKeyWithoutPlaintext",
                "kms:DescribeKey",
                "kms:RetireGrant",
            ],
            resources=[self.analytics_kms_key.key_arn],
        )

        # Create S3 bucket for RDS exports
        self.export_bucket_rds = s3.Bucket(
            self.scope,
            "S3ExportBucket",
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
            bucket_name=f"sagemaker-rds-export-{unique_id}-{account}-{region}",
            encryption_key=self.analytics_kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
        )

        # Add suppressions for RDS export bucket
        NagSuppressions.add_resource_suppressions(
            self.export_bucket_rds,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": "RDS export bucket is for analytics data - already has access logs via analytics_access_logs_bucket",
                },
                {
                    "id": "HIPAA.Security-S3BucketLoggingEnabled",
                    "reason": "RDS export bucket is for analytics data - already has access logs via analytics_access_logs_bucket",
                },
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": "RDS export bucket stores analytics exports - replication not required as data can be re-exported from source database",
                },
            ],
        )

        # Create S3 bucket for EFS exports
        self.export_bucket_efs = s3.Bucket(
            self.scope,
            "EFSExportBucket",
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
            bucket_name=f"sagemaker-efs-export-{unique_id}-{account}-{region}",
            encryption_key=self.analytics_kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
        )

        # Add suppressions for EFS export bucket
        NagSuppressions.add_resource_suppressions(
            self.export_bucket_efs,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": "EFS export bucket is for analytics data - already has access logs via analytics_access_logs_bucket",
                },
                {
                    "id": "HIPAA.Security-S3BucketLoggingEnabled",
                    "reason": "EFS export bucket is for analytics data - already has access logs via analytics_access_logs_bucket",
                },
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": "EFS export bucket stores analytics exports - replication not required as data can be re-exported from source EFS",
                },
            ],
        )

        # Get private subnet IDs
        private_subnets_ids = [ps.subnet_id for ps in vpc.private_subnets]

        # Create IAM role for Aurora database to export to S3
        aurora_s3_export_role = iam.Role(
            self.scope,
            "AuroraExportRole",
            assumed_by=iam.ServicePrincipal("export.rds.amazonaws.com"),
        )
        self.export_bucket_rds.grant_read_write(aurora_s3_export_role)
        aurora_s3_export_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonRDSDataFullAccess")
        )

        # Suppress Aurora export role findings
        NagSuppressions.add_resource_suppressions(
            aurora_s3_export_role,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AmazonRDSDataFullAccess is AWS managed policy required for RDS export functionality",
                    "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonRDSDataFullAccess"],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "S3 and KMS wildcard permissions required for RDS export operations",
                    "appliesTo": [
                        "Action::s3:Abort*",
                        "Action::s3:DeleteObject*",
                        "Action::s3:GetBucket*",
                        "Action::s3:GetObject*",
                        "Action::s3:List*",
                        "Action::kms:GenerateDataKey*",
                        "Action::kms:ReEncrypt*",
                        "Resource::<S3ExportBucket658E7E06.Arn>/*",
                    ],
                },
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy required for least-privilege RDS export permissions",
                },
            ],
            apply_to_children=True,
        )

        # Create IAM role for SageMaker (must start with "AmazonSageMaker")
        sagemaker_role = iam.Role(
            self.scope,
            "SageMakerExecutionRole",
            role_name=f"AmazonSageMakerSMRole{unique_id}{account}{region}",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
        )
        self.export_bucket_rds.grant_read_write(sagemaker_role)
        self.export_bucket_efs.grant_read_write(sagemaker_role)

        # Create EMR Serverless application
        stack_name = Stack.of(self.scope).stack_name
        emr_app = emrserverless.CfnApplication(
            self.scope,
            "EMRServerlessApp",
            release_label=emr_serverless_release_label,
            type="SPARK",
            name=f"{stack_name}-EMRServerlessApp",
        )

        # Create SageMaker Domain
        sagemaker_domain = sagemaker.CfnDomain(
            self.scope,
            "OpenEMRSagemakerDomain",
            auth_mode="IAM",
            kms_key_id=self.analytics_kms_key.key_id,
            default_user_settings=sagemaker.CfnDomain.UserSettingsProperty(
                execution_role=sagemaker_role.role_arn,
                r_studio_server_pro_app_settings=sagemaker.CfnDomain.RStudioServerProAppSettingsProperty(
                    access_status="ENABLED", user_group="R_STUDIO_ADMIN"
                ),
                sharing_settings=sagemaker.CfnDomain.SharingSettingsProperty(
                    notebook_output_option="Allowed", s3_kms_key_id=self.analytics_kms_key.key_id
                ),
            ),
            app_network_access_type="VpcOnly",
            default_space_settings=sagemaker.CfnDomain.DefaultSpaceSettingsProperty(
                execution_role=sagemaker_role.role_arn
            ),
            domain_name=f"{stack_name}-SageMakerDomain",
            vpc_id=vpc.vpc_id,
            subnet_ids=private_subnets_ids,
        )

        # Create task to sync EFS to S3
        sync_efs_to_s3_task = ecs.FargateTaskDefinition(
            self.scope,
            "SyncEFStoS3Task",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64),
        )

        sync_efs_to_s3_task.add_volume(
            name="SitesFolderVolume", efs_volume_configuration=efs_volume_configuration_for_sites_folder
        )

        # Script to sync EFS to S3
        command_array = [
            f"apk add --no-cache aws-cli && \
            aws s3 sync /var/www/localhost/htdocs/openemr/sites/ s3://{self.export_bucket_efs.bucket_name}"
        ]

        # Add container definition (this creates the execution role's DefaultPolicy)
        sync_efs_to_s3_container = sync_efs_to_s3_task.add_container(
            "AmazonLinuxContainer",
            logging=ecs.LogDriver.aws_logs(stream_prefix="ecs/efstos3", log_group=log_group),
            port_mappings=[ecs.PortMapping(container_port=container_port)],
            essential=True,
            container_name="openemr",
            entry_point=["/bin/sh", "-c"],
            command=command_array,
            image=ecs.ContainerImage.from_registry(f"openemr/openemr:{openemr_version}"),
        )

        # Suppress execution role inline policy (after container creates DefaultPolicy)
        NagSuppressions.add_resource_suppressions(
            sync_efs_to_s3_task.execution_role,
            [
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is generated by CDK for ECS task execution (CloudWatch Logs, ECR pull) - required for Fargate task execution",
                }
            ],
            apply_to_children=True,
        )

        # Create mount point for EFS (read-only for export)
        efs_mount_point = ecs.MountPoint(
            container_path="/var/www/localhost/htdocs/openemr/sites/", read_only=True, source_volume="SitesFolderVolume"
        )
        sync_efs_to_s3_container.add_mount_points(efs_mount_point)

        # Create Lambda for EFS to S3 export
        export_efs_to_s3_lambda = _lambda.Function(
            self.scope,
            "EFStoS3ExportLambda",
            runtime=lambda_python_runtime,
            code=_lambda.Code.from_asset("lambda"),
            architecture=_lambda.Architecture.ARM_64,
            handler="lambda_functions.sync_efs_to_s3",
            timeout=Duration.minutes(10),
        )

        # Apply Lambda suppressions
        suppress_lambda_common_findings(
            export_efs_to_s3_lambda, vpc_required=False, reason_suffix="Triggers ECS task for EFS export"
        )
        suppress_lambda_role_common_findings(export_efs_to_s3_lambda.role, role_type="ecs_task")

        private_subnet_id_string = ",".join(private_subnets_ids)
        export_efs_to_s3_lambda.add_environment("ECS_CLUSTER", ecs_cluster.cluster_arn)
        export_efs_to_s3_lambda.add_environment("TASK_DEFINITION", sync_efs_to_s3_task.task_definition_arn)
        export_efs_to_s3_lambda.add_environment("SUBNETS", private_subnet_id_string)
        export_efs_to_s3_lambda.add_environment("SECURITY_GROUPS", efs_only_security_group.security_group_id)

        # Allow EFS connections:
        # EFS traffic is initiated by the client (task/Lambda ENI) to the EFS mount target.
        # It's sufficient to allow inbound 2049 on the EFS security group from this SG.
        # Avoid adding explicit egress rules here, since the SG defaults to allowAllOutbound=True
        # and CDK will warn that custom egress rules are ignored.
        file_system_for_sites_folder.connections.allow_default_port_from(efs_only_security_group)

        # Grant permissions (this creates DefaultPolicy for the Lambda and task role)
        self.export_bucket_efs.grant_read_write(sync_efs_to_s3_task.task_role)
        sync_efs_to_s3_task.task_role.add_to_principal_policy(kms_policy_statement)
        sync_efs_to_s3_task.grant_run(export_efs_to_s3_lambda.grant_principal)

        # Suppress task role inline policy (after grants create DefaultPolicy)
        NagSuppressions.add_resource_suppressions(
            sync_efs_to_s3_task.task_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard permissions for S3 sync operations (s3:GetBucket*, s3:GetObject*, s3:List*, s3:Abort*, s3:DeleteObject*, kms:GenerateDataKey*, kms:ReEncrypt*) and EFS bucket resource (/*) are required for EFS to S3 sync functionality",
                    "appliesTo": [
                        "Action::s3:Abort*",
                        "Action::s3:DeleteObject*",
                        "Action::s3:GetBucket*",
                        "Action::s3:GetObject*",
                        "Action::s3:List*",
                        "Resource::<EFSExportBucketB8FC2AFD.Arn>/*",
                        "Action::kms:GenerateDataKey*",
                        "Action::kms:ReEncrypt*",
                    ],
                },
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is generated by CDK for EFS sync task (S3, KMS permissions) - required for data export functionality",
                },
            ],
            apply_to_children=True,
        )

        # Add inline policy suppression for EFS export Lambda (after grants create DefaultPolicy)
        NagSuppressions.add_resource_suppressions(
            export_efs_to_s3_lambda.role.node.find_child("DefaultPolicy").node.find_child("Resource"),
            [
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is generated by CDK for ECS task execution permissions - required for Lambda to trigger EFS sync task",
                }
            ],
        )

        # Grant RDS export access to S3 bucket
        self.export_bucket_rds.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["s3:*"],
                resources=[self.export_bucket_rds.bucket_arn, f"{self.export_bucket_rds.bucket_arn}/*"],
                principals=[iam.ServicePrincipal("export.rds.amazonaws.com")],
            )
        )
        self.export_bucket_rds.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["s3:*"],
                resources=[self.export_bucket_rds.bucket_arn, f"{self.export_bucket_rds.bucket_arn}/*"],
                principals=[iam.ArnPrincipal(aurora_s3_export_role.role_arn)],
            )
        )

        # Create Lambda for RDS to S3 export
        export_rds_to_s3_lambda = _lambda.Function(
            self.scope,
            "RDStoS3ExportLambda",
            runtime=lambda_python_runtime,
            code=_lambda.Code.from_asset("lambda"),
            architecture=_lambda.Architecture.ARM_64,
            handler="lambda_functions.export_from_rds_to_s3",
            timeout=Duration.minutes(10),
        )

        # Apply Lambda suppressions
        suppress_lambda_common_findings(
            export_rds_to_s3_lambda, vpc_required=False, reason_suffix="Triggers RDS snapshot export"
        )
        suppress_lambda_role_common_findings(export_rds_to_s3_lambda.role, role_type="basic")

        # Grant KMS permissions (this creates DefaultPolicy for the Lambda)
        self.analytics_kms_key.grant_encrypt_decrypt(export_rds_to_s3_lambda.grant_principal)
        self.analytics_kms_key.grant_encrypt_decrypt(aurora_s3_export_role)
        self.analytics_kms_key.grant_encrypt_decrypt(sagemaker_role)
        self.analytics_kms_key.grant_encrypt_decrypt(sync_efs_to_s3_task.task_role)

        # Add inline policy suppression for RDS export Lambda (after grants create DefaultPolicy)
        NagSuppressions.add_resource_suppressions(
            export_rds_to_s3_lambda.role.node.find_child("DefaultPolicy").node.find_child("Resource"),
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard permissions for KMS operations (kms:GenerateDataKey*, kms:ReEncrypt*) are required for RDS snapshot export encryption",
                    "appliesTo": ["Action::kms:GenerateDataKey*", "Action::kms:ReEncrypt*"],
                },
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is generated by CDK for RDS export permissions (KMS, RDS) - required for Lambda to trigger database snapshot export",
                },
            ],
        )

        # Add environment variables
        export_rds_to_s3_lambda.add_environment("DB_CLUSTER_ARN", db_instance.cluster_arn)
        export_rds_to_s3_lambda.add_environment("KMS_KEY_ID", self.analytics_kms_key.key_id)
        export_rds_to_s3_lambda.add_environment("S3_BUCKET_NAME", self.export_bucket_rds.bucket_name)
        export_rds_to_s3_lambda.add_environment("EXPORT_ROLE_ARN", aurora_s3_export_role.role_arn)

        # Grant RDS export permissions
        export_rds_to_s3_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["rds:StartExportTask", "rds:DescribeDBSnapshots", "rds:DescribeExportTasks"],
                resources=[db_instance.cluster_arn],
            )
        )
        export_rds_to_s3_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW, actions=["iam:PassRole"], resources=[aurora_s3_export_role.role_arn]
            )
        )
        export_rds_to_s3_lambda.add_to_role_policy(kms_policy_statement)

        # Grant Lambda invoke permissions
        export_efs_to_s3_lambda.grant_invoke(sagemaker_role)
        export_rds_to_s3_lambda.grant_invoke(sagemaker_role)

        # Apply SageMaker role suppressions after all grants create DefaultPolicy
        suppress_sagemaker_role_findings(sagemaker_role)
        NagSuppressions.add_resource_suppressions(
            sagemaker_role.node.find_child("DefaultPolicy").node.find_child("Resource"),
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard permissions are necessary for SageMaker role to access S3 buckets and invoke Lambda functions for analytics operations",
                    "appliesTo": [
                        "Resource::<EFSExportBucketB8FC2AFD.Arn>/*",
                        "Resource::<S3ExportBucket658E7E06.Arn>/*",
                        "Resource::<EFStoS3ExportLambda9ED3EC88.Arn>:*",
                        "Resource::<RDStoS3ExportLambda651B6E3D.Arn>:*",
                    ],
                },
            ],
        )

        # Create SageMaker user profile
        sagemaker_user = sagemaker.CfnUserProfile(
            self.scope,
            "SagemakerUserProfile",
            domain_id=sagemaker_domain.attr_domain_id,
            user_profile_name=f"{stack_name}-AnalyticsUser",
            user_settings=sagemaker.CfnUserProfile.UserSettingsProperty(
                security_groups=[efs_only_security_group.security_group_id],
                execution_role=sagemaker_role.role_arn,
                sharing_settings=sagemaker.CfnUserProfile.SharingSettingsProperty(
                    notebook_output_option="Allowed", s3_kms_key_id=self.analytics_kms_key.key_id
                ),
                r_studio_server_pro_app_settings=sagemaker.CfnUserProfile.RStudioServerProAppSettingsProperty(
                    access_status="ENABLED", user_group="R_STUDIO_ADMIN"
                ),
            ),
        )

        # Create IAM role for Glue (must start with "AmazonSageMaker")
        glue_role = iam.Role(
            self.scope,
            "GlueRoleForEMRServerless",
            role_name=f"AmazonSageMakerGlueRole{unique_id}{account}{region}",
            assumed_by=iam.ServicePrincipal("emr-serverless.amazonaws.com"),
            description="IAM Role with Glue permissions for EMR Serverless",
        )
        glue_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole"))
        glue_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:CreateDatabase",
                    "glue:GetDataBases",
                    "glue:CreateTable",
                    "glue:GetTable",
                    "glue:UpdateTable",
                    "glue:DeleteTable",
                    "glue:GetTables",
                    "glue:GetPartition",
                    "glue:GetPartitions",
                    "glue:CreatePartition",
                    "glue:BatchCreatePartition",
                    "glue:GetUserDefinedFunctions",
                ],
                resources=["*"],
            )
        )
        self.export_bucket_rds.grant_read_write(glue_role)
        self.export_bucket_efs.grant_read_write(glue_role)

        # Add suppressions for Glue role (AWS managed policy + inline policy for Glue operations)
        NagSuppressions.add_resource_suppressions(
            glue_role,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWS Glue service requires AWSGlueServiceRole managed policy for EMR Serverless integration",
                    "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSGlueServiceRole"],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard permissions for Glue data catalog operations (Resource::*) and S3 bucket access are required for EMR Serverless data processing and analytics workflows",
                    "appliesTo": [
                        "Resource::*",
                        "Action::s3:Abort*",
                        "Action::s3:DeleteObject*",
                        "Action::s3:GetBucket*",
                        "Action::s3:GetObject*",
                        "Action::s3:List*",
                        "Resource::<EFSExportBucketB8FC2AFD.Arn>/*",
                        "Resource::<S3ExportBucket658E7E06.Arn>/*",
                        "Action::kms:GenerateDataKey*",
                        "Action::kms:ReEncrypt*",
                    ],
                },
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is required for Glue data catalog permissions and S3 bucket access - provides least-privilege access for EMR Serverless analytics",
                },
            ],
            apply_to_children=True,
        )

        # Add SageMaker managed policies
        sagemaker_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFullAccess"))
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerClusterInstanceRolePolicy")
        )
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFeatureStoreAccess")
        )
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerModelGovernanceUseAccess")
        )
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerModelRegistryFullAccess")
        )
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerGroundTruthExecution")
        )
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerPipelinesIntegrations")
        )
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerCanvasFullAccess")
        )

        # Create comprehensive policy for EMR Serverless integration
        policy_statements = [
            # EMR Serverless application access
            iam.PolicyStatement(
                actions=[
                    "emr-serverless:StartApplication",
                    "emr-serverless:StopApplication",
                    "emr-serverless:UpdateApplication",
                    "emr-serverless:RunJob",
                    "emr-serverless:CancelJobRun",
                    "emr-serverless:GetJobRun",
                    "emr-serverless:GetApplication",
                    "emr-serverless:AccessLivyEndpoints",
                    "emr-serverless:GetDashboardForJobRun",
                ],
                effect=iam.Effect.ALLOW,
                resources=[f"arn:aws:emr-serverless:{region}:{account}:applications/{emr_app.ref}"],
            ),
            # List applications
            iam.PolicyStatement(
                sid="EMRServerlessUnTaggedActions",
                effect=iam.Effect.ALLOW,
                actions=["emr-serverless:ListApplications"],
                resources=[f"arn:aws:emr-serverless:{region}:{account}:/*"],
            ),
            # Pass role to EMR Serverless
            iam.PolicyStatement(
                sid="EMRServerlessPassRole",
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[glue_role.role_arn],
                conditions={
                    "StringLike": {
                        "iam:PassedToService": "emr-serverless.amazonaws.com",
                    }
                },
            ),
            # Create and tag EMR Serverless applications
            iam.PolicyStatement(
                sid="EMRServerlessCreateApplicationAction",
                effect=iam.Effect.ALLOW,
                actions=["emr-serverless:CreateApplication", "emr-serverless:TagResource"],
                resources=[f"arn:aws:emr-serverless:{region}:{account}:/*"],
                conditions={
                    "ForAllValues:StringEquals": {
                        "aws:TagKeys": [
                            "sagemaker:domain-arn",
                            "sagemaker:user-profile-arn",
                            "sagemaker:space-arn",
                        ]
                    },
                    "Null": {
                        "aws:RequestTag/sagemaker:domain-arn": "false",
                        "aws:RequestTag/sagemaker:user-profile-arn": "false",
                        "aws:RequestTag/sagemaker:space-arn": "false",
                    },
                },
            ),
            # Restrictive tagging policy
            iam.PolicyStatement(
                sid="EMRServerlessDenyPermissiveTaggingAction",
                effect=iam.Effect.DENY,
                actions=["emr-serverless:TagResource", "emr-serverless:UntagResource"],
                resources=[f"arn:aws:emr-serverless:{region}:{account}:/*"],
                conditions={
                    "Null": {
                        "aws:ResourceTag/sagemaker:domain-arn": "true",
                        "aws:ResourceTag/sagemaker:user-profile-arn": "true",
                        "aws:ResourceTag/sagemaker:space-arn": "true",
                    },
                },
            ),
            # Additional EMR Serverless actions
            iam.PolicyStatement(
                sid="EMRServerlessActions",
                effect=iam.Effect.ALLOW,
                actions=[
                    "emr-serverless:StartApplication",
                    "emr-serverless:StopApplication",
                    "emr-serverless:GetApplication",
                    "emr-serverless:DeleteApplication",
                    "emr-serverless:AccessLivyEndpoints",
                    "emr-serverless:GetDashboardForJobRun",
                ],
                resources=[f"arn:aws:emr-serverless:{region}:{account}:/applications/*"],
                conditions={
                    "Null": {
                        "aws:ResourceTag/sagemaker:domain-arn": "false",
                        "aws:ResourceTag/sagemaker:user-profile-arn": "false",
                        "aws:ResourceTag/sagemaker:space-arn": "false",
                    },
                },
            ),
            # ECR access for custom container images
            iam.PolicyStatement(
                sid="ECRRepositoryListGetPolicy",
                effect=iam.Effect.ALLOW,
                actions=["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:DescribeImages"],
                resources=[f"arn:aws:ecr:*:{account}:*/*"],
            ),
            # Monitor RDS export tasks
            iam.PolicyStatement(
                sid="RDSMonitorExportTasks",
                effect=iam.Effect.ALLOW,
                actions=["rds:DescribeExportTasks"],
                resources=[db_instance.cluster_arn],
            ),
            # Describe ECS tasks for EFS export
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["ecs:DescribeTasks"],
                resources=["*"],
                conditions={"ArnEquals": {"ecs:TaskArn": sync_efs_to_s3_task.task_definition_arn}},
            ),
            # KMS permissions
            kms_policy_statement,
        ]

        # Create and attach policy
        policy = iam.Policy(
            self.scope,
            "EMRServerlessPolicy",
            policy_name="EMRServerlessPolicy",
            statements=policy_statements,
        )
        policy.attach_to_role(sagemaker_role)

        # Add suppressions for EMRServerless policy
        # Pattern matches the format CDK Nag sees: region-specific with <AWS::AccountId> placeholder
        NagSuppressions.add_resource_suppressions(
            policy,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard permissions for EMR Serverless applications, ECR images, and ECS tasks are required for SageMaker Studio data science workflows",
                    "appliesTo": [
                        f"Resource::arn:aws:emr-serverless:{region}:<AWS::AccountId>:/*",
                        f"Resource::arn:aws:emr-serverless:{region}:<AWS::AccountId>:/applications/*",
                        "Resource::arn:aws:ecr:*:<AWS::AccountId>:*/*",
                        "Resource::*",
                    ],
                },
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is required for EMR Serverless and data science workflow permissions - provides least-privilege access for SageMaker Studio",
                },
            ],
        )

        # Create SageMaker VPC Endpoints
        self.sagemaker_api_interface_endpoint = vpc.add_interface_endpoint(
            "sagemaker_api_interface_endpoint",
            private_dns_enabled=True,
            service=ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_API,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )
        # Suppress false positives for SageMaker API endpoint security group
        for sg in self.sagemaker_api_interface_endpoint.connections.security_groups:
            suppress_vpc_endpoint_security_group_findings(sg, "SageMaker API")

        self.sagemaker_runtime_interface_endpoint = vpc.add_interface_endpoint(
            "sagemaker_runtime_interface_endpoint",
            private_dns_enabled=True,
            service=ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_RUNTIME,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )
        # Suppress false positives for SageMaker Runtime endpoint security group
        for sg in self.sagemaker_runtime_interface_endpoint.connections.security_groups:
            suppress_vpc_endpoint_security_group_findings(sg, "SageMaker Runtime")

        return {
            "analytics_kms_key": self.analytics_kms_key,
            "export_bucket_rds": self.export_bucket_rds,
            "export_bucket_efs": self.export_bucket_efs,
            "sagemaker_domain": sagemaker_domain,
            "sagemaker_user": sagemaker_user,
            "emr_app": emr_app,
        }
