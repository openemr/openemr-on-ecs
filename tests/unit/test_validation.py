"""Unit tests for validation module."""

import pytest

from openemr_ecs.validation import (
    ValidationError,
    validate_autoscaling_percentage,
    validate_capacity_values,
    validate_context,
    validate_fargate_cpu_memory,
    validate_timeout_parameter,
)


def test_validate_context_minimal_config(minimal_context):
    """Test validation with minimal valid configuration."""
    # Should not raise
    validate_context(minimal_context)


def test_validate_context_full_config(full_context):
    """Test validation with full configuration."""
    # Should not raise
    validate_context(full_context)


def test_validate_fargate_cpu_memory_valid():
    """Test CPU/memory validation with valid combinations."""
    # Valid combinations (function expects int, not strings)
    validate_fargate_cpu_memory(256, 512)  # Should not raise
    validate_fargate_cpu_memory(512, 1024)  # Should not raise
    validate_fargate_cpu_memory(1024, 2048)  # Should not raise
    validate_fargate_cpu_memory(2048, 4096)  # Should not raise
    validate_fargate_cpu_memory(4096, 8192)  # Should not raise


def test_validate_fargate_cpu_memory_invalid():
    """Test CPU/memory validation with invalid combinations."""
    # Invalid CPU/memory ratios
    with pytest.raises(ValidationError):
        validate_fargate_cpu_memory(256, 4096)  # Too much memory for 256 CPU (max is 2048)

    with pytest.raises(ValidationError):
        validate_fargate_cpu_memory(1024, 1024)  # Not enough memory for 1024 CPU (min is 2048)

    with pytest.raises(ValidationError):
        validate_fargate_cpu_memory(2048, 4095)  # Invalid memory amount (not in valid list)


def test_validate_route53_and_certificate_config_both_provided():
    """Test validation when both route53_domain and certificate_arn are provided."""
    # Both provided should work - certificate_arn takes precedence
    context = {
        "route53_domain": "example.com",
        "certificate_arn": "arn:aws:acm:us-west-2:123456789012:certificate/12345678-1234-1234-1234-123456789012",
        "configure_ses": False,
        "enable_monitoring_alarms": False,
    }
    # Should be valid (handled in validate_context)
    validate_context(context)


def test_validate_route53_and_certificate_config_neither_provided():
    """Test validation when neither is provided (should fail - certificate is required)."""
    context = {
        "route53_domain": None,  # Neither provided - should fail
        "certificate_arn": None,
        "configure_ses": False,
        "enable_monitoring_alarms": False,
    }
    # Should raise ValidationError - certificate is required
    with pytest.raises(ValidationError, match="Either 'route53_domain' or 'certificate_arn' must be provided"):
        validate_context(context)


def test_validate_email_forwarding_config_ses_enabled_with_email():
    """Test email forwarding validation when SES is enabled with email."""
    context = {
        "configure_ses": True,
        "email_forwarding_address": "test@example.com",
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
    }
    # Should be valid
    validate_context(context)


def test_validate_email_forwarding_config_ses_enabled_without_email():
    """Test email forwarding validation when SES is enabled without email."""
    # Note: The validation only validates email format if provided, not whether it's required
    # This test validates that missing email doesn't cause an error (validation is format-only)
    context = {
        "configure_ses": True,
        "email_forwarding_address": None,
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
    }
    # Should not raise - validation only checks format when email is provided
    validate_context(context)


def test_validate_email_forwarding_config_ses_disabled():
    """Test email forwarding validation when SES is disabled."""
    context = {
        "configure_ses": False,
        "email_forwarding_address": None,
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
    }
    # Should be valid (email not required when SES disabled)
    validate_context(context)


def test_validate_context_invalid_cpu_memory():
    """Test context validation catches invalid CPU/memory."""
    context = {
        "openemr_service_fargate_cpu": 256,  # Note: validation converts to int
        "openemr_service_fargate_memory": 4096,  # Invalid: too much memory for 256 CPU (max is 2048)
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError):
        validate_context(context)


def test_validate_context_invalid_email_format():
    """Test context validation catches invalid email format."""
    context = {
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
        "email_forwarding_address": "not-an-email",  # Invalid format
    }

    with pytest.raises(ValidationError):
        validate_context(context)


def test_validate_fargate_cpu_memory_invalid_cpu():
    """Test CPU/memory validation with invalid CPU value."""
    with pytest.raises(ValidationError, match="Invalid Fargate CPU value"):
        validate_fargate_cpu_memory(128, 512)  # Invalid CPU value (not in valid_combinations)


