"""Comprehensive tests for analytics module.

This test file provides extensive coverage for analytics.py which handles
SageMaker Studio, EMR Serverless, and data export configurations.
"""

import pytest
from aws_cdk import App, Environment, assertions


class TestSageMakerConfiguration:
    """Test SageMaker Studio configuration."""

    def test_sagemaker_created_when_enabled(self):
        """Test SageMaker domain is created when enabled."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("enable_sagemaker", "true")
        app.node.set_context("create_serverless_analytics_environment", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should create SageMaker domain
        template.resource_count_is("AWS::SageMaker::Domain", 1)

    def test_sagemaker_not_created_when_disabled(self):
        """Test SageMaker not created when disabled."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("enable_sagemaker", "false")
        app.node.set_context("create_serverless_analytics_environment", "false")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should not create SageMaker domain
        template.resource_count_is("AWS::SageMaker::Domain", 0)

    def test_sagemaker_execution_role_created(self):
        """Test SageMaker execution role is created."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("enable_sagemaker", "true")
        app.node.set_context("create_serverless_analytics_environment", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should have IAM roles for SageMaker
        roles = template.find_resources("AWS::IAM::Role")

        sagemaker_role_found = False
        for role_props in roles.values():
            props = role_props.get("Properties", {})
            assume_policy = props.get("AssumeRolePolicyDocument", {})
            statements = assume_policy.get("Statement", [])
            for stmt in statements:
                principal = stmt.get("Principal", {})
                if "sagemaker.amazonaws.com" in str(principal):
                    sagemaker_role_found = True
                    break

        assert sagemaker_role_found


class TestEMRServerlessConfiguration:
    """Test EMR Serverless configuration."""

    def test_emr_serverless_created_when_enabled(self):
        """Test EMR Serverless application is created when enabled."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("enable_emr_serverless", "true")
        app.node.set_context("create_serverless_analytics_environment", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should create EMR Serverless application
        template.resource_count_is("AWS::EMRServerless::Application", 1)

    def test_emr_serverless_not_created_when_disabled(self):
        """Test EMR Serverless not created when disabled."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("enable_emr_serverless", "false")
        app.node.set_context("create_serverless_analytics_environment", "false")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should not create EMR Serverless application
        template.resource_count_is("AWS::EMRServerless::Application", 0)


class TestDataExportConfiguration:
    """Test data export S3 buckets and Lambda functions."""

    def test_export_buckets_created_when_analytics_enabled(self):
        """Test S3 export buckets created when analytics enabled."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("create_serverless_analytics_environment", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should have S3 buckets for data export
        buckets = template.find_resources("AWS::S3::Bucket")

        # Should have multiple buckets including export buckets
        assert len(buckets) >= 2

    def test_export_lambdas_created(self):
        """Test Lambda functions for data export are created."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("create_serverless_analytics_environment", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should have Lambda functions for export
        functions = template.find_resources("AWS::Lambda::Function")

        # Should have at least one Lambda function
        assert len(functions) >= 1


class TestEFSExportTask:
    """Test EFS to S3 export ECS task."""

    def test_efs_export_task_created(self):
        """Test ECS task for EFS to S3 export is created."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("create_serverless_analytics_environment", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should have task definitions for export
        task_defs = template.find_resources("AWS::ECS::TaskDefinition")

        # Should have at least one task definition
        assert len(task_defs) >= 1


class TestAnalyticsModule:
    """Test analytics module structure."""

    def test_analytics_components_class_exists(self):
        """Test AnalyticsComponents class can be imported."""
        from openemr_ecs.analytics import AnalyticsComponents

        assert AnalyticsComponents is not None
        assert callable(AnalyticsComponents)


class TestVPCEndpointsForAnalytics:
    """Test VPC endpoints for SageMaker."""

    def test_sagemaker_vpc_endpoints_created(self):
        """Test VPC endpoints for SageMaker are created when enabled."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("enable_sagemaker", "true")
        app.node.set_context("create_serverless_analytics_environment", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should have VPC endpoints for SageMaker
        endpoints = template.find_resources("AWS::EC2::VPCEndpoint")

        # Should have at least one VPC endpoint
        assert len(endpoints) >= 1


class TestAnalyticsKMSKeys:
    """Test KMS encryption for analytics resources."""

    def test_analytics_kms_key_created(self):
        """Test KMS key for analytics is created."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        app.node.set_context("create_serverless_analytics_environment", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Should have KMS keys for encryption
        keys = template.find_resources("AWS::KMS::Key")

        # Should have at least one KMS key
        assert len(keys) >= 1


class TestConditionalAnalyticsCreation:
    """Test conditional creation of analytics resources."""

    @pytest.mark.parametrize(
        "analytics_enabled,expected_sagemaker",
        [
            ("true", True),
            ("false", False),
            (None, False),
        ],
    )
    def test_analytics_based_on_context(self, analytics_enabled, expected_sagemaker):
        """Test analytics resources created based on context."""
        app = App()
        app.node.set_context("route53_domain", "example.com")
        if analytics_enabled is not None:
            app.node.set_context("create_serverless_analytics_environment", analytics_enabled)
            if analytics_enabled == "true":
                app.node.set_context("enable_sagemaker", "true")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(
            app,
            "TestStack",
            env=Environment(account="123456789012", region="us-west-2"),
        )
        template = assertions.Template.from_stack(stack)

        # Check SageMaker domain count
        sagemaker_domains = template.find_resources("AWS::SageMaker::Domain")

        if expected_sagemaker:
            assert len(sagemaker_domains) >= 1
        else:
            assert len(sagemaker_domains) == 0
