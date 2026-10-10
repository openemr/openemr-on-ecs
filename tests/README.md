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
├── README.md                              # This file
├── __init__.py                            # Package initialization
├── conftest.py                            # Shared pytest fixtures
├── helpers.py                             # Test helper utilities
└── unit/                                  # Unit tests
    ├── __init__.py                        # Package initialization
    ├── test_openemr_ecs_stack.py          # Stack synthesis tests
    ├── test_analytics_comprehensive.py    # Analytics module tests
    ├── test_comprehensive_examples.py     # Comprehensive example tests
    ├── test_compute_comprehensive.py      # Compute module tests
    ├── test_database_comprehensive.py     # Database module tests
    ├── test_lambda_handlers.py            # Lambda function tests
    ├── test_monitoring.py                 # Monitoring module tests
    ├── test_security_comprehensive.py     # Security module tests
    ├── test_utils.py                      # Utility function tests
    ├── test_validation.py                 # Validation function tests
    └── test_version.py                    # Version management tests
```

### Test Organization

Tests are organized by scope:
- **Unit tests**: Test individual modules and functions in isolation
- **Stack tests**: Test complete stack synthesis and resource creation
- **Comprehensive tests**: In-depth testing of specific modules (analytics, compute, database, security)

## Running Tests

### Prerequisites

Install test dependencies:

```bash
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` provides the CDK application dependencies;
`requirements-dev.txt` provides the pinned test, quality, security, and MCP
tooling.

### Run All Tests

A single `pytest` run from the project root covers both this directory and
`tools/credential-rotation/tests/`. `pyproject.toml` sets `testpaths` to both
directories and adds `tools/credential-rotation/src` to `pythonpath`, so no
extra path setup is needed.

```bash
# From project root: the same selection CI uses, with the 100% coverage gate
pytest --cov -n auto -m "not integration and not floci"

# With a line-by-line report of anything not covered
pytest --cov --cov-report=term-missing -n auto -m "not integration and not floci"

# With an HTML coverage report in htmlcov/
pytest --cov --cov-report=html -n auto -m "not integration and not floci"

# With verbose output
pytest -v -m "not integration and not floci"
```

`-n auto` runs tests in parallel with pytest-xdist. Tests marked `floci` need
Docker and the Floci AWS emulator, and tests marked `integration` are excluded
from normal runs. See the [Floci guide](../docs/maintainers/floci.md).

> [!TIP]
> Leave off `--cov` when you run a single file or test. Coverage is enforced
> for the whole codebase, so a partial run with `--cov` always reports a
> coverage failure.

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

### Coverage Requirement

Line coverage must be **100% for all Python code**. The
`[tool.coverage.run]` and `[tool.coverage.report]` sections of
`pyproject.toml` measure the repository root plus `diagrams`, `lambda`,
`scripts`, and `tools/openemr-import-worker`, and set `fail_under = 100`.
Test files and generated or local-state directories are excluded, as is a
short, reviewed list of files that only run against real infrastructure:

- `tools/openemr-import-worker/ci_live_mysql_import.py`
- `tools/openemr-import-worker/ci_prepare_mysql_fixtures.py`
- `scripts/api_endpoint_test.py`
- `scripts/test_data_api.py`

Lines under `if __name__ == "__main__":` and `if TYPE_CHECKING:` are excluded.
New code needs tests that keep the total at 100%. Add a file to the omit list
only after review, and only when it can't run without live infrastructure.

### What the Tests Cover

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
    stack = OpenemrEcsStack(app, "TestStack", env=cdk.Environment(account="111111111111", region="us-west-2"))
    template = assertions.Template.from_stack(stack)

    # Assert
    template.has_resource_properties(
        "AWS::RDS::DBCluster",
        {
            "Engine": "aurora-mysql",
            # Add expected properties
        },
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
    },
)

# Or match specific properties while ignoring others
template.has_resource_properties(
    "AWS::ECS::Cluster",
    assertions.Match.object_like(
        {
            "ClusterSettings": assertions.Match.array_with(
                [assertions.Match.object_like({"Name": "containerInsights", "Value": "enabled"})]
            )
        }
    ),
)
```

