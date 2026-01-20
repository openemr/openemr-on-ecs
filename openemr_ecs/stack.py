"""CDK stack definitions for the OpenEMR on AWS Fargate deployment."""

import random
import string

from aws_cdk import (
    CfnOutput,
    CustomResource,
    Duration,
    Stack,
)
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from cdk_nag import NagSuppressions
from constructs import Construct

from .analytics import AnalyticsComponents
from .cleanup import CleanupComponents
from .compute import ComputeComponents

# Import modular components
from .constants import StackConstants
from .database import DatabaseComponents
from .kms_keys import KmsKeys
from .monitoring import MonitoringComponents
from .network import NetworkComponents
from .security import SecurityComponents
from .storage import StorageComponents
from .utils import is_true
from .validation import ValidationError, validate_context
from .version import __version__


class OpenemrEcsStack(Stack):
    """Provision the full OpenEMR reference architecture using CDK and AWS Fargate."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        """Initialise stack defaults and trigger creation of all managed resources."""
        super().__init__(scope, construct_id, **kwargs)

        # Store termination protection setting for later use
        self.enable_termination_protection = is_true(self.node.try_get_context("enable_stack_termination_protection"))

        # Get context dictionary for configuration
        context_keys = [
            "security_group_ip_range_ipv4",
            "security_group_ip_range_ipv6",
            "certificate_arn",
            "email_forwarding_address",
            "route53_domain",
            "openemr_service_fargate_minimum_capacity",
            "openemr_service_fargate_maximum_capacity",
            "openemr_service_fargate_cpu",
            "openemr_service_fargate_memory",
            "openemr_service_fargate_cpu_autoscaling_percentage",
            "openemr_service_fargate_memory_autoscaling_percentage",
            "openemr_resource_suffix",  # Keep it in the keys to allow user override
            "rds_deletion_protection",
            "disable_rds_deletion_protection_on_destroy",
            "enable_long_term_cloudtrail_monitoring",
            "enable_patient_portal",
            "enable_ecs_exec",
            "activate_openemr_apis",
            "enable_bedrock_integration",
            "enable_data_api",
            "enable_global_accelerator",
            "open_smtp_port",
            "configure_ses",
            "create_serverless_analytics_environment",
            "aurora_ml_inference_timeout",
            "net_read_timeout",
            "net_write_timeout",
            "wait_timeout",
            "connect_timeout",
            "max_execution_time",
            "enable_stack_termination_protection",
            "enable_monitoring_alarms",
            "monitoring_email",
            "deployment_notification_email",
        ]

        context = {key: self.node.try_get_context(key) for key in context_keys}

        # Validate context values before proceeding with deployment
        # This catches configuration errors early and prevents deployment failures
        try:
            validate_context(context)
        except ValidationError as e:
            raise ValueError(f"Context validation failed: {e}") from e

        # Generate a stable but unique resource suffix if not provided via context.
        # This prevents resource replacement on every synthesis while avoiding collisions.
        if not context.get("openemr_resource_suffix"):
            # Use stack name as seed for stability within a deployment
            random.seed(self.stack_name)
            random_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
            context["openemr_resource_suffix"] = random_suffix
            # Reset seed to avoid affecting other random calls
            random.seed()
            print(f"Generated stable resource suffix: {random_suffix}")
        else:
            print(f"Using provided resource suffix: {context['openemr_resource_suffix']}")

        # Database name is always "openemr" (hardcoded for consistency)
        print("Using standard database name: openemr")

        # Configuration constants
        self.cidr = StackConstants.DEFAULT_CIDR
        self.number_of_days_to_regenerate_ssl_materials = StackConstants.DEFAULT_SSL_REGENERATION_DAYS
        self.mysql_port = StackConstants.MYSQL_PORT
        self.valkey_port = StackConstants.VALKEY_PORT
        self.container_port = StackConstants.CONTAINER_PORT
        self.emr_serverless_release_label = StackConstants.EMR_SERVERLESS_RELEASE_LABEL
        self.aurora_mysql_engine_version = StackConstants.AURORA_MYSQL_ENGINE_VERSION
        self.lambda_python_runtime = StackConstants.LAMBDA_PYTHON_RUNTIME
        self.openemr_version = StackConstants.OPENEMR_VERSION

        # Initialize component modules
        # Initialize KMS keys first since many components need them
        kms_keys = KmsKeys(self, self.account, self.region)
        self.kms_keys = kms_keys

        network = NetworkComponents(self, self.cidr)
        storage = StorageComponents(self)
        database = DatabaseComponents(self)
        compute = ComputeComponents(self)
        security = SecurityComponents(self)
        analytics = AnalyticsComponents(self)
        monitoring = MonitoringComponents(self)
        cleanup = CleanupComponents(self)

        # Create network infrastructure
        self.vpc = network.create_vpc()
        db_sec_group, valkey_sec_group, lb_sec_group = network.create_security_groups(self.vpc, context)
        self.db_sec_group = db_sec_group
        self.valkey_sec_group = valkey_sec_group
        self.lb_sec_group = lb_sec_group

        # Create dedicated security group for ECS tasks
        # This group is configured BEFORE service creation to allow ingress rules
        # to be set up without circular dependencies
        # Set allow_all_outbound=False so we can add custom egress rules
        self.ecs_task_sec_group = ec2.SecurityGroup(
            self,
            "EcsTaskSecurityGroup",
            vpc=self.vpc,
            description="Security group for ECS Fargate tasks",
            allow_all_outbound=False,
        )

        # Allow ingress from ECS task security group to database, valkey, and EFS
        # These rules are set up BEFORE service creation to avoid circular dependencies
        self.db_sec_group.add_ingress_rule(
            self.ecs_task_sec_group, ec2.Port.tcp(3306), "Allow MySQL connections from ECS tasks"
        )
        self.valkey_sec_group.add_ingress_rule(
            self.ecs_task_sec_group, ec2.Port.tcp(6379), "Allow Redis connections from ECS tasks"
        )

        # Add egress rules to allow responses back to ECS tasks
        # Database and Valkey need to respond to ECS task connections on ephemeral ports
        # TCP response traffic uses ephemeral ports (32768-65535), not the service port
        self.db_sec_group.add_egress_rule(
            self.ecs_task_sec_group, ec2.Port.all_tcp(), "Allow MySQL responses to ECS tasks (all TCP ports)"
        )
        self.valkey_sec_group.add_egress_rule(
            self.ecs_task_sec_group, ec2.Port.all_tcp(), "Allow Redis responses to ECS tasks (all TCP ports)"
        )

        # Add egress rules from ECS tasks to reach database and Valkey
        self.ecs_task_sec_group.add_egress_rule(
            self.db_sec_group, ec2.Port.tcp(3306), "Allow ECS tasks to connect to MySQL"
        )
        self.ecs_task_sec_group.add_egress_rule(
            self.valkey_sec_group, ec2.Port.tcp(6379), "Allow ECS tasks to connect to Redis"
        )

        # Create storage infrastructure
        self.elb_log_bucket = storage.create_elb_log_bucket()

        if is_true(context.get("enable_long_term_cloudtrail_monitoring")):
            cloudtrail_result = storage.create_cloudtrail_logging(self.region)
            if cloudtrail_result:
                self.cloudtrail_log_bucket, self.cloudtrail_kms_key, self.trail = cloudtrail_result

        # Create load balancer
        self.alb = network.create_alb(self.vpc, lb_sec_group, self.elb_log_bucket, context)
        self.accelerator = network.accelerator

        # Create DNS and certificates
        # Certificate is required (enforced in validation) for HTTPS/end-to-end encryption
        # Route53 domain enables automatic certificate issuance, validation, and renewal via ACM
        # Note: If both route53_domain and certificate_arn are provided, certificate_arn takes precedence
        if context.get("route53_domain") and not context.get("certificate_arn"):
            # Only create new certificate if certificate_arn is not provided
            # This prevents redundant certificate creation
            try:
                self.certificate = security.create_dns_and_certificates(self.alb, self.accelerator, context)
            except Exception as e:
                raise ValueError(
                    f"Failed to create DNS and certificates for domain {context.get('route53_domain')}. "
                    f"Ensure the Route53 hosted zone exists and is accessible. Error: {e}"
                ) from e
        else:
            self.certificate = None

        # Handle certificate from ARN if provided (takes precedence over creating new cert via route53_domain)
        # Certificate is required - this should have been validated earlier, but ensure we have one
        if context.get("certificate_arn"):
            if self.certificate:
                # Both were provided - use the ARN and log a warning
                print(
                    f"WARNING: Both route53_domain and certificate_arn provided. Using certificate_arn: {context.get('certificate_arn')}"
                )
            self.certificate = acm.Certificate.from_certificate_arn(self, "domainCert", str(context.get("certificate_arn")))  # type: ignore

        # Safety check: Certificate is required for HTTPS (should have been caught by validation, but verify)
        if not self.certificate:
            raise ValueError(
                "Certificate is required for HTTPS (end-to-end encryption). "
                "Provide either 'route53_domain' (for automatic certificate management) or 'certificate_arn' in cdk.json context."
            )

        # Configure SES (only if route53_domain and configure_ses are both set)
        if context.get("route53_domain") and is_true(context.get("configure_ses")):
            ses_resources = security.configure_ses(
                self.vpc, self.lambda_python_runtime, self.region, context, kms_keys.central_key
            )
            # SES resources should always return a dict - fail if configuration fails
            if not ses_resources or not isinstance(ses_resources, dict):
                raise ValueError(
                    "SES configuration failed. Expected a dictionary of SES resources but got: "
                    f"{type(ses_resources).__name__}. Check Route53 domain configuration and SES setup."
                )

            self.smtp_password = ses_resources.get("smtp_password")
            self.smtp_user = ses_resources.get("smtp_user")
            self.smtp_host = ses_resources.get("smtp_host")
            self.smtp_port = ses_resources.get("smtp_port")
            self.smtp_secure = ses_resources.get("smtp_secure")
            self.patient_reminder_sender_email = ses_resources.get("patient_reminder_sender_email")
            self.patient_reminder_sender_name = ses_resources.get("patient_reminder_sender_name")
            self.practice_return_email_path = ses_resources.get("practice_return_email_path")
            self.smtp_interface_endpoint = getattr(security, "smtp_interface_endpoint", None)
            self.one_time_generate_smtp_credential_lambda = getattr(
                security, "one_time_generate_smtp_credential_lambda", None
            )
            self.ses_rule_set = security.ses_rule_set
        else:
            # Initialize SES attributes to None when not configured
            self.smtp_password = None
            self.smtp_user = None
            self.smtp_host = None
            self.smtp_port = None
            self.smtp_secure = None
            self.patient_reminder_sender_email = None
            self.patient_reminder_sender_name = None
            self.practice_return_email_path = None
            self.smtp_interface_endpoint = None
            self.one_time_generate_smtp_credential_lambda = None
            self.ses_rule_set = None

        # Create WAF
        security.create_waf(self.alb, kms_keys.central_key)

        # Create environment variables
        self._create_environment_variables()

        # Create password
        self._create_password()

        # Create database
        self.db_instance = database.create_db_instance(
            self.vpc, db_sec_group, self.aurora_mysql_engine_version, context, self.region, self.account
        )
        self.db_secret = database.db_secret

        # Create Valkey cluster (required for OpenEMR)
        valkey_result = database.create_valkey_cluster(self.vpc, valkey_sec_group, context)
        if not valkey_result:
            raise ValueError("Failed to create Valkey cluster - this is a required resource")
        (
            self.valkey_cluster,
            self.valkey_endpoint,
            self.php_valkey_tls_variable,
            self.mysql_ssl_ca_variable,
            self.mysql_ssl_enabled_variable,
        ) = valkey_result

        # Create EFS volumes (required for OpenEMR)
        efs_result = storage.create_efs_volumes(self.vpc, context)
        if not efs_result:
            raise ValueError("Failed to create EFS volumes - these are required resources")
        (
            self.file_system_for_sites_folder,
            self.file_system_for_ssl_folder,
            self.efs_volume_configuration_for_sites_folder,
            self.efs_volume_configuration_for_ssl_folder,
        ) = efs_result

        # Allow EFS connections from ECS task security group (NFS port 2049)
        # EFS rules are added after EFS volumes are created
        sites_efs_sg = self.file_system_for_sites_folder.connections.security_groups[0]
        ssl_efs_sg = self.file_system_for_ssl_folder.connections.security_groups[0]
        sites_efs_sg.add_ingress_rule(
            self.ecs_task_sec_group, ec2.Port.tcp(2049), "Allow NFS connections from ECS tasks"
        )
        ssl_efs_sg.add_ingress_rule(self.ecs_task_sec_group, ec2.Port.tcp(2049), "Allow NFS connections from ECS tasks")

        # Add egress rules for EFS to respond to ECS tasks
        # NFS response traffic uses ephemeral ports
        sites_efs_sg.add_egress_rule(
            self.ecs_task_sec_group, ec2.Port.all_tcp(), "Allow NFS responses to ECS tasks (all TCP ports)"
        )
        ssl_efs_sg.add_egress_rule(
            self.ecs_task_sec_group, ec2.Port.all_tcp(), "Allow NFS responses to ECS tasks (all TCP ports)"
        )

        # Add egress rules from ECS tasks to reach EFS
        self.ecs_task_sec_group.add_egress_rule(
            sites_efs_sg, ec2.Port.tcp(2049), "Allow ECS tasks to connect to sites EFS"
        )
        self.ecs_task_sec_group.add_egress_rule(ssl_efs_sg, ec2.Port.tcp(2049), "Allow ECS tasks to connect to SSL EFS")

        # Add load balancer to ECS task security group rules (HTTPS port 443)
        # Load balancer needs to reach ECS tasks on container port
        self.ecs_task_sec_group.add_ingress_rule(
            self.lb_sec_group, ec2.Port.tcp(self.container_port), "Allow ALB to reach ECS tasks"
        )
        self.lb_sec_group.add_egress_rule(
            self.ecs_task_sec_group, ec2.Port.tcp(self.container_port), "Allow ALB to connect to ECS tasks"
        )
        # ECS tasks respond to ALB on ephemeral ports (stateful connection)
        self.ecs_task_sec_group.add_egress_rule(
            self.lb_sec_group, ec2.Port.all_tcp(), "Allow ECS tasks to respond to ALB (all TCP ports)"
        )

        # Add egress rules for ECS tasks to reach external services
        # Required for: downloading RDS/Redis CA certificates, AWS API calls, etc.
        self.ecs_task_sec_group.add_egress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "Allow HTTPS egress for AWS services and certificate downloads"
        )

        # Create backup plan
        storage.create_backup_plan(
            self.db_instance, self.file_system_for_sites_folder, self.file_system_for_ssl_folder, context
        )
        self.backup_vault = storage.backup_vault

        # Create ECS cluster (required for OpenEMR)
        ecs_result = compute.create_ecs_cluster(self.vpc, self.db_instance, context, self.region)
        if not ecs_result:
            raise ValueError("Failed to create ECS cluster - this is a required resource")
        self.ecs_cluster, self.log_group, self.kms_key, self.ecs_exec_group, self.exec_bucket = ecs_result

        # Create and maintain TLS materials
        self.one_time_create_ssl_materials_lambda = security.create_and_maintain_tls_materials(
            self.ecs_cluster,
            self.log_group,
            self.vpc,
            self.file_system_for_ssl_folder,
            self.efs_volume_configuration_for_ssl_folder,
            self.openemr_version,
            self.container_port,
            self.lambda_python_runtime,
            self.number_of_days_to_regenerate_ssl_materials,
        )
        self.efs_only_security_group = security.efs_only_security_group

        # Create SSM parameters for API and portal configuration
        self._create_api_and_portal_parameters(context)

        # Create OpenEMR service
        self.openemr_service = compute.create_openemr_service(
            self.ecs_cluster,
            self.log_group,
            self.alb,
            self.certificate,
            self.vpc,
            self.db_instance,
            self.db_secret,
            self.password,
            self.valkey_endpoint,
            self.php_valkey_tls_variable,
            self.mysql_ssl_ca_variable,
            self.mysql_ssl_enabled_variable,
            self.mysql_port_var,
            self.swarm_mode,
            self.efs_volume_configuration_for_sites_folder,
            self.efs_volume_configuration_for_ssl_folder,
            self.file_system_for_sites_folder,
            self.file_system_for_ssl_folder,
            self.valkey_sec_group,
            self.ecs_task_sec_group,
            self.openemr_version,
            self.container_port,
            context,
            exec_bucket=getattr(self, "exec_bucket", None),
            ecs_exec_group=getattr(self, "ecs_exec_group", None),
            site_addr_oath=getattr(self, "site_addr_oath", None),
            activate_rest_api=getattr(self, "activate_rest_api", None),
            activate_fhir_service=getattr(self, "activate_fhir_service", None),
            portal_onsite_two_address=getattr(self, "portal_onsite_two_address", None),
            portal_onsite_two_enable=getattr(self, "portal_onsite_two_enable", None),
            ccda_alt_service_enable=getattr(self, "ccda_alt_service_enable", None),
            rest_portal_api=getattr(self, "rest_portal_api", None),
            smtp_password=self.smtp_password if is_true(context.get("configure_ses")) else None,
            smtp_user=self.smtp_user if is_true(context.get("configure_ses")) else None,
            smtp_host=self.smtp_host if is_true(context.get("configure_ses")) else None,
            smtp_port=self.smtp_port if is_true(context.get("configure_ses")) else None,
            smtp_secure=self.smtp_secure if is_true(context.get("configure_ses")) else None,
            patient_reminder_sender_email=(
                self.patient_reminder_sender_email if is_true(context.get("configure_ses")) else None
            ),
            patient_reminder_sender_name=(
                self.patient_reminder_sender_name if is_true(context.get("configure_ses")) else None
            ),
            practice_return_email_path=(
                self.practice_return_email_path
                if is_true(context.get("configure_ses")) and context.get("email_forwarding_address")
                else None
            ),
        )

        # Add explicit dependencies to ensure proper resource creation order
        # This prevents race conditions and ensures all prerequisites are ready before ECS service starts
        if self.one_time_create_ssl_materials_lambda:
            self.openemr_service.node.add_dependency(self.one_time_create_ssl_materials_lambda)

        # ECS service must wait for database to be ready (can take 10-15 minutes)
        self.openemr_service.node.add_dependency(self.db_instance)

        # ECS service must wait for Valkey cluster to be ready
        if self.valkey_cluster:
            self.openemr_service.node.add_dependency(self.valkey_cluster)

        # ECS service must wait for EFS file systems to be ready (mount targets created automatically)
        self.openemr_service.node.add_dependency(self.file_system_for_sites_folder)
        self.openemr_service.node.add_dependency(self.file_system_for_ssl_folder)

        # ECS service must wait for secrets to be created
        self.openemr_service.node.add_dependency(self.password)
        if self.db_secret:
            self.openemr_service.node.add_dependency(self.db_secret)

        # ECS service must wait for SSM parameters to be created
        self.openemr_service.node.add_dependency(self.swarm_mode)
        self.openemr_service.node.add_dependency(self.mysql_port_var)

        # Create serverless analytics environment (optional)
        self.sagemaker_domain_id = None
        if is_true(context.get("create_serverless_analytics_environment")):
            analytics_result = analytics.create_serverless_analytics_environment(
                self.vpc,
                self.db_instance,
                self.ecs_cluster,
                self.log_group,
                self.file_system_for_sites_folder,
                self.efs_volume_configuration_for_sites_folder,
                self.efs_only_security_group,  # type: ignore
                self.openemr_version,
                self.container_port,
                self.emr_serverless_release_label,
                self.lambda_python_runtime,
                self.account,
                self.region,
                self.node.addr,
            )
            self.sagemaker_api_interface_endpoint = analytics.sagemaker_api_interface_endpoint
            self.sagemaker_runtime_interface_endpoint = analytics.sagemaker_runtime_interface_endpoint
            # Store the SageMaker domain ID for cleanup
            if analytics_result and "sagemaker_domain" in analytics_result:
                self.sagemaker_domain_id = analytics_result["sagemaker_domain"].attr_domain_id

        # Create monitoring and alarms if enabled
        monitoring_alarms_topic = None
        if is_true(context.get("enable_monitoring_alarms")):
            monitoring_email = context.get("monitoring_email") or context.get("email_forwarding_address")
            deployment_email = context.get("deployment_notification_email") or monitoring_email

            # Create SNS topics for alerts
            if monitoring_email:
                monitoring_alarms_topic = monitoring.create_alarms_topic(monitoring_email)
                if deployment_email:
                    monitoring.create_deployment_topic(deployment_email)

            # Create ECS service alarms
            monitoring.create_ecs_service_alarms(self.openemr_service.service, monitoring_alarms_topic)

            # Create ALB health alarms
            if self.alb:
                monitoring.create_alb_health_alarms(
                    self.openemr_service.target_group, self.alb, monitoring_alarms_topic
                )

            # Create deployment failure alarm
            monitoring.create_deployment_failure_alarm(self.openemr_service.service, monitoring_alarms_topic)

        # Enable stack termination protection if configured
        # This prevents accidental deletion of production stacks
        if self.enable_termination_protection:
            self._enable_stack_termination_protection()

        # Create cleanup resource for automatic stack deletion handling
        # This resource automatically handles cleanup of resources that might block deletion:
        # - Disables RDS deletion protection
        # - Deactivates SES rule sets (CloudFormation deletes them)
        # - Deletes backup recovery points
        # - Cleans up SageMaker EFS file systems and ENIs
        # Note: This resource must be created after all resources it needs to clean up
        cleanup_resource = cleanup.create_cleanup_resource(
            db_cluster=self.db_instance,
            backup_vault=self.backup_vault,
            ses_rule_set=self.ses_rule_set,
            stack_name=self.stack_name,
            alb_arn=self.alb.load_balancer_arn,
            sagemaker_domain_id=self.sagemaker_domain_id,
        )

        # Configure deletion order to ensure cleanup runs before resources are deleted
        # Cleanup depends on resources so it's created after them
        # During deletion, CloudFormation deletes cleanup first (triggering Lambda)
        # The Lambda deletes SES rule set completely (deactivate, delete rules, delete rule set)
        # CloudFormation will not delete SES (RETAIN policy) - Lambda handles it completely
        if self.db_instance:
            cleanup_resource.node.add_dependency(self.db_instance)
        if self.backup_vault:
            cleanup_resource.node.add_dependency(self.backup_vault)
        if self.ses_rule_set:
            cleanup_resource.node.add_dependency(self.ses_rule_set)

        # Create CloudFormation outputs for important resources
        # These outputs make it easy to find critical resources after deployment
        self._create_outputs(context)

    def _enable_stack_termination_protection(self):
        """Enable CloudFormation stack termination protection via custom resource.

        This prevents accidental deletion of the stack. To delete the stack,
        termination protection must be disabled first.
        """
        # Create a custom resource to enable stack termination protection
        enable_protection_lambda = _lambda.Function(
            self,
            "EnableTerminationProtectionLambda",
            runtime=_lambda.Runtime.PYTHON_3_14,
            handler="index.handler",
            code=_lambda.Code.from_inline("""