def test_validate_autoscaling_percentage_none():
    """Test autoscaling percentage validation with None (should return default)."""
    result = validate_autoscaling_percentage(None, "test_param")
    assert result == 40


def test_validate_autoscaling_percentage_valid():
    """Test autoscaling percentage validation with valid values."""
    assert validate_autoscaling_percentage(50, "test_param") == 50
    assert validate_autoscaling_percentage(1, "test_param") == 1
    assert validate_autoscaling_percentage(100, "test_param") == 100
    assert validate_autoscaling_percentage("75", "test_param") == 75  # String should be converted


def test_validate_autoscaling_percentage_invalid_too_low():
    """Test autoscaling percentage validation with value too low."""
    with pytest.raises(ValidationError, match="must be between 1 and 100"):
        validate_autoscaling_percentage(0, "test_param")


def test_validate_autoscaling_percentage_invalid_too_high():
    """Test autoscaling percentage validation with value too high."""
    with pytest.raises(ValidationError, match="must be between 1 and 100"):
        validate_autoscaling_percentage(101, "test_param")


def test_validate_autoscaling_percentage_invalid_type():
    """Test autoscaling percentage validation with invalid type."""
    with pytest.raises(ValidationError, match="must be a valid integer"):
        validate_autoscaling_percentage("not-a-number", "test_param")

    with pytest.raises(ValidationError, match="must be a valid integer"):
        validate_autoscaling_percentage([], "test_param")


def test_validate_capacity_values_none():
    """Test capacity values validation with None (should use defaults)."""
    min_val, max_val = validate_capacity_values(None, None)
    assert min_val == 2
    assert max_val == 100


def test_validate_capacity_values_valid():
    """Test capacity values validation with valid values."""
    min_val, max_val = validate_capacity_values(5, 20)
    assert min_val == 5
    assert max_val == 20

    min_val, max_val = validate_capacity_values("10", "50")  # String should be converted
    assert min_val == 10
    assert max_val == 50


def test_validate_capacity_values_min_too_low():
    """Test capacity values validation with min < 1."""
    with pytest.raises(ValidationError, match="Minimum capacity must be at least 1"):
        validate_capacity_values(0, 10)


def test_validate_capacity_values_max_too_low():
    """Test capacity values validation with max < 1."""
    with pytest.raises(ValidationError, match="Maximum capacity must be at least 1"):
        validate_capacity_values(5, 0)


def test_validate_capacity_values_min_greater_than_max():
    """Test capacity values validation with min > max."""
    with pytest.raises(ValidationError, match="Minimum capacity.*cannot be greater than maximum capacity"):
        validate_capacity_values(20, 10)


def test_validate_timeout_parameter_none():
    """Test timeout parameter validation with None."""
    result = validate_timeout_parameter(None, "test_param")
    assert result is None


def test_validate_timeout_parameter_valid():
    """Test timeout parameter validation with valid values."""
    assert validate_timeout_parameter("30", "test_param") == "30"
    assert validate_timeout_parameter("1", "test_param") == "1"
    assert validate_timeout_parameter("3600", "test_param") == "3600"
    assert validate_timeout_parameter(60, "test_param") == "60"  # Int should be converted to string


def test_validate_timeout_parameter_invalid_zero():
    """Test timeout parameter validation with zero."""
    with pytest.raises(ValidationError, match="must be a positive integer"):
        validate_timeout_parameter("0", "test_param")


def test_validate_timeout_parameter_invalid_negative():
    """Test timeout parameter validation with negative value."""
    with pytest.raises(ValidationError, match="must be a positive integer"):
        validate_timeout_parameter("-1", "test_param")


def test_validate_timeout_parameter_invalid_type():
    """Test timeout parameter validation with invalid type."""
    with pytest.raises(ValidationError, match="must be a valid positive integer string"):
        validate_timeout_parameter("not-a-number", "test_param")

    with pytest.raises(ValidationError, match="must be a valid positive integer string"):
        validate_timeout_parameter([], "test_param")


def test_validate_context_invalid_cpu_type():
    """Test context validation catches invalid CPU type (ValueError/TypeError)."""
    context = {
        "openemr_service_fargate_cpu": "not-a-number",
        "openemr_service_fargate_memory": 2048,
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="Invalid Fargate CPU or memory values"):
        validate_context(context)


def test_validate_context_invalid_memory_type():
    """Test context validation catches invalid memory type (ValueError/TypeError)."""
    context = {
        "openemr_service_fargate_cpu": 1024,
        "openemr_service_fargate_memory": {"a": 1},  # Non-empty dict is truthy but can't be converted to int
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="Invalid Fargate CPU or memory values"):
        validate_context(context)


