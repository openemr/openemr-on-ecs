"""Floci-backed integration tests for live E2E AWS surfaces and mocked orchestration."""

from __future__ import annotations

import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import Any, Iterator

import boto3
import pytest

from openemr_ecs.constants import StackConstants
from tools._shared import CommandResult, ToolError, hash_account_id
from tools.live_e2e.aws import LiveE2EAws
from tools.live_e2e.emulator import FLOCI_E2E_ENVIRONMENT
from tools.live_e2e.floci_seed import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_REGION,
    DEFAULT_ROUTE53_DOMAIN,
    operator_session,
    seed_live_e2e_world,
    seed_owned_stack,
    seed_service_smoke_resources,
)
from tools.live_e2e.models import CheckResult, PhaseTiming
from tools.live_e2e.runner import (
    ACCOUNT_CONFIRMATION,
    APPROVAL_ENVIRONMENT,
    CREATE_CONFIRMATION,
    DESTROY_CONFIRMATION,
    ZONE_CONFIRMATION,
    LiveE2ERunner,
    stack_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.floci]

FLOCI_IMAGE = os.environ.get(
    "OPENEMR_FLOCI_IMAGE",
    "floci/floci:1.6.0@sha256:eab36252ea43a4a73928423f0372219052c5c6f87207f6c4754db14b91d6ed30",
)


