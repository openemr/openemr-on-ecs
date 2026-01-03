"""Helper utilities for testing."""

from typing import Any, Dict, Optional

from aws_cdk.assertions import Template


def create_minimal_context(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create minimal context for stack testing.

    Args:
        overrides: Optional dictionary to override default values

    Returns:
        Context dictionary suitable for CDK stack creation
    """
    default_context = {
        "openemr_service_fargate_minimum_capacity": 2,
        "openemr_service_fargate_maximum_capacity": 10,
        "openemr_service_fargate_cpu": 1024,
        "openemr_service_fargate_memory": 2048,
        "openemr_service_fargate_cpu_autoscaling_percentage": 40,
        "openemr_service_fargate_memory_autoscaling_percentage": 40,
        "rds_deletion_protection": False,
        "enable_monitoring_alarms": "false",
        "enable_stack_termination_protection": False,
    }

    if overrides:
        default_context.update(overrides)

    return default_context


def create_full_context(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create full-featured context for stack testing.

    Args:
        overrides: Optional dictionary to override default values

    Returns:
        Context dictionary with all optional features enabled
    """
    default_context = create_minimal_context()

    full_context = {
        **default_context,
        "enable_monitoring_alarms": "true",
        "monitoring_email": "test@example.com",
        "enable_ecs_exec": "true",
        "activate_openemr_apis": "true",
        "enable_bedrock_integration": "true",
        "enable_data_api": "true",
        "enable_global_accelerator": "true",
        "enable_patient_portal": "true",
        "configure_ses": "true",
        "email_forwarding_address": "test@example.com",
    }

    if overrides:
        full_context.update(overrides)

    return full_context


def assert_resource_exists(template: Template, resource_type: str, logical_id: str) -> None:
    """Assert that a resource exists in the template.

    Args:
        template: CDK Template instance
        resource_type: CloudFormation resource type (e.g., 'AWS::EC2::VPC')
        logical_id: Logical ID of the resource
    """
    resources = template.find_resources(resource_type)
    assert logical_id in resources, f"Resource {logical_id} of type {resource_type} not found"


def assert_resource_has_property(
    template: Template, resource_type: str, logical_id: str, property_path: str, expected_value: Any
) -> None:
    """Assert that a resource has a specific property value.

    Args:
        template: CDK Template instance
        resource_type: CloudFormation resource type
        logical_id: Logical ID of the resource
        property_path: Dot-separated path to the property (e.g., 'Properties.VpcId')
        expected_value: Expected value
    """
    resources = template.find_resources(resource_type)
    assert logical_id in resources, f"Resource {logical_id} not found"

    resource = resources[logical_id]
    props = resource.get("Properties", {})

    # Navigate nested properties
    parts = property_path.split(".")
    current = props
    for part in parts[:-1]:
        current = current.get(part, {})

    actual_value = current.get(parts[-1])
    assert (
        actual_value == expected_value
    ), f"Property {property_path} in {logical_id} has value {actual_value}, expected {expected_value}"
