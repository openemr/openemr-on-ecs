"""Helper functions for CDK Nag suppressions."""

from cdk_nag import NagSuppressions
from constructs import Construct


def suppress_lambda_common_findings(lambda_function, vpc_required=False, reason_suffix=""):
    """Add common CDK Nag suppressions for Lambda functions.

    Args:
        lambda_function: The Lambda function to suppress findings for
        vpc_required: If True, doesn't suppress VPC requirement
        reason_suffix: Additional context for the suppression reason
    """
    suppressions = []

    # Lambda concurrency limits
    suppressions.append(
        {
            "id": "HIPAA.Security-LambdaConcurrency",
            "reason": f"Lambda concurrency limits not set to allow auto-scaling based on demand. {reason_suffix}",
        }
    )

    # Lambda DLQ
    suppressions.append(
        {
            "id": "HIPAA.Security-LambdaDLQ",
            "reason": f"Dead Letter Queue not configured - this is a synchronous operation that fails fast. {reason_suffix}",
        }
    )

    # Lambda VPC (only if not required)
    if not vpc_required:
        suppressions.append(
            {
                "id": "HIPAA.Security-LambdaInsideVPC",
                "reason": f"Lambda does not require VPC access - performs AWS API operations only. {reason_suffix}",
            }
        )

    NagSuppressions.add_resource_suppressions(
        lambda_function,
        suppressions,
    )


def suppress_lambda_role_common_findings(lambda_role, role_type="basic"):
    """Add common suppressions for Lambda execution roles.

    Args:
        lambda_role: The Lambda execution role
        role_type: Type of role ("basic", "s3_access", "ecs_task")
    """
    suppressions = []

    # AWS managed policies - all Lambda functions use AWSLambdaBasicExecutionRole
    suppressions.append(
        {
            "id": "AwsSolutions-IAM4",
            "reason": "AWSLambdaBasicExecutionRole is AWS managed policy required for CloudWatch Logs access",
            "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"],
        }
    )

    # Inline policies
    suppressions.append(
        {
            "id": "HIPAA.Security-IAMNoInlinePolicy",
            "reason": "Inline policy required for least-privilege Lambda permissions specific to this function",
        }
    )

    # Wildcard permissions for S3 operations
    if role_type in ["s3_access", "ecs_task"]:
        suppressions.extend(
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "S3 wildcard permissions required for bucket operations - follows AWS SDK patterns",
                    "appliesTo": [
                        "Action::s3:GetBucket*",
                        "Action::s3:GetObject*",
                        "Action::s3:List*",
                        "Action::s3:Abort*",
                        "Action::s3:DeleteObject*",
                    ],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "S3 object-level permissions require /* suffix to access all objects in bucket",
                    "appliesTo": [
                        "Resource::<*Bucket*.Arn>/*",
                    ],
                },
            ]
        )

    # KMS wildcard permissions
    if role_type in ["s3_access", "ecs_task"]:
        suppressions.append(
            {
                "id": "AwsSolutions-IAM5",
                "reason": "KMS wildcard permissions required for S3 encryption operations",
                "appliesTo": [
                    "Action::kms:GenerateDataKey*",
                    "Action::kms:ReEncrypt*",
                ],
            }
        )

    # ECS task permissions
    if role_type == "ecs_task":
        suppressions.append(
            {
                "id": "AwsSolutions-IAM5",
                "reason": "Wildcard permissions required for ECS task execution and monitoring",
                "appliesTo": ["Resource::*"],
            }
        )

    NagSuppressions.add_resource_suppressions(
        lambda_role,
        suppressions,
        apply_to_children=True,
    )


def suppress_sagemaker_role_findings(sagemaker_role):
    """Add suppressions for SageMaker execution role.

    Args:
        sagemaker_role: The SageMaker execution role
    """
    # AWS managed policies required for SageMaker
    managed_policies = [
        "AmazonSageMakerFullAccess",
        "AmazonSageMakerClusterInstanceRolePolicy",
        "AmazonSageMakerFeatureStoreAccess",
        "AmazonSageMakerModelGovernanceUseAccess",
        "AmazonSageMakerModelRegistryFullAccess",
        "AmazonSageMakerGroundTruthExecution",
        "AmazonSageMakerPipelinesIntegrations",
        "AmazonSageMakerCanvasFullAccess",
    ]

    suppressions = [
        {
            "id": "AwsSolutions-IAM4",
            "reason": "AWS managed policies required for SageMaker Studio functionality - maintained by AWS",
            "appliesTo": [f"Policy::arn:<AWS::Partition>:iam::aws:policy/{policy}" for policy in managed_policies],
        }
    ]

    # Wildcard permissions for data science workflows
    suppressions.extend(
        [
            {
                "id": "AwsSolutions-IAM5",
                "reason": "S3 wildcard permissions required for data science workflows accessing multiple buckets and objects",
                "appliesTo": [
                    "Action::s3:GetBucket*",
                    "Action::s3:GetObject*",
                    "Action::s3:List*",
                    "Action::s3:Abort*",
                    "Action::s3:DeleteObject*",
                    "Resource::<*Bucket*.Arn>/*",
                ],
            },
            {
                "id": "AwsSolutions-IAM5",
                "reason": "KMS wildcard permissions required for S3 encryption in data science workflows",
                "appliesTo": [
                    "Action::kms:GenerateDataKey*",
                    "Action::kms:ReEncrypt*",
                ],
            },
            {
                "id": "AwsSolutions-IAM5",
                "reason": "Lambda invocation permissions with version suffix required for data export pipelines",
                "appliesTo": [
                    "Resource::<*Lambda*.Arn>:*",
                ],
            },
            {
                "id": "HIPAA.Security-IAMNoInlinePolicy",
                "reason": "Inline policy required for SageMaker-specific permissions tailored to this deployment",
            },
        ]
    )

    NagSuppressions.add_resource_suppressions(
        sagemaker_role,
        suppressions,
        apply_to_children=True,
    )


def suppress_vpc_endpoint_security_group_findings(security_group: Construct, endpoint_name: str):
    """Applies common NagSuppressions to VPC endpoint security groups.

    These suppressions address false positives from cdk_nag when intrinsic
    functions are used in security group rules (e.g., vpc.cidr_block, database port).
    """
    NagSuppressions.add_resource_suppressions(
        security_group,
        [
            {
                "id": "CdkNagValidationFailure",
                "reason": f"{endpoint_name} security group uses intrinsic functions (Fn::GetAtt) for dynamic values - cdk_nag cannot validate at synth time",
            },
            {
                "id": "AwsSolutions-EC23",
                "reason": f"{endpoint_name} security group ingress restricted to VPC CIDR - false positive due to intrinsic function",
            },
            {
                "id": "HIPAA.Security-EC2RestrictedCommonPorts",
                "reason": f"{endpoint_name} security group ports are restricted to VPC CIDR - false positive due to intrinsic function",
            },
            {
                "id": "HIPAA.Security-EC2RestrictedSSH",
                "reason": f"{endpoint_name} security group does not expose SSH (port 22) - false positive due to intrinsic function",
            },
        ],
        apply_to_children=True,
    )