def _floci_required() -> bool:
    return os.environ.get(FLOCI_E2E_ENVIRONMENT, "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="module")
def floci_container() -> Iterator[Any]:
    try:
        __import__("docker")
    except ImportError as exc:  # pragma: no cover - dependency pin regression
        if _floci_required():
            pytest.fail(f"Docker SDK is required in the Floci CI job: {exc}")
        pytest.skip(f"Docker SDK is not installed: {exc}")
    try:
        from floci import FlociContainer
    except ImportError as exc:  # pragma: no cover - dependency pin regression
        if _floci_required():
            pytest.fail(f"testcontainers-floci is required in the Floci CI job: {exc}")
        pytest.skip(f"testcontainers-floci is not installed: {exc}")

    try:
        with (
            FlociContainer(image=FLOCI_IMAGE)
            .with_account_id(DEFAULT_ACCOUNT_ID)
            .with_region(DEFAULT_REGION) as container
        ):
            yield container
    except Exception as exc:  # pragma: no cover - CI/docker environment gaps
        if _floci_required():
            pytest.fail(f"Floci container failed to start in the required lifecycle job: {exc}")
        pytest.skip(f"Floci container could not start: {exc}")


@pytest.fixture(scope="module")
def floci_endpoint(floci_container: Any) -> str:
    return str(floci_container.get_endpoint())


@pytest.fixture(scope="module")
def floci_admin_session(floci_container: Any) -> Any:
    return boto3.Session(
        region_name=floci_container.get_region(),
        aws_access_key_id=floci_container.get_access_key(),
        aws_secret_access_key=floci_container.get_secret_key(),
    )


@pytest.fixture(scope="module")
def seeded_world(floci_admin_session: Any, floci_endpoint: str) -> dict[str, str]:
    return seed_live_e2e_world(
        floci_admin_session,
        endpoint_url=floci_endpoint,
        account_id=DEFAULT_ACCOUNT_ID,
        region=DEFAULT_REGION,
        route53_domain=DEFAULT_ROUTE53_DOMAIN,
    )


@pytest.fixture(scope="module")
def floci_session(seeded_world: dict[str, str], floci_endpoint: str) -> Any:
    return operator_session(
        endpoint_url=floci_endpoint,
        region=DEFAULT_REGION,
        access_key_id=seeded_world["aws_access_key_id"],
        secret_access_key=seeded_world["aws_secret_access_key"],
    )


def _adapter(floci_session: Any, floci_endpoint: str) -> LiveE2EAws:
    return LiveE2EAws(
        region=DEFAULT_REGION,
        session=floci_session,
        endpoint_url=floci_endpoint,
        emulated=True,
    )


def test_floci_sts_identity(floci_session: Any, floci_endpoint: str, seeded_world: dict[str, str]) -> None:
    adapter = _adapter(floci_session, floci_endpoint)
    identity = adapter.identity()
    assert identity["account_id"] == seeded_world["account_id"]
    assert identity["account_hash"] == hash_account_id(seeded_world["account_id"])


def test_floci_service_smoke(floci_session: Any, floci_endpoint: str, seeded_world: dict[str, str]) -> None:
    del seeded_world
    details = seed_service_smoke_resources(floci_session, endpoint_url=floci_endpoint, region=DEFAULT_REGION)
    s3 = floci_session.client("s3", endpoint_url=floci_endpoint, region_name=DEFAULT_REGION)
    body = s3.get_object(Bucket=details["bucket_name"], Key="smoke.txt")["Body"].read()
    assert body == b"floci"
    kms = floci_session.client("kms", endpoint_url=floci_endpoint, region_name=DEFAULT_REGION)
    assert kms.describe_key(KeyId=details["kms_key_id"])["KeyMetadata"]["KeyId"]
    logs = floci_session.client("logs", endpoint_url=floci_endpoint, region_name=DEFAULT_REGION)
    groups = logs.describe_log_groups(logGroupNamePrefix=details["log_group"])["logGroups"]
    assert any(item.get("logGroupName") == details["log_group"] for item in groups)
    ecs = floci_session.client("ecs", endpoint_url=floci_endpoint, region_name=DEFAULT_REGION)
    clusters = ecs.describe_clusters(clusters=[details["ecs_cluster"]])["clusters"]
    assert clusters and clusters[0]["clusterName"] == details["ecs_cluster"]


def test_floci_live_e2e_preflight_adapter(
    floci_session: Any,
    floci_endpoint: str,
    seeded_world: dict[str, str],
) -> None:
    adapter = _adapter(floci_session, floci_endpoint)
    checks, facts = adapter.preflight(
        approved_account=seeded_world["account_id"],
        route53_domain=seeded_world["route53_domain"],
        bootstrap_stack_name=seeded_world["bootstrap_stack_name"],
    )
    names = {check.name for check in checks}
    assert "aws-identity" in names
    assert "cdk-bootstrap" in names
    assert "availability-zones" in names
    assert "quota-vpc-count" in names
    assert "deployment-write-permissions" in names
    assert facts["bootstrap_version"] == 21
    assert facts["hosted_zone_id"] == seeded_world["hosted_zone_id"]
    assert len(facts["availability_zones"]) == 2


def test_floci_owned_stack_cleanup(
    floci_session: Any,
    floci_endpoint: str,
    seeded_world: dict[str, str],
) -> None:
    del seeded_world
    run_id = "e2e-floci-owned"
    name = stack_name(run_id)
    adapter = _adapter(floci_session, floci_endpoint)
    seed_owned_stack(
        floci_session,
        endpoint_url=floci_endpoint,
        stack_name=name,
        run_id=run_id,
        region=DEFAULT_REGION,
    )
    owned = adapter.assert_owned_stack(name, run_id)
    assert "CREATE" in str(owned.get("StackStatus", "")) or "COMPLETE" in str(owned.get("StackStatus", ""))
    stack_id = adapter.delete_owned_stack(name, run_id)
    assert stack_id
    adapter.wait_for_stack_deleted(stack_id, timeout_seconds=60, poll_seconds=0.5)
    assert adapter.describe_stack(name) is None


def test_floci_rejects_unowned_stack_cleanup(
    floci_session: Any,
    floci_endpoint: str,
    seeded_world: dict[str, str],
) -> None:
    del seeded_world
    run_id = "e2e-floci-owner"
    other_run = "e2e-floci-other"
    name = stack_name(run_id)
    adapter = _adapter(floci_session, floci_endpoint)
    seed_owned_stack(
        floci_session,
        endpoint_url=floci_endpoint,
        stack_name=name,
        run_id=other_run,
        region=DEFAULT_REGION,
    )
    with pytest.raises(ToolError, match="ownership marker"):
        adapter.delete_owned_stack(name, run_id)


def test_floci_mocked_runner_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    floci_session: Any,
    floci_endpoint: str,
    seeded_world: dict[str, str],
) -> None:
    """Fast ownership/cleanup orchestration test; full CDK path is in test_floci_live_e2e_full."""

    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("# floci e2e\n", encoding="utf-8")
    (root / "cdk.json").write_text("{}\n", encoding="utf-8")
    (root / "openemr_ecs").mkdir()
    (root / "docs").mkdir()
    (root / "e2e-results").mkdir()
    (root / "node_modules" / ".bin").mkdir(parents=True)
    cdk_bin = root / "node_modules" / ".bin" / "cdk"
    cdk_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cdk_bin.chmod(0o755)
    cdk_assets_bin = root / "node_modules" / ".bin" / "cdk-assets"
    cdk_assets_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cdk_assets_bin.chmod(0o755)

    monkeypatch.setenv(FLOCI_E2E_ENVIRONMENT, "1")
    monkeypatch.setenv("OPENEMR_AWS_ENDPOINT_URL", floci_endpoint)
    credentials = floci_session.get_credentials()
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", credentials.access_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", credentials.secret_key)
    monkeypatch.setenv("CI", "true")

    run_id = "e2e-floci-mock"
    name = stack_name(run_id)

    def aws_factory(**kwargs: Any) -> LiveE2EAws:
        return LiveE2EAws(
            region=str(kwargs.get("region", DEFAULT_REGION)),
            session=floci_session,
            endpoint_url=floci_endpoint,
            emulated=True,
        )

    runner = LiveE2ERunner(root=root, aws_factory=aws_factory)
    monkeypatch.setattr(runner, "_git_commit_and_clean", lambda: "a" * 40)
    monkeypatch.setattr(runner, "_git_branch_and_repository", lambda: ("floci", "openemr/openemr-on-ecs"))
    monkeypatch.setattr(
        runner,
        "_local_checks",
        lambda _cdk: (
            (CheckResult("docker", "pass", "floci-mocked"),),
            {
                "python_version": "3.14.0",
                "node_version": "v24.1.0",
                "cdk_cli_version": "2.1029.0",
                "cdk_library_version": version("aws-cdk-lib"),
                "cdk_assets_version": "4.7.0",
            },
        ),
    )

    def fake_cdk_command(**kwargs: Any) -> CommandResult:
        operation = str(kwargs["operation"])
        run_dir = Path(kwargs["run_dir"])
        if operation == "synth":
            out = run_dir / "cdk.out"
            out.mkdir(parents=True, exist_ok=True)
            template = {
                "Resources": {
                    "Cluster": {"Type": "AWS::ECS::Cluster", "Properties": {}},
                    "Service": {"Type": "AWS::ECS::Service", "Properties": {}},
                    "Sites": {"Type": "AWS::EFS::FileSystem", "Properties": {}},
                    "Cache": {"Type": "AWS::ElastiCache::ServerlessCache", "Properties": {}},
                    "Alb": {"Type": "AWS::ElasticLoadBalancingV2::LoadBalancer", "Properties": {}},
                    "Db": {"Type": "AWS::RDS::DBCluster", "Properties": {}},
                    "WafAssoc": {"Type": "AWS::WAFv2::WebACLAssociation", "Properties": {}},
                },
                "Outputs": {"LiveE2ERunId": {"Value": run_id}},
            }
            template_file = f"{name}.template.json"
            (out / template_file).write_text(json.dumps(template), encoding="utf-8")
            (out / "manifest.json").write_text(
                json.dumps(
                    {
                        "artifacts": {
                            name: {
                                "type": "aws:cloudformation:stack",
                                "properties": {"templateFile": template_file},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (out / f"{name}.assets.json").write_text(json.dumps({"files": {}, "dockerImages": {}}), encoding="utf-8")
        elif operation == "deploy":
            seed_owned_stack(
                floci_session,
                endpoint_url=floci_endpoint,
                stack_name=name,
                run_id=run_id,
                region=DEFAULT_REGION,
            )
        return CommandResult(("cdk", operation), 0, "", "", 0.05)

    monkeypatch.setattr(runner, "_cdk_command", fake_cdk_command)
    monkeypatch.setattr("tools.live_e2e.runner._validate_e2e_template", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "tools.live_e2e.runner._assembly_template",
        lambda *_args, **_kwargs: {
            "Resources": {},
            "Outputs": {
                "LiveE2ERunId": {"Value": run_id},
                "OpenEMRVersion": {"Value": StackConstants.OPENEMR_VERSION},
            },
        },
    )
    monkeypatch.setattr(
        "tools.live_e2e.runner._assembly_versions",
        lambda *_args, **_kwargs: {
            "cdk_library_version": version("aws-cdk-lib"),
            "openemr_version": StackConstants.OPENEMR_VERSION,
            "aurora_version": StackConstants.AURORA_MYSQL_ENGINE_VERSION.aurora_mysql_full_version,
        },
    )
    monkeypatch.setattr("tools.live_e2e.runner._directory_fingerprint", lambda _path: "sha256:flociassembly")
    monkeypatch.setattr(
        "tools.live_e2e.runner._assembly_resource_inventory",
        lambda *_args, **_kwargs: {
            "AWS::ECS::Cluster": 1,
            "AWS::ECS::Service": 1,
            "AWS::EFS::FileSystem": 1,
            "AWS::ElastiCache::ServerlessCache": 1,
            "AWS::ElasticLoadBalancingV2::LoadBalancer": 1,
            "AWS::RDS::DBCluster": 1,
            "AWS::WAFv2::WebACLAssociation": 1,
        },
    )
    monkeypatch.setattr("tools.live_e2e.runner._docker_build_duration", lambda _path: 0.01)

    def fake_validate_deployment(
        self: LiveE2EAws, **kwargs: Any
    ) -> tuple[tuple[CheckResult, ...], tuple[PhaseTiming, ...]]:
        stack = self.assert_owned_stack(str(kwargs["stack_name_or_id"]), str(kwargs["run_id"]))
        return (
            (
                CheckResult("cloudformation-stack", "pass", str(stack.get("StackStatus"))),
                CheckResult("application-https", "pass", "floci-mocked https"),
            ),
            (PhaseTiming("post-deploy-https-wait", 0.01, "floci-mocked"),),
        )

    monkeypatch.setattr(LiveE2EAws, "validate_deployment", fake_validate_deployment)
    monkeypatch.setattr(LiveE2EAws, "event_phases", lambda self, *_args, **_kwargs: ())
    monkeypatch.setattr(LiveE2EAws, "bootstrap_asset_residuals", lambda self, _dir: ())
    monkeypatch.setattr(LiveE2EAws, "residual_resources", lambda self, _run_id: ())
    monkeypatch.setattr(LiveE2EAws, "cleanup_owned_log_groups", lambda self, *_args, **_kwargs: 0)
    monkeypatch.setattr(LiveE2EAws, "cleanup_owned_tagged_resources", lambda self, *_args, **_kwargs: 0)
    # Fixture stack has no real RDS; still record a durable identifier so cleanup
    # inventory rules match production (stack_id_hash implies RDS was inventoried).
    monkeypatch.setattr(
        LiveE2EAws,
        "owned_rds_cluster_identifiers",
        lambda self, *_args, **_kwargs: ("floci-mock-db",),
    )

    preflight_path = runner.preflight(
        approved_account=seeded_world["account_id"],
        region=DEFAULT_REGION,
        route53_domain=seeded_world["route53_domain"],
        allowed_ipv4_cidr="8.8.8.8/32",
        profile="default",
        aws_profile=None,
        cdk_command=str(cdk_bin),
        bootstrap_stack_name=seeded_world["bootstrap_stack_name"],
        confirm_dedicated_zone=ZONE_CONFIRMATION,
        confirm_non_production_account=ACCOUNT_CONFIRMATION,
        run_id=run_id,
        require_tty=False,
    )

    monkeypatch.setenv(APPROVAL_ENVIRONMENT, run_id)
    result = runner.run(
        preflight_path=preflight_path,
        approved_account=seeded_world["account_id"],
        confirm_create=CREATE_CONFIRMATION,
        confirm_destroy=DESTROY_CONFIRMATION,
        confirm_costs=True,
        require_tty=False,
        deploy_timeout_seconds=60,
        readiness_timeout_seconds=30,
        cleanup_timeout_seconds=180,
        poll_seconds=0.5,
    )
    state_path = root / ".live-e2e" / "runs" / run_id / "state.json"
    failure_detail = ""
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        failure_detail = f" failure_type={state.get('failure_type')!r} failure_detail={state.get('failure_detail')!r}"
    assert result.status == "passed", (
        f"status={result.status} failure_phase={result.failure_phase} "
        f"cleanup={result.cleanup_status} notes={result.notes}{failure_detail}"
    )
    assert result.cleanup_status in {"complete", "stack-deleted-with-expected-residuals", "not-required"}
    assert _adapter(floci_session, floci_endpoint).describe_stack(name) is None
