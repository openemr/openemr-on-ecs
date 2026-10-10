"""Input handling in network, security, database, and utils helpers, exercised without a full stack."""

from unittest.mock import MagicMock

import aws_cdk as cdk
import aws_cdk.assertions as assertions
import pytest

from openemr_ecs import network as network_module
from openemr_ecs.database import DatabaseComponents
from openemr_ecs.kms_keys import KmsKeys
from openemr_ecs.network import NetworkComponents
from openemr_ecs.security import SecurityComponents
from openemr_ecs.utils import is_live_e2e_emulated

ENV = cdk.Environment(account="123456789012", region="us-west-2")


def _stack(context: dict | None = None) -> tuple[cdk.Stack, KmsKeys]:
    app = cdk.App(context=context or {})
    stack = cdk.Stack(app, "ComponentStack", env=ENV)
    return stack, KmsKeys(stack, stack.account, stack.region)


class TestLiveE2EAvailabilityZones:
    def _create_vpc(self, context: dict):
        stack, kms_keys = _stack()
        NetworkComponents(stack, "10.0.0.0/16", kms_keys).create_vpc(context)
        return assertions.Template.from_stack(stack)

    def test_zones_require_live_run_id(self):
        with pytest.raises(ValueError, match="require live_e2e_run_id"):
            self._create_vpc({"live_e2e_availability_zones": '["us-west-2a", "us-west-2b"]'})

    def test_zones_must_be_json(self):
        with pytest.raises(ValueError, match="must be a JSON array"):
            self._create_vpc({"live_e2e_run_id": "run-123456", "live_e2e_availability_zones": "us-west-2a"})

    @pytest.mark.parametrize(
        "zones",
        [
            '"us-west-2a"',
            '["us-west-2a"]',
            '["us-west-2a", "us-west-2a"]',
            '["us-east-1a", "us-east-1b"]',
            '["us-west-2a", null]',
            '["us-west-2a", "us-west-2b", "us-west-2c"]',
        ],
    )
    def test_rejects_zones_that_are_not_two_unique_regional_zones(self, zones):
        with pytest.raises(ValueError, match="two unique standard Availability Zones in the stack Region"):
            self._create_vpc({"live_e2e_run_id": "run-123456", "live_e2e_availability_zones": zones})

    def test_accepts_already_parsed_zone_list(self):
        template = self._create_vpc(
            {"live_e2e_run_id": "run-123456", "live_e2e_availability_zones": ["us-west-2b", "us-west-2d"]}
        )
        zones = {
            subnet["Properties"]["AvailabilityZone"] for subnet in template.find_resources("AWS::EC2::Subnet").values()
        }
        assert zones == {"us-west-2b", "us-west-2d"}
        template.has_resource("AWS::EC2::VPC", {"DeletionPolicy": "Delete"})


class TestAutoLoadBalancerCidr:
    def _security_groups(self, context: dict) -> assertions.Template:
        stack, kms_keys = _stack()
        network = NetworkComponents(stack, "10.0.0.0/16", kms_keys)
        network.create_security_groups(network.create_vpc({}), context)
        return assertions.Template.from_stack(stack)

    def test_auto_resolves_current_public_ip_to_host_cidr(self, monkeypatch):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"203.0.113.7\n"
        urlopen = MagicMock(return_value=response)
        monkeypatch.setattr(network_module.urllib.request, "urlopen", urlopen)

        template = self._security_groups({"security_group_ip_range_ipv4": "auto"})

        assert urlopen.call_args.args == ("https://checkip.amazonaws.com/",)
        assert urlopen.call_args.kwargs["timeout"] == 5
        template.has_resource_properties(
            "AWS::EC2::SecurityGroup",
            {
                "SecurityGroupIngress": assertions.Match.array_with(
                    [assertions.Match.object_like({"CidrIp": "203.0.113.7/32", "FromPort": 443, "ToPort": 443})]
                )
            },
        )

    def test_auto_lookup_failure_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            network_module.urllib.request, "urlopen", MagicMock(side_effect=OSError("network unreachable"))
        )
        with pytest.raises(
            ValueError, match="Failed to resolve current IP address for 'auto' mode: network unreachable"
        ):
            self._security_groups({"security_group_ip_range_ipv4": "auto"})


class TestSecurityEarlyReturns:
    def test_dns_and_certificates_skipped_without_domain(self):
        stack, kms_keys = _stack()
        assert SecurityComponents(stack, kms_keys).create_dns_and_certificates(None, None, {}) is None
        assert assertions.Template.from_stack(stack).find_resources("AWS::CertificateManager::Certificate") == {}

    def test_hosted_zone_id_is_reserved_for_live_runs(self):
        stack, kms_keys = _stack()
        with pytest.raises(ValueError, match="route53_hosted_zone_id is reserved for live E2E runs"):
            SecurityComponents(stack, kms_keys).create_dns_and_certificates(
                None, None, {"route53_domain": "example.com", "route53_hosted_zone_id": "Z0123456789"}
            )

    @pytest.mark.parametrize(
        "context",
        [{}, {"route53_domain": "example.com", "configure_ses": "false"}, {"configure_ses": "true"}],
    )
    def test_ses_skipped_unless_domain_and_flag(self, context):
        stack, kms_keys = _stack()
        assert SecurityComponents(stack, kms_keys).configure_ses(None, None, "us-west-2", context, None) == {}
        assert assertions.Template.from_stack(stack).find_resources("AWS::SES::ReceiptRuleSet") == {}


def test_rotation_slot_secrets_require_database_cluster():
    stack, kms_keys = _stack()
    with pytest.raises(ValueError, match="Database cluster must be created before slot secrets"):
        DatabaseComponents(stack, kms_keys).create_rotation_slot_secrets()


class TestIsLiveE2EEmulated:
    CONTEXT = {"live_e2e_emulated": "true"}

    @pytest.fixture(autouse=True)
    def _floci_env(self, monkeypatch):
        monkeypatch.setenv("OPENEMR_FLOCI_E2E", "1")
        monkeypatch.delenv("OPENEMR_AWS_ENDPOINT_URL", raising=False)
        monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)

    @pytest.mark.parametrize(
        "endpoint",
        ["http://localhost:4566", "https://floci:4566", "http://emulator.localhost:4566", "http://[::1]:4566"],
    )
    def test_local_endpoints_are_emulated(self, monkeypatch, endpoint):
        monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)
        assert is_live_e2e_emulated(self.CONTEXT) is True

    def test_openemr_endpoint_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("OPENEMR_AWS_ENDPOINT_URL", "https://sts.us-west-2.amazonaws.com")
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        assert is_live_e2e_emulated(self.CONTEXT) is False

    @pytest.mark.parametrize(
        "endpoint",
        [
            "",
            "localhost:4566",
            "ftp://localhost:4566",
            "http://s3.localhost.amazonaws.com",
            "https://api.aws",
            "http://10.0.0.5:4566",
        ],
    )
    def test_non_local_or_malformed_endpoints_are_not_emulated(self, monkeypatch, endpoint):
        monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)
        assert is_live_e2e_emulated(self.CONTEXT) is False

    def test_requires_explicit_floci_flag(self, monkeypatch):
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        monkeypatch.setenv("OPENEMR_FLOCI_E2E", "no")
        assert is_live_e2e_emulated(self.CONTEXT) is False

    @pytest.mark.parametrize("context", [None, {}, {"live_e2e_emulated": "false"}])
    def test_requires_context_opt_in(self, monkeypatch, context):
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        assert is_live_e2e_emulated(context) is False