def test_validate_context_route53_domain_invalid_type():
    """Test context validation catches invalid route53_domain type."""
    context = {
        "route53_domain": 12345,  # Not a string
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="route53_domain must be a valid domain name"):
        validate_context(context)


def test_validate_context_route53_domain_too_short():
    """Test context validation catches route53_domain that is too short."""
    context = {
        "route53_domain": "ab",  # Too short (less than 3 chars)
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="route53_domain must be a valid domain name"):
        validate_context(context)


def test_validate_context_route53_domain_with_protocol():
    """Test context validation catches route53_domain with protocol."""
    context = {
        "route53_domain": "https://example.com",
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="route53_domain should not include protocol"):
        validate_context(context)


def test_validate_context_route53_domain_ends_with_dot():
    """Test context validation catches route53_domain ending with dot."""
    context = {
        "route53_domain": "example.com.",
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="route53_domain should not end with a dot"):
        validate_context(context)


def test_validate_context_certificate_arn_invalid_type():
    """Test context validation catches invalid certificate_arn type."""
    context = {
        "route53_domain": None,
        "certificate_arn": 12345,  # Not a string
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="certificate_arn must be a string"):
        validate_context(context)


def test_validate_context_certificate_arn_invalid_format():
    """Test context validation catches invalid certificate_arn format."""
    context = {
        "route53_domain": None,
        "certificate_arn": "invalid-arn",
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="certificate_arn must be a valid ACM certificate ARN"):
        validate_context(context)


def test_validate_context_ipv4_range_invalid_type():
    """Test context validation catches invalid IPv4 range type."""
    context = {
        "security_group_ip_range_ipv4": 12345,  # Not a string
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="security_group_ip_range_ipv4 must be a string CIDR block"):
        validate_context(context)


def test_validate_context_ipv4_range_invalid_format():
    """Test context validation catches invalid IPv4 range format."""
    context = {
        "security_group_ip_range_ipv4": "invalid-cidr",
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="security_group_ip_range_ipv4 must be a valid IPv4 CIDR block"):
        validate_context(context)


def test_validate_context_ipv6_range_invalid_type():
    """Test context validation catches invalid IPv6 range type."""
    context = {
        "security_group_ip_range_ipv6": 12345,  # Not a string
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="security_group_ip_range_ipv6 must be a string CIDR block"):
        validate_context(context)


def test_validate_context_ipv6_range_invalid_format():
    """Test context validation catches invalid IPv6 range format."""
    context = {
        "security_group_ip_range_ipv6": "invalid-ipv6",  # Missing /
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="security_group_ip_range_ipv6 must be a valid IPv6 CIDR block"):
        validate_context(context)


def test_validate_context_email_forwarding_invalid_type():
    """Test context validation catches invalid email_forwarding_address type."""
    context = {
        "email_forwarding_address": 12345,  # Not a string
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="email_forwarding_address must be a string"):
        validate_context(context)


def test_validate_context_email_forwarding_invalid_format_2():
    """Test context validation catches invalid email format (additional test)."""
    context = {
        "email_forwarding_address": "@example.com",  # Missing local part
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="email_forwarding_address must be a valid email address"):
        validate_context(context)


def test_validate_context_invalid_autoscaling_percentage():
    """Test context validation catches invalid autoscaling percentage."""
    context = {
        "openemr_service_fargate_cpu_autoscaling_percentage": 150,  # Invalid: > 100
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="must be between 1 and 100"):
        validate_context(context)


def test_validate_context_invalid_capacity_min_greater_than_max():
    """Test context validation catches invalid capacity (min > max)."""
    context = {
        "openemr_service_fargate_minimum_capacity": 20,
        "openemr_service_fargate_maximum_capacity": 10,
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="Minimum capacity.*cannot be greater than maximum capacity"):
        validate_context(context)


def test_validate_context_invalid_timeout_parameter():
    """Test context validation catches invalid timeout parameter."""
    context = {
        "net_read_timeout": "0",  # Invalid: must be positive
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="must be a positive integer"):
        validate_context(context)


def test_validate_context_bedrock_timeout_validation():
    """Test context validation validates timeout when Bedrock is enabled."""
    context = {
        "enable_bedrock_integration": "true",
        "aurora_ml_inference_timeout": "0",  # Invalid: must be positive
        "route53_domain": "example.com",  # Certificate required
        "certificate_arn": None,
        "configure_ses": False,
    }

    with pytest.raises(ValidationError, match="must be a positive integer"):
        validate_context(context)
