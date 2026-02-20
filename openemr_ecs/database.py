"""Database infrastructure: Aurora MySQL and ElastiCache Valkey clusters."""

from typing import Optional

from aws_cdk import (
    SecretValue,
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticache as elasticache
from aws_cdk import aws_iam as iam
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from cdk_nag import NagSuppressions
from constructs import Construct

from .nag_suppressions import suppress_vpc_endpoint_security_group_findings
from .utils import get_resource_suffix, is_true


class DatabaseComponents:
    """Creates and manages database and cache infrastructure.

    This class handles:
    - Aurora MySQL Serverless v2 cluster
    - ElastiCache Serverless Valkey (Redis-compatible) cluster
    - Database parameter groups with security settings
    - SSL/TLS configuration
    - Optional Bedrock integration for ML
    """

    def __init__(self, scope: Construct):
        """Initialize database components.

        Args:
            scope: The CDK construct scope
        """
        self.scope = scope
        self.db_instance: Optional[rds.DatabaseCluster] = None
        self.valkey_cluster: Optional[elasticache.CfnServerlessCache] = None
        self.db_secret: Optional[secretsmanager.Secret] = None
        self.valkey_endpoint: Optional[ssm.StringParameter] = None
        self.mysql_ssl_ca_variable: Optional[ssm.StringParameter] = None
        self.mysql_ssl_enabled_variable: Optional[ssm.StringParameter] = None
        self.php_valkey_tls_variable: Optional[ssm.StringParameter] = None
        self.rds_slot_secret: Optional[secretsmanager.Secret] = None

    def create_db_instance(
        self,
        vpc: ec2.Vpc,
        db_sec_group: ec2.SecurityGroup,
        aurora_mysql_engine_version: rds.AuroraMysqlEngineVersion,
        context: dict,
        region: str,
        account: str,
    ) -> rds.DatabaseCluster:
        """Create the Aurora MySQL Serverless v2 database cluster.

        Args:
            vpc: The VPC for the database
            db_sec_group: Security group for database access
            aurora_mysql_engine_version: Aurora MySQL engine version
            context: CDK context dictionary
            region: AWS region
            account: AWS account ID

        Returns:
            The created database cluster
        """
        # Create database secret with KMS encryption
        kms_key = self.scope.kms_keys.central_key

        self.db_secret = secretsmanager.Secret(
            self.scope,
            "db-secret",
            encryption_key=kms_key,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                include_space=False,
                secret_string_template='{"username": "dbadmin"}',
                generate_string_key="password",
            ),
        )

        # Suppress rotation warnings - Aurora manages credentials automatically
        NagSuppressions.add_resource_suppressions(
            self.db_secret,
            [
                {
                    "id": "AwsSolutions-SMG4",
                    "reason": "Database credentials are managed by Aurora Serverless v2 with automatic rotation through RDS",
                },
                {
                    "id": "HIPAA.Security-SecretsManagerRotationEnabled",
                    "reason": "Database credentials are managed by Aurora Serverless v2 with automatic rotation through RDS",
                },
            ],
        )

        db_credentials = rds.Credentials.from_secret(self.db_secret)

        # Database parameter configuration
        # Note: require_secure_transport=ON enforces SSL/TLS for all database connections.
        # This is a security best practice and is required for HIPAA compliance.
        # OpenEMR automatically uses SSL when the MySQL CA certificate is present.
        parameters = {
            "server_audit_logs_upload": "1",  # Upload audit logs to CloudWatch
            "log_queries_not_using_indexes": "1",  # Log slow queries for optimization
            "general_log": "1",  # Enable general query logging
            "slow_query_log": "1",  # Enable slow query logging
            "server_audit_logging": "1",  # Enable server audit logging
            "require_secure_transport": "ON",  # CRITICAL: Enforces SSL/TLS for all connections
            "server_audit_events": "CONNECT,QUERY,QUERY_DCL,QUERY_DDL,QUERY_DML,TABLE",  # Audit all database events
        }

        # Add timeout parameters from context (only if provided, to avoid None values)
        # None values in RDS parameter groups can cause deployment failures
        if context.get("net_read_timeout"):
            parameters["net_read_timeout"] = str(context.get("net_read_timeout"))
        if context.get("net_write_timeout"):
            parameters["net_write_timeout"] = str(context.get("net_write_timeout"))
        if context.get("wait_timeout"):
            parameters["wait_timeout"] = str(context.get("wait_timeout"))
        if context.get("connect_timeout"):
            parameters["connect_timeout"] = str(context.get("connect_timeout"))
        if context.get("max_execution_time"):
            parameters["max_execution_time"] = str(context.get("max_execution_time"))

        database_ml_role = None
        if is_true(context.get("enable_bedrock_integration")):
            database_ml_role = iam.Role(
                self.scope,
                "AuroraMLRole",
                assumed_by=iam.ServicePrincipal("rds.amazonaws.com"),
            )
            database_ml_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    resources=["arn:aws:bedrock:*::foundation-model/*"],
                )
            )
            parameters["aws_default_bedrock_role"] = database_ml_role.role_arn
            if context.get("aurora_ml_inference_timeout"):
                parameters["aurora_ml_inference_timeout"] = str(context.get("aurora_ml_inference_timeout"))

            # Suppress wildcard resource warnings for Bedrock foundation models
            NagSuppressions.add_resource_suppressions(
                database_ml_role,
                [
                    {
                        "id": "AwsSolutions-IAM5",
                        "reason": "Bedrock foundation model ARNs use wildcards by design - all models follow the pattern arn:aws:bedrock:*::foundation-model/*",
                        "appliesTo": ["Resource::arn:aws:bedrock:*::foundation-model/*"],
                    },
                    {
                        "id": "HIPAA.Security-IAMNoInlinePolicy",
                        "reason": "Inline policy is required for RDS Aurora ML integration with Bedrock - provides least-privilege access to foundation models",
                    },
                ],
                apply_to_children=True,
            )

        parameter_group = rds.ParameterGroup(
            self.scope,
            "ParameterGroup",
            engine=rds.DatabaseClusterEngine.aurora_mysql(version=aurora_mysql_engine_version),
            parameters=parameters,
        )

        # Deletion protection: on by default, but can be temporarily disabled (e.g., for cdk destroy) via context flag
        deletion_protection_enabled = is_true(context.get("rds_deletion_protection", "true"))
        if is_true(context.get("disable_rds_deletion_protection_on_destroy", "false")):
            deletion_protection_enabled = False

        # Get resource suffix for consistent naming
        suffix = get_resource_suffix(context)

        # Create database cluster
        # NOTE: When Performance Insights is enabled at the cluster level, CDK requires that
        # each instance's Performance Insights settings (enable + retention) match the cluster.
        # If we omit instance-level retention, the instance defaults to 7 days, which triggers
        # a synth-time ValidationError when the cluster is configured for LONG_TERM (731 days).

        if is_true(context.get("enable_data_api")):
            self.db_instance = rds.DatabaseCluster(
                self.scope,
                f"DatabaseCluster-{suffix}",
                engine=rds.DatabaseClusterEngine.aurora_mysql(version=aurora_mysql_engine_version),
                cluster_identifier=f"openemr-cluster-{suffix}",
                cloudwatch_logs_exports=["audit", "error", "general", "slowquery"],
                writer=rds.ClusterInstance.serverless_v2(
                    "writer",
                    enable_performance_insights=True,
                    performance_insight_retention=rds.PerformanceInsightRetention.LONG_TERM,
                ),
                enable_data_api=True,
                enable_performance_insights=True,
                performance_insight_retention=rds.PerformanceInsightRetention.LONG_TERM,
                serverless_v2_min_capacity=0.5,  # Minimum 0.5 ACU to prevent complete shutdown causing connection delays
                serverless_v2_max_capacity=256,
                storage_encrypted=True,
                parameter_group=parameter_group,
                credentials=db_credentials,
                readers=[
                    rds.ClusterInstance.serverless_v2(
                        "reader",
                        scale_with_writer=True,
                        enable_performance_insights=True,
                        performance_insight_retention=rds.PerformanceInsightRetention.LONG_TERM,
                    )
                ],
                security_groups=[db_sec_group],
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                vpc=vpc,
                deletion_protection=deletion_protection_enabled,
            )
        else:
            self.db_instance = rds.DatabaseCluster(
                self.scope,
                f"DatabaseCluster-{suffix}",
                engine=rds.DatabaseClusterEngine.aurora_mysql(version=aurora_mysql_engine_version),
                cluster_identifier=f"openemr-cluster-{suffix}",
                cloudwatch_logs_exports=["audit", "error", "general", "slowquery"],
                writer=rds.ClusterInstance.serverless_v2(
                    "writer",
                    enable_performance_insights=True,
                    performance_insight_retention=rds.PerformanceInsightRetention.LONG_TERM,
                ),
                enable_performance_insights=True,
                performance_insight_retention=rds.PerformanceInsightRetention.LONG_TERM,
                serverless_v2_min_capacity=0.5,  # Minimum 0.5 ACU to prevent complete shutdown causing connection delays
                serverless_v2_max_capacity=256,
                storage_encrypted=True,
                parameter_group=parameter_group,
                credentials=db_credentials,
                readers=[
                    rds.ClusterInstance.serverless_v2(
                        "reader",
                        scale_with_writer=True,
                        enable_performance_insights=True,
                        performance_insight_retention=rds.PerformanceInsightRetention.LONG_TERM,
                    )
                ],
                security_groups=[db_sec_group],
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                vpc=vpc,
                deletion_protection=deletion_protection_enabled,
            )

        # Add RDS suppressions for intentional configurations
        NagSuppressions.add_resource_suppressions(
            self.db_instance,
            [
                {
                    "id": "AwsSolutions-RDS6",
                    "reason": "IAM database authentication is not supported by OpenEMR (uses standard mysqli/PDO connections). Security is enforced through VPC isolation, security groups, SSL/TLS encryption, and Secrets Manager for credentials",
                },
                {
                    "id": "AwsSolutions-RDS10",
                    "reason": "Deletion protection is enabled by default and controlled via context flag for temporary disabling during stack destruction",
                },
                {
                    "id": "AwsSolutions-RDS11",
                    "reason": "Using standard MySQL port 3306 for compatibility with OpenEMR and existing tooling. Security is enforced through VPC isolation, security groups with least privilege, and SSL/TLS encryption",
                },
                {
                    "id": "AwsSolutions-RDS14",
                    "reason": "Backtrack is not supported for Aurora Serverless v2. Using AWS Backup with point-in-time recovery instead",
                },
                {
                    "id": "HIPAA.Security-RDSInBackupPlan",
                    "reason": "Database is backed up using AWS Backup with 7-year retention configured via BackupPlan in storage.py",
                },
                {
                    "id": "HIPAA.Security-RDSInstanceDeletionProtectionEnabled",
                    "reason": "Deletion protection is enabled by default via context flag. Can be temporarily disabled for stack destruction via cleanup Lambda",
                },
                {
                    "id": "HIPAA.Security-RDSEnhancedMonitoringEnabled",
                    "reason": "Enhanced monitoring is not available for Aurora Serverless v2. Using Performance Insights with LONG_TERM retention (731 days) and CloudWatch Logs (audit, error, general, slowquery) instead",
                },
            ],
            apply_to_children=True,
        )

        # Configure Bedrock integration if enabled
        if is_true(context.get("enable_bedrock_integration")) and database_ml_role:
            # Associate role with database cluster
            cfn_db_instance = self.db_instance.node.default_child
            cfn_db_instance.associated_roles = [  # type: ignore
                {
                    "featureName": "Bedrock",
                    "roleArn": database_ml_role.role_arn,
                },
            ]

            # Create VPC endpoints for Bedrock so we can use it from a private subnet
            # Create security group with allowAllOutbound=False to avoid CDK warning
            bedrock_sg = ec2.SecurityGroup(
                self.scope,
                "BedrockEndpointSecurityGroup",
                vpc=vpc,
                allow_all_outbound=False,
                description="Security group for Bedrock Runtime VPC endpoint",
            )

            # Suppress false positives for Bedrock endpoint security group
            suppress_vpc_endpoint_security_group_findings(bedrock_sg, "Bedrock Runtime")

            # Suppress CDK Nag validation failures for intrinsic function (database port)
            NagSuppressions.add_resource_suppressions(
                bedrock_sg,
                [
                    {
                        "id": "CdkNagValidationFailure",
                        "reason": "Security group port uses intrinsic function (database cluster endpoint port) which cannot be validated at synth time - port is determined at deployment",
                    },
                    {
                        "id": "HIPAA.Security-EC2RestrictedCommonPorts",
                        "reason": "Bedrock endpoint security group port (3306) is restricted to VPC resources only - false positive due to intrinsic function",
                    },
                    {
                        "id": "HIPAA.Security-EC2RestrictedSSH",
                        "reason": "Bedrock endpoint security group does not expose SSH (port 22) - false positive due to intrinsic function for database port",
                    },
                ],
                apply_to_children=True,
            )

            bedrock_runtime_interface_endpoint = vpc.add_interface_endpoint(
                "BedrockRuntimeEndpoint",
                private_dns_enabled=True,
                service=ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
                subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                security_groups=[bedrock_sg],
            )

            # Allow connections to and from the Bedrock endpoints to the database
            bedrock_runtime_interface_endpoint.connections.allow_default_port_from(db_sec_group)
            bedrock_runtime_interface_endpoint.connections.allow_default_port_to(db_sec_group)

            # Allow connections to and from the database to the Bedrock endpoints
            self.db_instance.connections.allow_default_port_from(bedrock_runtime_interface_endpoint)
            self.db_instance.connections.allow_default_port_to(bedrock_runtime_interface_endpoint)

            # Add policy that allows RDS to access Bedrock VPC endpoint
            bedrock_runtime_interface_endpoint.add_to_policy(
                iam.PolicyStatement(
                    principals=[database_ml_role],
                    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    resources=["arn:aws:bedrock:*::foundation-model/*"],
                    effect=iam.Effect.ALLOW,
                )
            )

        return self.db_instance

    def create_valkey_cluster(self, vpc: ec2.Vpc, valkey_sec_group: ec2.SecurityGroup, context: dict) -> tuple:
        """Create the ElastiCache Serverless Valkey (Redis-compatible) cluster.

        Valkey is used by OpenEMR for:
        - Session management
        - Application-level caching
        - Performance optimization

        The cluster is deployed in private subnets with TLS encryption enabled.
        Connection details are stored in SSM Parameter Store for secure access.

        Args:
            vpc: The VPC for the Valkey cluster
            valkey_sec_group: Security group for Valkey access
            context: CDK context dictionary

        Returns:
            Tuple of (valkey_cluster, valkey_endpoint, php_valkey_tls_variable,
                     mysql_ssl_ca_variable, mysql_ssl_enabled_variable)
        """
        private_subnets_ids = [ps.subnet_id for ps in vpc.private_subnets]

        # Create the Valkey cluster with a unique name based on the stack name and random suffix to avoid collisions
        # serverless_cache_name must be between 1-40 alphanumeric characters and start with a letter
        stack_name_sanitized = Stack.of(self.scope).stack_name.lower()[:20]
        suffix = get_resource_suffix(context)
        cache_name = f"{stack_name_sanitized}-{suffix}-valkey"

        self.valkey_cluster = elasticache.CfnServerlessCache(
            scope=self.scope,
            id="ValkeyCluster",
            engine="valkey",
            serverless_cache_name=cache_name,
            subnet_ids=private_subnets_ids,
            security_group_ids=[valkey_sec_group.security_group_id],
        )

        self.valkey_endpoint = ssm.StringParameter(
            self.scope,
            "valkey-endpoint",
            parameter_name="valkey_endpoint",
            string_value=self.valkey_cluster.attr_endpoint_address,
        )

        self.php_valkey_tls_variable = ssm.StringParameter(
            scope=self.scope, id="php-valkey-tls-variable", parameter_name="php_valkey_tls_variable", string_value="yes"
        )

        # MySQL SSL configuration - enable SSL for database connections
        self.mysql_ssl_ca_variable = ssm.StringParameter(
            scope=self.scope,
            id="mysql-ssl-ca-variable",
            parameter_name="mysql_ssl_ca_variable",
            string_value="/var/www/localhost/htdocs/openemr/sites/default/documents/certificates/mysql-ca",
        )

        self.mysql_ssl_enabled_variable = ssm.StringParameter(
            scope=self.scope,
            id="mysql-ssl-enabled-variable",
            parameter_name="mysql_ssl_enabled_variable",
            string_value="yes",
        )

        return (
            self.valkey_cluster,
            self.valkey_endpoint,
            self.php_valkey_tls_variable,
            self.mysql_ssl_ca_variable,
            self.mysql_ssl_enabled_variable,
        )

    def create_rotation_slot_secrets(self) -> secretsmanager.Secret:
        """Create dual-slot secret used by the credential rotation task.

        Returns:
            The RDS slot secret
        """
        if not self.db_instance:
            raise ValueError("Database cluster must be created before slot secrets")

        kms_key = self.scope.kms_keys.central_key

        self.rds_slot_secret = secretsmanager.Secret(
            self.scope,
            "RdsSlotSecret",
            encryption_key=kms_key,
            secret_string_value=SecretValue.unsafe_plain_text(
                (
                    '{"active_slot":"A",'
                    '"A":{"username":"openemr_a","password":"placeholder","host":"'
                    + str(self.db_instance.cluster_endpoint.hostname)
                    + '","port":"3306","dbname":"openemr"},'
                    '"B":{"username":"openemr_b","password":"placeholder","host":"'
                    + str(self.db_instance.cluster_endpoint.hostname)
                    + '","port":"3306","dbname":"openemr"}}'
                )
            ),
        )

        NagSuppressions.add_resource_suppressions(
            self.rds_slot_secret,
            [
                {
                    "id": "AwsSolutions-SMG4",
                    "reason": "Credential slot secrets are rotated by a dedicated ECS rotation task, not by built-in SM rotation Lambda",
                },
                {
                    "id": "HIPAA.Security-SecretsManagerRotationEnabled",
                    "reason": "Credential slot secrets are rotated by a dedicated ECS rotation task with application-aware flip/rollback",
                },
            ],
        )

        return self.rds_slot_secret
