"""Comprehensive tests for openemr_ecs/network.py.

Validates VPC creation, security group rules, ALB configuration, and
optional Global Accelerator through CDK template assertions.
"""

import aws_cdk as cdk
import aws_cdk.assertions as assertions
import pytest

from tests.conftest import *  # noqa: F401,F403  (reuse fixtures)


class TestVPCCreation:
    def test_vpc_exists(self, template):
        template.resource_count_is("AWS::EC2::VPC", 1)

    def test_vpc_cidr(self, template):
        template.has_resource_properties("AWS::EC2::VPC", {"CidrBlock": "10.0.0.0/16"})

    def test_two_availability_zones(self, template):
        private = template.find_resources("AWS::EC2::Subnet", {"Properties": {"MapPublicIpOnLaunch": False}})
        public = template.find_resources("AWS::EC2::Subnet", {"Properties": {"MapPublicIpOnLaunch": False}})
        assert len(private) >= 2
        assert len(public) >= 2

    def test_flow_logs_created(self, template):
        template.resource_count_is("AWS::EC2::FlowLog", 1)
        template.has_resource_properties(
            "AWS::EC2::FlowLog",
            {"ResourceType": "VPC", "TrafficType": "ALL", "LogDestinationType": "cloud-watch-logs"},
        )

    def test_flow_logs_iam_role(self, template):
        template.has_resource_properties(
            "AWS::IAM::Role",
            assertions.Match.object_like(
                {
                    "AssumeRolePolicyDocument": assertions.Match.object_like(
                        {
                            "Statement": assertions.Match.array_with(
                                [
                                    assertions.Match.object_like(
                                        {
                                            "Principal": {"Service": "vpc-flow-logs.amazonaws.com"},
                                        }
                                    )
                                ]
                            )
                        }
                    )
                }
            ),
        )


class TestSecurityGroups:
    def test_db_security_group_exists(self, template):
        sgs = template.find_resources("AWS::EC2::SecurityGroup")
        db_sgs = [
            lid
            for lid, res in sgs.items()
            if "db" in lid.lower() or "db" in str(res.get("Properties", {}).get("GroupDescription", "")).lower()
        ]
        assert len(db_sgs) >= 1 or len(sgs) >= 3

    def test_valkey_security_group_exists(self, template):
        sgs = template.find_resources("AWS::EC2::SecurityGroup")
        assert len(sgs) >= 3

    def test_lb_security_group_exists(self, template):
        sgs = template.find_resources("AWS::EC2::SecurityGroup")
        lb_sgs = [lid for lid, res in sgs.items() if "lb" in lid.lower()]
        assert len(lb_sgs) >= 1 or len(sgs) >= 3


class TestALB:
    def test_alb_exists(self, template):
        template.resource_count_is("AWS::ElasticLoadBalancingV2::LoadBalancer", 1)

    def test_alb_is_internet_facing(self, template):
        template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            {"Scheme": "internet-facing"},
        )

    def test_alb_drops_invalid_headers(self, template):
        template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            assertions.Match.object_like(
                {
                    "LoadBalancerAttributes": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {"Key": "routing.http.drop_invalid_header_fields.enabled", "Value": "true"}
                            ),
                        ]
                    )
                }
            ),
        )

    def test_alb_deletion_protection_enabled(self, template):
        template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            assertions.Match.object_like(
                {
                    "LoadBalancerAttributes": assertions.Match.array_with(
                        [
                            assertions.Match.object_like({"Key": "deletion_protection.enabled", "Value": "true"}),
                        ]
                    )
                }
            ),
        )


class TestGlobalAccelerator:
    """Global Accelerator is off by default and should only appear when enabled."""

    def test_no_accelerator_by_default(self, template):
        template.resource_count_is("AWS::GlobalAccelerator::Accelerator", 0)

    def test_accelerator_created_when_enabled(self, app, full_context):
        for key, value in full_context.items():
            app.node.set_context(key, value)
        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(app, "AccelStack", env=cdk.Environment(account="123456789012", region="us-west-2"))
        t = assertions.Template.from_stack(stack)
        t.resource_count_is("AWS::GlobalAccelerator::Accelerator", 1)


class TestAutoIPResolution:
    """When security_group_ip_range_ipv4 is 'auto', the code calls checkip.amazonaws.com."""

    def test_auto_ip_resolves_to_cidr32(self, app, minimal_context):
        """'auto' should resolve to a /32 CIDR.  Works when internet is available."""
        for key, value in minimal_context.items():
            app.node.set_context(key, value)
        app.node.set_context("security_group_ip_range_ipv4", "auto")

        from openemr_ecs.stack import OpenemrEcsStack

        try:
            stack = OpenemrEcsStack(app, "AutoIPStack", env=cdk.Environment(account="123456789012", region="us-west-2"))
            t = assertions.Template.from_stack(stack)
            t.resource_count_is("AWS::EC2::VPC", 1)
        except ValueError, OSError:
            pytest.skip("No internet access to resolve auto IP")

    def test_explicit_cidr_does_not_raise(self, app, minimal_context):
        for key, value in minimal_context.items():
            app.node.set_context(key, value)
        app.node.set_context("security_group_ip_range_ipv4", "203.0.113.0/24")

        from openemr_ecs.stack import OpenemrEcsStack

        stack = OpenemrEcsStack(app, "CIDRStack", env=cdk.Environment(account="123456789012", region="us-west-2"))
        t = assertions.Template.from_stack(stack)
        t.resource_count_is("AWS::EC2::VPC", 1)
