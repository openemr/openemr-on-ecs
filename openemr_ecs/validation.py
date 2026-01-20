"""Validation utilities for context values and configuration."""

from typing import Any, Dict, Optional


class ValidationError(Exception):
    """Raised when context validation fails."""

    pass


def validate_fargate_cpu_memory(cpu: int, memory: int) -> None:
    """Validate Fargate CPU and memory are compatible.

    Fargate has specific CPU/memory combinations that are valid.
    See: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html

    Args:
        cpu: CPU units (256, 512, 1024, 2048, 4096)
        memory: Memory in MiB

    Raises:
        ValidationError: If CPU/memory combination is invalid
    """
    # Valid Fargate combinations (CPU: [memory options in MiB])
    valid_combinations = {
        256: [512, 1024, 2048],
        512: [1024, 2048, 3072, 4096],
        1024: [2048, 3072, 4096, 5120, 6144, 7168, 8192],
        2048: [4096, 5120, 6144, 7168, 8192, 9216, 10240, 11264, 12288, 13312, 14336, 15360, 16384],
        4096: [
            8192,
            9216,
            10240,
            11264,
            12288,
            13312,
            14336,
            15360,
            16384,
            17408,
            18432,
            19456,
            20480,
            21504,
            22528,
            23552,
            24576,
            25600,
            26624,
            27648,
            28672,
            29696,
            30720,
        ],
    }

    if cpu not in valid_combinations:
        raise ValidationError(f"Invalid Fargate CPU value: {cpu}. Valid values are: {list(valid_combinations.keys())}")

    if memory not in valid_combinations[cpu]:
        raise ValidationError(
            f"Invalid Fargate memory {memory} MiB for CPU {cpu}. Valid memory values: {valid_combinations[cpu]}"
        )


def validate_autoscaling_percentage(value: Optional[Any], name: str) -> int:
    """Validate autoscaling percentage is in valid range.

    Args:
        value: The percentage value to validate
        name: Name of the parameter for error messages

    Returns:
        The validated percentage as an integer

    Raises:
        ValidationError: If value is not in range 1-100
    """
    if value is None:
        return 40  # Default

    try:
        percentage = int(value)
        if percentage < 1 or percentage > 100:
            raise ValidationError(f"{name} must be between 1 and 100, got: {percentage}")
        return percentage
    except (ValueError, TypeError) as _:
        raise ValidationError(f"{name} must be a valid integer between 1 and 100, got: {value}")


def validate_capacity_values(min_capacity: Optional[Any], max_capacity: Optional[Any]) -> tuple[int, int]:
    """Validate min and max capacity values.

    Args:
        min_capacity: Minimum capacity value
        max_capacity: Maximum capacity value

    Returns:
        Tuple of (validated_min, validated_max)

    Raises:
        ValidationError: If values are invalid or min > max
    """
    min_val = int(min_capacity) if min_capacity is not None else 2
    max_val = int(max_capacity) if max_capacity is not None else 100

    if min_val < 1:
        raise ValidationError(f"Minimum capacity must be at least 1, got: {min_val}")

    if max_val < 1:
        raise ValidationError(f"Maximum capacity must be at least 1, got: {max_val}")

    if min_val > max_val:
        raise ValidationError(f"Minimum capacity ({min_val}) cannot be greater than maximum capacity ({max_val})")

    return min_val, max_val


def validate_timeout_parameter(value: Optional[str], name: str) -> Optional[str]:
    """Validate timeout parameter is a valid positive integer string.

    Args:
        value: The timeout value to validate
        name: Name of the parameter for error messages

    Returns:
        The validated timeout string or None if not provided

    Raises:
        ValidationError: If value is not a valid positive integer
    """
    if value is None:
        return None

    try:
        timeout = int(value)
        if timeout < 1:
            raise ValidationError(f"{name} must be a positive integer, got: {timeout}")
        return str(timeout)
    except (ValueError, TypeError) as _:
        raise ValidationError(f"{name} must be a valid positive integer string, got: {value}")


