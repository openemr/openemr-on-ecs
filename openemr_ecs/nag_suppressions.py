"""Helper functions for CDK Nag suppressions (cdk-nag v3 / aws-cdk-lib Validations API)."""

from aws_cdk import Validations
from constructs import Construct


def acknowledge_findings(construct: Construct, findings: list) -> None:
    """Acknowledge (suppress) cdk-nag findings on a construct and its descendants.

    This is the cdk-nag v3 equivalent of the old cdk-nag v2
    ``NagSuppressions.add_resource_suppressions`` helper. cdk-nag v3 removed
    ``NagSuppressions`` in favor of CDK's native ``Validations.of().acknowledge()``
    API. Acknowledgments made on a construct apply to that construct and all of
    its descendants in the construct tree, so v2's ``apply_to_children=True``
    is now the (only) default behavior.

    We write directly to the CDK "acknowledged rules" construct metadata (the
    same metadata key/mechanism ``Validations.of(construct).acknowledge()``
    uses internally) instead of calling that method directly, for two reasons:

    1. This app registers cdk-nag packs with ``verbose=True`` (see app.py),
       which routes violations through CDK's Annotations system. In that mode
       cdk-nag only recognizes acknowledgments whose ID is qualified with the
       pack name, e.g. "AwsSolutions::AwsSolutions-IAM5[Resource::*]" rather
       than the bare "AwsSolutions-IAM5[Resource::*]" shown in cdk-nag's own
       docs (which assume non-verbose output). The public
       ``Validations.acknowledge()`` API does not add this pack-name prefix.
    2. aws-cdk-lib's ``Validations.acknowledge()`` has a confirmed upstream bug
       (https://github.com/cdklabs/cdk-nag/issues/2351): its ID validation
       rejects any finding ID containing more than one "::", which includes
       every AWS managed policy ARN (e.g.
       "AwsSolutions-IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/...]").

    cdk-nag itself reads this metadata directly with no such restrictions, so
    writing it ourselves (with the pack-name prefix) is safe and is how these
    findings must be acknowledged given verbose=True.

    Args:
        construct: The construct to acknowledge findings on.
        findings: A list of dicts, each with:
            - "id": the rule ID (e.g. "AwsSolutions-IAM5"). The pack name
              (the segment before the first "-") is used as the metadata
              key prefix required by verbose-mode acknowledgment.
            - "reason": the reason for the acknowledgment
            - "appliesTo" (optional): a list of finding-specific suffixes.
              v3 has no bulk suppression, so each suffix becomes its own
              granular "RuleId[suffix]" acknowledgment.
    """
    for finding in findings:
        rule_id = finding["id"]
        reason = finding["reason"]
        pack_name = rule_id.split("-", 1)[0]
        applies_to = finding.get("appliesTo")
        if applies_to:
            for suffix in applies_to:
                construct.node.add_metadata(
                    Validations.ACKNOWLEDGED_RULES_METADATA_KEY, {f"{pack_name}::{rule_id}[{suffix}]": reason}
                )
        else:
            construct.node.add_metadata(
                Validations.ACKNOWLEDGED_RULES_METADATA_KEY, {f"{pack_name}::{rule_id}": reason}
            )


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

    acknowledge_findings(lambda_function, suppressions)


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

    acknowledge_findings(lambda_role, suppressions)


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

    acknowledge_findings(sagemaker_role, suppressions)


def suppress_vpc_endpoint_security_group_findings(security_group: Construct, endpoint_name: str):
    """Applies common acknowledgments to VPC endpoint security groups.

    These acknowledgments address false positives from cdk_nag when intrinsic
    functions are used in security group rules (e.g., vpc.cidr_block, database port).
    """
    acknowledge_findings(
        security_group,
        [
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
    )
