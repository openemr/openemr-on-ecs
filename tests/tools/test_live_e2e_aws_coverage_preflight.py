"""Offline coverage for live E2E AWS preflight, ownership, and probe helpers.

Every AWS client is an in-memory fake; nothing here can reach AWS.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import BotoCoreError, ClientError

import tools.live_e2e.aws as aws_module
from tools._shared import ToolError, hash_account_id
from tools.live_e2e.aws import (
    _LOCAL_CLEANUP_ACTIONS,
    _WRITE_ACTIONS,
    LiveE2EAws,
    _iam_principal_arn,
    _ignore_transient_cleanup_or_raise,
    _is_missing_resource_error,
    _is_retryable_cleanup_error,
    _is_unsupported_emulator_operation,
    _required_output,
)

ACCOUNT = "123456789012"
CALLER = f"arn:aws:iam::{ACCOUNT}:user/operator"
DOMAIN = "e2e.example.org"


def _client_error(code: str, message: str = "", operation: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


class _Session:
    """Session fake that returns preconfigured per-service clients."""

    def __init__(self, clients: dict[str, Any]) -> None:
        self.clients = clients
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def client(self, service: str, **kwargs: Any) -> Any:
        self.calls.append((service, kwargs))
        return self.clients[service]


def _paginator(*pages: dict[str, Any]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = list(pages)
    return paginator


def _allow_all(**kwargs: Any) -> dict[str, Any]:
    return {
        "EvaluationResults": [{"EvalActionName": action, "EvalDecision": "allowed"} for action in kwargs["ActionNames"]]
    }


def _preflight_clients(*, hosted_zone_id: str = "/hostedzone/z123abc") -> dict[str, MagicMock]:
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": ACCOUNT, "Arn": CALLER}
    sts.assume_role.return_value = {"Credentials": {}}

    route53 = MagicMock()
    route53.list_hosted_zones_by_name.return_value = {
        "HostedZones": [
            {"Id": hosted_zone_id, "Name": f"{DOMAIN}.", "Config": {"PrivateZone": False}},
            {"Id": "/hostedzone/OTHER", "Name": f"other.{DOMAIN}."},
        ]
    }
    route53.get_paginator.return_value = _paginator(
        {
            "ResourceRecordSets": [
                {"Name": f"{DOMAIN}.", "Type": "NS"},
                {"Name": f"{DOMAIN}.", "Type": "SOA"},
            ]
        }
    )

    cloudformation = MagicMock()
    cloudformation.describe_stacks.return_value = {
        "Stacks": [
            {
                "StackStatus": "UPDATE_COMPLETE",
                "Outputs": [{"OutputKey": "BootstrapVersion", "OutputValue": "27"}],
                "Parameters": [{"ParameterKey": "Qualifier", "ParameterValue": "hnb659fds"}],
            }
        ]
    }

    ec2 = MagicMock()
    ec2.describe_availability_zones.return_value = {
        "AvailabilityZones": [
            {"ZoneName": "us-east-1c", "State": "available"},
            {"ZoneName": "us-east-1a", "State": "available", "ZoneType": "availability-zone"},
            {"ZoneName": "us-east-1b", "State": "available"},
            {"ZoneName": "us-east-1d", "State": "impaired"},
            {"ZoneName": "us-east-1-bos-1a", "State": "available", "ZoneType": "local-zone"},
        ]
    }
    ec2.get_paginator.return_value = _paginator({"Vpcs": [{}, {}]})
    ec2.describe_addresses.return_value = {"Addresses": [{}]}

    rds = MagicMock()
    rds.get_paginator.return_value = _paginator({"DBClusters": [{}]}, {"DBClusters": []})

    iam = MagicMock()
    iam.simulate_principal_policy.side_effect = _allow_all

    quotas = MagicMock()

    def quota(**kwargs: Any) -> dict[str, Any]:
        if kwargs["QuotaCode"] == "L-3032A538":
            return {
                "Quota": {
                    "Value": 16.0,
                    "UsageMetric": {
                        "MetricNamespace": "AWS/Usage",
                        "MetricName": "ResourceCount",
                        "MetricDimensions": {"Service": "Fargate", "Resource": "vCPU"},
                    },
                }
            }
        return {"Quota": {"Value": 10.0}}

    quotas.get_service_quota.side_effect = quota

    cloudwatch = MagicMock()
    cloudwatch.get_metric_statistics.return_value = {"Datapoints": [{"Maximum": 4.0}, {"Maximum": 6.0}, {}]}

    return {
        "sts": sts,
        "route53": route53,
        "cloudformation": cloudformation,
        "ec2": ec2,
        "ecs": MagicMock(),
        "rds": rds,
        "efs": MagicMock(),
        "kms": MagicMock(),
        "backup": MagicMock(),
        "wafv2": MagicMock(),
        "iam": iam,
        "service-quotas": quotas,
        "cloudwatch": cloudwatch,
    }


def _emulated(session: Any) -> LiveE2EAws:
    return LiveE2EAws(
        region="us-east-1",
        session=session,
        endpoint_url="http://127.0.0.1:4566",
        emulated=True,
    )


# --- error classification -------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    (
        (RuntimeError("does not exist"), False),
        (_client_error("ClusterNotFoundException"), True),
        (_client_error("Other", "Bucket Does Not Exist"), True),
        (_client_error("Other", "The NAT gateway cannot be found"), True),
        (_client_error("Other", "Invalid NAT gateway id"), True),
        (_client_error("Other", "access denied"), False),
    ),
)
def test_missing_resource_classification(exc: BaseException, expected: bool) -> None:
    assert _is_missing_resource_error(exc) is expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    (
        (RuntimeError("in use"), False),
        (_client_error("DependencyViolation"), True),
        (_client_error("Other", "Subnet has dependencies"), True),
        (_client_error("Other", "Task definition is in the process of being deleted"), True),
        (_client_error("Other", "access denied"), False),
    ),
)
def test_retryable_cleanup_classification(exc: BaseException, expected: bool) -> None:
    assert _is_retryable_cleanup_error(exc) is expected


def test_ignore_transient_cleanup_swallows_known_errors_and_wraps_others() -> None:
    _ignore_transient_cleanup_or_raise(_client_error("NotFoundException"), action="x")
    _ignore_transient_cleanup_or_raise(_client_error("ResourceInUse"), action="x")

    original = _client_error("AccessDenied", "nope")
    with pytest.raises(ToolError, match="Delete thing failed") as raised:
        _ignore_transient_cleanup_or_raise(original, action="Delete thing")
    assert raised.value.__cause__ is original


@pytest.mark.parametrize(
    ("exc", "expected"),
    (
        (_client_error("UnknownOperationException"), True),
        (_client_error("501"), True),
        (_client_error("Other", "Operation not implemented yet"), True),
        (_client_error("Other", "Unknown operation ListThings"), True),
        (RuntimeError("Not Implemented by floci"), True),
        (_client_error("AccessDenied", "denied"), False),
        (RuntimeError("boom"), False),
    ),
)
def test_unsupported_emulator_operation_classification(exc: BaseException, expected: bool) -> None:
    assert _is_unsupported_emulator_operation(exc) is expected


def test_required_output_rejects_missing_or_empty_values() -> None:
    assert _required_output({"A": "value"}, "A") == "value"
    with pytest.raises(ToolError, match="Stack output is missing: B"):
        _required_output({"B": ""}, "B")


def test_iam_principal_arn_derivation() -> None:
    assert (
        _iam_principal_arn("arn:aws-us-gov:sts::123456789012:assumed-role/Deployer/session-1")
        == "arn:aws-us-gov:iam::123456789012:role/Deployer"
    )
    assert _iam_principal_arn(CALLER) == CALLER
    role = "arn:aws:iam::123456789012:role/Ops"
    assert _iam_principal_arn(role) == role
    with pytest.raises(ToolError, match="Cannot derive"):
        _iam_principal_arn("arn:aws:sts::123456789012:assumed-role//session")
    with pytest.raises(ToolError, match="Cannot derive"):
        _iam_principal_arn("arn:aws:sts::123456789012:federated-user/bob")


# --- construction and clients ----------------------------------------------


def test_endpoint_url_requires_explicit_emulated_mode() -> None:
    with pytest.raises(ToolError, match="explicit emulated mode"):
        LiveE2EAws(region="us-east-1", session=SimpleNamespace(), endpoint_url="http://127.0.0.1:4566")


def test_emulator_session_uses_environment_or_test_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []
    monkeypatch.setattr(aws_module.boto3, "Session", lambda **kwargs: created.append(kwargs) or "session")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("AWS_SESSION_TOKEN", "")

    adapter = LiveE2EAws(region="us-west-2", endpoint_url="http://127.0.0.1:4566", emulated=True)

    assert adapter.session == "session"
    assert created == [
        {
            "region_name": "us-west-2",
            "aws_access_key_id": "test",
            "aws_secret_access_key": "test",
            "aws_session_token": None,
        }
    ]

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "fake-token")
    LiveE2EAws(region="us-west-2", endpoint_url="http://127.0.0.1:4566", emulated=True)
    assert created[-1]["aws_access_key_id"] == "AKIAFAKE"
    assert created[-1]["aws_session_token"] == "fake-token"


def test_profile_session_is_created_from_profile_and_region(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []
    monkeypatch.setattr(aws_module.boto3, "Session", lambda **kwargs: created.append(kwargs) or "session")

    adapter = LiveE2EAws(region="eu-west-1", profile_name="e2e")

    assert adapter.session == "session"
    assert created == [{"profile_name": "e2e", "region_name": "eu-west-1"}]


def test_clients_are_cached_and_carry_endpoint_and_region() -> None:
    session = _Session({"ecs": object(), "route53": object()})
    adapter = _emulated(session)

    first = adapter.client("ecs")
    assert adapter.client("ecs") is first
    adapter.client("route53", global_service=True)

    assert session.calls == [
        ("ecs", {"region_name": "us-east-1", "endpoint_url": "http://127.0.0.1:4566"}),
        ("route53", {"endpoint_url": "http://127.0.0.1:4566"}),
    ]


# --- identity and preflight ------------------------------------------------


def test_identity_hashes_account_and_rejects_invalid_responses() -> None:
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": ACCOUNT, "Arn": CALLER}
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"sts": sts}))

    assert adapter.identity() == {
        "account_id": ACCOUNT,
        "account_hash": hash_account_id(ACCOUNT),
        "caller_arn": CALLER,
    }

    for response in ({"Account": "123", "Arn": CALLER}, {"Account": ACCOUNT, "Arn": "user/operator"}, {}):
        sts.get_caller_identity.return_value = response
        with pytest.raises(ToolError, match="invalid caller identity"):
            adapter.identity()


def test_preflight_happy_path_runs_every_probe_against_real_aws_shape() -> None:
    clients = _preflight_clients()
    adapter = LiveE2EAws(region="us-east-1", session=_Session(clients))

    checks, details = adapter.preflight(
        approved_account=ACCOUNT,
        route53_domain=f"{DOMAIN}.",
        bootstrap_stack_name="CDKToolkit",
    )

    assert details == {
        "bootstrap_version": 27,
        "hosted_zone_id": "Z123ABC",
        "availability_zones": ["us-east-1a", "us-east-1b"],
    }
    by_name = {check.name: check for check in checks}
    assert all(check.status == "pass" for check in checks)
    assert by_name["aws-identity"].detail == f"account={hash_account_id(ACCOUNT)}; region=us-east-1"
    assert by_name["cdk-bootstrap"].detail == "CDKToolkit is UPDATE_COMPLETE at template version 27"
    assert by_name["availability-zones"].detail == "3 standard Availability Zones available"
    assert by_name["cdk-bootstrap-role-assumption"].detail == "assumed 4 required bootstrap roles"
    assert by_name["deployment-write-permissions"].detail == (
        f"{len(_WRITE_ACTIONS)} representative actions allowed for the execution role"
    )
    assert by_name["quota-vpc-count"].detail == "configured=10; observed-usage=2; required-headroom=1"
    assert by_name["quota-elastic-ip-count"].detail == "configured=10; observed-usage=1; required-headroom=2"
    assert by_name["quota-fargate-vcpu"].detail == "configured=16; observed-usage=6; required-headroom=4"
    assert by_name["quota-rds-clusters"].detail == "configured=10; observed-usage=1; required-headroom=1"
    assert by_name["ec2-read"].detail == "API probe succeeded"

    clients["route53"].list_hosted_zones_by_name.assert_called_once_with(DNSName=DOMAIN, MaxItems="2")
    clients["cloudformation"].describe_stacks.assert_called_once_with(StackName="CDKToolkit")
    clients["ec2"].describe_availability_zones.assert_called_once_with(AllAvailabilityZones=False)
    assumed = [call.kwargs["RoleArn"] for call in clients["sts"].assume_role.call_args_list]
    assert assumed == [
        f"arn:aws:iam::{ACCOUNT}:role/cdk-hnb659fds-{purpose}-role-{ACCOUNT}-us-east-1"
        for purpose in ("deploy", "file-publishing", "image-publishing", "lookup")
    ]
    simulated = [call.kwargs for call in clients["iam"].simulate_principal_policy.call_args_list]
    assert simulated[0]["PolicySourceArn"] == (
        f"arn:aws:iam::{ACCOUNT}:role/cdk-hnb659fds-cfn-exec-role-{ACCOUNT}-us-east-1"
    )
    assert simulated[1] == {"PolicySourceArn": CALLER, "ActionNames": list(_LOCAL_CLEANUP_ACTIONS)}
    metric_call = clients["cloudwatch"].get_metric_statistics.call_args.kwargs
    assert metric_call["Namespace"] == "AWS/Usage"
    assert metric_call["Statistics"] == ["Maximum"]
    assert metric_call["Dimensions"] == [
        {"Name": "Service", "Value": "Fargate"},
        {"Name": "Resource", "Value": "vCPU"},
    ]


def test_emulated_preflight_tolerates_unsupported_reads_and_prefixes_zone_id() -> None:
    clients = _preflight_clients(hosted_zone_id="/hostedzone/abc123")
    clients["backup"].list_backup_vaults.side_effect = _client_error("UnknownOperationException")
    adapter = _emulated(_Session(clients))

    checks, details = adapter.preflight(
        approved_account=ACCOUNT,
        route53_domain=DOMAIN,
        bootstrap_stack_name="CDKToolkit",
    )

    assert details["hosted_zone_id"] == "ZABC123"
    by_name = {check.name: check for check in checks}
    assert by_name["backup-read"].detail == "floci-emulated; operation unsupported by emulator"
    assert by_name["deployment-write-permissions"].detail == (
        "floci-emulated; IAM policy simulation skipped after role existence checks"
    )
    assert by_name["local-cleanup-permissions"].detail == "floci-emulated; cleanup principal simulation skipped"
    assert by_name["quota-fargate-vcpu"].detail == ("floci-emulated; service-quotas unavailable; required-headroom=4")
    clients["iam"].get_role.assert_called_once_with(RoleName=f"cdk-hnb659fds-cfn-exec-role-{ACCOUNT}-us-east-1")
    clients["iam"].simulate_principal_policy.assert_not_called()
    clients["service-quotas"].get_service_quota.assert_not_called()


def _stub_preflight_internals(adapter: LiveE2EAws, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter, "_assert_dedicated_zone_records", lambda zone: None)
    monkeypatch.setattr(adapter, "_permission_probes", lambda: [])
    monkeypatch.setattr(adapter, "_write_permission_probes", lambda **_: ())
    monkeypatch.setattr(adapter, "_quota_probes", lambda: [])


def _mutate_private_zone(clients: dict[str, MagicMock]) -> None:
    clients["route53"].list_hosted_zones_by_name.return_value = {
        "HostedZones": [{"Id": "/hostedzone/Z1", "Name": DOMAIN, "Config": {"PrivateZone": True}}]
    }


def _mutate_bootstrap(**stack: Any) -> Any:
    def mutate(clients: dict[str, MagicMock]) -> None:
        clients["cloudformation"].describe_stacks.return_value = {"Stacks": [stack]}

    return mutate


def _mutate_az_error(clients: dict[str, MagicMock]) -> None:
    clients["ec2"].describe_availability_zones.side_effect = _client_error("UnauthorizedOperation")


def _mutate_one_az(clients: dict[str, MagicMock]) -> None:
    clients["ec2"].describe_availability_zones.return_value = {
        "AvailabilityZones": [{"ZoneName": "us-east-1a", "State": "available"}]
    }


def _mutate_bad_zone_id(clients: dict[str, MagicMock]) -> None:
    clients["route53"].list_hosted_zones_by_name.return_value = {
        "HostedZones": [{"Id": "/hostedzone/bad-id", "Name": DOMAIN}]
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (_mutate_private_zone, "must be public"),
        (_mutate_bootstrap(StackStatus="ROLLBACK_COMPLETE"), "not ready: ROLLBACK_COMPLETE"),
        (_mutate_bootstrap(StackStatus="CREATE_COMPLETE"), "valid BootstrapVersion"),
        (
            _mutate_bootstrap(
                StackStatus="CREATE_COMPLETE",
                Outputs=[{"OutputKey": "BootstrapVersion", "OutputValue": "abc"}],
            ),
            "valid BootstrapVersion",
        ),
        (
            _mutate_bootstrap(
                StackStatus="IMPORT_COMPLETE",
                Outputs=[{"OutputKey": "BootstrapVersion", "OutputValue": "20"}],
            ),
            "version 20 is too old",
        ),
        (_mutate_az_error, "Cannot resolve available deployment zones"),
        (_mutate_one_az, "at least two available standard Availability Zones"),
        (_mutate_bad_zone_id, "invalid hosted-zone ID"),
    ),
)
def test_preflight_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
    message: str,
) -> None:
    clients = _preflight_clients()
    mutate(clients)
    adapter = LiveE2EAws(region="us-east-1", session=_Session(clients))
    _stub_preflight_internals(adapter, monkeypatch)

    with pytest.raises(ToolError, match=message):
        adapter.preflight(approved_account=ACCOUNT, route53_domain=DOMAIN, bootstrap_stack_name="CDKToolkit")


def test_preflight_rejects_unapproved_account_before_other_probes() -> None:
    clients = _preflight_clients()
    adapter = LiveE2EAws(region="us-east-1", session=_Session(clients))

    with pytest.raises(ToolError, match="does not match --approved-account"):
        adapter.preflight(approved_account="999999999999", route53_domain=DOMAIN, bootstrap_stack_name="CDKToolkit")
    clients["route53"].list_hosted_zones_by_name.assert_not_called()


def test_hosted_zone_requires_exactly_one_exact_match() -> None:
    route53 = MagicMock()
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"route53": route53}))

    route53.list_hosted_zones_by_name.return_value = {"HostedZones": [{"Name": f"sub.{DOMAIN}."}]}
    with pytest.raises(ToolError, match="No exact Route 53 hosted zone"):
        adapter._hosted_zone(DOMAIN)

    route53.list_hosted_zones_by_name.return_value = {
        "HostedZones": [{"Id": "1", "Name": f"{DOMAIN}."}, {"Id": "2", "Name": DOMAIN.upper()}]
    }
    with pytest.raises(ToolError, match="Multiple exact"):
        adapter._hosted_zone(DOMAIN)

    route53.list_hosted_zones_by_name.return_value = {"HostedZones": [{"Id": "1", "Name": f"{DOMAIN}."}]}
    assert adapter._hosted_zone(DOMAIN) == {"Id": "1", "Name": f"{DOMAIN}."}


def test_dedicated_zone_records_validate_identifier_name_and_unknown_records() -> None:
    route53 = MagicMock()
    route53.get_paginator.return_value = _paginator({"ResourceRecordSets": [{"Name": f"{DOMAIN}.", "Type": "NS"}, {}]})
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"route53": route53}))

    with pytest.raises(ToolError, match="no valid identifier"):
        adapter._assert_dedicated_zone_records({"Id": "", "Name": DOMAIN})
    with pytest.raises(ToolError, match="no valid name"):
        adapter._assert_dedicated_zone_records({"Id": "/hostedzone/Z1", "Name": "."})
    with pytest.raises(ToolError, match="unexpected records: unknown:unknown"):
        adapter._assert_dedicated_zone_records({"Id": "/hostedzone/Z1", "Name": DOMAIN})

    route53.get_paginator.return_value = _paginator({"ResourceRecordSets": [{"Name": f"{DOMAIN}.", "Type": "NS"}]})
    with pytest.raises(ToolError, match="exactly its apex NS and SOA records$"):
        adapter._assert_dedicated_zone_records({"Id": "/hostedzone/Z1", "Name": DOMAIN})


# --- permission and quota probes -------------------------------------------


def test_permission_probes_wrap_real_aws_failures() -> None:
    clients = _preflight_clients()
    clients["kms"].list_aliases.side_effect = _client_error("AccessDeniedException", "denied")
    adapter = LiveE2EAws(region="us-east-1", session=_Session(clients))

    with pytest.raises(ToolError, match=r"Required AWS read probe failed \(kms-read\)"):
        adapter._permission_probes()
    clients["wafv2"].list_web_acls.assert_not_called()


def test_emulated_permission_probes_still_fail_on_supported_errors() -> None:
    clients = _preflight_clients()
    clients["ecs"].list_clusters.side_effect = _client_error("AccessDeniedException", "denied")
    adapter = _emulated(_Session(clients))

    with pytest.raises(ToolError, match=r"\(ecs-read\)"):
        adapter._permission_probes()


def test_write_permission_probes_reject_root_bad_qualifier_and_failed_assume() -> None:
    clients = _preflight_clients()
    adapter = LiveE2EAws(region="us-east-1", session=_Session(clients))
    bootstrap: dict[str, Any] = {"Parameters": []}

    with pytest.raises(ToolError, match="root credentials are not supported"):
        adapter._write_permission_probes(
            caller_arn=f"arn:aws:iam::{ACCOUNT}:root", account_id=ACCOUNT, bootstrap=bootstrap
        )
    with pytest.raises(ToolError, match="qualifier is invalid"):
        adapter._write_permission_probes(
            caller_arn=CALLER,
            account_id=ACCOUNT,
            bootstrap={"Parameters": [{"ParameterKey": "Qualifier", "ParameterValue": "bad/qual"}]},
        )

    clients["sts"].assume_role.side_effect = _client_error("AccessDenied", "denied")
    with pytest.raises(ToolError, match="Cannot assume required CDK bootstrap deploy role"):
        adapter._write_permission_probes(caller_arn=CALLER, account_id=ACCOUNT, bootstrap=bootstrap)


def test_emulated_role_existence_check_reports_missing_role() -> None:
    iam = MagicMock()
    iam.get_role.side_effect = _client_error("NoSuchEntity")
    adapter = _emulated(_Session({"iam": iam}))

    with pytest.raises(ToolError, match="Required IAM role is missing in the emulator: cdk-exec"):
        adapter._assert_role_exists(f"arn:aws:iam::{ACCOUNT}:role/cdk-exec")


def test_simulate_actions_passes_resources_and_reports_denials() -> None:
    iam = MagicMock()
    iam.simulate_principal_policy.return_value = {
        "EvaluationResults": [
            {"EvalActionName": "s3:GetObject", "EvalDecision": "allowed"},
            {"EvalActionName": "s3:PutObject", "EvalDecision": "implicitDeny"},
        ]
    }
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"iam": iam}))

    with pytest.raises(ToolError, match="role lacks required actions: s3:DeleteObject, s3:PutObject"):
        adapter._simulate_actions(
            principal_arn="arn:aws:iam::1:role/r",
            actions=("s3:GetObject", "s3:PutObject", "s3:DeleteObject"),
            label="role",
            resource_arns=("arn:aws:s3:::bucket/*",),
        )
    iam.simulate_principal_policy.assert_called_once_with(
        PolicySourceArn="arn:aws:iam::1:role/r",
        ActionNames=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
        ResourceArns=["arn:aws:s3:::bucket/*"],
    )

    iam.simulate_principal_policy.side_effect = BotoCoreError()
    with pytest.raises(ToolError, match="Cannot simulate role permissions"):
        adapter._simulate_actions(principal_arn="arn:aws:iam::1:role/r", actions=("s3:GetObject",), label="role")


def test_quota_probes_fail_on_api_errors_and_insufficient_headroom() -> None:
    clients = _preflight_clients()
    adapter = LiveE2EAws(region="us-east-1", session=_Session(clients))

    clients["service-quotas"].get_service_quota.side_effect = _client_error("NoSuchResourceException")
    with pytest.raises(ToolError, match="Cannot verify required service quota vpc-count"):
        adapter._quota_probes()

    clients["service-quotas"].get_service_quota.side_effect = None
    clients["service-quotas"].get_service_quota.return_value = {"Quota": {"Value": 2.0}}
    with pytest.raises(ToolError, match="vpc-count has 0 available; live E2E requires 1"):
        adapter._quota_probes()


@pytest.mark.parametrize(
    "quota",
    (
        {},
        {"UsageMetric": "not-a-dict"},
        {"UsageMetric": {"MetricNamespace": "AWS/Usage"}},
        {"UsageMetric": {"MetricNamespace": "AWS/Usage", "MetricName": "x", "MetricDimensions": []}},
    ),
)
def test_quota_usage_without_a_usable_metric_is_zero(quota: dict[str, Any]) -> None:
    cloudwatch = MagicMock()
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"cloudwatch": cloudwatch}))

    assert adapter._quota_usage("fargate-vcpu", quota) == 0.0
    cloudwatch.get_metric_statistics.assert_not_called()


def test_quota_usage_uses_recommended_statistic_and_defaults_to_zero() -> None:
    cloudwatch = MagicMock()
    cloudwatch.get_metric_statistics.return_value = {"Datapoints": []}
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"cloudwatch": cloudwatch}))
    quota = {
        "UsageMetric": {
            "MetricNamespace": "AWS/Usage",
            "MetricName": "ResourceCount",
            "MetricDimensions": {},
            "MetricStatisticRecommendation": "Sum",
        }
    }

    assert adapter._quota_usage("fargate-vcpu", quota) == 0.0
    call = cloudwatch.get_metric_statistics.call_args.kwargs
    assert call["Statistics"] == ["Sum"]
    assert call["Period"] == 300
    assert (call["EndTime"] - call["StartTime"]).total_seconds() >= 15 * 60


# --- stack description and ownership ---------------------------------------


def test_describe_stack_handles_missing_errors_and_empty_responses() -> None:
    cloudformation = MagicMock()
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"cloudformation": cloudformation}))

    cloudformation.describe_stacks.side_effect = _client_error("ValidationError", "Stack with id x does not exist")
    assert adapter.describe_stack("x") is None
    assert adapter._describe_stack_raw("x") is None

    denied = _client_error("AccessDenied", "denied")
    cloudformation.describe_stacks.side_effect = denied
    with pytest.raises(ClientError) as raised:
        adapter.describe_stack("x")
    assert raised.value is denied
    with pytest.raises(ClientError):
        adapter._describe_stack_raw("x")

    cloudformation.describe_stacks.side_effect = None
    cloudformation.describe_stacks.return_value = {"Stacks": []}
    assert adapter.describe_stack("x") is None
    assert adapter._describe_stack_raw("x") is None

    stack = {"StackId": "id", "StackStatus": "DELETE_COMPLETE"}
    cloudformation.describe_stacks.return_value = {"Stacks": [stack]}
    assert adapter.describe_stack("x") is stack
    assert adapter._describe_stack_raw("x") is stack


def test_emulated_describe_stack_hides_terminal_tombstones() -> None:
    cloudformation = MagicMock()
    adapter = _emulated(_Session({"cloudformation": cloudformation}))

    cloudformation.describe_stacks.return_value = {"Stacks": [{"StackId": "id", "StackStatus": "DELETE_COMPLETE"}]}
    assert adapter.describe_stack("x") is None

    cloudformation.describe_stacks.return_value = {"Stacks": [{"StackId": "id", "StackStatus": "DELETE_FAILED"}]}
    cloudformation.list_stack_resources.return_value = {
        "StackResourceSummaries": [{"ResourceStatus": "DELETE_COMPLETE"}]
    }
    assert adapter.describe_stack("x") is None
    cloudformation.list_stack_resources.assert_called_once_with(StackName="id")


def test_emulated_tombstone_requires_a_stack_reference_and_deleted_resources() -> None:
    cloudformation = MagicMock()
    cloudformation.list_stack_resources.return_value = {
        "StackResourceSummaries": [{"ResourceStatus": "DELETE_COMPLETE"}, {"ResourceStatus": "DELETE_FAILED"}]
    }
    adapter = _emulated(_Session({"cloudformation": cloudformation}))

    assert adapter._emulated_delete_tombstone_is_empty({}) is False
    assert adapter._emulated_delete_tombstone_is_empty({"StackName": "named"}) is False
    cloudformation.list_stack_resources.assert_called_once_with(StackName="named")


def test_stack_resources_and_events_follow_next_tokens() -> None:
    cloudformation = MagicMock()
    cloudformation.list_stack_resources.side_effect = [
        {"StackResourceSummaries": [{"LogicalResourceId": "A"}], "NextToken": "t1"},
        {"StackResourceSummaries": [{"LogicalResourceId": "B"}]},
    ]
    cloudformation.describe_stack_events.side_effect = [
        {"StackEvents": [{"EventId": "1"}], "NextToken": "e1"},
        {"StackEvents": [{"EventId": "2"}]},
    ]
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"cloudformation": cloudformation}))

    assert adapter._stack_resources("stack") == [{"LogicalResourceId": "A"}, {"LogicalResourceId": "B"}]
    assert [call.kwargs for call in cloudformation.list_stack_resources.call_args_list] == [
        {"StackName": "stack"},
        {"StackName": "stack", "NextToken": "t1"},
    ]
    assert adapter._stack_events("stack") == [{"EventId": "1"}, {"EventId": "2"}]
    assert cloudformation.describe_stack_events.call_args.kwargs == {"StackName": "stack", "NextToken": "e1"}


def _ownership_adapter(monkeypatch: pytest.MonkeyPatch, stack: dict[str, Any] | None, template: Any) -> LiveE2EAws:
    cloudformation = MagicMock()
    cloudformation.get_template.return_value = {"TemplateBody": template}
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"cloudformation": cloudformation}))
    monkeypatch.setattr(adapter, "describe_stack", lambda _: stack)
    return adapter


def test_assert_owned_stack_accepts_matching_output_without_reading_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = {"StackId": "id", "Outputs": [{"OutputKey": "LiveE2ERunId", "OutputValue": "e2e-run"}]}
    adapter = _ownership_adapter(monkeypatch, stack, None)

    assert adapter.assert_owned_stack("name", "e2e-run") is stack
    adapter.client("cloudformation").get_template.assert_not_called()


def test_assert_owned_stack_accepts_dict_template_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = {"StackId": "id", "Outputs": []}
    adapter = _ownership_adapter(monkeypatch, stack, {"Outputs": {"LiveE2ERunId": {"Value": "e2e-run"}}})

    assert adapter.assert_owned_stack("name", "e2e-run") is stack
    adapter.client("cloudformation").get_template.assert_called_once_with(StackName="id", TemplateStage="Original")


@pytest.mark.parametrize(
    ("stack", "template", "message"),
    (
        (None, {}, "stack does not exist"),
        ({"StackId": "id"}, "{not json", "not valid JSON"),
        ({"StackId": "id"}, json.dumps(["list"]), "invalid shape"),
        ({"StackId": "id"}, {"Outputs": []}, "expected live E2E ownership marker"),
        ({"StackId": "id"}, {"Outputs": {"LiveE2ERunId": "e2e-run"}}, "expected live E2E ownership marker"),
        ({"StackId": "id"}, {"Outputs": {"LiveE2ERunId": {"Value": "other"}}}, "expected live E2E ownership marker"),
    ),
)
def test_assert_owned_stack_refuses_unproven_ownership(
    monkeypatch: pytest.MonkeyPatch,
    stack: dict[str, Any] | None,
    template: Any,
    message: str,
) -> None:
    adapter = _ownership_adapter(monkeypatch, stack, template)

    with pytest.raises(ToolError, match=message):
        adapter.assert_owned_stack("name", "e2e-run")


def test_delete_owned_stack_skips_absent_stacks_and_deletes_by_stack_id(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = {"StackId": "arn:stack/id", "Outputs": [{"OutputKey": "LiveE2ERunId", "OutputValue": "e2e-run"}]}
    adapter = _ownership_adapter(monkeypatch, None, None)
    assert adapter.delete_owned_stack("name", "e2e-run") is None
    adapter.client("cloudformation").delete_stack.assert_not_called()

    adapter = _ownership_adapter(monkeypatch, stack, None)
    assert adapter.delete_owned_stack("name", "e2e-run") == "arn:stack/id"
    adapter.client("cloudformation").delete_stack.assert_called_once_with(StackName="arn:stack/id")
