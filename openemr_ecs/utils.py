"""Shared utility functions for the OpenEMR CDK stack."""

from typing import Optional


def is_true(val: Optional[str]) -> bool:
    """Check if a context value represents a true boolean.

    Context values from CDK are strings, so we need to normalize them.
    This function handles None, empty strings, and various true representations.

    Args:
        val: The value to check (typically from context.get())

    Returns:
        True if the value represents true, False otherwise
    """
    if val is None:
        return False
    return str(val).lower() == "true"


def get_resource_suffix(context: dict) -> str:
    """Get the resource suffix from context, with a safe default.

    Args:
        context: CDK context dictionary

    Returns:
        The resource suffix string, or 'default' if not provided
    """
    result = context.get("openemr_resource_suffix", "default")
    return str(result) if result is not None else "default"
