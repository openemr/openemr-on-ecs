"""Unit tests for OpenEMR ECS CDK stack.

Note: These tests are integration-level tests that require full stack synthesis.
Some tests may fail due to dependency cycles or missing context in test environment.
These issues don't affect actual deployments but indicate tests need more setup.
"""

import pytest

# Import fixtures from conftest.py
pytest_plugins = []

# Mark all stack tests as integration tests (require full stack setup)
pytestmark = pytest.mark.integration


def test_rds_cluster_created(template):
    """Test that RDS Aurora cluster is created."""
    rds_clusters = template.find_resources("AWS::RDS::DBCluster")
    assert rds_clusters, "Expected an Aurora DB cluster to be defined"


def test_load_balancer_created(template):
    """Test that Application Load Balancer is created."""
    load_balancers = template.find_resources("AWS::ElasticLoadBalancingV2::LoadBalancer")
    assert load_balancers, "Expected an Application Load Balancer to be defined"


def test_ecs_cluster_created(template):
    """Test that ECS cluster is created."""
    ecs_clusters = template.find_resources("AWS::ECS::Cluster")
    assert ecs_clusters, "Expected an ECS cluster to be defined"


def test_access_log_bucket_encrypted(template):
    """Test that S3 buckets for ALB logs are encrypted."""
    buckets = template.find_resources("AWS::S3::Bucket")
    assert buckets, "Expected S3 buckets to be defined"

    encrypted_buckets = [props for props in buckets.values() if props.get("Properties", {}).get("BucketEncryption")]
    assert encrypted_buckets, "Expected at least one encrypted bucket for ALB access logs"


def test_stack_version_output(template):
    """Test that stack version is included in outputs."""
    template.has_output("StackVersion", {})
