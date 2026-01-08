#!/usr/bin/env python3
"""Comprehensive CDK synthesis test script for testing all configuration combinations.

This script tests CDK stack synthesis with various configuration combinations
to ensure all features work correctly and pass cdk-nag validation.

Usage:
    python test-cdk-synthesis.py [--verbose] [--fail-fast]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


def log(message: str) -> None:
    """Print a log message with timestamp."""
    print(f"{Colors.BLUE}[INFO]{Colors.NC} {message}")


def success(message: str) -> None:
    """Print a success message."""
    print(f"{Colors.GREEN}✓{Colors.NC} {message}")


def error(message: str) -> None:
    """Print an error message."""
    print(f"{Colors.RED}✗{Colors.NC} {message}")


def warning(message: str) -> None:
    """Print a warning message."""
    print(f"{Colors.YELLOW}⚠{Colors.NC} {message}")


# Test configurations with various feature combinations
TEST_CONFIGURATIONS = [
    {
        "name": "minimal",
        "description": "Minimal configuration with only required features",
        "config": {
            "enable_global_accelerator": False,
            "enable_bedrock_integration": False,
            "enable_data_api": False,
            "create_serverless_analytics_environment": False,
            "enable_monitoring_alarms": False,
        },
    },
    {
        "name": "minimal-with-monitoring",
        "description": "Minimal configuration with monitoring alarms enabled",
        "config": {
            "enable_global_accelerator": False,
            "enable_bedrock_integration": False,
            "enable_data_api": False,
            "create_serverless_analytics_environment": False,
            "enable_monitoring_alarms": True,
            "monitoring_email": "test@example.com",
        },
    },
    {
        "name": "standard",
        "description": "Standard configuration with Bedrock and Data API",
        "config": {
            "enable_global_accelerator": False,
            "enable_bedrock_integration": True,
            "enable_data_api": True,
            "create_serverless_analytics_environment": False,
            "enable_monitoring_alarms": False,
        },
    },
    {
        "name": "standard-with-monitoring",
        "description": "Standard configuration with monitoring alarms",
        "config": {
            "enable_global_accelerator": False,
            "enable_bedrock_integration": True,
            "enable_data_api": True,
            "create_serverless_analytics_environment": False,
            "enable_monitoring_alarms": True,
            "monitoring_email": "test@example.com",
        },
    },
    {
        "name": "full-featured",
        "description": "Full-featured configuration with all optional features",
        "config": {
            "enable_global_accelerator": True,
            "enable_bedrock_integration": True,
            "enable_data_api": True,
            "create_serverless_analytics_environment": True,
            "enable_monitoring_alarms": False,
        },
    },
    {
        "name": "full-featured-with-monitoring",
        "description": "Full-featured configuration with monitoring alarms",
        "config": {
            "enable_global_accelerator": True,
            "enable_bedrock_integration": True,
            "enable_data_api": True,
            "create_serverless_analytics_environment": True,
            "enable_monitoring_alarms": True,
            "monitoring_email": "test@example.com",
        },
    },
    {
        "name": "api-portal-enabled",
        "description": "Configuration with APIs and patient portal enabled",
        "config": {
            "enable_global_accelerator": False,
            "enable_bedrock_integration": False,
            "enable_data_api": False,
            "create_serverless_analytics_environment": False,
            "enable_monitoring_alarms": False,
            "activate_openemr_apis": True,
            "enable_patient_portal": True,
        },
    },
    {
        "name": "cloudtrail-enabled",
        "description": "Configuration with CloudTrail logging enabled",
        "config": {
            "enable_global_accelerator": False,
            "enable_bedrock_integration": False,
            "enable_data_api": False,
            "create_serverless_analytics_environment": False,
            "enable_monitoring_alarms": False,
            "enable_long_term_cloudtrail_monitoring": True,
        },
    },
]

# Dummy certificate ARN for synthesis testing
CERT_ARN = "arn:aws:acm:us-west-2:123456789012:certificate/00000000-0000-0000-0000-000000000000"


def update_cdk_json(config: Dict[str, any], cdk_json_path: Path, backup_path: Path) -> None:
    """Temporarily update cdk.json with test configuration.
    
    Args:
        config: Configuration dictionary to apply
        cdk_json_path: Path to cdk.json file
        backup_path: Path to backup cdk.json file
    """
    # Backup original cdk.json
    with open(cdk_json_path, 'r') as f:
        original_config = json.load(f)
    
    with open(backup_path, 'w') as f:
        json.dump(original_config, f, indent=2)
    
    # Update certificate_arn
    original_config['context']['certificate_arn'] = CERT_ARN
    
    # Apply test configuration
    for key, value in config.items():
        original_config['context'][key] = value
    
    # Write updated config
    with open(cdk_json_path, 'w') as f:
        json.dump(original_config, f, indent=2)


def restore_cdk_json(cdk_json_path: Path, backup_path: Path) -> None:
    """Restore original cdk.json from backup.
    
    Args:
        cdk_json_path: Path to cdk.json file
        backup_path: Path to backup cdk.json file
    """
    if backup_path.exists():
        with open(backup_path, 'r') as f:
            original_config = json.load(f)
        
        with open(cdk_json_path, 'w') as f:
            json.dump(original_config, f, indent=2)
        
        backup_path.unlink()


def test_configuration(
    config_name: str,
    config_description: str,
    config: Dict[str, any],
    cdk_json_path: Path,
    verbose: bool = False,
) -> Tuple[bool, str]:
    """Test a single configuration.
    
    Args:
        config_name: Name of the configuration
        config_description: Description of the configuration
        config: Configuration dictionary
        cdk_json_path: Path to cdk.json file
        verbose: Whether to print verbose output
    
    Returns:
        Tuple of (success, error_message)
    """
    log(f"Testing configuration: {config_name}")
    log(f"Description: {config_description}")
    
    backup_path = cdk_json_path.parent / "cdk.json.backup"
    
    try:
        # Update cdk.json with test configuration
        update_cdk_json(config, cdk_json_path, backup_path)
        
        # Run cdk synth
        log("Running cdk synth...")
        result = subprocess.run(
            ["cdk", "synth", "--no-lookups"],
            cwd=cdk_json_path.parent,
            capture_output=True,
            text=True,
        )
        
        if result.returncode == 0:
            success(f"Synthesis successful for {config_name}")
            
            # Check for cdk-nag errors in output
            if "[Error at" in result.stdout or "[Error at" in result.stderr:
                error_msg = "CDK Nag errors found in output"
                error(error_msg)
                if verbose:
                    print("\n--- CDK Nag Errors ---")
                    for line in (result.stdout + result.stderr).split('\n'):
                        if "[Error at" in line:
                            print(line)
                    print("--- End CDK Nag Errors ---\n")
                return False, error_msg
            
            return True, ""
        else:
            error_msg = f"Synthesis failed for {config_name}"
            error(error_msg)
            if verbose:
                print("\n--- Error Output ---")
                print(result.stderr)
                print(result.stdout)
                print("--- End Error Output ---\n")
            return False, result.stderr
    
    finally:
        # Always restore original cdk.json
        restore_cdk_json(cdk_json_path, backup_path)


def main() -> int:
    """Run all configuration tests.
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    parser = argparse.ArgumentParser(description="Test CDK synthesis with various configurations")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print verbose output")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failure")
    args = parser.parse_args()
    
    print("=" * 60)
    print("CDK Synthesis Test Suite")
    print("=" * 60)
    print()
    print(f"Testing {len(TEST_CONFIGURATIONS)} configurations...")
    print()
    
    # Get path to cdk.json
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    cdk_json_path = project_root / "cdk.json"
    
    if not cdk_json_path.exists():
        error(f"cdk.json not found at {cdk_json_path}")
        return 1
    
    # Run tests
    passed = 0
    failed = 0
    failed_configs = []
    
    for test_config in TEST_CONFIGURATIONS:
        print("-" * 60)
        result, error_msg = test_configuration(
            test_config["name"],
            test_config["description"],
            test_config["config"],
            cdk_json_path,
            args.verbose,
        )
        print()
        
        if result:
            passed += 1
        else:
            failed += 1
            failed_configs.append((test_config["name"], error_msg))
            if args.fail_fast:
                warning("Stopping on first failure (--fail-fast)")
                break
    
    # Print summary
    print("=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"{Colors.GREEN}Passed:{Colors.NC} {passed}")
    print(f"{Colors.RED}Failed:{Colors.NC} {failed}")
    print()
    
    if failed == 0:
        success("All tests passed!")
        return 0
    else:
        error("Some tests failed:")
        for config_name, error_msg in failed_configs:
            print(f"  - {config_name}")
            if args.verbose and error_msg:
                print(f"    Error: {error_msg[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

