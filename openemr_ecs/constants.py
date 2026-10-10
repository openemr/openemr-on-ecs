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
    EMR_SERVERLESS_RELEASE_LABEL = "emr-7.14.0"
    # Check: https://docs.aws.amazon.com/emr/latest/EMR-Serverless-UserGuide/release-versions.html

    AURORA_MYSQL_ENGINE_VERSION = rds.AuroraMysqlEngineVersion.VER_3_12_0
    # Note: When updating, verify that Bedrock integration is supported if enable_bedrock_integration is used.
    # Some newer engine versions may not have Bedrock integration enabled initially.

    LAMBDA_PYTHON_RUNTIME = _lambda.Runtime.PYTHON_3_14
    # Using Python 3.14 for latest features and security updates.
    # Update this when AWS deprecates older Python runtimes.

    # Credential Rotation Task Python Version
    CREDENTIAL_ROTATION_PYTHON_VERSION = "3.14"
    # Base image: python:{version}-slim. Update when upgrading the rotation container.

    # Container Image Version
    OPENEMR_VERSION = "8.4.1"
    OPENEMR_ARM64_DIGEST = "sha256:22bc7022e0fb3f88909d87a428849038a722a736634c1dbbae2f07e92ab362ad"
    # Require an ARM64 Docker tag that matches an official, non-prerelease
    # OpenEMR GitHub release and verify this immutable platform digest. The
    # version audit enforces both constraints.
