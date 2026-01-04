"""Pytest configuration and shared fixtures.

This module provides reusable fixtures for CDK stack testing.
"""

import json
from pathlib import Path

import aws_cdk as cdk
import aws_cdk.assertions as assertions
import pytest

from openemr_ecs.stack import OpenemrEcsStack


@pytest.fixture
def app():
    """Create CDK app for testing."""
    project_root = Path(__file__).resolve().parents[2]
    context = {}
    cdk_json = project_root / "cdk.json"
    if cdk_json.exists():
        context = json.loads(cdk_json.read_text()).get("context", {})
    return cdk.App(context=context)


@pytest.fixture
def stack(app):
    """Create OpenEMR stack with minimal config."""
    # Set minimal context values to avoid validation errors
    # Don't set keys that should use defaults (like CPU/memory) - let them use defaults
    minimal_ctx = {
        "route53_domain": "example.com",  # Certificate required - use route53_domain for minimal config
        "certificate_arn": None,
        "configure_ses": False,
        "enable_monitoring_alarms": False,
        "create_serverless_analytics_environment": False,
        "enable_global_accelerator": False,
        "enable_bedrock_integration": False,
        "enable_data_api": False,
    }
    for key, value in minimal_ctx.items():
        app.node.set_context(key, value)

    return OpenemrEcsStack(
        app,
        "TestStack",
        env=cdk.Environment(account="123456789012", region="us-west-2"),
    )


@pytest.fixture
def template(stack):
    """Get CloudFormation template from stack."""
    return assertions.Template.from_stack(stack)


@pytest.fixture
def minimal_context():
    """Minimal valid context configuration."""
    return {
        "route53_domain": "example.com",  # Certificate required - use route53_domain for minimal config
        "certificate_arn": None,
        "configure_ses": False,
        "enable_monitoring_alarms": False,
        "create_serverless_analytics_environment": False,
        "enable_global_accelerator": False,
        "enable_bedrock_integration": False,
        "enable_data_api": False,
        "openemr_service_fargate_cpu": None,
        "openemr_service_fargate_memory": None,
    }


@pytest.fixture
def full_context():
    """Full-featured context configuration."""
    return {
        "route53_domain": "example.com",
        "certificate_arn": None,
        "configure_ses": True,
        "email_forwarding_address": "test@example.com",
        "enable_global_accelerator": True,
        "enable_monitoring_alarms": True,
        "monitoring_email": "monitor@example.com",
        "create_serverless_analytics_environment": True,
        "enable_bedrock_integration": True,
        "enable_data_api": True,
        "openemr_service_fargate_cpu": "1024",
        "openemr_service_fargate_memory": "2048",
    }
