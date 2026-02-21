# Test Suite

This directory contains unit tests and integration tests for the OpenEMR CDK stack.

## Table of Contents

- [Overview](#overview)
- [Test Structure](#test-structure)
- [Running Tests](#running-tests)
- [Test Coverage](#test-coverage)
- [Writing Tests](#writing-tests)
- [CI/CD Integration](#cicd-integration)

## Overview

The test suite validates that the CDK stack:
- Synthesizes correctly with various configurations
- Creates expected AWS resources
- Validates configuration parameters
- Handles edge cases appropriately

**Test Types**:
- **Unit Tests**: Test individual modules and functions
- **Synthesis Tests**: Validate CDK template generation
- **Integration Tests**: Test resource creation and dependencies

## Test Structure

```
tests/
├── README.md                     # This file
└── unit/                         # Unit tests
    └── test_openemr_ecs_stack.py # Stack synthesis tests
```

### Test Organization

Tests are organized by scope:
- **Unit tests**: Test individual components in isolation
- **Stack tests**: Test complete stack synthesis and resource creation

## Running Tests

### Prerequisites

Install test dependencies:

```bash
pip install -r requirements-dev.txt
```

The `requirements-dev.txt` should include:
```
pytest>=7.0.0
pytest-cov>=4.0.0
aws-cdk-lib>=2.0.0
constructs>=10.0.0
```

### Run All Tests

```bash
# From project root
pytest tests/

# With verbose output
pytest tests/ -v

# With coverage report
pytest tests/ --cov=. --cov-report=html
```

### Run Specific Test File

```bash
pytest tests/unit/test_openemr_ecs_stack.py
```

### Run Specific Test

```bash
pytest tests/unit/test_openemr_ecs_stack.py::test_rds_cluster_created
```

### Run Tests with Output

```bash
# Show print statements
pytest tests/ -s

# Show detailed diff for assertions
pytest tests/ -vv
```

## Test Coverage

Current test coverage includes:

### Stack Synthesis Tests

- ✅ RDS cluster creation
- ✅ ECS cluster creation
- ✅ EFS file systems creation
- ✅ Security groups creation
- ✅ Load balancer creation
- ✅ Backup plan configuration
- ✅ WAF rules creation

### Configuration Validation Tests

- ✅ Context parameter validation
- ✅ CPU/memory compatibility checks
- ✅ Route53 and certificate configuration
- ✅ Email forwarding configuration

### Resource Property Tests

- ✅ Resource naming patterns
- ✅ Tag application
- ✅ IAM policy generation
- ✅ Security group rules

## Writing Tests

### Test Template

```python
import aws_cdk as cdk
import aws_cdk.assertions as assertions

# Import your stack - adjust path to match your project structure
from openemr_ecs.stack import OpenemrEcsStack


def test_resource_created():
    """Test that a specific resource is created."""
    # Arrange
    app = cdk.App()
    stack = OpenemrEcsStack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-west-2")
    )
    template = assertions.Template.from_stack(stack)
    
    # Assert
    template.has_resource_properties(
        "AWS::RDS::DBCluster",
        {
            "Engine": "aurora-mysql",
            # Add expected properties
        }
    )
```

### Common Test Patterns

**Testing Resource Existence**:
```python
# Verify exactly one RDS cluster is created
template.resource_count_is("AWS::RDS::DBCluster", 1)
```

**Testing Resource Properties**:
```python
# Use assertions.Match for flexible matching
template.has_resource_properties(
    "AWS::ECS::Cluster",
    {
        # Match any cluster name containing "cluster"
        "ClusterName": assertions.Match.string_like_regexp(".*cluster.*")
    }
)

# Or match specific properties while ignoring others
template.has_resource_properties(
    "AWS::ECS::Cluster",
    assertions.Match.object_like({
        "ClusterSettings": assertions.Match.array_with([
            assertions.Match.object_like({
                "Name": "containerInsights",
                "Value": "enabled"
            })
        ])
    })
)
```

**Testing Outputs**:
```python
# Check that an output exists with expected properties
template.has_output(
    "LoadBalancerDNS",
    assertions.Match.object_like({
        "Description": assertions.Match.any_value(),
        "Value": assertions.Match.any_value()
    })
)

# Or check for specific output value patterns
template.has_output(
    "ClusterArn",
    assertions.Match.object_like({
        "Value": assertions.Match.object_like({
            "Fn::GetAtt": assertions.Match.array_with([
                assertions.Match.string_like_regexp(".*Cluster.*"),
                "Arn"
            ])
        })
    })
)
```

**Testing IAM Policies**:
```python
template.has_resource_properties(
    "AWS::IAM::Role",
    {
        "AssumeRolePolicyDocument": {
            "Statement": assertions.Match.array_with([
                assertions.Match.object_like({
                    "Action": "sts:AssumeRole",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "ecs-tasks.amazonaws.com"
                    }
                })
            ])
        }
    }
)
```

**Testing Security Groups**:
```python
template.has_resource_properties(
    "AWS::EC2::SecurityGroup",
    {
        "SecurityGroupIngress": assertions.Match.array_with([
            assertions.Match.object_like({
                "FromPort": 443,
                "ToPort": 443,
                "IpProtocol": "tcp"
            })
        ])
    }
)
```

**Capturing Resource Values for Cross-Reference Testing**:
```python
from aws_cdk.assertions import Capture

# Capture a value for later assertions
security_group_capture = Capture()
template.has_resource_properties(
    "AWS::EC2::SecurityGroup",
    {
        "GroupDescription": security_group_capture
    }
)

# Use the captured value
assert "OpenEMR" in security_group_capture.as_string()
```

## CI/CD Integration

Tests run automatically in GitHub Actions. Check `.github/workflows/ci.yml` for the exact trigger configuration.

**Common CI triggers include**:
- Pull requests to `main` or `develop`
- Pushes to `main` or `develop`
- Manual workflow dispatch

### CI Test Pipeline

1. **Unit Tests**: Run pytest suite
2. **CDK Synthesis**: Validate stack synthesis
3. **Linting**: Check code style (if configured)

### Local Pre-commit Testing

Run tests before committing:

```bash
# Run tests
pytest tests/

# Check for linting issues (if flake8 is configured)
flake8 openemr_ecs/ tests/

# Type checking (if mypy is configured)
mypy openemr_ecs/
```

**Note**: Linting and type checking require additional configuration files (`.flake8`, `mypy.ini`, or `pyproject.toml`). Verify these exist before running.

## Test Best Practices

### 1. Test Isolation

- Each test should be independent
- Use fixtures for common setup
- Avoid sharing state between tests

```python
import pytest

@pytest.fixture
def app():
    """Create a fresh CDK app for each test."""
    return cdk.App()

@pytest.fixture
def template(app):
    """Create a stack template for testing."""
    stack = OpenemrEcsStack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-west-2")
    )
    return assertions.Template.from_stack(stack)

def test_with_fixture(template):
    """Test using the fixture."""
    template.resource_count_is("AWS::RDS::DBCluster", 1)
```

### 2. Descriptive Names

- Use clear, descriptive test names
- Follow pattern: `test_<what>_<expected_behavior>`
- Examples: `test_rds_cluster_uses_aurora_mysql`, `test_efs_filesystem_is_encrypted`

### 3. Arrange-Act-Assert

- **Arrange**: Set up test conditions
- **Act**: Execute the code under test
- **Assert**: Verify expected outcomes

### 4. Test Edge Cases

- Test with minimal configuration
- Test with maximum configuration
- Test with invalid configurations (expect errors)
- Test error conditions

```python
import pytest

def test_invalid_cpu_memory_combination_raises_error():
    """Test that invalid CPU/memory combinations are rejected."""
    app = cdk.App(context={
        "cpu": 256,
        "memory": 8192  # Invalid: 256 CPU doesn't support 8GB memory
    })
    
    with pytest.raises(ValueError, match="Invalid CPU/memory combination"):
        OpenemrEcsStack(app, "TestStack")
```

### 5. Use Match Utilities

CDK's `assertions.Match` provides flexible matching:

| Method | Use Case |
|--------|----------|
| `Match.any_value()` | Match any non-null value |
| `Match.absent()` | Verify property doesn't exist |
| `Match.object_like({})` | Partial object matching |
| `Match.array_with([])` | Array contains elements |
| `Match.string_like_regexp()` | Regex string matching |
| `Match.serialized_json()` | Match JSON strings |

## Debugging Tests

### Enable Debugging Output

```bash
# Show detailed output
pytest tests/ -vv -s

# Stop on first failure
pytest tests/ -x

# Show local variables on failure
pytest tests/ --tb=long

# Run only failed tests from last run
pytest tests/ --lf
```

### Inspect Generated Template

```python
import json

def test_debug_template(template):
    """Debug test to view full template."""
    # Print full template as formatted JSON
    print(json.dumps(template.to_json(), indent=2))
    
    # Find all resources of a specific type
    resources = template.find_resources("AWS::RDS::DBCluster")
    print(json.dumps(resources, indent=2))
```

### Common Issues

**Import Errors**:
```bash
# Verify package is importable
python -c "from openemr_ecs.stack import OpenemrEcsStack"

# Check PYTHONPATH includes project root
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

- Ensure `__init__.py` files exist in all package directories
- Verify the import path matches your project structure

**Synthesis Failures**:
- Check CDK context configuration
- Verify all required parameters are provided
- Ensure environment (account/region) is specified for environment-aware stacks
- Review CDK synthesis error messages for missing dependencies

**Assertion Failures**:
- Use `template.to_json()` to compare actual vs expected
- Check CloudFormation resource type names are correct (e.g., `AWS::RDS::DBCluster` not `AWS::RDS::Cluster`)
- Verify property names match CloudFormation spec (case-sensitive)
- Use `Match.object_like()` for partial matching when you don't need to verify all properties

**Test Discovery Issues**:
```bash
# Verify pytest can find tests
pytest tests/ --collect-only

# Check test file naming (must start with test_ or end with _test.py)
```

## Future Test Additions

Planned test coverage expansions:

- [ ] Integration tests with actual AWS resources
- [ ] Configuration validation edge cases
- [ ] Cleanup automation tests
- [ ] Backup and restore functionality tests
- [ ] Monitoring and alarm tests
- [ ] Security group rule validation
- [ ] IAM policy correctness
- [ ] Snapshot testing for template stability

## Related Documentation

- [TESTING-PLAN.md](../TESTING-PLAN.md) - Comprehensive testing plan
- [TEST-EXECUTION-GUIDE.md](../TEST-EXECUTION-GUIDE.md) - Manual test execution
- [scripts/stress-test.sh](../scripts/stress-test.sh) - Stress testing script
- [pytest Documentation](https://docs.pytest.org/) - Pytest framework docs
- [CDK Assertions Module](https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.assertions.html) - CDK assertions API reference
- [CDK Testing Guide](https://docs.aws.amazon.com/cdk/v2/guide/testing.html) - AWS CDK testing
