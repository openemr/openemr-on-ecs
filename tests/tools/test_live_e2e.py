"""Tests for guarded live E2E policy, timing, and reporting."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk import aws_ssm as ssm
from aws_cdk.assertions import Template
from botocore.exceptions import ClientError

import tools.live_e2e.runner as runner_module
from app import (
    LIVE_E2E_RUNNER_ENVIRONMENT,
    assert_live_e2e_runner_context,
    bind_live_e2e_availability_zone_context,
    stack_id_for_live_e2e,
)
from openemr_ecs.stack import OpenemrEcsStack
from openemr_ecs.utils import get_ssm_parameter_name
from tools._shared import CommandResult, ToolError, fingerprint, hash_account_id
from tools.live_e2e import docker_proxy
from tools.live_e2e.aws import (
    _FATAL_LOG_PATTERN,
    _LOCAL_CLEANUP_ACTIONS,
    LiveE2EAws,
    _iam_principal_arn,
)
from tools.live_e2e.cli import build_parser
from tools.live_e2e.models import SCHEMA_VERSION, CheckResult, PhaseTiming, ResidualResource, RunResult
from tools.live_e2e.progress import ProgressReporter
from tools.live_e2e.report import (
    append_result,
    empty_history,
    load_history,
    render_markdown,
    update_cleanup_result,
)
from tools.live_e2e.runner import (
    _CI_ENVIRONMENT_SIGNALS,
    ACCOUNT_CONFIRMATION,
    APPROVAL_ENVIRONMENT,
    CREATE_CONFIRMATION,
    DESTROY_CONFIRMATION,
    KEEP_CONFIRMATION,
    ZONE_CONFIRMATION,
    LiveE2ERunner,
    _assembly_versions,
    _directory_fingerprint,
    _docker_build_duration,
    _file_sha256,
    _repository_slug,
    _resolve_cdk_executable,
    _validate_e2e_template,
    deployment_contexts,
    stack_name,
    validate_inputs,
)


def _result(run_id: str = "e2e-20260731t120000z-abcd1234") -> RunResult:
    return RunResult(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        started_at="2026-07-31T12:00:00Z",
        finished_at="2026-07-31T12:20:00Z",
        git_commit="a" * 40,
        branch="feature/live-e2e",
        repository="openemr/openemr-on-ecs",
        account_hash="sha256:abcdef012345",
        region="us-east-1",
        safe_stack_id="sha256:012345abcdef",
        profile="default",
        configuration_fingerprint="sha256:0123456789abcdef",
        bootstrap_state="ready-v27",
        python_version="3.14.0",
        node_version="v24.1.0",
        cdk_cli_version="2.1135.1",
        cdk_library_version="2.264.0",
        cdk_assets_version="4.7.0",
        openemr_version="8.2.0",
        aurora_version="8.0.mysql_aurora.3.12.0",
        test_runner_version="1.0.0",
        status="passed",
        stack_status="CREATE_COMPLETE",
        cleanup_status="complete",
        failure_phase=None,
        phases=(
            PhaseTiming("cdk-deploy", 600.1, "local-monotonic-clock"),
            PhaseTiming("application-https-ready", 120.2, "local-monotonic-clock-with-https-probes"),
            PhaseTiming("total", 1200.3, "local-monotonic-clock"),
        ),
        checks=(CheckResult("application-https", "pass", "HTTPS returned OpenEMR"),),
    )


def _root(path: Path) -> Path:
    (path / "cdk.json").write_text("{}\n", encoding="utf-8")
    (path / "openemr_ecs").mkdir()
    return path


def _safe_e2e_template() -> dict[str, Any]:
    return {
        "Outputs": {"LiveE2ERunId": {"Value": "e2e-safe-run"}},
        "Resources": {
            "Certificate": {"Type": "AWS::CertificateManager::Certificate", "Properties": {}},
            "Dns": {
                "Type": "AWS::Route53::RecordSet",
                "Properties": {"HostedZoneId": "ZSAFE123", "AliasTarget": {"DNSName": "example"}},
            },
            "Listener": {
                "Type": "AWS::ElasticLoadBalancingV2::Listener",
                "Properties": {"Certificates": [{"CertificateArn": {"Ref": "Certificate"}}]},
            },
            "Service": {"Type": "AWS::ECS::Service", "Properties": {"DesiredCount": 1}},
            "Database": {
                "Type": "AWS::RDS::DBCluster",
                "Properties": {"DeletionProtection": False},
                "DeletionPolicy": "Delete",
                "UpdateReplacePolicy": "Delete",
            },
            "Logs": {
                "Type": "AWS::Logs::LogGroup",
                "Properties": {},
                "DeletionPolicy": "Delete",
                "UpdateReplacePolicy": "Delete",
            },
            "Parameter": {
                "Type": "AWS::SSM::Parameter",
                "Properties": {"Name": "swarm_mode_safe123"},
            },
        },
    }


@pytest.fixture(autouse=True)
def _clear_inherited_ci_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    for signal in _CI_ENVIRONMENT_SIGNALS:
        monkeypatch.delenv(signal, raising=False)


def test_synthesized_e2e_safety_policy_rejects_isolation_and_lifecycle_drift() -> None:
    template = _safe_e2e_template()
    _validate_e2e_template(
        template,
        run_id="e2e-safe-run",
        hosted_zone_id="ZSAFE123",
        resource_suffix="safe123",
        profile="default",
    )

    template["Resources"]["Parameter"]["Properties"]["Name"] = "swarm_mode"
    with pytest.raises(ToolError, match="SSM parameters"):
        _validate_e2e_template(
            template,
            run_id="e2e-safe-run",
            hosted_zone_id="ZSAFE123",
            resource_suffix="safe123",
            profile="default",
        )


def test_empty_report_never_claims_an_unmeasured_timing() -> None:
    report = render_markdown(empty_history())
    assert "No live E2E deployment has been approved or measured yet." in report
    assert "Median" not in report
    assert "0m" not in report


def test_result_append_is_idempotent_and_report_is_deterministic(tmp_path: Path) -> None:
    history_path = tmp_path / "history.json"
    report_path = tmp_path / "report.md"
    result = _result()

    append_result(history_path, report_path, result)
    first = report_path.read_bytes()
    append_result(history_path, report_path, result)

    assert report_path.read_bytes() == first
    history = load_history(history_path)
    assert len(history["runs"]) == 1
    assert "20m 00s" in first.decode("utf-8")
    assert "## Latest successful measurement" in first.decode("utf-8")
    assert "## Profile statistics" in first.decode("utf-8")
    assert "## Methodology" in first.decode("utf-8")
    assert "## Comparability caveats" in first.decode("utf-8")
    assert "e2e-results/history.json" in first.decode("utf-8")
    assert "123456789012" not in history_path.read_text(encoding="utf-8")


def test_history_rejects_raw_accounts_and_credential_like_values(tmp_path: Path) -> None:
    history_path = tmp_path / "history.json"
    report_path = tmp_path / "report.md"

    with pytest.raises(ToolError, match="identifier"):
        append_result(
            history_path,
            report_path,
            replace(_result(), notes=("password=must-not-leak",)),
        )
    with pytest.raises(ToolError, match="account"):
        append_result(
            history_path,
            report_path,
            replace(_result(), account_hash="123456789012"),
        )
    with pytest.raises(ToolError, match="host"):
        append_result(
            history_path,
            report_path,
            replace(_result(), notes=("application at openemr.e2e.example.org",)),
        )


def test_cleanup_retry_updates_existing_history_and_regenerates_report(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "history.json"
    report_path = tmp_path / "report.md"
    append_result(
        history_path,
        report_path,
        replace(
            _result(),
            status="failed",
            cleanup_status="retained-on-failure",
            failure_phase="cleanup",
        ),
    )

    updated = update_cleanup_result(
        history_path,
        report_path,
        run_id=_result().run_id,
        cleanup_status="complete",
        residuals=(),
        phase=PhaseTiming(
            "cleanup-retry",
            42.0,
            "local-monotonic-clock",
        ),
        finished_at="2026-07-31T13:00:00Z",
    )

    assert updated
    run = load_history(history_path)["runs"][0]
    assert run["status"] == "passed"
    assert run["cleanup_status"] == "complete"
    assert run["failure_phase"] is None
    assert any(phase["name"] == "cleanup-retry" for phase in run["phases"])


def test_failed_cleanup_retry_never_promotes_run_to_passed(tmp_path: Path) -> None:
    history_path = tmp_path / "history.json"
    report_path = tmp_path / "report.md"
    append_result(
        history_path,
        report_path,
        replace(
            _result(),
            status="failed",
            cleanup_status="failed",
            failure_phase="cleanup",
        ),
    )

    update_cleanup_result(
        history_path,
        report_path,
        run_id=_result().run_id,
        cleanup_status="failed",
        residuals=(ResidualResource("s3", "sha256:abcdef123456", "unexpected-residual"),),
        phase=PhaseTiming("cleanup-retry", 1.0, "local-monotonic-clock"),
        finished_at="2026-07-31T13:00:00Z",
    )

    run = load_history(history_path)["runs"][0]
    assert run["status"] == "failed"
    assert run["failure_phase"] == "cleanup"
    assert "Latest successful measurement" in report_path.read_text(encoding="utf-8")


def test_contexts_are_isolated_and_profiles_are_explicit() -> None:
    default = deployment_contexts(
        run_id="e2e-valid-run",
        account_id="123456789012",
        region="us-east-1",
        availability_zones=("us-east-1a", "us-east-1b"),
        route53_domain="test.example.org",
        hosted_zone_id="ZTEST123",
        allowed_ipv4_cidr="8.8.8.8/32",
        profile="default",
    )
    alternate = deployment_contexts(
        run_id="e2e-valid-run",
        account_id="123456789012",
        region="us-east-1",
        availability_zones=("us-east-1a", "us-east-1b"),
        route53_domain="test.example.org",
        hosted_zone_id="ZTEST123",
        allowed_ipv4_cidr="8.8.8.8/32",
        profile="api-enabled",
    )

    assert default["live_e2e_run_id"] == "e2e-valid-run"
    assert "certificate_arn" not in default
    assert default["rds_deletion_protection"] == "false"
    assert default["disable_rds_deletion_protection_on_destroy"] == "true"
    assert default["security_group_ip_range_ipv4"] == "8.8.8.8/32"
    assert default["activate_openemr_apis"] == "false"
    assert default["enable_long_term_cloudtrail_monitoring"] == "false"
    assert default["enable_global_accelerator"] == "false"
    assert default["create_serverless_analytics_environment"] == "false"
    assert default["route53_hosted_zone_id"] == "ZTEST123"
    assert default["openemr_import_target"] == "false"
    assert default["openemr_service_fargate_minimum_capacity"] == "1"
    assert default["openemr_service_fargate_maximum_capacity"] == "1"
    assert default["live_e2e_availability_zones"] == '["us-east-1a","us-east-1b"]'
    assert alternate["activate_openemr_apis"] == "true"
    assert alternate["enable_data_api"] == "true"
    assert stack_name("e2e-valid-run").startswith("OpenemrE2E-")
    assert stack_id_for_live_e2e(None) == "OpenemrEcsStack"
    assert stack_id_for_live_e2e("e2e-valid-run") == stack_name("e2e-valid-run")
    with pytest.raises(ValueError, match="live_e2e_run_id"):
        stack_id_for_live_e2e("INVALID")
    assert _repository_slug("git@github.com:openemr/openemr-on-ecs.git") == "openemr/openemr-on-ecs"
    assert _repository_slug("https://github.com/openemr/openemr-on-ecs.git") == "openemr/openemr-on-ecs"
    assert (
        _iam_principal_arn("arn:aws:sts::123456789012:assumed-role/team/platform/deployer/session")
        == "arn:aws:iam::123456789012:role/team/platform/deployer"
    )


def test_default_vs_live_ssm_synthesis_preserves_logical_ids() -> None:
    def synthesize(context: dict[str, str]) -> dict[str, Any]:
        app = cdk.App()
        stack = cdk.Stack(app, "OpenemrEcsStack")
        ssm.StringParameter(
            stack,
            "swarm-mode",
            parameter_name=get_ssm_parameter_name("swarm_mode", context),
            string_value="yes",
        )
        return Template.from_stack(stack).to_json()

    default = synthesize({})
    live = synthesize(
        {
            "live_e2e_run_id": "e2e-valid-run",
            "openemr_resource_suffix": "e2e0123456789",
        }
    )
    assert default["Resources"].keys() == live["Resources"].keys()
    logical_id = next(iter(default["Resources"]))
    assert default["Resources"][logical_id]["Properties"]["Name"] == "swarm_mode"
    assert live["Resources"][logical_id]["Properties"]["Name"] == "swarm_mode_e2e0123456789"


def test_live_stack_synthesis_satisfies_guarded_template_policy() -> None:
    contexts = deployment_contexts(
        run_id="e2e-synth-check",
        account_id="123456789012",
        region="us-east-1",
        availability_zones=("us-east-1a", "us-east-1b"),
        route53_domain="e2e.example.org",
        hosted_zone_id="ZTEST123",
        allowed_ipv4_cidr="8.8.8.8/32",
        profile="default",
    )
    app = cdk.App(context=contexts)
    stack = OpenemrEcsStack(
        app,
        "OpenemrE2E-test",
        stack_name="OpenemrE2E-test",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    template = Template.from_stack(stack).to_json()

    _validate_e2e_template(
        template,
        run_id="e2e-synth-check",
        hosted_zone_id="ZTEST123",
        resource_suffix=contexts["openemr_resource_suffix"],
        profile="default",
    )
    assert template["Outputs"]["LiveE2ERunId"]["Value"] == "e2e-synth-check"


def test_default_stack_synthesis_retains_production_lifecycle_and_names() -> None:
    context = {
        "certificate_arn": ("arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-4000-8000-000000000000"),
        "security_group_ip_range_ipv4": "8.8.8.8/32",
        "rds_deletion_protection": "true",
        "enable_long_term_cloudtrail_monitoring": "false",
        "enable_monitoring_alarms": "false",
        "enable_stack_termination_protection": "false",
        "enable_patient_portal": "false",
        "enable_ecs_exec": "false",
        "activate_openemr_apis": "false",
        "enable_bedrock_integration": "false",
        "enable_data_api": "false",
        "enable_global_accelerator": "false",
        "configure_ses": "false",
        "create_serverless_analytics_environment": "false",
    }
    app = cdk.App(context=context)
    stack = OpenemrEcsStack(
        app,
        "OpenemrEcsStack",
        stack_name="OpenemrEcsStack",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    template = Template.from_stack(stack).to_json()

    assert "LiveE2ERunId" not in template["Outputs"]
    assert "DatabaseClusterArn" not in template["Outputs"]
    assert "OpenEMRVersion" not in template["Outputs"]
    assert "LiveE2ERunId" not in json.dumps(template)

    clusters = [resource for resource in template["Resources"].values() if resource["Type"] == "AWS::RDS::DBCluster"]
    assert len(clusters) == 1
    assert clusters[0]["Properties"]["DeletionProtection"] is True
    assert clusters[0]["DeletionPolicy"] == "Snapshot"
    assert clusters[0]["UpdateReplacePolicy"] == "Snapshot"

    parameter_names = {
        resource["Properties"]["Name"]
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::SSM::Parameter"
    }
    assert parameter_names == {
        "swarm_mode",
        "mysql_port",
        "valkey_endpoint",
        "php_valkey_tls_variable",
        "mysql_ssl_ca_variable",
        "mysql_ssl_enabled_variable",
    }


def test_plan_json_and_noninteractive_modes_are_explicit_cli_options() -> None:
    args = build_parser().parse_args(
        [
            "plan",
            "--approved-account",
            "123456789012",
            "--region",
            "us-east-1",
            "--route53-domain",
            "e2e.example.org",
            "--allowed-ipv4-cidr",
            "8.8.8.8/32",
            "--confirm-dedicated-zone",
            ZONE_CONFIRMATION,
            "--confirm-non-production-account",
            ACCOUNT_CONFIRMATION,
            "--noninteractive",
            "--json",
        ]
    )
    assert args.command == "plan"
    assert args.noninteractive
    assert args.json


def test_synth_command_is_lookup_free_and_uses_current_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    run_dir = root / ".live-e2e" / "runs" / "e2e-command-test"
    run_dir.mkdir(parents=True)
    captured: dict[str, Any] = {}

    def fake_run_command(argv: list[str], **kwargs: Any) -> CommandResult:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return CommandResult(tuple(argv), 0, "", "", 0.1)

    monkeypatch.setattr("tools.live_e2e.runner.run_command", fake_run_command)
    LiveE2ERunner(root=root)._cdk_command(
        cdk_command="/opt/cdk",
        operation="synth",
        run_id="e2e-command-test",
        contexts={"live_e2e_run_id": "e2e-command-test"},
        run_dir=run_dir,
        aws_profile=None,
        account_id="123456789012",
        region="us-east-1",
        timeout_seconds=60,
    )

    argv = captured["argv"]
    assert argv[:3] == ["/opt/cdk", "synth", stack_name("e2e-command-test")]
    assert "--no-lookups" in argv
    assert "--app" in argv
    assert sys.executable in argv[argv.index("--app") + 1]
    assert captured["kwargs"]["env"]["OPENEMR_LIVE_E2E_RUNNER_RUN_ID"] == "e2e-command-test"


def test_local_toolchain_rejects_node_22(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    bin_dir = root / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    cdk = bin_dir / "cdk"
    assets = bin_dir / "cdk-assets"
    cdk.write_text("#!/bin/sh\n", encoding="utf-8")
    assets.write_text("#!/bin/sh\n", encoding="utf-8")
    cdk.chmod(0o755)
    assets.chmod(0o755)

    versions = {
        "git": "git version 2.50.0",
        "aws": "aws-cli/2.27.0",
        "node": "v22.21.1",
        "docker": "28.0.0",
        str(cdk): "2.1135.1",
        str(assets): "4.7.0",
    }

    def fake_run_command(argv: tuple[str, ...], **_kwargs: Any) -> CommandResult:
        return CommandResult(tuple(argv), 0, versions[str(argv[0])], "", 0.01)

    monkeypatch.setattr(runner_module, "run_command", fake_run_command)
    monkeypatch.setattr(runner_module.shutil, "which", lambda command: f"/usr/bin/{command}")

    with pytest.raises(ToolError, match=r"Node\.js 24\.x"):
        LiveE2ERunner(root=root)._local_checks(str(cdk))


def test_cdk_resolution_is_bounded_to_pinned_local_binary(tmp_path: Path) -> None:
    root = _root(tmp_path)
    cdk = root / "node_modules" / ".bin" / "cdk"
    cdk.parent.mkdir(parents=True)
    cdk.write_text("#!/bin/sh\n", encoding="utf-8")
    cdk.chmod(0o755)

    assert _resolve_cdk_executable(root, "cdk") == str(cdk.resolve())
    with pytest.raises(ToolError, match="repository-pinned"):
        _resolve_cdk_executable(root, "/usr/local/bin/cdk")


def test_cloud_assembly_provides_exact_deployment_versions(tmp_path: Path) -> None:
    stack = stack_name("e2e-version-test")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifacts": {stack: {"properties": {"templateFile": "stack.template.json"}}}}),
        encoding="utf-8",
    )
    (tmp_path / "stack.template.json").write_text(
        json.dumps(
            {
                "Outputs": {"OpenEMRVersion": {"Value": "8.2.0"}},
                "Resources": {
                    "Database": {
                        "Type": "AWS::RDS::DBCluster",
                        "Properties": {"EngineVersion": "8.0.mysql_aurora.3.12.0"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert _assembly_versions(tmp_path, stack) == {
        "openemr_version": "8.2.0",
        "aurora_version": "8.0.mysql_aurora.3.12.0",
    }


def test_docker_build_timing_is_structured_and_bounded(tmp_path: Path) -> None:
    timing = tmp_path / "docker.jsonl"
    timing.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "schema_version": 1,
                        "category": "build",
                        "duration_seconds": 2.25,
                        "returncode": 0,
                    }
                ),
                json.dumps(
                    {
                        "schema_version": 1,
                        "category": "publish",
                        "duration_seconds": 1.5,
                        "returncode": 0,
                    }
                ),
            )
        ),
        encoding="utf-8",
    )
    assert _docker_build_duration(timing) == 2.25
    timing.write_text('{"category":"build","duration_seconds":"many"}\n', encoding="utf-8")
    with pytest.raises(ToolError, match="entry"):
        _docker_build_duration(timing)


def test_docker_proxy_never_records_forwarded_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o700)
    timing = tmp_path / "timing.jsonl"
    monkeypatch.setenv("OPENEMR_E2E_REAL_DOCKER", str(docker))
    monkeypatch.setenv("OPENEMR_E2E_DOCKER_TIMINGS", str(timing))
    monkeypatch.setattr(
        "sys.argv",
        ["docker-proxy", "login", "--password", "must-not-leak"],
    )

    assert docker_proxy.main() == 0
    content = timing.read_text(encoding="utf-8")
    assert "must-not-leak" not in content
    assert json.loads(content)["category"] == "other"


@pytest.mark.parametrize(
    ("domain", "cidr"),
    (
        ("example.org", "8.8.8.8/32"),
        ("e2e.example.org", "0.0.0.0/0"),
        ("e2e.example.org", "10.0.0.1/32"),
        ("e2e.example.org", "224.0.0.1/32"),
        ("e2e.invalid.example", "8.8.8.8/32"),
    ),
)
def test_scope_validation_rejects_shared_zones_and_unsafe_networks(domain: str, cidr: str) -> None:
    with pytest.raises(ToolError):
        validate_inputs(
            approved_account="123456789012",
            region="us-east-1",
            route53_domain=domain,
            allowed_ipv4_cidr=cidr,
            profile="default",
        )


@pytest.mark.parametrize("message", ("PHP Fatal error: boom", "Fatal PHP error: boom"))
def test_fatal_log_detection_accepts_normal_php_word_order(message: str) -> None:
    assert _FATAL_LOG_PATTERN.search(message)


def test_live_e2e_hosted_zone_must_have_only_delegation_records() -> None:
    class Paginator:
        def __init__(self, records: list[dict[str, str]]) -> None:
            self.records = records

        def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs == {"HostedZoneId": "/hostedzone/E2E"}
            return [{"ResourceRecordSets": self.records}]

    class Route53:
        def __init__(self, records: list[dict[str, str]]) -> None:
            self.records = records

        def get_paginator(self, operation: str) -> Paginator:
            assert operation == "list_resource_record_sets"
            return Paginator(self.records)

    adapter = LiveE2EAws(region="us-east-1", session=SimpleNamespace())
    adapter._clients["route53:True"] = Route53(
        [
            {"Name": "e2e.example.org.", "Type": "NS"},
            {"Name": "e2e.example.org.", "Type": "SOA"},
        ]
    )
    adapter._assert_dedicated_zone_records({"Id": "/hostedzone/E2E", "Name": "e2e.example.org."})

    adapter._clients["route53:True"] = Route53(
        [
            {"Name": "e2e.example.org.", "Type": "NS"},
            {"Name": "e2e.example.org.", "Type": "SOA"},
            {"Name": "child.e2e.example.org.", "Type": "NS"},
        ]
    )
    with pytest.raises(ToolError, match="exactly its apex"):
        adapter._assert_dedicated_zone_records({"Id": "/hostedzone/E2E", "Name": "e2e.example.org."})


def test_preflight_checks_direct_local_cleanup_permissions() -> None:
    simulations: list[tuple[str, tuple[str, ...], str]] = []

    class Sts:
        def assume_role(self, **_: Any) -> dict[str, Any]:
            return {"Credentials": {}}

    class Session:
        def client(self, service: str, **_: Any) -> Any:
            assert service == "sts"
            return Sts()

    class Adapter(LiveE2EAws):
        def _simulate_actions(
            self,
            *,
            principal_arn: str,
            actions: tuple[str, ...],
            label: str,
            resource_arns: tuple[str, ...] = (),
        ) -> None:
            assert not resource_arns
            simulations.append((principal_arn, actions, label))

    adapter = Adapter(region="us-east-1", session=Session())
    checks = adapter._write_permission_probes(
        caller_arn="arn:aws:iam::123456789012:user/operator",
        account_id="123456789012",
        bootstrap={"Parameters": [{"ParameterKey": "Qualifier", "ParameterValue": "hnb659fds"}]},
    )

    local = next(item for item in simulations if item[2] == "local cleanup principal")
    assert local[0] == "arn:aws:iam::123456789012:user/operator"
    assert local[1] == _LOCAL_CLEANUP_ACTIONS
    assert {
        "cloudformation:DeleteStack",
        "tag:GetResources",
        "s3:DeleteObjectVersion",
        "ecs:DeleteService",
        "ec2:DeleteVpc",
        "kms:ScheduleKeyDeletion",
    }.issubset(local[1])
    assert any(check.name == "local-cleanup-permissions" for check in checks)


def test_cleanup_ownership_accepts_json_template_body(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "e2e-owned-stack"
    stack = {"StackId": "stack-id", "Outputs": []}

    class CloudFormation:
        def get_template(self, **_: Any) -> dict[str, str]:
            return {
                "TemplateBody": json.dumps(
                    {
                        "Outputs": {
                            "LiveE2ERunId": {
                                "Value": run_id,
                            }
                        }
                    }
                )
            }

    adapter = LiveE2EAws(region="us-east-1", session=SimpleNamespace())
    adapter._clients["cloudformation:False"] = CloudFormation()
    monkeypatch.setattr(adapter, "describe_stack", lambda _: stack)

    assert adapter.assert_owned_stack("stack-id", run_id) is stack


def test_delete_complete_is_a_terminal_cleanup_success(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = LiveE2EAws(region="us-east-1", session=SimpleNamespace())
    monkeypatch.setattr(
        adapter,
        "_describe_stack_raw",
        lambda _: {"StackStatus": "DELETE_COMPLETE"},
    )
    monkeypatch.setattr("tools.live_e2e.aws.time.sleep", lambda _: pytest.fail("must not sleep"))

    adapter.wait_for_stack_deleted(
        "stack-id",
        timeout_seconds=1,
        poll_seconds=0.01,
    )


def test_emulated_delete_failed_requires_resources_to_be_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    class CloudFormation:
        def delete_stack(self, **_: Any) -> dict[str, Any]:
            return {}

    adapter = LiveE2EAws(
        region="us-east-1",
        session=SimpleNamespace(),
        endpoint_url="http://127.0.0.1:4566",
        emulated=True,
    )
    adapter._clients["cloudformation:False"] = CloudFormation()
    failed = {
        "StackId": "stack-arn",
        "StackStatus": "DELETE_FAILED",
        "StackStatusReason": "resource still exists",
    }
    monkeypatch.setattr(adapter, "_describe_stack_raw", lambda _: failed)
    monkeypatch.setattr(adapter, "_emulated_delete_tombstone_is_empty", lambda _: False)
    monkeypatch.setattr("tools.live_e2e.aws.time.sleep", lambda _: None)

    with pytest.raises(ToolError, match="remaining resources"):
        adapter.wait_for_stack_deleted("stack-arn", timeout_seconds=1, poll_seconds=0.01)


def test_emulated_describe_does_not_mask_nonempty_delete_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = {
        "StackId": "stack-arn",
        "StackStatus": "DELETE_FAILED",
        "StackStatusReason": "resource still exists",
    }

    class CloudFormation:
        def describe_stacks(self, **_: Any) -> dict[str, Any]:
            return {"Stacks": [failed]}

    adapter = LiveE2EAws(
        region="us-east-1",
        session=SimpleNamespace(),
        endpoint_url="http://127.0.0.1:4566",
        emulated=True,
    )
    adapter._clients["cloudformation:False"] = CloudFormation()
    monkeypatch.setattr(adapter, "_emulated_delete_tombstone_is_empty", lambda _: False)

    assert adapter.describe_stack("stack-arn") is failed


def test_delete_failed_nonempty_s3_bucket_is_emptied_and_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = iter(
        [
            {
                "StackId": "stack-arn",
                "StackStatus": "DELETE_FAILED",
                "StackStatusReason": "The following resource(s) failed to delete: [elbaccesslogsbucket].",
            },
            {"StackId": "stack-arn", "StackStatus": "DELETE_IN_PROGRESS"},
            {"StackId": "stack-arn", "StackStatus": "DELETE_COMPLETE"},
        ]
    )
    deleted_stacks: list[str] = []
    emptied: list[str] = []

    class CloudFormation:
        def get_paginator(self, name: str) -> Any:
            assert name == "describe_stack_events"

            class Paginator:
                def paginate(self, **_: Any) -> Any:
                    yield {
                        "StackEvents": [
                            {
                                "ResourceStatus": "DELETE_FAILED",
                                "ResourceType": "AWS::S3::Bucket",
                                "LogicalResourceId": "elbaccesslogsbucket",
                                "PhysicalResourceId": "openemr-elb-access-logs-123",
                                "ResourceStatusReason": (
                                    "The bucket you tried to delete is not empty. "
                                    "You must delete all versions in the bucket."
                                ),
                            }
                        ]
                    }

            return Paginator()

        def delete_stack(self, **kwargs: Any) -> dict[str, Any]:
            deleted_stacks.append(kwargs["StackName"])
            return {}

    adapter = LiveE2EAws(region="us-east-1", session=SimpleNamespace())
    adapter._clients["cloudformation:False"] = CloudFormation()
    monkeypatch.setattr(adapter, "_describe_stack_raw", lambda _: next(statuses))
    monkeypatch.setattr(
        adapter,
        "_empty_versioned_s3_bucket",
        lambda bucket: emptied.append(bucket) or 7,
    )
    monkeypatch.setattr("tools.live_e2e.aws.time.sleep", lambda _: None)

    adapter.wait_for_stack_deleted("stack-arn", timeout_seconds=5, poll_seconds=0.01)

    assert emptied == ["openemr-elb-access-logs-123"]
    assert deleted_stacks == ["stack-arn"]


def test_empty_versioned_s3_bucket_deletes_versions_and_markers() -> None:
    deleted_batches: list[list[dict[str, str]]] = []

    class S3:
        def get_paginator(self, name: str) -> Any:
            class Paginator:
                def paginate(self, **_: Any) -> Any:
                    if name == "list_multipart_uploads":
                        yield {"Uploads": []}
                        return
                    assert name == "list_object_versions"
                    yield {
                        "Versions": [
                            {"Key": "a", "VersionId": "v1"},
                            {"Key": "b", "VersionId": "v2"},
                        ],
                        "DeleteMarkers": [{"Key": "c", "VersionId": "v3"}],
                    }

            return Paginator()

        def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
            deleted_batches.append(list(kwargs["Delete"]["Objects"]))
            return {}

    adapter = LiveE2EAws(region="us-east-1", session=SimpleNamespace())
    adapter._clients["s3:False"] = S3()

    removed = adapter._empty_versioned_s3_bucket("bucket-a")

    assert removed == 3
    assert deleted_batches == [
        [
            {"Key": "a", "VersionId": "v1"},
            {"Key": "b", "VersionId": "v2"},
            {"Key": "c", "VersionId": "v3"},
        ]
    ]


def test_live_e2e_context_requires_runner_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "e2e-runner-guard"
    monkeypatch.delenv(LIVE_E2E_RUNNER_ENVIRONMENT, raising=False)
    with pytest.raises(ValueError, match="reserved"):
        assert_live_e2e_runner_context(run_id)

    monkeypatch.setenv(LIVE_E2E_RUNNER_ENVIRONMENT, run_id)
    assert_live_e2e_runner_context(run_id)


def test_live_e2e_zones_bind_to_cdk_provider_context() -> None:
    app = cdk.App()
    bind_live_e2e_availability_zone_context(
        app,
        live_e2e_run_id="e2e-runner-guard",
        raw_availability_zones='["us-east-1a","us-east-1b"]',
        account="123456789012",
        region="us-east-1",
    )
    assert app.node.try_get_context("availability-zones:account=123456789012:region=us-east-1") == [
        "us-east-1a",
        "us-east-1b",
    ]

    with pytest.raises(ValueError, match="reserved"):
        bind_live_e2e_availability_zone_context(
            cdk.App(),
            live_e2e_run_id=None,
            raw_availability_zones='["us-east-1a","us-east-1b"]',
            account="123456789012",
            region="us-east-1",
        )


def test_run_stays_locked_without_every_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    runner = LiveE2ERunner(root=root, aws_factory=lambda **_: pytest.fail("AWS must not be called"))
    run_id = "e2e-locked-run"
    preflight_dir = root / ".live-e2e" / "preflight"
    preflight_dir.mkdir(parents=True)
    preflight = preflight_dir / f"{run_id}.json"
    created = datetime.now(timezone.utc)
    preflight.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "created_at": created.isoformat(),
                "expires_at": (created + timedelta(hours=4)).isoformat(),
                "git_commit": "a" * 40,
                "branch": "feature/live-e2e",
                "repository": "openemr/openemr-on-ecs",
                "account_id": "123456789012",
                "account_hash": hash_account_id("123456789012"),
                "region": "us-east-1",
                "availability_zones": ["us-east-1a", "us-east-1b"],
                "route53_domain": "e2e.example.org",
                "hosted_zone_id": "ZTEST123",
                "allowed_ipv4_cidr": "8.8.8.8/32",
                "profile": "default",
                "cdk_command": "/usr/bin/false",
                "stack_name": stack_name(run_id),
                "context_fingerprint": "deadbeefdeadbeef",
                "assembly_fingerprint": "sha256:not-reached",
                "bootstrap_version": 27,
                "versions": {
                    "python_version": "3.14.0",
                    "node_version": "v24.1.0",
                    "cdk_cli_version": "2.1135.1",
                    "cdk_library_version": "2.264.0",
                    "cdk_executable_sha256": "sha256:" + "b" * 64,
                    "cdk_assets_version": "4.7.0",
                    "cdk_assets_executable_sha256": "sha256:" + "c" * 64,
                    "openemr_version": "8.2.0",
                    "aurora_version": "8.0.mysql_aurora.3.12.0",
                },
                "resource_count": 1,
                "resource_types": {"AWS::CloudFormation::WaitConditionHandle": 1},
                "preflight_phases": [
                    {
                        "name": "preflight",
                        "duration_seconds": 1.0,
                        "source": "local-monotonic-clock",
                        "started_at": None,
                        "finished_at": None,
                    }
                ],
                "checks": [],
            }
        ),
        encoding="utf-8",
    )
    preflight.chmod(0o600)
    monkeypatch.setenv(APPROVAL_ENVIRONMENT, run_id)

    with pytest.raises(ToolError, match="confirm-create"):
        runner.run(
            preflight_path=preflight,
            approved_account="123456789012",
            confirm_create="wrong",
            confirm_destroy=DESTROY_CONFIRMATION,
            confirm_costs=True,
            require_tty=False,
        )
    assert not (root / ".live-e2e" / "runs").exists()


def test_cleanup_rejects_dirty_worktree_before_aws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class DirtyRunner(LiveE2ERunner):
        def _git_commit_for_cleanup(self) -> str:
            raise ToolError("Live E2E cleanup requires a clean worktree")

    run_id = "e2e-dirty-cleanup"
    runner = DirtyRunner(
        root=_root(tmp_path),
        aws_factory=lambda **_: pytest.fail("AWS must not be called"),
    )
    monkeypatch.setenv(APPROVAL_ENVIRONMENT, run_id)

    with pytest.raises(ToolError, match="clean worktree"):
        runner.cleanup(
            run_id=run_id,
            approved_account="123456789012",
            region="us-east-1",
            aws_profile=None,
            confirm_destroy=DESTROY_CONFIRMATION,
            require_tty=False,
        )


def test_cleanup_allows_only_generated_timing_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    runner = LiveE2ERunner(root=root)
    status_output = " M docs/maintainers/deployment-timing.md\n M e2e-results/history.json\n"

    def fake_run_command(
        argv: tuple[str, ...],
        **_: Any,
    ) -> CommandResult:
        if argv[1] == "status":
            return CommandResult(argv, 0, status_output, "", 0.01)
        assert argv[1:] == ("rev-parse", "HEAD")
        return CommandResult(argv, 0, "a" * 40 + "\n", "", 0.01)

    monkeypatch.setattr(runner_module, "run_command", fake_run_command)
    assert runner._git_commit_for_cleanup() == "a" * 40

    status_output = " M README.md\n"
    with pytest.raises(ToolError, match="generated timing"):
        runner._git_commit_for_cleanup()


def test_live_e2e_local_state_root_is_owner_only_and_not_a_symlink(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir()
    root = _root(private_root)
    runner = LiveE2ERunner(root=root)

    with runner._lock():
        assert runner.local_root.stat().st_mode & 0o777 == 0o700

    linked_root_path = tmp_path / "linked"
    linked_root_path.mkdir()
    linked_root = _root(linked_root_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (linked_root / ".live-e2e").symlink_to(outside, target_is_directory=True)
    linked_runner = LiveE2ERunner(root=linked_root)

    with pytest.raises(ToolError, match="symlinked"):
        with linked_runner._lock():
            pytest.fail("symlinked state directory must never be locked")


@pytest.mark.parametrize("signal", _CI_ENVIRONMENT_SIGNALS)
def test_ci_environment_blocks_live_actions_before_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal: str,
) -> None:
    runner = LiveE2ERunner(
        root=_root(tmp_path),
        aws_factory=lambda **_: pytest.fail("AWS must not be called"),
    )
    monkeypatch.setenv(signal, "true")
    with pytest.raises(ToolError, match="disabled in CI"):
        runner.preflight(
            approved_account="123456789012",
            region="us-east-1",
            route53_domain="e2e.example.org",
            allowed_ipv4_cidr="8.8.8.8/32",
            profile="default",
            aws_profile=None,
            cdk_command="cdk",
            bootstrap_stack_name="CDKToolkit",
            confirm_dedicated_zone=ZONE_CONFIRMATION,
            confirm_non_production_account=ACCOUNT_CONFIRMATION,
            require_tty=False,
        )


class _CleanupAdapter:
    def __init__(self) -> None:
        self.describe_calls = 0
        self.delete_called = False
        self.deleted = False

    def identity(self) -> dict[str, str]:
        return {
            "account_id": "123456789012",
            "account_hash": hash_account_id("123456789012"),
        }

    def describe_stack(self, _: str) -> dict[str, Any] | None:
        self.describe_calls += 1
        if self.deleted or self.describe_calls == 1:
            return None
        return {
            "StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/test/id",
            "StackStatus": "CREATE_COMPLETE",
        }

    def event_phases(self, *_: Any, **__: Any) -> tuple[PhaseTiming, ...]:
        return ()

    def validate_deployment(self, **_: Any) -> Any:
        raise ToolError("injected validation failure")

    def owned_rds_cluster_identifiers(
        self,
        _: str,
        run_id: str,
    ) -> tuple[str, ...]:
        assert run_id == "e2e-cleanup-test"
        return ("owned-database",)

    def delete_owned_stack(self, _: str, run_id: str) -> str:
        assert run_id == "e2e-cleanup-test"
        self.delete_called = True
        return "stack-id"

    def wait_for_stack_deleted(self, *_: Any, **__: Any) -> None:
        assert self.delete_called
        self.deleted = True

    def residual_resources(self, _: str) -> tuple[Any, ...]:
        assert self.deleted
        return ()

    def bootstrap_asset_residuals(self, _: Path) -> tuple[Any, ...]:
        assert self.deleted
        return ()

    def cleanup_owned_log_groups(
        self,
        _: str,
        __: str,
        rds_cluster_identifiers: tuple[str, ...] = (),
    ) -> int:
        assert self.deleted
        assert rds_cluster_identifiers == ("owned-database",)
        return 0

    def cleanup_owned_tagged_resources(self, *_: Any, **__: Any) -> int:
        assert self.deleted
        return 0


class _CommandRunner(LiveE2ERunner):
    def _git_commit_and_clean(self) -> str:
        return "a" * 40

    def _cdk_command(self, **_: Any) -> CommandResult:
        return CommandResult(("cdk", "deploy"), 0, "", "", 2.5)

    def _local_checks(
        self,
        cdk_command: str,
    ) -> tuple[tuple[CheckResult, ...], dict[str, str]]:
        assert Path(cdk_command) == self.root / "node_modules" / ".bin" / "cdk"
        return (), {
            "python_version": "3.14.0",
            "node_version": "v24.1.0",
            "cdk_cli_version": "2.1135.1",
            "cdk_library_version": "2.264.0",
            "cdk_assets_version": "4.7.0",
        }


def _write_approved_preflight(root: Path, run_id: str) -> Path:
    contexts = deployment_contexts(
        run_id=run_id,
        account_id="123456789012",
        region="us-east-1",
        availability_zones=("us-east-1a", "us-east-1b"),
        route53_domain="e2e.example.org",
        hosted_zone_id="ZTEST123",
        allowed_ipv4_cidr="8.8.8.8/32",
        profile="default",
    )
    preflight_dir = root / ".live-e2e" / "preflight"
    run_dir = root / ".live-e2e" / "runs" / run_id
    cdk = root / "node_modules" / ".bin" / "cdk"
    cdk_assets = root / "node_modules" / ".bin" / "cdk-assets"
    cdk_assets.parent.mkdir(parents=True, exist_ok=True)
    cdk.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cdk.chmod(0o755)
    cdk_assets.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cdk_assets.chmod(0o755)
    preflight_dir.mkdir(parents=True)
    (run_dir / "cdk.out").mkdir(parents=True)
    (run_dir / "cdk.out" / "manifest.json").write_text("{}\n", encoding="utf-8")
    preflight = preflight_dir / f"{run_id}.json"
    created = datetime.now(timezone.utc)
    preflight.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "created_at": created.isoformat(),
                "expires_at": (created + timedelta(hours=4)).isoformat(),
                "git_commit": "a" * 40,
                "branch": "feature/live-e2e",
                "repository": "openemr/openemr-on-ecs",
                "account_id": "123456789012",
                "account_hash": hash_account_id("123456789012"),
                "region": "us-east-1",
                "availability_zones": ["us-east-1a", "us-east-1b"],
                "route53_domain": "e2e.example.org",
                "hosted_zone_id": "ZTEST123",
                "allowed_ipv4_cidr": "8.8.8.8/32",
                "profile": "default",
                "aws_profile": None,
                "cdk_command": str(cdk),
                "stack_name": stack_name(run_id),
                "context_fingerprint": fingerprint(contexts),
                "assembly_fingerprint": _directory_fingerprint(run_dir / "cdk.out"),
                "bootstrap_version": 27,
                "versions": {
                    "python_version": "3.14.0",
                    "node_version": "v24.1.0",
                    "cdk_cli_version": "2.1135.1",
                    "cdk_library_version": "2.264.0",
                    "cdk_executable_sha256": _file_sha256(cdk),
                    "cdk_assets_version": "4.7.0",
                    "cdk_assets_executable_sha256": _file_sha256(cdk_assets),
                    "openemr_version": "8.2.0",
                    "aurora_version": "8.0.mysql_aurora.3.12.0",
                },
                "resource_count": 1,
                "resource_types": {"AWS::CloudFormation::WaitConditionHandle": 1},
                "preflight_phases": [
                    {
                        "name": "preflight",
                        "duration_seconds": 1.0,
                        "source": "local-monotonic-clock",
                        "started_at": None,
                        "finished_at": None,
                    }
                ],
                "checks": [],
            }
        ),
        encoding="utf-8",
    )
    preflight.chmod(0o600)
    return preflight


def test_consumed_preflight_cannot_be_reused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    runner = LiveE2ERunner(root=root)
    preflight = _write_approved_preflight(root, "e2e-consumed-test")
    value = json.loads(preflight.read_text(encoding="utf-8"))
    value["consumed_at"] = datetime.now(timezone.utc).isoformat()

    with pytest.raises(ToolError, match="already consumed"):
        runner._revalidate_preflight(value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("node_version", "v22.21.1"),
        ("cdk_executable_sha256", "sha256:" + "0" * 64),
        ("cdk_assets_executable_sha256", "sha256:" + "1" * 64),
    ),
)
def test_run_rejects_toolchain_drift_after_preflight(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    root = _root(tmp_path)
    runner = _CommandRunner(root=root)
    preflight = _write_approved_preflight(root, "e2e-toolchain-drift")
    value = json.loads(preflight.read_text(encoding="utf-8"))
    value["versions"][field] = replacement

    with pytest.raises(ToolError, match=field):
        runner._revalidate_preflight(value)


def test_validation_failure_still_deletes_owned_stack_and_records_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    adapter = _CleanupAdapter()
    runner = _CommandRunner(root=root, aws_factory=lambda **_: adapter)
    run_id = "e2e-cleanup-test"
    preflight = _write_approved_preflight(root, run_id)
    monkeypatch.setenv(APPROVAL_ENVIRONMENT, run_id)

    result = runner.run(
        preflight_path=preflight,
        approved_account="123456789012",
        confirm_create=CREATE_CONFIRMATION,
        confirm_destroy=DESTROY_CONFIRMATION,
        confirm_costs=True,
        require_tty=False,
        poll_seconds=0.001,
    )

    assert result.status == "failed"
    assert result.cleanup_status == "complete"
    assert adapter.delete_called
    assert adapter.deleted
    assert load_history(root / "e2e-results" / "history.json")["runs"][0]["run_id"] == run_id
    raw_result = root / ".live-e2e" / "runs" / run_id / "result.json"
    assert raw_result.is_file()
    assert raw_result.stat().st_mode & 0o077 == 0


class _RetentionAdapter(_CleanupAdapter):
    def assert_owned_stack(self, stack_id: str, run_id: str) -> dict[str, Any]:
        assert stack_id.startswith("arn:aws:cloudformation:")
        assert run_id == "e2e-retain-test"
        return {"StackId": stack_id}

    def delete_owned_stack(self, _: str, run_id: str) -> str:
        pytest.fail(f"retained run {run_id} must not be deleted")

    def wait_for_stack_deleted(self, *_: Any, **__: Any) -> None:
        pytest.fail("retained run must not wait for deletion")

    def residual_resources(self, _: str) -> tuple[Any, ...]:
        return ()

    def bootstrap_asset_residuals(self, _: Path) -> tuple[Any, ...]:
        return ()


def test_explicit_keep_on_failure_preserves_owned_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    adapter = _RetentionAdapter()
    runner = _CommandRunner(root=root, aws_factory=lambda **_: adapter)
    run_id = "e2e-retain-test"
    preflight = _write_approved_preflight(root, run_id)
    monkeypatch.setenv(APPROVAL_ENVIRONMENT, run_id)

    result = runner.run(
        preflight_path=preflight,
        approved_account="123456789012",
        confirm_create=CREATE_CONFIRMATION,
        confirm_destroy=DESTROY_CONFIRMATION,
        confirm_costs=True,
        keep_on_failure=True,
        confirm_keep_on_failure=KEEP_CONFIRMATION,
        require_tty=False,
        poll_seconds=0.001,
    )

    assert result.status == "failed"
    assert result.cleanup_status == "retained-on-failure"
    assert not adapter.delete_called
    assert not adapter.deleted


def test_workflows_never_invoke_live_e2e() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow_paths = [
        *(root / ".github" / "workflows").glob("*.yml"),
        *(root / ".github" / "workflows").glob("*.yaml"),
    ]
    workflows = "\n".join(path.read_text(encoding="utf-8") for path in workflow_paths)
    for operation in ("preflight", "plan", "run", "cleanup"):
        assert f"tools.live_e2e {operation}" not in workflows


class _AssetS3:
    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"Bucket": "bootstrap-assets", "Key": "asset.zip"}
        return {}


class _AssetEcr:
    def describe_images(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {
            "repositoryName": "bootstrap-images",
            "imageIds": [{"imageTag": "image-hash"}],
        }
        return {"imageDetails": [{}]}


class _AssetSession:
    def client(self, service: str, **_: Any) -> Any:
        return {"s3": _AssetS3(), "ecr": _AssetEcr()}[service]


def test_bootstrap_asset_inventory_reports_shared_retained_assets(tmp_path: Path) -> None:
    (tmp_path / "stack.assets.json").write_text(
        json.dumps(
            {
                "files": {
                    "file-hash": {
                        "destinations": {
                            "current": {
                                "bucketName": "bootstrap-assets",
                                "objectKey": "asset.zip",
                                "region": "us-east-1",
                            }
                        }
                    }
                },
                "dockerImages": {
                    "image-hash": {
                        "destinations": {
                            "current": {
                                "repositoryName": "bootstrap-images",
                                "imageTag": "image-hash",
                                "region": "us-east-1",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    adapter = LiveE2EAws(region="us-east-1", session=_AssetSession())

    residuals = adapter.bootstrap_asset_residuals(tmp_path)

    assert {item.resource_type for item in residuals} == {
        "cdk-bootstrap-ecr-asset",
        "cdk-bootstrap-s3-asset",
    }
    assert {item.disposition for item in residuals} == {"shared-content-addressed-asset"}
    assert all(item.identifier_hash.startswith("sha256:") for item in residuals)


def test_asset_partition_token_uses_destination_region_partition(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class StsClient:
        def assume_role(self, **kwargs: Any) -> dict[str, Any]:
            captured["role_arn"] = kwargs["RoleArn"]
            return {
                "Credentials": {
                    "AccessKeyId": "access",
                    "SecretAccessKey": "secret",
                    "SessionToken": "token",
                }
            }

    class SourceSession:
        def get_partition_for_region(self, region: str) -> str:
            captured["partition_region"] = region
            return "aws-us-gov"

        def client(self, service: str, **_kwargs: Any) -> Any:
            assert service == "sts"
            return StsClient()

    target_client = object()

    class AssumedSession:
        def client(self, service: str, **kwargs: Any) -> object:
            captured["target"] = (service, kwargs)
            return target_client

    monkeypatch.setattr("tools.live_e2e.aws.boto3.Session", lambda **_kwargs: AssumedSession())
    adapter = LiveE2EAws(region="us-gov-west-1", session=SourceSession())

    result = adapter._asset_client(
        "s3",
        {
            "region": "us-gov-west-1",
            "assumeRoleArn": (
                "arn:${AWS::Partition}:iam::123456789012:"
                "role/cdk-hnb659fds-file-publishing-role-123456789012-us-gov-west-1"
            ),
        },
    )

    assert result is target_client
    assert captured["partition_region"] == "us-gov-west-1"
    assert captured["role_arn"].startswith("arn:aws-us-gov:iam::")
    assert captured["target"] == ("s3", {"region_name": "us-gov-west-1"})


class _LogPaginator:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def paginate(
        self,
        *,
        logGroupNamePrefix: str,
    ) -> list[dict[str, Any]]:
        return [{"logGroups": [{"logGroupName": name} for name in self.names if name.startswith(logGroupNamePrefix)]}]


class _Logs:
    def __init__(self, names: list[str]) -> None:
        self.deleted: list[str] = []
        self.names = names

    def get_paginator(self, operation: str) -> _LogPaginator:
        assert operation == "describe_log_groups"
        return _LogPaginator(self.names)

    def delete_log_group(self, *, logGroupName: str) -> None:
        self.deleted.append(logGroupName)
        self.names.remove(logGroupName)


class _LogSession:
    def __init__(self, logs: _Logs) -> None:
        self.logs = logs

    def client(self, service: str, **_: Any) -> _Logs:
        assert service == "logs"
        return self.logs


def _owned_tag_mapping(arn: str, run_id: str) -> dict[str, Any]:
    return {
        "ResourceARN": arn,
        "Tags": [
            {"Key": "LiveE2ERunId", "Value": run_id},
            {
                "Key": "aws:cloudformation:stack-name",
                "Value": stack_name(run_id),
            },
        ],
    }


def test_rds_log_inventory_comes_from_owned_stack_resources() -> None:
    run_id = "e2e-rds-log-test"

    class CloudFormation:
        def describe_stacks(self, **_: Any) -> dict[str, Any]:
            return {
                "Stacks": [
                    {
                        "StackId": "owned-stack-id",
                        "StackStatus": "CREATE_COMPLETE",
                        "Outputs": [
                            {
                                "OutputKey": "LiveE2ERunId",
                                "OutputValue": run_id,
                            }
                        ],
                    }
                ]
            }

        def list_stack_resources(self, **_: Any) -> dict[str, Any]:
            return {
                "StackResourceSummaries": [
                    {
                        "ResourceType": "AWS::RDS::DBCluster",
                        "PhysicalResourceId": "owned-database",
                    }
                ]
            }

    class Session:
        def client(self, service: str, **_: Any) -> CloudFormation:
            assert service == "cloudformation"
            return CloudFormation()

    adapter = LiveE2EAws(region="us-east-1", session=Session())

    assert adapter.owned_rds_cluster_identifiers(
        "owned-stack-id",
        run_id,
    ) == ("owned-database",)


def test_tagged_orphan_sweep_deletes_ecs_and_network_residuals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "e2e-tagged-sweep"
    arns = [
        "arn:aws:ecs:us-east-1:123456789012:service/cluster-a/service-a",
        "arn:aws:ecs:us-east-1:123456789012:cluster/cluster-a",
        "arn:aws:ecs:us-east-1:123456789012:task-definition/family:1",
        "arn:aws:ec2:us-east-1:123456789012:natgateway/nat-1",
        "arn:aws:ec2:us-east-1:123456789012:subnet/subnet-1",
        "arn:aws:kms:us-east-1:123456789012:key/key-pending",
    ]
    calls: list[tuple[str, str]] = []

    class Tagging:
        def get_paginator(self, name: str) -> Any:
            assert name == "get_resources"

            class Paginator:
                def paginate(self, **_: Any) -> Any:
                    yield {"ResourceTagMappingList": [_owned_tag_mapping(arn, run_id) for arn in arns]}

            return Paginator()

    class ECS:
        def describe_services(self, **kwargs: Any) -> dict[str, Any]:
            if any(":service/" in arn for arn in arns):
                return {"services": [{"status": "ACTIVE", "serviceName": kwargs["services"][0]}]}
            return {"services": []}

        def describe_clusters(self, **kwargs: Any) -> dict[str, Any]:
            if any(":cluster/" in arn for arn in arns):
                return {"clusters": [{"status": "ACTIVE", "clusterName": kwargs["clusters"][0]}]}
            return {"clusters": []}

        def describe_task_definition(self, **kwargs: Any) -> dict[str, Any]:
            if any(":task-definition/" in arn for arn in arns):
                return {"taskDefinition": {"status": "ACTIVE", "taskDefinitionArn": kwargs["taskDefinition"]}}
            raise ClientError(
                {"Error": {"Code": "ClientException", "Message": "The referenced task definition does not exist."}},
                "DescribeTaskDefinition",
            )

        def update_service(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("update_service", kwargs["service"]))
            return {}

        def delete_service(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("delete_service", kwargs["service"]))
            arns[:] = [arn for arn in arns if ":service/" not in arn]
            return {}

        def delete_cluster(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("delete_cluster", kwargs["cluster"]))
            arns[:] = [arn for arn in arns if ":cluster/" not in arn]
            return {}

        def deregister_task_definition(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("deregister_task_definition", kwargs["taskDefinition"]))
            return {}

        def delete_task_definitions(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("delete_task_definitions", kwargs["taskDefinitions"][0]))
            arns[:] = [arn for arn in arns if ":task-definition/" not in arn]
            return {}

    class EC2:
        def describe_nat_gateways(self, **kwargs: Any) -> dict[str, Any]:
            nat_id = kwargs["NatGatewayIds"][0]
            if any(nat_id in arn for arn in arns):
                return {"NatGateways": [{"NatGatewayId": nat_id, "State": "available"}]}
            return {"NatGateways": []}

        def delete_nat_gateway(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("delete_nat_gateway", kwargs["NatGatewayId"]))
            return {}

        def get_waiter(self, name: str) -> Any:
            assert name == "nat_gateway_deleted"

            class Waiter:
                def wait(self, **_: Any) -> None:
                    arns[:] = [arn for arn in arns if ":natgateway/" not in arn]

            return Waiter()

        def delete_subnet(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("delete_subnet", kwargs["SubnetId"]))
            arns[:] = [arn for arn in arns if ":subnet/" not in arn]
            return {}

    class KMS:
        def describe_key(self, **kwargs: Any) -> dict[str, Any]:
            return {"KeyMetadata": {"KeyState": "PendingDeletion", "KeyId": kwargs["KeyId"]}}

    class Session:
        def client(self, service: str, **_: Any) -> Any:
            return {
                "resourcegroupstaggingapi": Tagging(),
                "ecs": ECS(),
                "ec2": EC2(),
                "kms": KMS(),
            }[service]

    adapter = LiveE2EAws(region="us-east-1", session=Session())
    monkeypatch.setattr("tools.live_e2e.aws.time.sleep", lambda _: None)

    passes = adapter.cleanup_owned_tagged_resources(run_id, timeout_seconds=5, poll_seconds=0.01)

    assert passes >= 1
    assert [arn for arn in arns if ":kms:" not in arn] == []
    assert ("delete_service", "service-a") in calls
    assert ("delete_cluster", "cluster-a") in calls
    assert ("delete_subnet", "subnet-1") in calls


def test_tagged_resource_discovery_rejects_run_tag_outside_owned_stack() -> None:
    run_id = "e2e-tag-collision"

    class Tagging:
        def get_paginator(self, name: str) -> Any:
            assert name == "get_resources"

            class Paginator:
                def paginate(self, **_: Any) -> Any:
                    yield {
                        "ResourceTagMappingList": [
                            {
                                "ResourceARN": ("arn:aws:ec2:us-east-1:123456789012:vpc/vpc-unrelated"),
                                "Tags": [
                                    {"Key": "LiveE2ERunId", "Value": run_id},
                                    {
                                        "Key": "aws:cloudformation:stack-name",
                                        "Value": "UnrelatedStack",
                                    },
                                ],
                            }
                        ]
                    }

            return Paginator()

    class Session:
        def client(self, service: str, **_: Any) -> Any:
            assert service == "resourcegroupstaggingapi"
            return Tagging()

    adapter = LiveE2EAws(region="us-east-1", session=Session())
    with pytest.raises(ToolError, match="outside the owned"):
        adapter.assert_run_id_available(run_id)


def _untagged_by_deletion_mapping(arn: str, run_id: str) -> dict[str, Any]:
    """CloudFormation drops its own tags once the owning stack is deleted."""

    return {"ResourceARN": arn, "Tags": [{"Key": "LiveE2ERunId", "Value": run_id}]}


def test_post_deletion_discovery_tolerates_stack_tag_dropped_by_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "e2e-stack-tag-gone"
    kms_arn = "arn:aws:kms:us-east-1:123456789012:key/085198d5-1cd7-4049-914b-ac4a6cf14ade"

    class Tagging:
        def get_paginator(self, name: str) -> Any:
            assert name == "get_resources"

            class Paginator:
                def paginate(self, **_: Any) -> Any:
                    yield {"ResourceTagMappingList": [_untagged_by_deletion_mapping(kms_arn, run_id)]}

            return Paginator()

    class KMS:
        def describe_key(self, **_: Any) -> dict[str, Any]:
            return {"KeyMetadata": {"KeyState": "PendingDeletion"}}

    class Session:
        def client(self, service: str, **_: Any) -> Any:
            return {"resourcegroupstaggingapi": Tagging(), "kms": KMS()}[service]

    adapter = LiveE2EAws(region="us-east-1", session=Session())
    monkeypatch.setattr("tools.live_e2e.aws.time.sleep", lambda _: None)

    passes = adapter.cleanup_owned_tagged_resources(run_id, timeout_seconds=5, poll_seconds=0.01)
    residuals = adapter.residual_resources(run_id)

    assert passes == 0
    assert [r.disposition for r in residuals] == ["scheduled-deletion-expected"]


def test_post_deletion_discovery_still_rejects_a_foreign_stack_tag() -> None:
    run_id = "e2e-foreign-stack"

    class Tagging:
        def get_paginator(self, name: str) -> Any:
            assert name == "get_resources"

            class Paginator:
                def paginate(self, **_: Any) -> Any:
                    yield {
                        "ResourceTagMappingList": [
                            {
                                "ResourceARN": "arn:aws:ec2:us-east-1:123456789012:vpc/vpc-unrelated",
                                "Tags": [
                                    {"Key": "LiveE2ERunId", "Value": run_id},
                                    {"Key": "aws:cloudformation:stack-name", "Value": "UnrelatedStack"},
                                ],
                            }
                        ]
                    }

            return Paginator()

    class Session:
        def client(self, service: str, **_: Any) -> Any:
            assert service == "resourcegroupstaggingapi"
            return Tagging()

    adapter = LiveE2EAws(region="us-east-1", session=Session())
    with pytest.raises(ToolError, match="outside the owned"):
        adapter.residual_resources(run_id)


def test_tagged_orphan_sweep_ignores_task_definition_already_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "e2e-taskdef-race"
    task_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/family:1"
    arns = [task_arn]
    deregister_calls = 0
    status = "ACTIVE"

    class Tagging:
        def get_paginator(self, name: str) -> Any:
            assert name == "get_resources"

            class Paginator:
                def paginate(self, **_: Any) -> Any:
                    yield {"ResourceTagMappingList": [_owned_tag_mapping(arn, run_id) for arn in arns]}

            return Paginator()

    class ECS:
        def describe_task_definition(self, **kwargs: Any) -> dict[str, Any]:
            if not arns:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ClientException",
                            "Message": "The referenced task definition does not exist.",
                        }
                    },
                    "DescribeTaskDefinition",
                )
            return {"taskDefinition": {"status": status, "taskDefinitionArn": kwargs["taskDefinition"]}}

        def deregister_task_definition(self, **kwargs: Any) -> dict[str, Any]:
            nonlocal deregister_calls, status
            deregister_calls += 1
            # CloudFormation already started deleting the revision.
            status = "DELETE_IN_PROGRESS"
            raise ClientError(
                {
                    "Error": {
                        "Code": "ClientException",
                        "Message": (
                            "The task definition could not be deregistered because "
                            "it is in the process of being deleted."
                        ),
                    }
                },
                "DeregisterTaskDefinition",
            )

        def delete_task_definitions(self, **kwargs: Any) -> dict[str, Any]:
            # Still tagged for one more poll, then disappears from the service API.
            return {}

    class Session:
        def client(self, service: str, **_: Any) -> Any:
            return {
                "resourcegroupstaggingapi": Tagging(),
                "ecs": ECS(),
            }[service]

    adapter = LiveE2EAws(region="us-east-1", session=Session())
    polls = {"n": 0}

    def _sleep(_: float) -> None:
        polls["n"] += 1
        if polls["n"] >= 1 and status == "DELETE_IN_PROGRESS":
            arns.clear()

    monkeypatch.setattr("tools.live_e2e.aws.time.sleep", _sleep)

    passes = adapter.cleanup_owned_tagged_resources(run_id, timeout_seconds=5, poll_seconds=0.01)

    assert passes >= 1
    assert deregister_calls >= 1
    assert arns == []


def test_tagged_orphan_sweep_skips_tag_index_lag_for_missing_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "e2e-tag-lag"
    arns = [
        "arn:aws:ec2:us-east-1:123456789012:natgateway/nat-gone",
        "arn:aws:ecs:us-east-1:123456789012:cluster/cluster-gone",
        "arn:aws:ec2:us-east-1:123456789012:vpc-flow-log/fl-gone",
    ]

    class Tagging:
        def get_paginator(self, name: str) -> Any:
            assert name == "get_resources"

            class Paginator:
                def paginate(self, **_: Any) -> Any:
                    yield {"ResourceTagMappingList": [_owned_tag_mapping(arn, run_id) for arn in arns]}

            return Paginator()

    class ECS:
        def describe_clusters(self, **kwargs: Any) -> dict[str, Any]:
            return {"clusters": []}

    class EC2:
        def describe_nat_gateways(self, **kwargs: Any) -> dict[str, Any]:
            return {"NatGateways": []}

        def describe_flow_logs(self, **kwargs: Any) -> dict[str, Any]:
            return {"FlowLogs": []}

    class Session:
        def client(self, service: str, **_: Any) -> Any:
            return {
                "resourcegroupstaggingapi": Tagging(),
                "ecs": ECS(),
                "ec2": EC2(),
            }[service]

    adapter = LiveE2EAws(region="us-east-1", session=Session())
    monkeypatch.setattr("tools.live_e2e.aws.time.sleep", lambda _: None)

    passes = adapter.cleanup_owned_tagged_resources(run_id, timeout_seconds=5, poll_seconds=0.01)
    residuals = adapter.residual_resources(run_id)

    assert passes == 0
    assert residuals == ()


def test_orphan_log_cleanup_is_bounded_to_exact_e2e_stack_prefix() -> None:
    name = stack_name("e2e-log-test")
    matching_log = f"/aws/lambda/{name}-CustomA"
    rds_log = "/aws/rds/cluster/owned-database/error"
    logs = _Logs([matching_log, rds_log, "/aws/lambda/unrelated"])
    adapter = LiveE2EAws(region="us-east-1", session=_LogSession(logs))

    deleted = adapter.cleanup_owned_log_groups(
        name,
        "e2e-log-test",
        ("owned-database",),
    )

    assert deleted == 2
    assert logs.deleted == [matching_log, rds_log]
    with pytest.raises(ToolError, match="outside"):
        adapter.cleanup_owned_log_groups("OpenemrEcsStack", "e2e-log-test")


class _CloudFormation:
    def describe_stack_events(self, **_: Any) -> dict[str, Any]:
        def stamp(minute: int) -> datetime:
            return datetime(2026, 7, 31, 12, minute, tzinfo=timezone.utc)

        return {
            "StackEvents": [
                {
                    "ResourceType": "AWS::CloudFormation::Stack",
                    "ResourceStatus": "CREATE_IN_PROGRESS",
                    "Timestamp": stamp(0),
                },
                {
                    "ResourceType": "AWS::RDS::DBCluster",
                    "ResourceStatus": "CREATE_IN_PROGRESS",
                    "Timestamp": stamp(2),
                },
                {
                    "ResourceType": "AWS::RDS::DBCluster",
                    "ResourceStatus": "CREATE_COMPLETE",
                    "Timestamp": stamp(12),
                },
                {
                    "ResourceType": "AWS::CloudFormation::Stack",
                    "ResourceStatus": "CREATE_COMPLETE",
                    "Timestamp": stamp(15),
                },
            ]
        }


class _Session:
    def client(self, service: str, **_: Any) -> Any:
        assert service == "cloudformation"
        return _CloudFormation()


def test_phase_timing_uses_cloudformation_api_timestamps() -> None:
    adapter = LiveE2EAws(region="us-east-1", session=_Session())
    phases = {phase.name: phase for phase in adapter.event_phases("stack-id", operation="CREATE")}

    assert phases["cloudformation-deployment"].duration_seconds == 900
    assert phases["aurora-provisioning"].duration_seconds == 600
    assert all(phase.source == "cloudformation-events-api" for phase in phases.values())


class _Waiter:
    def wait(self, **_: Any) -> None:
        return None


class _ValidationEcs:
    def get_waiter(self, name: str) -> _Waiter:
        assert name == "services_stable"
        return _Waiter()

    def describe_services(self, **_: Any) -> dict[str, Any]:
        return {
            "services": [
                {
                    "runningCount": 2,
                    "desiredCount": 2,
                    "deployments": [{"rolloutState": "COMPLETED"}],
                    "events": [],
                }
            ]
        }

    def list_tasks(self, **_: Any) -> dict[str, Any]:
        return {"taskArns": ["task-1", "task-2"]}

    def describe_tasks(self, **_: Any) -> dict[str, Any]:
        task = {
            "lastStatus": "RUNNING",
            "healthStatus": "HEALTHY",
            "containers": [{"lastStatus": "RUNNING", "healthStatus": "HEALTHY"}],
        }
        return {"tasks": [task, task]}


class _ValidationElbv2:
    def describe_target_health(self, **_: Any) -> dict[str, Any]:
        return {
            "TargetHealthDescriptions": [
                {"TargetHealth": {"State": "healthy"}},
                {"TargetHealth": {"State": "healthy"}},
            ]
        }


class _ValidationEfs:
    def describe_file_systems(self, **_: Any) -> dict[str, Any]:
        return {"FileSystems": [{"LifeCycleState": "available"}]}


class _ValidationRds:
    def describe_db_clusters(self, **_: Any) -> dict[str, Any]:
        return {"DBClusters": [{"Status": "available"}]}


class _ValidationCache:
    def describe_serverless_caches(self, **_: Any) -> dict[str, Any]:
        return {"ServerlessCaches": [{"Status": "available"}]}


class _ValidationWaf:
    def get_web_acl_for_resource(self, **_: Any) -> dict[str, Any]:
        return {"WebACL": {"Name": "test"}}


class _ValidationLogs:
    def filter_log_events(self, **_: Any) -> dict[str, Any]:
        return {"events": [{"message": "OpenEMR startup complete"}]}


class _ValidationSession:
    def __init__(self) -> None:
        self.clients = {
            "ecs": _ValidationEcs(),
            "elbv2": _ValidationElbv2(),
            "efs": _ValidationEfs(),
            "rds": _ValidationRds(),
            "elasticache": _ValidationCache(),
            "wafv2": _ValidationWaf(),
            "logs": _ValidationLogs(),
        }

    def client(self, service: str, **_: Any) -> Any:
        return self.clients[service]


class _ValidationAdapter(LiveE2EAws):
    def __init__(self) -> None:
        super().__init__(region="us-east-1", session=_ValidationSession())

    def assert_owned_stack(self, _: str, run_id: str) -> dict[str, Any]:
        assert run_id == "e2e-validation"
        return {
            "StackId": "stack-id",
            "StackStatus": "CREATE_COMPLETE",
            "CreationTime": datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
            "Outputs": [
                {"OutputKey": "ECSClusterName", "OutputValue": "cluster"},
                {"OutputKey": "ECSServiceName", "OutputValue": "service"},
                {"OutputKey": "EFSSitesFileSystemId", "OutputValue": "fs-sites"},
                {"OutputKey": "EFSSSLFileSystemId", "OutputValue": "fs-ssl"},
                {
                    "OutputKey": "DatabaseClusterArn",
                    "OutputValue": "arn:aws:rds:us-east-1:111122223333:cluster:test",
                },
                {
                    "OutputKey": "ApplicationURL",
                    "OutputValue": "https://openemr.test.example",
                },
                {"OutputKey": "LogGroupName", "OutputValue": "/aws/ecs/test"},
            ],
        }

    def _stack_resources(self, _: str) -> list[dict[str, Any]]:
        return [
            {"ResourceType": "AWS::ECS::Cluster", "ResourceStatus": "CREATE_COMPLETE"},
            {"ResourceType": "AWS::ECS::Service", "ResourceStatus": "CREATE_COMPLETE"},
            {"ResourceType": "AWS::EFS::FileSystem", "ResourceStatus": "CREATE_COMPLETE"},
            {
                "ResourceType": "AWS::ElastiCache::ServerlessCache",
                "ResourceStatus": "CREATE_COMPLETE",
                "PhysicalResourceId": "cache",
            },
            {
                "ResourceType": "AWS::ElasticLoadBalancingV2::LoadBalancer",
                "ResourceStatus": "CREATE_COMPLETE",
                "PhysicalResourceId": "arn:aws:elasticloadbalancing:us-east-1:111122223333:loadbalancer/app/test",
            },
            {
                "ResourceType": "AWS::ElasticLoadBalancingV2::TargetGroup",
                "ResourceStatus": "CREATE_COMPLETE",
                "PhysicalResourceId": "target-group",
            },
            {"ResourceType": "AWS::RDS::DBCluster", "ResourceStatus": "CREATE_COMPLETE"},
            {
                "ResourceType": "AWS::WAFv2::WebACLAssociation",
                "ResourceStatus": "CREATE_COMPLETE",
            },
        ]


def test_deployment_validation_checks_health_logs_waf_and_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        status_code=200,
        text="<title>OpenEMR</title>",
        elapsed=SimpleNamespace(total_seconds=lambda: 0.05),
    )
    monkeypatch.setattr(
        "tools.live_e2e.aws.requests.get",
        lambda *_args, **_kwargs: response,
    )

    checks, timings = _ValidationAdapter().validate_deployment(
        stack_name_or_id="stack-id",
        run_id="e2e-validation",
        profile="default",
        https_timeout_seconds=1,
        poll_seconds=0.001,
    )

    check_names = {check.name for check in checks}
    assert {
        "ecs-task-health",
        "load-balancer-targets",
        "elasticache-serverless",
        "waf-association",
        "startup-logs",
        "application-smoke",
    }.issubset(check_names)
    timing_names = {timing.name for timing in timings}
    assert {
        "ecs-steady-state-validation",
        "aurora-availability-validation",
        "elasticache-availability-validation",
        "http-readiness",
        "application-smoke-test",
    }.issubset(timing_names)


def test_progress_reporter_emits_phases_and_rate_limits_heartbeats(tmp_path: Path) -> None:
    stream_path = tmp_path / "progress.log"
    with stream_path.open("w", encoding="utf-8") as handle:
        progress = ProgressReporter(
            enabled=True,
            verbose=True,
            stream=handle,
            heartbeat_interval_seconds=60.0,
        )
        progress.phase("deploy", "starting")
        progress.heartbeat("still working", force=True)
        progress.heartbeat("should be suppressed")
        progress.detail("verbose detail")
        with progress.pulse("blocking", interval_seconds=60.0):
            pass

    text = stream_path.read_text(encoding="utf-8")
    assert "==> deploy — starting" in text
    assert "still working" in text
    assert text.count("should be suppressed") == 0
    assert "verbose detail" in text
    assert "blocking finished" in text