def validate_context(context: Dict[str, Any]) -> None:
    """Validate all context values for deployment safety.

    This function performs comprehensive validation of all context parameters
    to catch configuration errors before deployment.

    Args:
        context: CDK context dictionary

    Raises:
        ValidationError: If any validation fails
    """
    # Validate Fargate CPU and memory
    cpu = context.get("openemr_service_fargate_cpu") or 1024
    memory = context.get("openemr_service_fargate_memory") or 2048

    try:
        cpu_int = int(cpu) if cpu is not None else 1024
        memory_int = int(memory) if memory is not None else 2048
        validate_fargate_cpu_memory(cpu_int, memory_int)
    except (ValueError, TypeError) as e:
        raise ValidationError(f"Invalid Fargate CPU or memory values: {e}")

    # Validate autoscaling percentages
    validate_autoscaling_percentage(
        context.get("openemr_service_fargate_cpu_autoscaling_percentage"),
        "openemr_service_fargate_cpu_autoscaling_percentage",
    )
    validate_autoscaling_percentage(
        context.get("openemr_service_fargate_memory_autoscaling_percentage"),
        "openemr_service_fargate_memory_autoscaling_percentage",
    )

    # Validate capacity values
    validate_capacity_values(
        context.get("openemr_service_fargate_minimum_capacity"), context.get("openemr_service_fargate_maximum_capacity")
    )

    # Validate timeout parameters (if provided)
    validate_timeout_parameter(context.get("net_read_timeout"), "net_read_timeout")
    validate_timeout_parameter(context.get("net_write_timeout"), "net_write_timeout")
    validate_timeout_parameter(context.get("wait_timeout"), "wait_timeout")
    validate_timeout_parameter(context.get("connect_timeout"), "connect_timeout")
    validate_timeout_parameter(context.get("max_execution_time"), "max_execution_time")

    # Check if Bedrock integration is enabled (using same logic as utils.is_true)
    bedrock_enabled = context.get("enable_bedrock_integration")
    if bedrock_enabled and str(bedrock_enabled).lower() == "true":
        validate_timeout_parameter(context.get("aurora_ml_inference_timeout"), "aurora_ml_inference_timeout")

    # Validate Route53 domain and certificate configuration
    # REQUIREMENT: A certificate is required for HTTPS (end-to-end encryption)
    # Either route53_domain (for automatic certificate issuance) OR certificate_arn must be provided
    route53_domain = context.get("route53_domain")
    certificate_arn = context.get("certificate_arn")

    # Normalize None/null values - CDK CLI may pass "null" as a string when using -c key=null
    if route53_domain == "null" or route53_domain is None:
        route53_domain = None
    if certificate_arn == "null" or certificate_arn is None:
        certificate_arn = None

    # Require at least one certificate source for HTTPS (end-to-end encryption)
    if not route53_domain and not certificate_arn:
        raise ValidationError(
            "Either 'route53_domain' or 'certificate_arn' must be provided for HTTPS (end-to-end encryption). "
            "For automated certificate management, provide 'route53_domain' - ACM will automatically issue, "
            "validate, and renew the certificate. Alternatively, provide 'certificate_arn' to use an existing ACM certificate."
        )

    # Note: If both certificate_arn and route53_domain are provided, certificate_arn takes precedence
    # This is allowed - the code handles this correctly by using certificate_arn

    if route53_domain:
        # Validate domain format (basic check)
        if not isinstance(route53_domain, str) or len(route53_domain) < 3:
            raise ValidationError(f"route53_domain must be a valid domain name, got: {route53_domain}")
        # Domain should not start with http:// or https://
        if route53_domain.startswith(("http://", "https://")):
            raise ValidationError(
                f"route53_domain should not include protocol (http:// or https://), got: {route53_domain}"
            )
        # Domain should not end with a dot
        if route53_domain.endswith("."):
            raise ValidationError(f"route53_domain should not end with a dot, got: {route53_domain}")

    # Validate certificate ARN format if provided (None/null values are allowed)
    if certificate_arn:
        if not isinstance(certificate_arn, str):
            raise ValidationError(f"certificate_arn must be a string, got: {type(certificate_arn).__name__}")
        if not certificate_arn.startswith("arn:aws:acm:"):
            raise ValidationError(
                f"certificate_arn must be a valid ACM certificate ARN starting with 'arn:aws:acm:', got: {certificate_arn[:50]}..."
            )

    # Validate IP ranges for security groups
    ipv4_range = context.get("security_group_ip_range_ipv4")
    ipv6_range = context.get("security_group_ip_range_ipv6")

    if ipv4_range:
        if not isinstance(ipv4_range, str):
            raise ValidationError(
                f"security_group_ip_range_ipv4 must be a string CIDR block or 'auto', got: {type(ipv4_range).__name__}"
            )
        # Allow "auto" value - will be resolved during deployment
        if ipv4_range == "auto":
            pass  # Valid, will be resolved at deployment time
        else:
            # Basic CIDR validation (format: x.x.x.x/y)
            import re

            cidr_pattern = r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$"
            if not re.match(cidr_pattern, ipv4_range):
                raise ValidationError(
                    f"security_group_ip_range_ipv4 must be a valid IPv4 CIDR block (e.g., '10.0.0.0/8') or 'auto', got: {ipv4_range}"
                )

    if ipv6_range:
        if not isinstance(ipv6_range, str):
            raise ValidationError(
                f"security_group_ip_range_ipv6 must be a string CIDR block, got: {type(ipv6_range).__name__}"
            )
        # Basic IPv6 CIDR validation (format: xxxx:xxxx::/y)
        if "/" not in ipv6_range:
            raise ValidationError(
                f"security_group_ip_range_ipv6 must be a valid IPv6 CIDR block (e.g., '::/0'), got: {ipv6_range}"
            )

    # Validate email forwarding address format if provided
    email_forwarding = context.get("email_forwarding_address")
    if email_forwarding:
        if not isinstance(email_forwarding, str):
            raise ValidationError(f"email_forwarding_address must be a string, got: {type(email_forwarding).__name__}")
        # Basic email format validation
        import re

        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, email_forwarding):
            raise ValidationError(f"email_forwarding_address must be a valid email address, got: {email_forwarding}")
