"""KMS keys for encryption at rest across all services."""

from aws_cdk import RemovalPolicy
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from constructs import Construct


class KmsKeys:
    """Creates and manages KMS keys for encryption at rest."""

    def __init__(self, scope: Construct, account: str, region: str):
        """Initialize KMS keys for the stack.

        Args:
            scope: CDK scope
            account: AWS account ID
            region: AWS region
        """
        self.scope = scope
        self.account = account
        self.region = region

        # Create a central KMS key for most services
        self.central_key = kms.Key(
            scope,
            "CentralEncryptionKey",
            description="Central KMS key for encrypting CloudWatch Logs, Secrets, SNS, and other resources",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Allow CloudWatch Logs to use the key
        self.central_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="Allow CloudWatch Logs",
                principals=[iam.ServicePrincipal(f"logs.{region}.amazonaws.com")],
                actions=[
                    "kms:Encrypt",
                    "kms:Decrypt",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:CreateGrant",
                    "kms:DescribeKey",
                ],
                resources=["*"],
                conditions={
                    "ArnLike": {"kms:EncryptionContext:aws:logs:arn": f"arn:aws:logs:{region}:{account}:log-group:*"}
                },
            )
        )

        # Allow SNS to use the key
        self.central_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="Allow SNS",
                principals=[iam.ServicePrincipal("sns.amazonaws.com")],
                actions=["kms:Decrypt", "kms:GenerateDataKey*"],
                resources=["*"],
            )
        )

        # Allow Secrets Manager to use the key
        self.central_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="Allow Secrets Manager",
                principals=[iam.ServicePrincipal("secretsmanager.amazonaws.com")],
                actions=["kms:Decrypt", "kms:GenerateDataKey*", "kms:CreateGrant"],
                resources=["*"],
            )
        )

        # Create an S3-specific key (S3 has special requirements)
        self.s3_key = kms.Key(
            scope,
            "S3EncryptionKey",
            description="KMS key for encrypting S3 buckets",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Allow S3 to use the key
        self.s3_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="Allow S3",
                principals=[iam.ServicePrincipal("s3.amazonaws.com")],
                actions=["kms:Decrypt", "kms:GenerateDataKey*"],
                resources=["*"],
            )
        )

        # Allow CloudTrail to use the S3 key
        self.s3_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="Allow CloudTrail",
                principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
                actions=["kms:GenerateDataKey*", "kms:Decrypt"],
                resources=["*"],
            )
        )
