"""End-to-end orchestration of the live E2E runner against fully faked AWS, CDK, and Git boundaries."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import tools.live_e2e.runner as runner_module
from tools._shared import CommandResult, ToolError, fingerprint, hash_account_id
from tools.live_e2e.emulator import AWS_ENDPOINT_ENVIRONMENT, FLOCI_E2E_ENVIRONMENT
from tools.live_e2e.models import CheckResult, PhaseTiming, ResidualResource
from tools.live_e2e.report import load_history
from tools.live_e2e.runner import (
    _CI_ENVIRONMENT_SIGNALS,
    ACCOUNT_CONFIRMATION,
    APPROVAL_ENVIRONMENT,
    CREATE_CONFIRMATION,
    DESTROY_CONFIRMATION,
    KEEP_CONFIRMATION,
    SCHEMA_VERSION,
    ZONE_CONFIRMATION,
    LiveE2ERunner,
    stack_name,
)

ACCOUNT = "123456789012"
REGION = "us-east-1"
RUN_ID = "e2e-flow-run"
STACK_ARN = "arn:aws:cloudformation:us-east-1:123456789012:stack/OpenemrE2E/flow"
VERSIONS = {
    "python_version": "3.14.0",
    "node_version": "v24.1.0",
    "cdk_cli_version": "2.1135.1",
    "cdk_library_version": "2.264.0",
    "cdk_assets_version": "4.7.0",
}


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        *_CI_ENVIRONMENT_SIGNALS,
        FLOCI_E2E_ENVIRONMENT,
        AWS_ENDPOINT_ENVIRONMENT,
        "AWS_ENDPOINT_URL",
        APPROVAL_ENVIRONMENT,
    ):
        monkeypatch.delenv(name, raising=False)


class _Clock:
    def __init__(self) -> None:
        self.now = 5_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        self.now += 0.25
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def time_ns(self) -> int:
        return 42


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(runner_module, "time", fake)
    return fake


def _template(run_id: str, suffix: str, *, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    resources: dict[str, Any] = {
        "Certificate": {"Type": "AWS::CertificateManager::Certificate", "Properties": {}},
        "Dns": {
            "Type": "AWS::Route53::RecordSet",
            "Properties": {"HostedZoneId": "ZTEST123", "AliasTarget": {"DNSName": "alb"}},
        },
        "Listener": {
            "Type": "AWS::ElasticLoadBalancingV2::Listener",
            "Properties": {"Certificates": [{"CertificateArn": {"Ref": "Certificate"}}]},
        },
        "Cluster": {"Type": "AWS::ECS::Cluster", "Properties": {}},
        "Service": {"Type": "AWS::ECS::Service", "Properties": {"DesiredCount": 1}},
        "Sites": {"Type": "AWS::EFS::FileSystem", "Properties": {}},
        "Cache": {"Type": "AWS::ElastiCache::ServerlessCache", "Properties": {}},
        "Alb": {"Type": "AWS::ElasticLoadBalancingV2::LoadBalancer", "Properties": {}},
        "Waf": {"Type": "AWS::WAFv2::WebACLAssociation", "Properties": {}},
        "Database": {
            "Type": "AWS::RDS::DBCluster",
            "Properties": {"DeletionProtection": False, "EngineVersion": "8.0.mysql_aurora.3.12.0"},
            "DeletionPolicy": "Delete",
            "UpdateReplacePolicy": "Delete",
        },
        "Parameter": {"Type": "AWS::SSM::Parameter", "Properties": {"Name": f"swarm_mode_{suffix}"}},
    }
    for name in drop:
        del resources[name]
    return {
        "Outputs": {"LiveE2ERunId": {"Value": run_id}, "OpenEMRVersion": {"Value": "8.2.0"}},
        "Resources": resources,
    }


class _Adapter:
    """In-memory stand-in for LiveE2EAws; records every call and never touches AWS."""

    def __init__(self) -> None:
        self.stack: dict[str, Any] | None = None
        self.calls: list[tuple[str, Any]] = []
        self.account = ACCOUNT
        self.validate_error: BaseException | None = None
        self.delete_event_error: Exception | None = None
        self.tagged_error: Exception | None = None
        self.owned_error: Exception | None = None
        self.residuals: tuple[ResidualResource, ...] = ()
        self.asset_residuals: tuple[ResidualResource, ...] = ()
        self.log_groups = 0

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def preflight(self, **kwargs: Any) -> tuple[tuple[CheckResult, ...], dict[str, Any]]:
        self.calls.append(("preflight", kwargs))
        return (CheckResult("aws-identity", "pass", "non-root operator"),), {
            "availability_zones": ["us-east-1a", "us-east-1b"],
            "hosted_zone_id": "ZTEST123",
            "bootstrap_version": 27,
        }

    def identity(self) -> dict[str, str]:
        self.calls.append(("identity", None))
        return {"account_id": self.account, "account_hash": hash_account_id(self.account)}

    def describe_stack(self, name: str) -> dict[str, Any] | None:
        self.calls.append(("describe_stack", name))
        return self.stack

    def assert_run_id_available(self, run_id: str) -> None:
        self.calls.append(("assert_run_id_available", run_id))

    def event_phases(self, stack_id: str, *, operation: str) -> tuple[PhaseTiming, ...]:
        self.calls.append(("event_phases", operation))
        if operation == "DELETE" and self.delete_event_error is not None:
            raise self.delete_event_error
        return (PhaseTiming(f"cloudformation-{operation.lower()}", 10.0, "cloudformation-events"),)

    def validate_deployment(self, **kwargs: Any) -> Any:
        self.calls.append(("validate_deployment", kwargs))
        if self.validate_error is not None:
            raise self.validate_error
        return (
            (CheckResult("application-https", "pass", "HTTPS returned OpenEMR"),),
            (PhaseTiming("application-https-ready", 30.0, "local-monotonic-clock-with-https-probes"),),
        )

    def owned_rds_cluster_identifiers(self, stack: str, run_id: str) -> tuple[str, ...]:
        self.calls.append(("owned_rds_cluster_identifiers", (stack, run_id)))
        return ("owned-db",)

    def delete_owned_stack(self, stack: str, run_id: str) -> str:
        self.calls.append(("delete_owned_stack", (stack, run_id)))
        return STACK_ARN

    def wait_for_stack_deleted(self, stack: str, *, timeout_seconds: float, poll_seconds: float) -> None:
        self.calls.append(("wait_for_stack_deleted", (stack, timeout_seconds, poll_seconds)))
        self.stack = None

    def assert_owned_stack(self, stack_id: str, run_id: str) -> dict[str, Any]:
        self.calls.append(("assert_owned_stack", (stack_id, run_id)))
        if self.owned_error is not None:
            raise self.owned_error
        return {"StackId": stack_id}

    def cleanup_owned_log_groups(self, name: str, run_id: str, rds: tuple[str, ...]) -> int:
        self.calls.append(("cleanup_owned_log_groups", (name, run_id, rds)))
        return self.log_groups

    def cleanup_owned_tagged_resources(self, run_id: str, *, timeout_seconds: float, poll_seconds: float) -> int:
        self.calls.append(("cleanup_owned_tagged_resources", (run_id, timeout_seconds, poll_seconds)))
        if self.tagged_error is not None:
            raise self.tagged_error
        return 2

    def residual_resources(self, run_id: str) -> tuple[ResidualResource, ...]:
        self.calls.append(("residual_resources", run_id))
        return self.residuals

    def bootstrap_asset_residuals(self, assembly: Path) -> tuple[ResidualResource, ...]:
        self.calls.append(("bootstrap_asset_residuals", assembly))
        return self.asset_residuals


class _FlowRunner(LiveE2ERunner):
    def __init__(self, root: Path, adapter: _Adapter) -> None:
        super().__init__(root=root, aws_factory=self._factory)
        self.adapter = adapter
        self.factory_calls: list[dict[str, Any]] = []
        self.cdk_ok = {"synth": True, "diff": True, "publish-assets": True, "deploy": True}
        self.cdk_calls: list[dict[str, Any]] = []
        self.create_stack_on_deploy = True
        self.template_drop: tuple[str, ...] = ()

    def _factory(self, **kwargs: Any) -> _Adapter:
        self.factory_calls.append(kwargs)
        return self.adapter

    def _git_commit_and_clean(self) -> str:
        return "a" * 40

    def _git_commit_for_cleanup(self) -> str:
        return "a" * 40

    def _git_branch_and_repository(self) -> tuple[str, str]:
        return "feature/live-e2e", "openemr/openemr-on-ecs"

    def _local_checks(self, cdk_command: str) -> tuple[tuple[CheckResult, ...], dict[str, str]]:
        assert cdk_command == str((self.root / "node_modules" / ".bin" / "cdk").resolve())
        return (CheckResult("local-node", "pass", "v24.1.0"),), dict(VERSIONS)

    def _cdk_command(self, **kwargs: Any) -> CommandResult:
        operation = kwargs["operation"]
        run_dir: Path = kwargs["run_dir"]
        self.cdk_calls.append(kwargs)
        ok = self.cdk_ok[operation]
        if operation == "synth" and ok:
            assembly = run_dir / "cdk.out"
            assembly.mkdir(parents=True, exist_ok=True)
            name = stack_name(kwargs["run_id"])
            template = _template(
                kwargs["run_id"],
                kwargs["contexts"]["openemr_resource_suffix"],
                drop=self.template_drop,
            )
            (assembly / "stack.template.json").write_text(json.dumps(template), encoding="utf-8")
            (assembly / "manifest.json").write_text(
                json.dumps({"artifacts": {name: {"properties": {"templateFile": "stack.template.json"}}}}),
                encoding="utf-8",
            )
        if operation == "deploy" and self.create_stack_on_deploy:
            self.adapter.stack = {"StackId": STACK_ARN, "StackStatus": "CREATE_COMPLETE"}
        if not ok:
            (run_dir / f"{operation}.log").write_text(f"error: {operation} boom\n", encoding="utf-8")
        return CommandResult(("cdk", operation), 0 if ok else 1, "", "", 4.0)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "openemr_ecs").mkdir(parents=True)
    (root / "cdk.json").write_text("{}\n", encoding="utf-8")
    for name in ("cdk", "cdk-assets"):
        binary = root / "node_modules" / ".bin" / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text(f"#!/bin/sh\necho {name}\n", encoding="utf-8")
        binary.chmod(0o755)
    return root.resolve()


def _preflight(runner: _FlowRunner, **overrides: Any) -> Path:
    values: dict[str, Any] = {
        "approved_account": ACCOUNT,
        "region": REGION,
        "route53_domain": "e2e.openemr.org",
        "allowed_ipv4_cidr": "8.8.8.8/32",
        "profile": "default",
        "aws_profile": None,
        "cdk_command": "node_modules/.bin/cdk",
        "bootstrap_stack_name": "CDKToolkit",
        "confirm_dedicated_zone": ZONE_CONFIRMATION,
        "confirm_non_production_account": ACCOUNT_CONFIRMATION,
        "run_id": RUN_ID,
        "require_tty": False,
    }
    values.update(overrides)
    return runner.preflight(**values)


def _run(runner: _FlowRunner, path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Any:
    monkeypatch.setenv(APPROVAL_ENVIRONMENT, RUN_ID)
    values: dict[str, Any] = {
        "preflight_path": path,
        "approved_account": ACCOUNT,
        "confirm_create": CREATE_CONFIRMATION,
        "confirm_destroy": DESTROY_CONFIRMATION,
        "confirm_costs": True,
        "require_tty": False,
        "deploy_timeout_seconds": 600,
        "readiness_timeout_seconds": 300,
        "cleanup_timeout_seconds": 1200,
        "poll_seconds": 1,
    }
    values.update(overrides)
    return runner.run(**values)


def _cleanup(runner: _FlowRunner, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> tuple[str, int]:
    monkeypatch.setenv(APPROVAL_ENVIRONMENT, RUN_ID)
    values: dict[str, Any] = {
        "run_id": RUN_ID,
        "approved_account": ACCOUNT,
        "region": REGION,
        "aws_profile": None,
        "confirm_destroy": DESTROY_CONFIRMATION,
        "require_tty": False,
        "timeout_seconds": 3000,
        "poll_seconds": 1,
    }
    values.update(overrides)
    return runner.cleanup(**values)


def _state(root: Path) -> dict[str, Any]:
    return json.loads((root / ".live-e2e" / "runs" / RUN_ID / "state.json").read_text(encoding="utf-8"))


def _phase_names(result: Any) -> list[str]:
    return [phase.name for phase in result.phases]


# --- preflight ---------------------------------------------------------------------------


def test_preflight_writes_owner_only_approval_record(tmp_path: Path, clock: _Clock) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    runner = _FlowRunner(root, adapter)

    path = _preflight(runner, aws_profile="e2e-profile")

    assert path == root / ".live-e2e" / "preflight" / f"{RUN_ID}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["run_id"] == RUN_ID
    assert record["stack_name"] == stack_name(RUN_ID)
    assert record["account_id"] == ACCOUNT
    assert record["account_hash"] == runner._account_label(ACCOUNT)
    assert record["account_hash"] != hash_account_id(ACCOUNT)
    assert record["aws_profile"] == "e2e-profile"
    assert record["resource_count"] == 11
    assert record["resource_types"]["AWS::RDS::DBCluster"] == 1
    assert record["bootstrap_version"] == 27
    assert record["versions"]["openemr_version"] == "8.2.0"
    assert record["versions"]["aurora_version"] == "8.0.mysql_aurora.3.12.0"
    assert record["versions"]["cdk_executable_sha256"].startswith("sha256:")
    assert [check["name"] for check in record["checks"]] == [
        "local-node",
        "aws-identity",
        "stack-name-available",
        "cdk-synthesis",
        "synthesized-safety-policy",
        "synthesized-resource-plan",
        "cdk-diff",
    ]
    assert [phase["name"] for phase in record["preflight_phases"]] == [
        "tool-validation",
        "cdk-synthesis",
        "cdk-diff",
        "preflight",
    ]
    assert record["context_fingerprint"] == fingerprint(
        runner_module.deployment_contexts(
            run_id=RUN_ID,
            account_id=ACCOUNT,
            region=REGION,
            availability_zones=("us-east-1a", "us-east-1b"),
            route53_domain="e2e.openemr.org",
            hosted_zone_id="ZTEST123",
            allowed_ipv4_cidr="8.8.8.8/32",
            profile="default",
        )
    )
    assert [call["operation"] for call in runner.cdk_calls] == ["synth", "diff"]
    assert runner.factory_calls == [
        {"region": REGION, "profile_name": "e2e-profile", "endpoint_url": None, "emulated": False}
    ]
    assert ("assert_run_id_available", RUN_ID) in adapter.calls
    assert "delete_owned_stack" not in adapter.names()


def test_preflight_generates_run_id_when_not_supplied(tmp_path: Path, clock: _Clock) -> None:
    runner = _FlowRunner(_repo(tmp_path), _Adapter())
    path = _preflight(runner, run_id=None)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["run_id"].startswith("e2e-")
    assert path.name == f"{record['run_id']}.json"


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"confirm_dedicated_zone": "yes"}, "--confirm-dedicated-zone must equal"),
        ({"confirm_non_production_account": "yes"}, "--confirm-non-production-account must equal"),
    ),
)
def test_preflight_requires_scope_confirmations_before_aws(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    runner = _FlowRunner(_repo(tmp_path), _Adapter())
    with pytest.raises(ToolError, match=message):
        _preflight(runner, **overrides)
    assert runner.factory_calls == []


def test_preflight_refuses_existing_stack(tmp_path: Path, clock: _Clock) -> None:
    adapter = _Adapter()
    adapter.stack = {"StackId": STACK_ARN}
    runner = _FlowRunner(_repo(tmp_path), adapter)
    with pytest.raises(ToolError, match="Refusing to reuse existing stack"):
        _preflight(runner)
    assert runner.cdk_calls == []


@pytest.mark.parametrize(
    ("failing", "message"),
    (
        ("synth", "CDK synthesis failed; inspect .live-e2e/runs/e2e-flow-run/synth.log"),
        ("diff", "CDK no-change-set diff failed; inspect .live-e2e/runs/e2e-flow-run/diff.log"),
    ),
)
def test_preflight_fails_closed_on_cdk_errors(tmp_path: Path, clock: _Clock, failing: str, message: str) -> None:
    root = _repo(tmp_path)
    runner = _FlowRunner(root, _Adapter())
    runner.cdk_ok[failing] = False
    with pytest.raises(ToolError, match=message):
        _preflight(runner)
    assert not (root / ".live-e2e" / "preflight" / f"{RUN_ID}.json").exists()


def test_preflight_rejects_assembly_missing_expected_resources(tmp_path: Path, clock: _Clock) -> None:
    runner = _FlowRunner(_repo(tmp_path), _Adapter())
    runner.template_drop = ("Waf", "Cache")
    with pytest.raises(
        ToolError,
        match="missing expected resource types: AWS::ElastiCache::ServerlessCache, AWS::WAFv2::WebACLAssociation",
    ):
        _preflight(runner)
    assert [call["operation"] for call in runner.cdk_calls] == ["synth"]


# --- run ---------------------------------------------------------------------------------


def test_approved_run_deploys_validates_cleans_up_and_records_history(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    adapter.log_groups = 3
    adapter.asset_residuals = (ResidualResource("ecr-image", "sha256:aaaaaaaaaaaa", "shared-content-addressed-asset"),)
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)

    result = _run(runner, path, monkeypatch)

    assert result.status == "passed"
    assert result.failure_phase is None
    assert result.stack_status == "CREATE_COMPLETE"
    assert result.cleanup_status == "stack-deleted-with-expected-residuals"
    assert result.residuals == adapter.asset_residuals
    assert result.notes == ()
    assert [call["operation"] for call in runner.cdk_calls] == ["synth", "diff", "publish-assets", "deploy"]
    assert _phase_names(result) == [
        "tool-validation",
        "cdk-synthesis",
        "cdk-diff",
        "preflight",
        "asset-build",
        "asset-publication",
        "asset-pipeline",
        "cdk-deploy",
        "deployment-with-assets",
        "cloudformation-create",
        "application-https-ready",
        "cleanup-request",
        "cloudformation-delete",
        "tagged-resource-cleanup",
        "residual-resource-verification",
        "cleanup",
        "total",
    ]
    assert next(phase for phase in result.phases if phase.name == "tagged-resource-cleanup").source == "passes=2"
    validate_kwargs = dict(adapter.calls)["validate_deployment"]
    assert validate_kwargs == {
        "stack_name_or_id": STACK_ARN,
        "run_id": RUN_ID,
        "profile": "default",
        "https_timeout_seconds": 300,
        "poll_seconds": 1,
    }
    assert ("delete_owned_stack", (STACK_ARN, RUN_ID)) in adapter.calls
    assert ("wait_for_stack_deleted", (STACK_ARN, 1200, 1)) in adapter.calls
    assert ("cleanup_owned_log_groups", (stack_name(RUN_ID), RUN_ID, ("owned-db",))) in adapter.calls
    assert ("cleanup_owned_tagged_resources", (RUN_ID, 900.0, 2.0)) in adapter.calls

    state = _state(root)
    assert state["status"] == "passed"
    assert state["cleanup_status"] == "stack-deleted-with-expected-residuals"
    assert state["deleted_orphan_log_groups"] == 3
    assert state["rds_cluster_identifiers"] == ["owned-db"]
    assert state["stack_id_hash"] == fingerprint(STACK_ARN)
    assert state["residual_count"] == 1
    preflight = json.loads(path.read_text(encoding="utf-8"))
    assert preflight["consumed_at"]

    history = load_history(root / "e2e-results" / "history.json")
    assert [run["run_id"] for run in history["runs"]] == [RUN_ID]
    assert history["runs"][0]["status"] == "passed"
    assert "Latest successful measurement" in (root / "docs" / "maintainers" / "deployment-timing.md").read_text(
        encoding="utf-8"
    )
    raw = json.loads((root / ".live-e2e" / "runs" / RUN_ID / "result.json").read_text(encoding="utf-8"))
    assert raw["stack_name"] == stack_name(RUN_ID)
    assert raw["result"]["status"] == "passed"

    with pytest.raises(ToolError, match="already consumed"):
        _run(runner, path, monkeypatch)


def test_asset_pipeline_failure_skips_deploy_and_needs_no_stack_cleanup(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)
    runner.cdk_ok["publish-assets"] = False

    result = _run(runner, path, monkeypatch)

    assert result.status == "failed"
    assert result.failure_phase == "deployment"
    assert result.cleanup_status == "not-required"
    assert result.stack_status == "not-created"
    assert [call["operation"] for call in runner.cdk_calls][-1] == "publish-assets"
    assert "delete_owned_stack" not in adapter.names()
    assert "bootstrap_asset_residuals" in adapter.names()
    assert clock.sleeps == [1, 1, 1]
    assert result.notes == ("Live E2E did not pass; inspect the owner-only local run log and state.",)
    state = _state(root)
    assert state["failure_type"] == "ToolError"
    assert "CDK asset build or publication failed" in state["failure_detail"]
    assert "error: publish-assets boom" in state["failure_detail"]


def test_failed_deploy_with_created_stack_still_deletes_it(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)
    runner.cdk_ok["deploy"] = False

    result = _run(runner, path, monkeypatch)

    assert result.status == "failed"
    assert result.failure_phase == "deployment"
    assert result.stack_status == "CREATE_COMPLETE"
    assert result.cleanup_status == "complete"
    assert ("delete_owned_stack", (STACK_ARN, RUN_ID)) in adapter.calls
    assert "validate_deployment" not in adapter.names()
    assert "CDK deployment command failed" in _state(root)["failure_detail"]


def test_successful_deploy_without_visible_stack_is_a_failure(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)
    runner.create_stack_on_deploy = False

    result = _run(runner, path, monkeypatch)

    assert result.status == "failed"
    assert result.cleanup_status == "not-required"
    assert _state(root)["failure_detail"] == "CDK reported success but the stack does not exist"


def test_interrupted_validation_is_recorded_and_cleaned_up(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    adapter.validate_error = KeyboardInterrupt()
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)

    result = _run(runner, path, monkeypatch)

    assert result.status == "interrupted"
    assert result.failure_phase == "validation"
    assert result.cleanup_status == "complete"
    assert "wait_for_stack_deleted" in adapter.names()
    assert _state(root)["failure_type"] == "KeyboardInterrupt"
    assert load_history(root / "e2e-results" / "history.json")["runs"][0]["status"] == "interrupted"


def test_delete_timing_failure_is_best_effort(tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    adapter.delete_event_error = RuntimeError("stack events gone")
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)

    result = _run(runner, path, monkeypatch)

    assert result.status == "passed"
    assert result.cleanup_status == "complete"
    assert "cloudformation-delete" not in _phase_names(result)
    state = _state(root)
    assert state["delete_timing_status"] == "unavailable"
    assert state["delete_timing_failure_type"] == "RuntimeError"


def test_unexpected_residuals_fail_an_otherwise_passing_run(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    adapter.residuals = (ResidualResource("s3-bucket", "sha256:bbbbbbbbbbbb", "unexpected-residual"),)
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)

    result = _run(runner, path, monkeypatch)

    assert result.status == "failed"
    assert result.failure_phase == "cleanup"
    assert result.cleanup_status == "failed"
    assert result.residuals == adapter.residuals
    assert result.notes == ()


def test_cleanup_exception_marks_run_failed_then_cleanup_retry_promotes_it(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    adapter.tagged_error = ToolError("tag sweep failed")
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)

    result = _run(runner, path, monkeypatch)

    assert result.status == "failed"
    assert result.failure_phase == "cleanup"
    assert result.cleanup_status == "failed"
    assert _state(root)["failure_detail"] == "tag sweep failed"
    history_path = root / "e2e-results" / "history.json"
    assert load_history(history_path)["runs"][0]["cleanup_status"] == "failed"

    adapter.tagged_error = None
    adapter.stack = {"StackId": STACK_ARN, "StackStatus": "DELETE_FAILED"}
    adapter.log_groups = 1
    adapter.calls.clear()

    status, residual_count = _cleanup(runner, monkeypatch)

    assert (status, residual_count) == ("complete", 0)
    assert adapter.names()[:2] == ["identity", "describe_stack"]
    assert ("owned_rds_cluster_identifiers", (stack_name(RUN_ID), RUN_ID)) in adapter.calls
    assert ("delete_owned_stack", (stack_name(RUN_ID), RUN_ID)) in adapter.calls
    assert ("wait_for_stack_deleted", (STACK_ARN, 3000, 1)) in adapter.calls
    assert ("cleanup_owned_tagged_resources", (RUN_ID, 900.0, 2.0)) in adapter.calls
    run = load_history(history_path)["runs"][0]
    assert run["status"] == "passed"
    assert run["cleanup_status"] == "complete"
    assert run["failure_phase"] is None
    assert run["phases"][-1]["name"] == "cleanup-retry"
    assert _state(root)["deleted_orphan_log_groups"] == 1


def test_cleanup_without_run_history_records_cleanup_only_evidence(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)
    preflight = json.loads(path.read_text(encoding="utf-8"))
    adapter.calls.clear()

    status, residual_count = _cleanup(runner, monkeypatch, aws_profile=None)

    assert (status, residual_count) == ("not-required", 0)
    assert "delete_owned_stack" not in adapter.names()
    run = load_history(root / "e2e-results" / "history.json")["runs"][0]
    assert run["run_id"] == RUN_ID
    assert run["status"] == "interrupted"
    assert run["failure_phase"] == "run-history-unavailable"
    assert run["stack_status"] == "unknown-after-interruption"
    assert run["started_at"] == preflight["created_at"]
    assert run["metadata"] == {"timing_schema": "openemr-live-e2e-v1", "record_scope": "cleanup-only"}
    assert [phase["name"] for phase in run["phases"]] == ["cleanup-retry"]
    assert _state(root)["cleanup_status"] == "not-required"


@pytest.mark.parametrize(
    ("overrides", "approval", "message"),
    (
        ({"confirm_destroy": "no"}, RUN_ID, "--confirm-destroy must equal"),
        ({}, "e2e-other-run", "must equal the run ID"),
    ),
)
def test_cleanup_requires_destroy_confirmation_and_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], approval: str, message: str
) -> None:
    runner = _FlowRunner(_repo(tmp_path), _Adapter())
    values: dict[str, Any] = {
        "run_id": RUN_ID,
        "approved_account": ACCOUNT,
        "region": REGION,
        "aws_profile": None,
        "confirm_destroy": DESTROY_CONFIRMATION,
        "require_tty": False,
    }
    values.update(overrides)
    monkeypatch.setenv(APPROVAL_ENVIRONMENT, approval)
    with pytest.raises(ToolError, match=message):
        runner.cleanup(**values)
    assert runner.factory_calls == []


@pytest.mark.parametrize(
    ("overrides", "identity", "message"),
    (
        ({"region": "us-west-2"}, ACCOUNT, "Cleanup region does not match"),
        ({"approved_account": "210987654321"}, ACCOUNT, "Cleanup account does not match"),
        ({"aws_profile": "other"}, ACCOUNT, "Cleanup AWS profile does not match"),
        ({}, "210987654321", "Active AWS account does not match --approved-account"),
    ),
)
def test_cleanup_rejects_scope_drift(
    tmp_path: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    identity: str,
    message: str,
) -> None:
    adapter = _Adapter()
    runner = _FlowRunner(_repo(tmp_path), adapter)
    _preflight(runner)
    adapter.calls.clear()
    adapter.account = identity
    with pytest.raises(ToolError, match=message):
        _cleanup(runner, monkeypatch, **overrides)
    assert "describe_stack" not in adapter.names()


def test_run_rejects_account_change_and_preexisting_stack(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)

    adapter.account = "210987654321"
    with pytest.raises(ToolError, match="Active AWS account changed after preflight"):
        _run(runner, path, monkeypatch)

    adapter.account = ACCOUNT
    adapter.stack = {"StackId": STACK_ARN}
    with pytest.raises(ToolError, match="unexpectedly exists before deployment"):
        _run(runner, path, monkeypatch)
    assert "consumed_at" not in json.loads(path.read_text(encoding="utf-8"))
    assert "publish-assets" not in [call["operation"] for call in runner.cdk_calls]


def test_run_rejects_tampered_context_fingerprint(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["context_fingerprint"] = "0" * 16
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ToolError, match="Preflight context fingerprint is invalid"):
        _run(runner, path, monkeypatch)
    assert "publish-assets" not in [call["operation"] for call in runner.cdk_calls]


def test_keep_on_failure_without_created_stack_needs_no_cleanup(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)
    runner.cdk_ok["publish-assets"] = False

    result = _run(runner, path, monkeypatch, keep_on_failure=True, confirm_keep_on_failure=KEEP_CONFIRMATION)

    assert result.status == "failed"
    assert result.cleanup_status == "not-required"
    assert "assert_owned_stack" not in adapter.names()
    cleanup_request = next(phase for phase in result.phases if phase.name == "cleanup-request")
    assert cleanup_request.source == "explicit-keep-on-failure"


def test_keep_on_failure_ownership_error_marks_cleanup_failed(
    tmp_path: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    adapter = _Adapter()
    adapter.validate_error = ToolError("validation broke")
    adapter.owned_error = ToolError("ownership marker mismatch")
    runner = _FlowRunner(root, adapter)
    path = _preflight(runner)

    result = _run(runner, path, monkeypatch, keep_on_failure=True, confirm_keep_on_failure=KEEP_CONFIRMATION)

    assert result.status == "failed"
    assert result.failure_phase == "validation"
    assert result.cleanup_status == "failed"
    assert "delete_owned_stack" not in adapter.names()
    assert _state(root)["failure_detail"] == "validation broke"


def test_regenerate_report_runs_under_lock(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    runner = _FlowRunner(root, _Adapter())
    runner.regenerate_report()
    report = (root / "docs" / "maintainers" / "deployment-timing.md").read_text(encoding="utf-8")
    assert "No live E2E deployment has been approved or measured yet." in report
    assert (root / ".live-e2e" / "live-e2e.lock").is_file()


# --- approvals, preflight loading and revalidation ---------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"approved_account": "210987654321"}, "does not match the preflight account"),
        ({"confirm_destroy": "no"}, "--confirm-destroy must equal"),
        ({"confirm_costs": False}, "--confirm-costs is required"),
        ({"keep_on_failure": True, "confirm_keep_on_failure": "keep"}, "--confirm-keep-on-failure must equal"),
        ({"confirm_keep_on_failure": KEEP_CONFIRMATION}, "valid only with --keep-on-failure"),
        ({"run_id": "e2e-other-run"}, "must equal the approved run ID"),
    ),
)
def test_run_approvals_are_all_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], message: str
) -> None:
    monkeypatch.setenv(APPROVAL_ENVIRONMENT, RUN_ID)
    values: dict[str, Any] = {
        "run_id": RUN_ID,
        "approved_account": ACCOUNT,
        "account_id": ACCOUNT,
        "confirm_create": CREATE_CONFIRMATION,
        "confirm_destroy": DESTROY_CONFIRMATION,
        "confirm_costs": True,
        "keep_on_failure": False,
        "confirm_keep_on_failure": None,
    }
    values.update(overrides)
    with pytest.raises(ToolError, match=message):
        LiveE2ERunner(root=_repo(tmp_path))._assert_approvals(**values)


@pytest.fixture
def approved(tmp_path: Path, clock: _Clock) -> tuple[_FlowRunner, Path, dict[str, Any]]:
    runner = _FlowRunner(_repo(tmp_path), _Adapter())
    path = _preflight(runner)
    return runner, path, json.loads(path.read_text(encoding="utf-8"))


def _shifted(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"created_at": _shifted(1), "expires_at": _shifted(5)}, "creation time is in the future"),
        ({"expires_at": _shifted(-10)}, "validity window is invalid"),
        ({"created_at": _shifted(-5), "expires_at": _shifted(-1)}, "Preflight expired"),
        ({"git_commit": "b" * 40}, "Git commit changed after preflight"),
        ({"assembly_fingerprint": "sha256:" + "0" * 64}, "cloud assembly changed after preflight"),
        ({"cdk_command": "/nonexistent/cdk"}, "CDK executable is no longer available"),
    ),
)
def test_revalidate_preflight_rejects_stale_or_drifted_records(
    approved: tuple[_FlowRunner, Path, dict[str, Any]], overrides: dict[str, Any], message: str
) -> None:
    runner, _, record = approved
    record.update(overrides)
    with pytest.raises(ToolError, match=message):
        runner._revalidate_preflight(record)


def test_revalidate_preflight_accepts_fresh_record(approved: tuple[_FlowRunner, Path, dict[str, Any]]) -> None:
    runner, _, record = approved
    runner._revalidate_preflight(record)


def test_load_preflight_returns_validated_record(approved: tuple[_FlowRunner, Path, dict[str, Any]]) -> None:
    runner, path, record = approved
    assert runner._load_preflight(path) == record


def _write(path: Path, value: Any, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    path.chmod(mode)
    return path


def test_load_preflight_rejects_unsafe_locations(approved: tuple[_FlowRunner, Path, dict[str, Any]]) -> None:
    runner, path, record = approved
    preflight_dir = path.parent

    link = preflight_dir / "e2e-linked-run.json"
    link.symlink_to(path)
    with pytest.raises(ToolError, match="regular non-symlink file"):
        runner._load_preflight(link)

    outside = _write(runner.root / "outside.json", record)
    with pytest.raises(ToolError, match="inside .live-e2e/preflight"):
        runner._load_preflight(outside)

    directory = preflight_dir / "e2e-dir-run.json"
    directory.mkdir()
    with pytest.raises(ToolError, match="regular non-symlink file"):
        runner._load_preflight(directory)

    shared = _write(preflight_dir / "e2e-shared-run.json", record, mode=0o644)
    with pytest.raises(ToolError, match="must be owner-only"):
        runner._load_preflight(shared)

    broken = _write(preflight_dir / "e2e-broken-run.json", "{not json")
    with pytest.raises(ToolError, match="Cannot read preflight"):
        runner._load_preflight(broken)

    renamed = _write(preflight_dir / "e2e-renamed-run.json", record)
    with pytest.raises(ToolError, match="filename does not match its run ID"):
        runner._load_preflight(renamed)


def _without(key: str) -> dict[str, Any]:
    return {key: _MISSING}


_MISSING = object()


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"schema_version": SCHEMA_VERSION + 1}, "Unsupported preflight schema"),
        (_without("checks"), "missing required fields"),
        ({"account_id": 123456789012}, "account ID is invalid"),
        ({"region": "Mars"}, "Region is invalid"),
        ({"availability_zones": ["us-east-1a", "us-east-1a"]}, "Availability Zone inventory is invalid"),
        ({"availability_zones": ["us-east-1a", "us-west-2b"]}, "Availability Zone inventory is invalid"),
        ({"availability_zones": "us-east-1a"}, "Availability Zone inventory is invalid"),
        ({"hosted_zone_id": "/hostedzone/ZTEST123"}, "hosted-zone ID is invalid"),
        ({"stack_name": "OpenemrEcsStack"}, "stack name is invalid"),
        ({"versions": {"python_version": "3.14.0"}}, "version inventory is invalid"),
        ({"bootstrap_version": True}, "bootstrap version is invalid"),
        ({"bootstrap_version": "27"}, "bootstrap version is invalid"),
        ({"resource_count": 0}, "resource inventory is invalid"),
        ({"resource_types": []}, "resource inventory is invalid"),
        ({"preflight_phases": []}, "phase timings are invalid"),
        ({"preflight_phases": [{"name": "preflight", "duration_seconds": -1, "source": "x"}]}, "entry is invalid"),
        ({"preflight_phases": [{"name": "preflight", "duration_seconds": False, "source": "x"}]}, "entry is invalid"),
    ),
)
def test_load_preflight_rejects_malformed_records(
    approved: tuple[_FlowRunner, Path, dict[str, Any]], overrides: dict[str, Any], message: str
) -> None:
    runner, path, record = approved
    for key, value in overrides.items():
        if value is _MISSING:
            del record[key]
        else:
            record[key] = value
    _write(path, record)
    with pytest.raises(ToolError, match=message):
        runner._load_preflight(path)


def test_load_preflight_rejects_blank_version_entries(approved: tuple[_FlowRunner, Path, dict[str, Any]]) -> None:
    runner, path, record = approved
    record["versions"]["openemr_version"] = ""
    _write(path, record)
    with pytest.raises(ToolError, match="version inventory is invalid"):
        runner._load_preflight(path)
    assert os.stat(path).st_mode & 0o777 == 0o600
