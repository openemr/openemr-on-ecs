"""Security infrastructure: WAF, certificates, DNS, SES, and SSL materials."""

from typing import Optional

from aws_cdk import (
    ArnFormat,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as event_targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ses as ses
from aws_cdk import aws_ses_actions as ses_actions
from aws_cdk import aws_ssm as ssm
from aws_cdk import aws_wafv2 as wafv2
from aws_cdk import (
    triggers,
)
from cdk_nag import NagSuppressions
from constructs import Construct

from .nag_suppressions import (
    suppress_lambda_common_findings,
    suppress_lambda_role_common_findings,
    suppress_vpc_endpoint_security_group_findings,
)
from .utils import is_true


class SecurityComponents:
    """Creates and manages security infrastructure.

    This class handles:
    - WAF v2 web application firewall
    - ACM certificates and Route53 DNS
    - Amazon SES email configuration
    - SSL/TLS materials generation and maintenance
    """

    def __init__(self, scope: Construct):
        """Initialize security components.

        Args:
            scope: The CDK construct scope
        """
        self.scope = scope
        self.certificate: Optional[acm.Certificate] = None
        self.one_time_create_ssl_materials_lambda: Optional[triggers.TriggerFunction] = None
        self.efs_only_security_group: Optional[ec2.SecurityGroup] = None
        self.smtp_password: Optional[secretsmanager.Secret] = None
        self.smtp_user: Optional[ssm.StringParameter] = None
        self.smtp_host: Optional[ssm.StringParameter] = None
        self.smtp_port: Optional[ssm.StringParameter] = None
        self.smtp_secure: Optional[ssm.StringParameter] = None
        self.patient_reminder_sender_email: Optional[ssm.StringParameter] = None
        self.patient_reminder_sender_name: Optional[ssm.StringParameter] = None
        self.practice_return_email_path: Optional[ssm.StringParameter] = None
        self.email_storage_bucket: Optional[s3.Bucket] = None
        self.smtp_interface_endpoint: Optional[ec2.InterfaceVpcEndpoint] = None
        self.ses_rule_set: Optional[ses.ReceiptRuleSet] = None
        self.one_time_generate_smtp_credential_lambda: Optional[triggers.TriggerFunction] = None

    def create_waf(self, alb: elb.ApplicationLoadBalancer, kms_key) -> wafv2.CfnWebACL:
        """Create the WAFv2 web ACL with comprehensive protection rules.

        Implements multiple layers of protection:
        - AWS Managed Rules (Common Rule Set, SQL Injection, Known Bad Inputs)
        - Rate limiting (2000 requests per 5 minutes per IP)
        - Suspicious user-agent blocking (bots, scrapers, crawlers, spiders)

        Args:
            alb: Application Load Balancer to protect

        Returns:
            The created WAF web ACL
        """
        # Create regex pattern set for suspicious user-agents
        stack_name = Stack.of(self.scope).stack_name
        regex_pattern_set = wafv2.CfnRegexPatternSet(
            self.scope,
            "SuspiciousUserAgentPatternSet",
            name=f"{stack_name}-ua-suspicious",
            scope="REGIONAL",
            regular_expression_list=["bot", "scraper", "crawler", "spider"],
        )

        # Create WAF Web ACL with comprehensive rules
        web_acl = wafv2.CfnWebACL(
            self.scope,
            "web-acl",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow=wafv2.CfnWebACL.AllowActionProperty()),
            scope="REGIONAL",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{stack_name}-waf-acl-metric",
                sampled_requests_enabled=True,
            ),
            name=f"{stack_name}-waf-acl",
            description="WAF Web ACL for OpenEMR application",
            rules=[
                # Rule 1: AWS Managed Rules - Core Rule Set (OWASP Top 10 protection)
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=1,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            name="AWSManagedRulesCommonRuleSet", vendor_name="AWS"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWSManagedRulesCommonRuleSetMetric",
                        sampled_requests_enabled=True,
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                ),
                # Rule 2: AWS Managed Rules - SQL Injection Protection
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesSQLiRuleSet",
                    priority=2,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            name="AWSManagedRulesSQLiRuleSet", vendor_name="AWS"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWSManagedRulesSQLiRuleSetMetric",
                        sampled_requests_enabled=True,
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                ),
                # Rule 3: AWS Managed Rules - Known Bad Inputs
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    priority=3,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            name="AWSManagedRulesKnownBadInputsRuleSet", vendor_name="AWS"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWSManagedRulesKnownBadInputsRuleSetMetric",
                        sampled_requests_enabled=True,
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                ),
                # Rule 4: Rate Limiting (2000 requests per 5 minutes per IP)
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitRule",
                    priority=4,
                    action=wafv2.CfnWebACL.RuleActionProperty(block=wafv2.CfnWebACL.BlockActionProperty()),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=2000, aggregate_key_type="IP"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimitRuleMetric",
                        sampled_requests_enabled=True,
                    ),
                ),
                # Rule 5: Suspicious User-Agent Blocking
                wafv2.CfnWebACL.RuleProperty(
                    name="SuspiciousUserAgentRule",
                    priority=5,
                    action=wafv2.CfnWebACL.RuleActionProperty(block=wafv2.CfnWebACL.BlockActionProperty()),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        regex_pattern_set_reference_statement=wafv2.CfnWebACL.RegexPatternSetReferenceStatementProperty(
                            arn=regex_pattern_set.attr_arn,
                            field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                # CloudFormation expects "Name" (capitalized) for SingleHeader
                                single_header={"Name": "user-agent"}
                            ),
                            text_transformations=[
                                wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                            ],
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="SuspiciousUserAgentRuleMetric",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )

        waf_log_group = logs.LogGroup(
            self.scope,
            "WAF-Log-Group",
            log_group_name=f"aws-waf-logs-{stack_name.lower()}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
            encryption_key=kms_key,  # Encrypt WAF logs with KMS
        )

        # Add NagSuppression for WAF Log Group
        NagSuppressions.add_resource_suppressions(
            waf_log_group,
            [
                {
                    "id": "HIPAA.Security-CloudWatchLogGroupEncrypted",
                    "reason": "CloudWatch Log Group is encrypted with a KMS key.",
                }
            ],
        )

        wafv2.CfnWebACLAssociation(
            self.scope, "WebACLAssociation", resource_arn=alb.load_balancer_arn, web_acl_arn=web_acl.attr_arn
        )

        wafv2.CfnLoggingConfiguration(
            self.scope,
            "waf-logging-configuration",
            resource_arn=web_acl.attr_arn,
            log_destination_configs=[
                Stack.of(self.scope).format_arn(
                    arn_format=ArnFormat.COLON_RESOURCE_NAME,
                    service="logs",
                    resource="log-group",
                    resource_name=waf_log_group.log_group_name,
                )
            ],
        )

        web_acl.node.add_dependency(alb)

        return web_acl

    def create_dns_and_certificates(
        self, alb: elb.ApplicationLoadBalancer, accelerator, context: dict
    ) -> Optional[acm.Certificate]:
        """Set up Route 53 records and ACM certificates when domain context is supplied.

        Args:
            alb: Application Load Balancer
            accelerator: Global Accelerator (optional)
            context: CDK context dictionary

        Returns:
            The created ACM certificate or None
        """
        if not context.get("route53_domain"):
            return None

        # Define the hosted zone in Route 53
        hosted_zone = route53.HostedZone.from_lookup(
            self.scope, "HostedZoneForRoute53", domain_name=str(context.get("route53_domain"))
        )

        self.certificate = acm.Certificate(
            self.scope,
            "Certificate",
            domain_name="*." + str(context.get("route53_domain")),
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        # Create Route 53 alias record
        if is_true(context.get("enable_global_accelerator")) and accelerator:
            route53.ARecord(
                self.scope,
                "AliasRecordOpenEMR",
                zone=hosted_zone,
                target=route53.RecordTarget.from_alias(targets.GlobalAcceleratorDomainTarget(accelerator.dns_name)),
                record_name="openemr",
            )
        else:
            route53.ARecord(
                self.scope,
                "AliasRecordOpenEMR",
                zone=hosted_zone,
                target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(alb)),
                record_name="openemr",
            )

        return self.certificate

    def configure_ses(
        self, vpc: ec2.Vpc, lambda_python_runtime: _lambda.Runtime, region: str, context: dict, kms_key
    ) -> dict:
        """Configure Amazon SES receipt rules and identities for inbound/outbound mail.

        Args:
            vpc: The VPC for VPC endpoints
            lambda_python_runtime: Lambda Python runtime version
            region: AWS region
            context: CDK context dictionary
            kms_key: KMS key for S3 bucket encryption

        Returns:
            Dictionary with SES-related resources (smtp_password, smtp_user, etc.)
        """
        if not (context.get("route53_domain") and is_true(context.get("configure_ses"))):
            return {}

        # Define the hosted zone in Route 53
        hosted_zone = route53.HostedZone.from_lookup(
            self.scope, "HostedZoneForSES", domain_name=str(context.get("route53_domain"))
        )

        # Create an SES domain identity for email verification
        ses_domain_identity = ses.EmailIdentity(
            self.scope,
            "SESIdentity",
            identity=ses.Identity.public_hosted_zone(hosted_zone),
            mail_from_domain="services." + str(context.get("route53_domain")),
        )

        # Create 3 CNAME Records Necessary to Verify Domain Identity
        route53.CnameRecord(
            self.scope,
            "DkimCnameRecord1",
            zone=hosted_zone,
            record_name=ses_domain_identity.dkim_dns_token_name1,
            domain_name=ses_domain_identity.dkim_dns_token_value1,
        )
        route53.CnameRecord(
            self.scope,
            "DkimCnameRecord2",
            zone=hosted_zone,
            record_name=ses_domain_identity.dkim_dns_token_name2,
            domain_name=ses_domain_identity.dkim_dns_token_value2,
        )
        route53.CnameRecord(
            self.scope,
            "DkimCnameRecord3",
            zone=hosted_zone,
            record_name=ses_domain_identity.dkim_dns_token_name3,
            domain_name=ses_domain_identity.dkim_dns_token_value3,
        )

        # Set up DMARC
        route53.TxtRecord(
            self.scope,
            "DmarcRecord",
            zone=hosted_zone,
            record_name="_dmarc",
            values=["v=DMARC1;p=quarantine;rua=mailto:help@" + str(context.get("route53_domain"))],
        )

        # Create IAM user for SES SMTP access
        stack_name = Stack.of(self.scope).stack_name
        ses_smtp_user = iam.User(self.scope, "SmtpUser", user_name=f"ses-smtp-user-{stack_name.lower()}")
        ses_domain_identity.grant_send_email(ses_smtp_user)

        # Suppress IAM user group membership requirement - this is a service account for SMTP
        NagSuppressions.add_resource_suppressions(
            ses_smtp_user,
            [
                {
                    "id": "HIPAA.Security-IAMUserGroupMembership",
                    "reason": "SMTP user is a service account for SES email sending - not a human user. IAM user required for SMTP authentication (cannot use IAM role)",
                },
                {
                    "id": "HIPAA.Security-IAMUserNoPolicies",
                    "reason": "SMTP user requires inline policy for SES send permissions - this is a service account, not a human user",
                },
            ],
            apply_to_children=True,
        )

        # Suppress inline policy for SMTP user's DefaultPolicy (CDK-generated)
        # Note: This suppression must be added after grant_send_email() creates the policy
        NagSuppressions.add_resource_suppressions(
            ses_smtp_user,
            [
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is generated by CDK grant_send_email() for SES permissions - required for SMTP user functionality",
                }
            ],
            apply_to_children=True,
        )

        # Generate SMTP credentials
        access_key = iam.AccessKey(self.scope, "SmtpAccessKey", user=ses_smtp_user)

        # Get KMS key for secrets encryption
        kms_key = self.scope.kms_keys.central_key

        # Create secrets for SMTP credentials
        self.smtp_password = secretsmanager.Secret(
            self.scope,
            "smtp-secret",
            encryption_key=kms_key,
        )
        secret_access_key = secretsmanager.Secret(
            self.scope,
            "secret-access-key",
            secret_object_value={"password": access_key.secret_access_key},
            encryption_key=kms_key,
        )

        # Suppress rotation warnings for SMTP credentials (IAM user based, not auto-rotatable)
        for secret in [self.smtp_password, secret_access_key]:
            NagSuppressions.add_resource_suppressions(
                secret,
                [
                    {
                        "id": "AwsSolutions-SMG4",
                        "reason": "SMTP credentials based on IAM Access Keys cannot be automatically rotated - must be manually rotated by creating new IAM user",
                    },
                    {
                        "id": "HIPAA.Security-SecretsManagerRotationEnabled",
                        "reason": "SMTP credentials based on IAM Access Keys cannot be automatically rotated - must be manually rotated by creating new IAM user",
                    },
                ],
            )

        # Create Lambda to generate SMTP credentials
        self.one_time_generate_smtp_credential_lambda = triggers.TriggerFunction(
            self.scope,
            "SMTPSetup",
            runtime=lambda_python_runtime,
            code=_lambda.Code.from_asset("lambda"),
            architecture=_lambda.Architecture.ARM_64,
            handler="lambda_functions.generate_smtp_credential",
            timeout=Duration.minutes(10),
        )

        # Add suppressions for SMTP setup Lambda (before grants)
        suppress_lambda_common_findings(
            self.one_time_generate_smtp_credential_lambda,
            vpc_required=False,
            reason_suffix="Generates SMTP credentials via AWS API, does not require VPC access.",
        )
        suppress_lambda_role_common_findings(self.one_time_generate_smtp_credential_lambda.role, role_type="smtp_setup")

        # Grant permissions (this creates the DefaultPolicy)
        secret_access_key.grant_read(self.one_time_generate_smtp_credential_lambda.role)   # type: ignore
        self.smtp_password.grant_write(self.one_time_generate_smtp_credential_lambda.role) # type: ignore

        # Add suppressions for DefaultPolicy (after grants create it)
        NagSuppressions.add_resource_suppressions(
            self.one_time_generate_smtp_credential_lambda.role.node.find_child("DefaultPolicy").node.find_child(
                "Resource"
            ),
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard permissions are necessary for Secrets Manager actions (GetSecretValue) and resource (SecretAccessKey*) for SMTP credential generation.",
                    "appliesTo": [
                        "Action::secretsmanager:GetSecretValue",
                        "Resource::arn:<AWS::Partition>:secretsmanager:<AWS::Region>:<AWS::Account>:secret:SecretAccessKey*",
                    ],
                },
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is generated by CDK for specific Secrets Manager permissions for SMTP credential generation.",
                },
            ],
        )

        self.one_time_generate_smtp_credential_lambda.add_environment("SECRET_ACCESS_KEY", secret_access_key.secret_arn)
        self.one_time_generate_smtp_credential_lambda.add_environment("SMTP_PASSWORD", self.smtp_password.secret_arn)

        # Store SMTP configuration in SSM Parameters
        self.smtp_user = ssm.StringParameter(
            scope=self.scope, id="smtp-user", parameter_name="smtp_user", string_value=access_key.access_key_id
        )
        self.smtp_host = ssm.StringParameter(
            scope=self.scope,
            id="smtp-host",
            parameter_name="smtp_host",
            string_value=f"email-smtp.{region}.amazonaws.com",
        )
        self.smtp_port = ssm.StringParameter(
            scope=self.scope, id="smtp-port", parameter_name="smtp_port", string_value="587"
        )
        self.smtp_secure = ssm.StringParameter(
            scope=self.scope, id="smtp-secure", parameter_name="smtp_secure", string_value="tls"
        )
        self.patient_reminder_sender_email = ssm.StringParameter(
            scope=self.scope,
            id="patient-reminder-sender-email",
            parameter_name="patient_reminder_sender_email",
            string_value=f"notifications@services.{context.get('route53_domain')}",
        )
        self.patient_reminder_sender_name = ssm.StringParameter(
            scope=self.scope,
            id="patient-reminder-sender-name",
            parameter_name="patient_reminder_sender_name",
            string_value="OpenEMR",
        )

        # Create VPC endpoint for SMTP
        self.smtp_interface_endpoint = vpc.add_interface_endpoint(
            "smtp_vpc_interface_endpoint",
            private_dns_enabled=True,
            service=ec2.InterfaceVpcEndpointAwsService.EMAIL_SMTP,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )
        # Suppress false positives for SMTP endpoint security group
        for sg in self.smtp_interface_endpoint.connections.security_groups:
            suppress_vpc_endpoint_security_group_findings(sg, "SMTP")

        # Validate Email Receiving Domain
        route53.MxRecord(
            self.scope,
            "MxReceivingRecord",
            values=[route53.MxRecordValue(host_name=f"inbound-smtp.{region}.amazonaws.com", priority=10)],
            zone=hosted_zone,
            record_name=str(context.get("route53_domain")),
        )

        # Create S3 bucket for email storage (stores incoming emails temporarily)
        # Note: SES doesn't support KMS encryption for email delivery to S3 (AWS limitation)
        # Using S3-managed encryption (SSE-S3) instead
        self.email_storage_bucket = s3.Bucket(
            self.scope,
            "EmailStorageBucket",
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,  # SES requires SSE-S3, not KMS
            enforce_ssl=True,
            versioned=True,
        )

        # Add NagSuppressions for EmailStorageBucket
        NagSuppressions.add_resource_suppressions(
            self.email_storage_bucket,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": "This bucket stores temporary incoming emails forwarded by Lambda - server access logging not needed for ephemeral content",
                },
                {
                    "id": "HIPAA.Security-S3BucketLoggingEnabled",
                    "reason": "This bucket stores temporary incoming emails forwarded by Lambda - server access logging not needed for ephemeral content",
                },
                {
                    "id": "HIPAA.Security-S3BucketReplicationEnabled",
                    "reason": "This bucket stores temporary incoming emails that are immediately forwarded by Lambda - replication not needed for ephemeral content",
                },
                {
                    "id": "HIPAA.Security-S3DefaultEncryptionKMS",
                    "reason": "SES email delivery to S3 requires S3-managed encryption (SSE-S3), not KMS - this is an AWS service limitation",
                },
                {
                    "id": "AwsSolutions-S3",
                    "reason": "SES email delivery to S3 requires S3-managed encryption (SSE-S3), not KMS - this is an AWS service limitation",
                },
            ],
        )

        # Grant SES permissions to write to S3
        ses_write_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("ses.amazonaws.com")],
            actions=["s3:PutObject", "s3:PutObjectAcl"],
            resources=[f"{self.email_storage_bucket.bucket_arn}/*"],
        )
        self.email_storage_bucket.add_to_resource_policy(ses_write_policy)

        # Set up SES Rule Set
        # Note: Deletion is handled by cleanup Lambda, not CloudFormation
        self.ses_rule_set = ses.ReceiptRuleSet(self.scope, "RuleSet")

        # Set deletion policy to RETAIN - our cleanup Lambda will handle deletion
        cfn_rule_set = self.ses_rule_set.node.default_child
        cfn_rule_set.apply_removal_policy(RemovalPolicy.RETAIN)

        email_forwarding_address = context.get("email_forwarding_address")
        if email_forwarding_address:
            # Create Lambda function to process and forward emails
            email_forwarding_lambda = _lambda.Function(
                self.scope,
                "EmailForwardingLambda",
                runtime=lambda_python_runtime,
                code=_lambda.Code.from_asset("lambda"),
                architecture=_lambda.Architecture.ARM_64,
                handler="lambda_functions.send_email",
                environment={
                    "FORWARD_TO": email_forwarding_address,
                    "SOURCE_ARN": ses_domain_identity.email_identity_arn,
                    "SOURCE_NAME": f"help@{context.get('route53_domain')}",  # type: ignore
                    "BUCKET_NAME": self.email_storage_bucket.bucket_name,
                },
            )

            # Add suppressions for email forwarding Lambda (custom resource, doesn't need VPC/DLQ)
            suppress_lambda_common_findings(
                email_forwarding_lambda, vpc_required=False, reason_suffix="Custom resource for SES email forwarding"
            )
            suppress_lambda_role_common_findings(email_forwarding_lambda.role, role_type="email_forwarding")  # type: ignore

            # Grant permissions (creates DefaultPolicy)
            self.email_storage_bucket.grant_read(email_forwarding_lambda)
            ses_domain_identity.grant_send_email(email_forwarding_lambda)

            # Suppress wildcard S3 permissions and inline policy (after grants create DefaultPolicy)
            NagSuppressions.add_resource_suppressions(
                email_forwarding_lambda.role.node.find_child("DefaultPolicy").node.find_child("Resource"),  # type: ignore
                [
                    {
                        "id": "AwsSolutions-IAM5",
                        "reason": "Wildcard S3 permissions are granted by CDK for bucket read access - necessary for Lambda to read email objects",
                        "appliesTo": [
                            "Action::s3:GetBucket*",
                            "Action::s3:GetObject*",
                            "Action::s3:List*",
                            "Resource::<EmailStorageBucket61C70CE5.Arn>/*",
                        ],
                    },
                    {
                        "id": "HIPAA.Security-IAMNoInlinePolicy",
                        "reason": "Inline policy is generated by CDK for S3 and SES permissions - required for email forwarding functionality",
                    },
                ],
            )

            self.ses_rule_set.add_rule(
                "ForwardingRule",
                recipients=[f"help@{context.get('route53_domain')}"],  # type: ignore
                enabled=True,
                scan_enabled=True,
                tls_policy=ses.TlsPolicy.REQUIRE,
                actions=[
                    ses_actions.S3(bucket=self.email_storage_bucket),
                    ses_actions.Lambda(function=email_forwarding_lambda),
                ],
            )

            self.practice_return_email_path = ssm.StringParameter(
                scope=self.scope,
                id="practice-return-email-path",
                parameter_name="practice_return_email_path",
                string_value=email_forwarding_address,
            )
        else:
            self.ses_rule_set.add_rule(
                "ForwardingRule",
                recipients=[f"help@{context.get('route53_domain')}"],  # type: ignore
                enabled=True,
                scan_enabled=True,
                tls_policy=ses.TlsPolicy.REQUIRE,
                actions=[ses_actions.S3(bucket=self.email_storage_bucket)],
            )

        # Create Lambda to activate rule set
        set_rule_set_to_active = triggers.TriggerFunction(
            self.scope,
            "MakeRuleSetActive",
            runtime=lambda_python_runtime,
            code=_lambda.Code.from_asset("lambda"),
            architecture=_lambda.Architecture.ARM_64,
            handler="lambda_functions.make_ruleset_active",
            timeout=Duration.minutes(10),
            environment={"RULE_SET_NAME": self.ses_rule_set.receipt_rule_set_name},
        )

        policy_statement = iam.PolicyStatement(
            effect=iam.Effect.ALLOW, actions=["ses:SetActiveReceiptRuleSet"], resources=["*"]
        )
        set_rule_set_to_active.add_to_role_policy(policy_statement)

        # Add suppressions for MakeRuleSetActive Lambda (custom resource, doesn't need VPC/DLQ)
        suppress_lambda_common_findings(
            set_rule_set_to_active, vpc_required=False, reason_suffix="Custom resource to activate SES rule set"
        )
        suppress_lambda_role_common_findings(set_rule_set_to_active.role, role_type="ses_activation")  # type: ignore

        # Suppress wildcard resource for SES (required by AWS service)
        NagSuppressions.add_resource_suppressions(
            set_rule_set_to_active.role,  # type: ignore
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "SES SetActiveReceiptRuleSet action requires wildcard resource - this is an AWS service requirement",
                    "appliesTo": ["Resource::*"],
                },
            ],
            apply_to_children=True,
        )

        return {
            "smtp_password": self.smtp_password,
            "smtp_user": self.smtp_user,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_secure": self.smtp_secure,
            "patient_reminder_sender_email": self.patient_reminder_sender_email,
            "patient_reminder_sender_name": self.patient_reminder_sender_name,
            "practice_return_email_path": self.practice_return_email_path,
        }

    def create_and_maintain_tls_materials(
        self,
        ecs_cluster: ecs.Cluster,
        log_group: logs.LogGroup,
        vpc: ec2.Vpc,
        file_system_for_ssl_folder,
        efs_volume_configuration_for_ssl_folder: ecs.EfsVolumeConfiguration,
        openemr_version: str,
        container_port: int,
        lambda_python_runtime: _lambda.Runtime,
        number_of_days_to_regenerate_ssl_materials: int,
    ) -> _lambda.Function:
        """Define tasks, lambdas, and schedules that refresh internal TLS materials.

        Args:
            ecs_cluster: ECS cluster for running SSL generation tasks
            log_group: CloudWatch log group
            vpc: VPC for security groups
            file_system_for_ssl_folder: EFS file system for SSL certificates
            efs_volume_configuration_for_ssl_folder: EFS volume configuration
            openemr_version: OpenEMR container version
            container_port: Container port
            lambda_python_runtime: Lambda Python runtime
            number_of_days_to_regenerate_ssl_materials: Days between SSL regeneration

        Returns:
            The one-time SSL materials creation Lambda function
        """
        # Create generate SSL materials task definition
        create_ssl_materials_task = ecs.FargateTaskDefinition(
            self.scope,
            "CreateSSLMaterialsTaskDefinition",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64),
        )

        create_ssl_materials_task.add_volume(
            name="SslFolderVolume", efs_volume_configuration=efs_volume_configuration_for_ssl_folder
        )

        # Script generates self-signed SSL materials using OpenSSL
        command_array = ["mkdir -p /etc/ssl/certs/ && \
            mkdir -p /etc/ssl/private/ && \
            openssl genrsa 2048 > /etc/ssl/private/selfsigned.key.pem && \
            openssl req -new -x509 -nodes -sha256 -days 365 -key /etc/ssl/private/selfsigned.key.pem \
            -outform PEM -out /etc/ssl/certs/selfsigned.cert.pem -config /swarm-pieces/ssl/openssl.cnf \
            -subj '/CN=localhost' && \
            cp /etc/ssl/private/selfsigned.key.pem /etc/ssl/private/webserver.key.pem && \
            cp /etc/ssl/certs/selfsigned.cert.pem /etc/ssl/certs/webserver.cert.pem && \
            touch /etc/ssl/docker-selfsigned-configured"]

        # Add container definition (this creates the execution role's DefaultPolicy)
        ssl_maintenance_container = create_ssl_materials_task.add_container(
            "AmazonLinuxContainer",
            logging=ecs.LogDriver.aws_logs(stream_prefix="ecs/sslmaintenance", log_group=log_group),
            port_mappings=[ecs.PortMapping(container_port=container_port)],
            essential=True,
            container_name="openemr",
            entry_point=["/bin/sh", "-c"],
            command=command_array,
            image=ecs.ContainerImage.from_registry(f"openemr/openemr:{openemr_version}"),
        )

        # Suppress inline policy for execution role (after container creates the DefaultPolicy)
        NagSuppressions.add_resource_suppressions(
            create_ssl_materials_task.execution_role,
            [
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy is generated by CDK for ECS task execution (CloudWatch Logs, ECR pull) - required for Fargate task execution",
                }
            ],
            apply_to_children=True,
        )

        # Create mount point for EFS
        efs_mount_point_for_ssl_folder = ecs.MountPoint(
            container_path="/etc/ssl/", read_only=False, source_volume="SslFolderVolume"
        )
        ssl_maintenance_container.add_mount_points(efs_mount_point_for_ssl_folder)

        # Get private subnet IDs
        private_subnets_ids = [ps.subnet_id for ps in vpc.private_subnets]
        private_subnet_id_string = ",".join(private_subnets_ids)

        # Create EFS-only security group
        self.efs_only_security_group = ec2.SecurityGroup(self.scope, "EFSOnlySecurityGroup", vpc=vpc)
        security_group_id = self.efs_only_security_group.security_group_id

        # Allow security group to access EFS
        file_system_for_ssl_folder.connections.allow_default_port_from(self.efs_only_security_group)

        # Create Lambda for scheduled SSL materials generation
        create_ssl_materials_lambda = _lambda.Function(
            self.scope,
            "MaintainSSLMaterialsLambda",
            runtime=lambda_python_runtime,
            code=_lambda.Code.from_asset("lambda"),
            architecture=_lambda.Architecture.ARM_64,
            handler="lambda_functions.generate_ssl_materials",
            timeout=Duration.minutes(10),
        )

        # Create IAM policy statement
        policy_statement = iam.PolicyStatement(
            effect=iam.Effect.ALLOW, actions=["ecs:RunTask", "ecs:DescribeTasks"], resources=["*"]
        )
        policy_statement.add_condition("ArnEquals", {"ecs:cluster": ecs_cluster.cluster_arn})

        # Grant permissions
        create_ssl_materials_task.grant_run(create_ssl_materials_lambda.grant_principal)
        create_ssl_materials_lambda.add_to_role_policy(policy_statement)

        # Add suppressions for MaintainSSLMaterialsLambda (custom resource, doesn't need VPC/DLQ)
        suppress_lambda_common_findings(
            create_ssl_materials_lambda,
            vpc_required=False,
            reason_suffix="Custom resource to trigger ECS task for SSL generation",
        )
        suppress_lambda_role_common_findings(create_ssl_materials_lambda.role, role_type="ecs_task")  # type: ignore

        # Suppress wildcard ECS permissions (required for RunTask)
        NagSuppressions.add_resource_suppressions(
            create_ssl_materials_lambda.role,  # type: ignore
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "ECS RunTask and DescribeTasks require wildcard resources with cluster condition - this is an AWS service requirement",
                    "appliesTo": ["Resource::*"],
                },
            ],
            apply_to_children=True,
        )

        # Add environment variables
        create_ssl_materials_lambda.add_environment("ECS_CLUSTER", ecs_cluster.cluster_arn)
        create_ssl_materials_lambda.add_environment("TASK_DEFINITION", create_ssl_materials_task.task_definition_arn)
        create_ssl_materials_lambda.add_environment("SUBNETS", private_subnet_id_string)
        create_ssl_materials_lambda.add_environment("SECURITY_GROUPS", security_group_id)

        # Add schedule for regular SSL regeneration
        events.Rule(
            self.scope,
            "RegularScheduleforSSLMaintenance",
            schedule=events.Schedule.rate(Duration.days(number_of_days_to_regenerate_ssl_materials)),
            targets=[event_targets.LambdaFunction(create_ssl_materials_lambda)],
        )

        # Create one-time SSL setup Lambda (runs before OpenEMR containers start)
        self.one_time_create_ssl_materials_lambda = triggers.TriggerFunction(
            self.scope,
            "OneTimeSSLSetup",
            runtime=lambda_python_runtime,
            code=_lambda.Code.from_asset("lambda"),
            architecture=_lambda.Architecture.ARM_64,
            handler="lambda_functions.generate_ssl_materials",
            timeout=Duration.minutes(10),
        )

        # Grant permissions
        create_ssl_materials_task.grant_run(self.one_time_create_ssl_materials_lambda.grant_principal)
        self.one_time_create_ssl_materials_lambda.add_to_role_policy(policy_statement)

        # Add suppressions for OneTimeSSLSetup Lambda (custom resource, doesn't need VPC/DLQ)
        suppress_lambda_common_findings(
            self.one_time_create_ssl_materials_lambda,
            vpc_required=False,
            reason_suffix="Custom resource to trigger ECS task for initial SSL generation",
        )
        suppress_lambda_role_common_findings(self.one_time_create_ssl_materials_lambda.role, role_type="ecs_task")  # type: ignore

        # Suppress wildcard ECS permissions (required for RunTask)
        NagSuppressions.add_resource_suppressions(
            self.one_time_create_ssl_materials_lambda.role,  # type: ignore
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "ECS RunTask and DescribeTasks require wildcard resources with cluster condition - this is an AWS service requirement",
                    "appliesTo": ["Resource::*"],
                },
            ],
            apply_to_children=True,
        )

        # Add environment variables
        self.one_time_create_ssl_materials_lambda.add_environment("ECS_CLUSTER", ecs_cluster.cluster_arn)
        self.one_time_create_ssl_materials_lambda.add_environment(
            "TASK_DEFINITION", create_ssl_materials_task.task_definition_arn
        )
        self.one_time_create_ssl_materials_lambda.add_environment("SUBNETS", private_subnet_id_string)
        self.one_time_create_ssl_materials_lambda.add_environment("SECURITY_GROUPS", security_group_id)

        return self.one_time_create_ssl_materials_lambda
