"""Constants and version configuration for the OpenEMR on AWS Fargate deployment."""

from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_rds as rds


class StackConstants:
    """Centralized constants for the OpenEMR stack.

    This class contains all version numbers, ports, and configuration constants
    that are used throughout the stack. Update these values when new versions
    are released or when configuration needs to change.
    """

    # Network Configuration
    DEFAULT_CIDR = "10.0.0.0/16"
    DEFAULT_SSL_REGENERATION_DAYS = 2

    # Port Configuration (DO NOT CHANGE - these are protocol standards)
    MYSQL_PORT = 3306
    VALKEY_PORT = 6379
    CONTAINER_PORT = 443  # HTTPS

    # AWS Service Versions
    # Update these when new versions are released
    EMR_SERVERLESS_RELEASE_LABEL = "emr-7.12.0"
    # Check: https://docs.aws.amazon.com/emr/latest/EMR-Serverless-UserGuide/release-versions.html

    AURORA_MYSQL_ENGINE_VERSION = rds.AuroraMysqlEngineVersion.VER_3_11_1
    # Note: When updating, verify that Bedrock integration is supported if enable_bedrock_integration is used.
    # Some newer engine versions may not have Bedrock integration enabled initially.

    LAMBDA_PYTHON_RUNTIME = _lambda.Runtime.PYTHON_3_14
    # Using Python 3.14 for latest features and security updates.
    # Update this when AWS deprecates older Python runtimes.

    # Credential Rotation Task Python Version
    CREDENTIAL_ROTATION_PYTHON_VERSION = "3.14"
    # Base image: python:{version}-slim. Update when upgrading the rotation container.

    # Container Image Version
    OPENEMR_VERSION = "8.0.0"
    # Use the second-latest tagged version for the "openemr/openemr" docker container.
    # This is typically the stable version (latest may be a release candidate).
    # Update this to match the OpenEMR version you want to deploy.
    # Check available versions: https://hub.docker.com/r/openemr/openemr/tags