import boto3
import cfnresponse

def handler(event, context):
    cloudformation = boto3.client('cloudformation')
    stack_name = event['ResourceProperties']['StackName']

    try:
        if event['RequestType'] in ['Create', 'Update']:
            cloudformation.update_termination_protection(
                EnableTerminationProtection=True,
                StackName=stack_name
            )
            print(f"Enabled termination protection for stack: {stack_name}")
        elif event['RequestType'] == 'Delete':
            cloudformation.update_termination_protection(
                EnableTerminationProtection=False,
                StackName=stack_name
            )
            print(f"Disabled termination protection for stack: {stack_name}")

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=str(e))
"""),
            timeout=Duration.seconds(30),
        )

        # Grant permission to update stack termination protection
        enable_protection_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["cloudformation:UpdateTerminationProtection"],
                resources=[f"arn:aws:cloudformation:{self.region}:{self.account}:stack/{self.stack_name}/*"],
            )
        )

        # Create the custom resource
        CustomResource(
            self,
            "TerminationProtectionResource",
            service_token=enable_protection_lambda.function_arn,
            properties={"StackName": self.stack_name},
        )

    def _create_environment_variables(self):
        """Persist reusable application settings in Parameter Store for ECS tasks."""
        self.swarm_mode = ssm.StringParameter(self, "swarm-mode", parameter_name="swarm_mode", string_value="yes")
        self.mysql_port_var = ssm.StringParameter(
            self, "mysql-port", parameter_name="mysql_port", string_value=str(self.mysql_port)
        )

    def _create_password(self):
        """Generate the OpenEMR admin secret with a safe character set."""
        # Keep only these very safe special characters available for passwords.
        safe_specials = "!()<>^{}~"

        # Exclude every other punctuation character (shell/JSON troublemakers and others that could cause bugs).
        exclude_chars = "".join(ch for ch in string.punctuation if ch not in safe_specials)

        self.password = secretsmanager.Secret(
            self,
            "Password",
            encryption_key=self.kms_keys.central_key,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"admin"}',
                generate_string_key="password",
                password_length=16,
                include_space=False,
                require_each_included_type=True,
                exclude_characters=exclude_chars,
            ),
        )

        # Suppress rotation warnings for admin password (manually rotated by administrators)
        NagSuppressions.add_resource_suppressions(
            self.password,
            [
                {
                    "id": "AwsSolutions-SMG4",
                    "reason": "Admin password is manually rotated by system administrators - automatic rotation not appropriate for admin credentials",
                },
                {
                    "id": "HIPAA.Security-SecretsManagerRotationEnabled",
                    "reason": "Admin password is manually rotated by system administrators - automatic rotation not appropriate for admin credentials",
                },
            ],
        )

    def _create_api_and_portal_parameters(self, context: dict):
        """Create SSM parameters for OpenEMR APIs and patient portal configuration."""
        # Determine base URL - always HTTPS since certificate is required
        if context.get("route53_domain"):
            base_url = f"https://openemr.{context.get('route53_domain')}"
        else:
            if is_true(context.get("enable_global_accelerator")) and self.accelerator:
                base_url = f"https://{self.accelerator.dns_name}"
            else:
                base_url = f"https://{self.alb.load_balancer_dns_name}"

        # API activation parameters
        if is_true(context.get("activate_openemr_apis")):
            self.activate_fhir_service = ssm.StringParameter(
                self, "activate-fhir-service", parameter_name="activate_fhir_service", string_value="1"
            )
            self.activate_rest_api = ssm.StringParameter(
                self, "activate-rest-api", parameter_name="activate_rest_api", string_value="1"
            )

        # Patient portal parameters
        if is_true(context.get("enable_patient_portal")):
            self.portal_onsite_two_address = ssm.StringParameter(
                self,
                "portal-onsite-two-address",
                parameter_name="portal_onsite_two_address",
                string_value=f"{base_url}/portal/",
            )
            self.portal_onsite_two_enable = ssm.StringParameter(
                self, "portal-onsite-two-enable", parameter_name="portal_onsite_two_enable", string_value="1"
            )
            self.ccda_alt_service_enable = ssm.StringParameter(
                self, "ccda-alt-service-enable", parameter_name="ccda_alt_service_enable", string_value="3"
            )
            self.rest_portal_api = ssm.StringParameter(
                self, "rest-portal-api", parameter_name="rest_portal_api", string_value="1"
            )

        # Site address OAuth (required for APIs or portal)
        if is_true(context.get("activate_openemr_apis")) or is_true(context.get("enable_patient_portal")):
            self.site_addr_oath = ssm.StringParameter(
                self, "site-addr-oath", parameter_name="site_addr_oath", string_value=base_url
            )

    def _create_outputs(self, context: dict):
        """Create CloudFormation outputs for critical resources.

        These outputs make it easy to find important resources after deployment
        and are useful for integration with other systems or documentation.

        Args:
            context: CDK context dictionary
        """
        # Application Load Balancer DNS name (primary access point)
        CfnOutput(
            self,
            "LoadBalancerDNS",
            value=self.alb.load_balancer_dns_name,
            description="DNS name of the Application Load Balancer for OpenEMR",
        )

        # Full application URL
        if context.get("route53_domain"):
            app_url = f"https://openemr.{context.get('route53_domain')}"
        elif is_true(context.get("enable_global_accelerator")) and self.accelerator:
            app_url = f"https://{self.accelerator.dns_name}"
        else:
            app_url = f"https://{self.alb.load_balancer_dns_name}"

        CfnOutput(self, "ApplicationURL", value=app_url, description="Full URL to access the OpenEMR application")

        # Database cluster endpoint
        CfnOutput(
            self,
            "DatabaseEndpoint",
            value=self.db_instance.cluster_endpoint.hostname,
            description="Aurora MySQL cluster endpoint (use this for database connections)",
        )

        # Database cluster reader endpoint
        CfnOutput(
            self,
            "DatabaseReaderEndpoint",
            value=self.db_instance.cluster_read_endpoint.hostname,
            description="Aurora MySQL cluster reader endpoint (for read-only connections)",
        )

        # Valkey/Redis endpoint
        if self.valkey_endpoint:
            CfnOutput(
                self,
                "ValkeyEndpoint",
                value=self.valkey_endpoint.parameter_name,
                description="SSM Parameter name containing Valkey/Redis endpoint (use AWS CLI or console to retrieve value)",
            )

        # ECS cluster name
        CfnOutput(
            self,
            "ECSClusterName",
            value=self.ecs_cluster.cluster_name,
            description="Name of the ECS cluster running OpenEMR",
        )

        # ECS service name
        CfnOutput(
            self,
            "ECSServiceName",
            value=self.openemr_service.service.service_name,
            description="Name of the ECS service running OpenEMR containers",
        )

        # CloudWatch log group
        CfnOutput(
            self,
            "LogGroupName",
            value=self.log_group.log_group_name,
            description="CloudWatch Log Group for OpenEMR container logs",
        )

        # EFS file system IDs
        CfnOutput(
            self,
            "EFSSitesFileSystemId",
            value=self.file_system_for_sites_folder.file_system_id,
            description="EFS file system ID for OpenEMR sites data",
        )

        CfnOutput(
            self,
            "EFSSSLFileSystemId",
            value=self.file_system_for_ssl_folder.file_system_id,
            description="EFS file system ID for SSL certificates",
        )

        # Backup vault name
        if self.backup_vault:
            CfnOutput(
                self,
                "BackupVaultName",
                value=self.backup_vault.backup_vault_name,
                description="AWS Backup vault name containing recovery points for RDS and EFS",
            )

        # Database secret ARN
        if self.db_secret:
            CfnOutput(
                self,
                "DatabaseSecretARN",
                value=self.db_secret.secret_arn,
                description="ARN of the Secrets Manager secret containing database credentials",
            )

        # OpenEMR admin password secret ARN
        CfnOutput(
            self,
            "OpenEMRPasswordSecretARN",
            value=self.password.secret_arn,
            description="ARN of the Secrets Manager secret containing OpenEMR admin password",
        )

        # Stack termination protection status
        if self.enable_termination_protection:
            CfnOutput(
                self,
                "StackTerminationProtection",
                value="ENABLED",
                description="Stack termination protection is enabled. Disable it before stack deletion.",
            )

        # Stack version
        CfnOutput(self, "StackVersion", value=__version__, description="Version of the CDK stack deployed")