**Testing Outputs**:
```python
# Check that an output exists with expected properties
template.has_output(
    "LoadBalancerDNS",
    assertions.Match.object_like({"Description": assertions.Match.any_value(), "Value": assertions.Match.any_value()}),
)

# Or check for specific output value patterns
template.has_output(
    "ClusterArn",
    assertions.Match.object_like(
        {
            "Value": assertions.Match.object_like(
                {"Fn::GetAtt": assertions.Match.array_with([assertions.Match.string_like_regexp(".*Cluster.*"), "Arn"])}
            )
        }
    ),
)
```

**Testing IAM Policies**:
```python
template.has_resource_properties(
    "AWS::IAM::Role",
    {
        "AssumeRolePolicyDocument": {
            "Statement": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                        }
                    )
                ]
            )
        }
    },
)
```

**Testing Security Groups**:
```python
template.has_resource_properties(
    "AWS::EC2::SecurityGroup",
    {
        "SecurityGroupIngress": assertions.Match.array_with(
            [assertions.Match.object_like({"FromPort": 443, "ToPort": 443, "IpProtocol": "tcp"})]
        )
    },
)
```

**Capturing Resource Values for Cross-Reference Testing**:
```python
from aws_cdk.assertions import Capture

# Capture a value for later assertions
security_group_capture = Capture()
template.has_resource_properties("AWS::EC2::SecurityGroup", {"GroupDescription": security_group_capture})

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

1. **Unit Tests**: One pytest run over `tests/` and
   `tools/credential-rotation/tests/` with the 100% coverage gate
2. **CDK Synthesis**: Validate stack synthesis and the 16-configuration matrix
3. **Code Quality**: `ruff format --check`, `ruff check`, and `mypy`
4. **Security Scan**: `pip-audit` (Ruff's security rules run with Code Quality)

Every job is described in [CI and automation](../docs/maintainers/ci.md).

### Local Pre-commit Testing

Run the same checks as CI before committing:

```bash
# Run tests with the coverage gate
pytest --cov -n auto -m "not integration and not floci"

# Check formatting and linting (including the security rules)
ruff format --check .
ruff check .

# Type checking (the exact path list CI uses)
mypy app.py openemr_ecs/ diagrams/ tools/_shared.py tools/version_audit/ \
  tools/openemr_import/ tools/openemr-import-worker/worker.py \
  tools/credential-rotation/src/ scripts/check_npm_audit.py \
  scripts/test-cdk-synthesis.py
```

Use `ruff format .` and `ruff check --fix .` to apply fixes. Ruff and mypy are
configured in `pyproject.toml`.
`pre-commit run` runs the Ruff hooks and other file checks on staged changes;
see the [maintainer guide](../docs/maintainers/README.md#pre-commit-hooks).

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
    stack = OpenemrEcsStack(app, "TestStack", env=cdk.Environment(account="111111111111", region="us-west-2"))
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
    app = cdk.App(
        context={
            "cpu": 256,
            "memory": 8192,  # Invalid: 256 CPU doesn't support 8GB memory
        }
    )

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

- [scripts/stress-test.sh](../scripts/stress-test.sh) - Stress testing script
- [scripts/test-cdk-synthesis.py](../scripts/test-cdk-synthesis.py) - Configuration matrix testing
- [Local testing guide](../docs/guides/local-testing.md) - Docker Compose startup tests
- [pytest Documentation](https://docs.pytest.org/) - Pytest framework docs
- [CDK Assertions Module](https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.assertions.html) - CDK assertions API reference
- [CDK Testing Guide](https://docs.aws.amazon.com/cdk/v2/guide/testing.html) - AWS CDK testing
