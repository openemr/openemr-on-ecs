"""Guarded, local-only live E2E orchestration."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import importlib.metadata
import ipaddress
import json
import os
import platform
import re
import shlex
import shutil
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence, cast

from tools._shared import (
    ToolError,
    atomic_write_json,
    atomic_write_text,
    ensure_owner_only_directory,
    fingerprint,
    hash_account_id,
    redact_text,
    repository_root,
    run_command,
    utc_now,
)

from .aws import LiveE2EAws, unexpected_residuals
from .emulator import (
    assert_safe_emulator_endpoint,
    is_floci_e2e_enabled,
    resolve_emulator_endpoint_url,
)
from .models import SCHEMA_VERSION, CheckResult, PhaseTiming, ResidualResource, RunResult
from .progress import NULL_PROGRESS, ProgressReporter
from .report import append_result, regenerate_report, update_cleanup_result

CREATE_CONFIRMATION = "CREATE LIVE E2E"
DESTROY_CONFIRMATION = "DESTROY LIVE E2E"
ZONE_CONFIRMATION = "DEDICATED E2E ZONE"
ACCOUNT_CONFIRMATION = "NON-PRODUCTION ACCOUNT"
KEEP_CONFIRMATION = "KEEP FAILED E2E"
APPROVAL_ENVIRONMENT = "OPENEMR_LIVE_E2E_APPROVED_RUN_ID"
PREFLIGHT_TTL_HOURS = 4
RUNNER_VERSION = "1.0.0"
SUPPORTED_PYTHON = (3, 14)
SUPPORTED_NODE_MAJOR = 24
_CI_ENVIRONMENT_SIGNALS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "CIRCLECI",
    "CODEBUILD_BUILD_ARN",
    "CODEBUILD_BUILD_ID",
    "BITBUCKET_BUILD_NUMBER",
    "TEAMCITY_VERSION",
    "TRAVIS",
    "TF_BUILD",
    "JENKINS_URL",
)
_RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{5,47}")
_REGION = re.compile(r"[a-z]{2}(?:-gov)?-[a-z]+-\d")
_PROFILES: dict[str, dict[str, str]] = {
    "default": {},
    "api-enabled": {
        "activate_openemr_apis": "true",
        "enable_data_api": "true",
        "enable_patient_portal": "true",
    },
}
_PREFLIGHT_EXPECTED_RESOURCE_TYPES = {
    "AWS::ECS::Cluster",
    "AWS::ECS::Service",
    "AWS::EFS::FileSystem",
    "AWS::ElastiCache::ServerlessCache",
    "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "AWS::RDS::DBCluster",
    "AWS::WAFv2::WebACLAssociation",
}


class LiveE2ERunner:
    """Run preflight, deploy, validate, measure, and always attempt teardown."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        aws_factory: Any = LiveE2EAws,
        progress: ProgressReporter | None = None,
    ) -> None:
        self.root = repository_root(root)
        self.local_root = self.root / ".live-e2e"
        self.history_path = self.root / "e2e-results" / "history.json"
        self.report_path = self.root / "docs" / "maintainers" / "deployment-timing.md"
        self.aws_factory = aws_factory
        self.progress = progress or NULL_PROGRESS

    def preflight(
        self,
        *,
        approved_account: str,
        region: str,
        route53_domain: str,
        allowed_ipv4_cidr: str,
        profile: str,
        aws_profile: str | None,
        cdk_command: str,
        bootstrap_stack_name: str,
        confirm_dedicated_zone: str,
        confirm_non_production_account: str,
        run_id: str | None = None,
        require_tty: bool = True,
    ) -> Path:
        """Perform local and read-only AWS checks, then synthesize the exact stack."""

        self._assert_local_execution(require_tty=require_tty)
        if confirm_dedicated_zone != ZONE_CONFIRMATION:
            raise ToolError(f"--confirm-dedicated-zone must equal {ZONE_CONFIRMATION!r}")
        if confirm_non_production_account != ACCOUNT_CONFIRMATION:
            raise ToolError(f"--confirm-non-production-account must equal {ACCOUNT_CONFIRMATION!r}")
        run_id = validate_run_id(run_id or new_run_id())
        validate_inputs(
            approved_account=approved_account,
            region=region,
            route53_domain=route53_domain,
            allowed_ipv4_cidr=allowed_ipv4_cidr,
            profile=profile,
        )
        with self._lock():
            preflight_started = time.monotonic()
            self.progress.phase("preflight", f"run_id={run_id}")
            self.progress.info("Validating local tools and clean git worktree")
            git_commit = self._git_commit_and_clean()
            branch, repository = self._git_branch_and_repository()
            resolved_cdk_command = _resolve_cdk_executable(self.root, cdk_command)
            tool_validation_started = time.monotonic()
            local_checks, local_versions = self._local_checks(resolved_cdk_command)
            local_versions["cdk_executable_sha256"] = _file_sha256(Path(resolved_cdk_command))
            local_versions["cdk_assets_executable_sha256"] = _file_sha256(
                (self.root / "node_modules" / ".bin" / "cdk-assets").resolve()
            )
            tool_validation_duration = time.monotonic() - tool_validation_started
            checks = list(local_checks)
            self.progress.info("Running read-only AWS preflight checks")
            adapter = self._aws_adapter(region=region, profile_name=aws_profile)
            aws_checks, aws_facts = adapter.preflight(
                approved_account=approved_account,
                route53_domain=route53_domain,
                bootstrap_stack_name=bootstrap_stack_name,
            )
            checks.extend(aws_checks)
            if adapter.describe_stack(stack_name(run_id)) is not None:
                raise ToolError(f"Refusing to reuse existing stack {stack_name(run_id)}")
            adapter.assert_run_id_available(run_id)
            checks.append(CheckResult("stack-name-available", "pass", "unique E2E stack does not exist"))

            runs_root = self.local_root / "runs"
            _ensure_owner_only_directory(runs_root)
            run_dir = self._run_dir(run_id)
            _ensure_owner_only_directory(run_dir, exist_ok=False)
            contexts = deployment_contexts(
                run_id=run_id,
                account_id=approved_account,
                region=region,
                availability_zones=aws_facts["availability_zones"],
                route53_domain=route53_domain,
                hosted_zone_id=str(aws_facts["hosted_zone_id"]),
                allowed_ipv4_cidr=allowed_ipv4_cidr,
                profile=profile,
                emulated=is_floci_e2e_enabled(resolve_emulator_endpoint_url()),
            )
            self.progress.info("Synthesizing the exact live E2E cloud assembly")
            synth = self._cdk_command(
                cdk_command=resolved_cdk_command,
                operation="synth",
                run_id=run_id,
                contexts=contexts,
                run_dir=run_dir,
                aws_profile=aws_profile,
                account_id=approved_account,
                region=region,
                timeout_seconds=30 * 60,
            )
            if not synth.ok:
                raise ToolError(f"CDK synthesis failed; inspect {run_dir.relative_to(self.root)}/synth.log")
            checks.append(CheckResult("cdk-synthesis", "pass", "exact live E2E stack synthesized locally"))
            assembly_fingerprint = _directory_fingerprint(run_dir / "cdk.out")
            deployment_versions = _assembly_versions(
                run_dir / "cdk.out",
                stack_name(run_id),
            )
            _validate_e2e_template(
                _assembly_template(run_dir / "cdk.out", stack_name(run_id)),
                run_id=run_id,
                hosted_zone_id=str(aws_facts["hosted_zone_id"]),
                resource_suffix=str(contexts["openemr_resource_suffix"]),
                profile=profile,
            )
            checks.append(
                CheckResult(
                    "synthesized-safety-policy",
                    "pass",
                    "ownership, isolation, lifecycle, DNS, and profile invariants verified",
                )
            )
            resource_types = _assembly_resource_inventory(
                run_dir / "cdk.out",
                stack_name(run_id),
            )
            missing_types = sorted(_PREFLIGHT_EXPECTED_RESOURCE_TYPES - set(resource_types))
            if missing_types:
                raise ToolError("Synthesized stack is missing expected resource types: " + ", ".join(missing_types))
            resource_count = sum(resource_types.values())
            checks.append(
                CheckResult(
                    "synthesized-resource-plan",
                    "pass",
                    f"{resource_count} resources across {len(resource_types)} types",
                )
            )
            self.progress.info("Running template-only CDK diff (no change set)")
            diff = self._cdk_command(
                cdk_command=resolved_cdk_command,
                operation="diff",
                run_id=run_id,
                contexts=contexts,
                run_dir=run_dir,
                aws_profile=aws_profile,
                account_id=approved_account,
                region=region,
                timeout_seconds=30 * 60,
            )
            if not diff.ok:
                raise ToolError(f"CDK no-change-set diff failed; inspect {run_dir.relative_to(self.root)}/diff.log")
            checks.append(
                CheckResult(
                    "cdk-diff",
                    "pass",
                    "template-only diff completed without creating a change set",
                )
            )
            assembly_fingerprint = _directory_fingerprint(run_dir / "cdk.out")

            created = datetime.now(timezone.utc).replace(microsecond=0)
            preflight_phases = (
                PhaseTiming(
                    "tool-validation",
                    round(tool_validation_duration, 3),
                    "local-monotonic-clock",
                ),
                PhaseTiming(
                    "cdk-synthesis",
                    synth.duration_seconds,
                    "local-monotonic-clock",
                ),
                PhaseTiming(
                    "cdk-diff",
                    diff.duration_seconds,
                    "local-monotonic-clock-no-change-set",
                ),
                PhaseTiming(
                    "preflight",
                    round(time.monotonic() - preflight_started, 3),
                    "local-monotonic-clock",
                ),
            )
            record = {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "created_at": _timestamp(created),
                "expires_at": _timestamp(created + timedelta(hours=PREFLIGHT_TTL_HOURS)),
                "git_commit": git_commit,
                "branch": branch,
                "repository": repository,
                "account_id": approved_account,
                "account_hash": self._account_label(approved_account),
                "region": region,
                "availability_zones": aws_facts["availability_zones"],
                "route53_domain": route53_domain,
                "hosted_zone_id": aws_facts["hosted_zone_id"],
                "allowed_ipv4_cidr": allowed_ipv4_cidr,
                "profile": profile,
                "aws_profile": aws_profile,
                "bootstrap_stack_name": bootstrap_stack_name,
                "cdk_command": resolved_cdk_command,
                "stack_name": stack_name(run_id),
                "context_fingerprint": fingerprint(contexts),
                "assembly_fingerprint": assembly_fingerprint,
                "bootstrap_version": aws_facts["bootstrap_version"],
                "versions": {**local_versions, **deployment_versions},
                "resource_count": resource_count,
                "resource_types": resource_types,
                "preflight_phases": [asdict(phase) for phase in preflight_phases],
                "checks": [asdict(check) for check in checks],
            }
            path = self._preflight_path(run_id)
            _ensure_owner_only_directory(path.parent)
            atomic_write_json(path, record)
            os.chmod(path, 0o600)
            self.progress.info(
                f"Preflight complete ({len(checks)} checks, {resource_count} resources); approval file ready"
            )
            return path

    def run(
        self,
        *,
        preflight_path: Path,
        approved_account: str,
        confirm_create: str,
        confirm_destroy: str,
        confirm_costs: bool,
        keep_on_failure: bool = False,
        confirm_keep_on_failure: str | None = None,
        require_tty: bool = True,
        deploy_timeout_seconds: float = 90 * 60,
        readiness_timeout_seconds: float = 30 * 60,
        cleanup_timeout_seconds: float = 60 * 60,
        poll_seconds: float = 20,
    ) -> RunResult:
        """Execute one approved deployment and guarantee a cleanup attempt."""

        self._assert_local_execution(require_tty=require_tty)
        preflight = self._load_preflight(preflight_path)
        run_id = str(preflight["run_id"])
        self._assert_approvals(
            run_id=run_id,
            approved_account=approved_account,
            account_id=str(preflight["account_id"]),
            confirm_create=confirm_create,
            confirm_destroy=confirm_destroy,
            confirm_costs=confirm_costs,
            keep_on_failure=keep_on_failure,
            confirm_keep_on_failure=confirm_keep_on_failure,
        )
        with self._lock():
            self.progress.phase("run", f"run_id={run_id}")
            self.progress.info("Revalidating preflight and consuming approval")
            self._revalidate_preflight(preflight)
            adapter = self._aws_adapter(
                region=str(preflight["region"]),
                profile_name=_optional_string(preflight.get("aws_profile")),
            )
            identity = adapter.identity()
            if identity["account_id"] != preflight["account_id"]:
                raise ToolError("Active AWS account changed after preflight")
            if adapter.describe_stack(str(preflight["stack_name"])) is not None:
                raise ToolError("Live E2E stack unexpectedly exists before deployment")
            preflight["consumed_at"] = utc_now()
            atomic_write_json(self._preflight_path(run_id), preflight)

            return self._run_approved(
                preflight=preflight,
                adapter=adapter,
                approved_account=approved_account,
                deploy_timeout_seconds=deploy_timeout_seconds,
                readiness_timeout_seconds=readiness_timeout_seconds,
                cleanup_timeout_seconds=cleanup_timeout_seconds,
                poll_seconds=poll_seconds,
                keep_on_failure=keep_on_failure,
            )

    def cleanup(
        self,
        *,
        run_id: str,
        approved_account: str,
        region: str,
        aws_profile: str | None,
        confirm_destroy: str,
        require_tty: bool = True,
        timeout_seconds: float = 60 * 60,
        poll_seconds: float = 20,
    ) -> tuple[str, int]:
        """Retry deletion for an interrupted run, with account and ownership checks."""

        self._assert_local_execution(require_tty=require_tty)
        run_id = validate_run_id(run_id)
        if confirm_destroy != DESTROY_CONFIRMATION:
            raise ToolError(f"--confirm-destroy must equal {DESTROY_CONFIRMATION!r}")
        if os.environ.get(APPROVAL_ENVIRONMENT) != run_id:
            raise ToolError(f"{APPROVAL_ENVIRONMENT} must equal the run ID")
        validate_inputs(
            approved_account=approved_account,
            region=region,
            route53_domain="e2e.invalid.example",
            allowed_ipv4_cidr="192.0.2.1/32",
            profile="default",
            allow_documentation_values=True,
        )
        # Cleanup may follow a run that updated only the tracked sanitized
        # history/report. Reject every other worktree change before AWS access.
        self._git_commit_for_cleanup()
        with self._lock():
            cleanup_started = time.monotonic()
            self.progress.phase("cleanup", f"run_id={run_id}")
            preflight = self._load_preflight(self._preflight_path(run_id))
            if preflight["region"] != region:
                raise ToolError("Cleanup region does not match the owner-only preflight record")
            if preflight["account_id"] != approved_account:
                raise ToolError("Cleanup account does not match the owner-only preflight record")
            recorded_profile = _optional_string(preflight.get("aws_profile"))
            if aws_profile is not None and aws_profile != recorded_profile:
                raise ToolError("Cleanup AWS profile does not match the owner-only preflight record")
            adapter = self._aws_adapter(region=region, profile_name=recorded_profile)
            identity = adapter.identity()
            if identity["account_id"] != approved_account:
                raise ToolError("Active AWS account does not match --approved-account")
            name = str(preflight["stack_name"])
            stack = adapter.describe_stack(name)
            rds_cluster_identifiers = self._recorded_rds_cluster_identifiers(run_id)
            if stack is not None:
                self.progress.info("Owned stack still present; requesting deletion")
                rds_cluster_identifiers = adapter.owned_rds_cluster_identifiers(name, run_id)
                self._update_state(
                    run_id,
                    {"rds_cluster_identifiers": list(rds_cluster_identifiers)},
                )
                stack_id = adapter.delete_owned_stack(name, run_id)
                adapter.wait_for_stack_deleted(
                    stack_id or name,
                    timeout_seconds=timeout_seconds,
                    poll_seconds=poll_seconds,
                )
            else:
                self.progress.info("Stack already absent; sweeping owned leftovers")
            deleted_log_groups = adapter.cleanup_owned_log_groups(
                name,
                run_id,
                rds_cluster_identifiers,
            )
            if deleted_log_groups:
                self.progress.info(f"Deleted {deleted_log_groups} owned CloudWatch log group(s)")
            adapter.cleanup_owned_tagged_resources(
                run_id,
                timeout_seconds=min(900.0, timeout_seconds),
                poll_seconds=max(poll_seconds, 2.0),
            )
            residuals = _merge_residuals(
                adapter.residual_resources(run_id),
                adapter.bootstrap_asset_residuals(self._run_dir(run_id) / "cdk.out"),
            )
            status = (
                "not-required"
                if stack is None and not residuals and deleted_log_groups == 0
                else _cleanup_status(residuals)
            )
            self._update_state(
                run_id,
                {
                    "cleanup_status": status,
                    "deleted_orphan_log_groups": deleted_log_groups,
                    "residual_count": len(residuals),
                },
            )
            cleanup_phase = PhaseTiming(
                "cleanup-retry",
                round(time.monotonic() - cleanup_started, 3),
                "local-monotonic-clock",
            )
            finished_at = utc_now()
            updated = update_cleanup_result(
                self.history_path,
                self.report_path,
                run_id=run_id,
                cleanup_status=status,
                residuals=residuals,
                phase=cleanup_phase,
                finished_at=finished_at,
            )
            if not updated:
                state = json.loads(self._state_path(run_id).read_text(encoding="utf-8"))
                versions = preflight["versions"]
                append_result(
                    self.history_path,
                    self.report_path,
                    RunResult(
                        schema_version=SCHEMA_VERSION,
                        run_id=run_id,
                        started_at=str(state.get("started_at", preflight["created_at"])),
                        finished_at=finished_at,
                        git_commit=str(preflight["git_commit"]),
                        branch=str(preflight["branch"]),
                        repository=str(preflight["repository"]),
                        account_hash=str(preflight["account_hash"]),
                        region=region,
                        safe_stack_id=("sha256:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]),
                        profile=str(preflight["profile"]),
                        configuration_fingerprint=(f"sha256:{preflight['context_fingerprint']}"),
                        bootstrap_state=(f"ready-v{int(preflight['bootstrap_version'])}"),
                        python_version=str(versions["python_version"]),
                        node_version=str(versions["node_version"]),
                        cdk_cli_version=str(versions["cdk_cli_version"]),
                        cdk_library_version=str(versions["cdk_library_version"]),
                        cdk_assets_version=str(versions["cdk_assets_version"]),
                        openemr_version=str(versions["openemr_version"]),
                        aurora_version=str(versions["aurora_version"]),
                        test_runner_version=RUNNER_VERSION,
                        status="interrupted",
                        stack_status="unknown-after-interruption",
                        cleanup_status=status,
                        failure_phase="run-history-unavailable",
                        phases=(cleanup_phase,),
                        residuals=residuals,
                        notes=(
                            "Original run history was unavailable; this record "
                            "contains cleanup-only recovery evidence.",
                        ),
                        metadata={
                            "timing_schema": "openemr-live-e2e-v1",
                            "record_scope": "cleanup-only",
                        },
                    ),
                )
            return status, len(residuals)

    def regenerate_report(self) -> None:
        """Regenerate committed timing documentation under the runner lock."""

        with self._lock():
            regenerate_report(self.history_path, self.report_path)

    def _run_approved(
        self,
        *,
        preflight: dict[str, Any],
        adapter: LiveE2EAws,
        approved_account: str,
        deploy_timeout_seconds: float,
        readiness_timeout_seconds: float,
        cleanup_timeout_seconds: float,
        poll_seconds: float,
        keep_on_failure: bool,
    ) -> RunResult:
        run_id = str(preflight["run_id"])
        run_dir = self._run_dir(run_id)
        contexts = deployment_contexts(
            run_id=run_id,
            account_id=str(preflight["account_id"]),
            region=str(preflight["region"]),
            availability_zones=preflight["availability_zones"],
            route53_domain=str(preflight["route53_domain"]),
            hosted_zone_id=str(preflight["hosted_zone_id"]),
            allowed_ipv4_cidr=str(preflight["allowed_ipv4_cidr"]),
            profile=str(preflight["profile"]),
            emulated=is_floci_e2e_enabled(resolve_emulator_endpoint_url()),
        )
        if fingerprint(contexts) != preflight["context_fingerprint"]:
            raise ToolError("Preflight context fingerprint is invalid")

        started_at = utc_now()
        total_started = time.monotonic()
        phases = [PhaseTiming(**item) for item in preflight["preflight_phases"]]
        checks = tuple(
            CheckResult(
                name=str(item["name"]),
                status=str(item["status"]),
                detail="preflight check completed",
            )
            for item in preflight["checks"]
        )
        deployment_checks: tuple[CheckResult, ...] = ()
        status = "failed"
        stack_status = "not-created"
        cleanup_status = "not-required"
        residuals: tuple[ResidualResource, ...] = ()
        stack_id: str | None = None
        deployment_attempted = False
        failure: BaseException | None = None
        active_phase = "deployment"
        failure_phase: str | None = None
        self._write_state(
            run_id,
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "account_hash": preflight["account_hash"],
                "region": preflight["region"],
                "profile": preflight["profile"],
                "stack_name": preflight["stack_name"],
                "git_commit": preflight["git_commit"],
                "started_at": started_at,
                "status": "deploying",
                "cleanup_status": "pending",
            },
        )

        try:
            deployment_pipeline_started = time.monotonic()
            deployment_attempted = True
            self._update_state(run_id, {"deployment_attempted": True})
            self.progress.phase("asset-pipeline", "building and publishing CDK assets")
            asset_pipeline = self._cdk_command(
                cdk_command=str(preflight["cdk_command"]),
                operation="publish-assets",
                run_id=run_id,
                contexts=contexts,
                run_dir=run_dir,
                aws_profile=_optional_string(preflight.get("aws_profile")),
                account_id=approved_account,
                region=str(preflight["region"]),
                timeout_seconds=deploy_timeout_seconds,
            )
            asset_build_duration = _docker_build_duration(run_dir / "docker-timings.jsonl")
            phases.extend(
                (
                    PhaseTiming(
                        "asset-build",
                        asset_build_duration,
                        "docker-proxy-monotonic-clock",
                    ),
                    PhaseTiming(
                        "asset-publication",
                        round(
                            max(
                                0.0,
                                asset_pipeline.duration_seconds - asset_build_duration,
                            ),
                            3,
                        ),
                        "cdk-asset-pipeline-minus-docker-build",
                    ),
                    PhaseTiming(
                        "asset-pipeline",
                        asset_pipeline.duration_seconds,
                        "local-monotonic-clock",
                    ),
                )
            )
            if not asset_pipeline.ok:
                raise ToolError(
                    _cdk_failure_message(run_dir, "publish-assets", "CDK asset build or publication failed")
                )
            self.progress.info(f"Asset pipeline finished in {asset_pipeline.duration_seconds:.1f}s")

            self.progress.phase("cdk-deploy", f"stack={preflight['stack_name']}")
            deploy = self._cdk_command(
                cdk_command=str(preflight["cdk_command"]),
                operation="deploy",
                run_id=run_id,
                contexts=contexts,
                run_dir=run_dir,
                aws_profile=_optional_string(preflight.get("aws_profile")),
                account_id=approved_account,
                region=str(preflight["region"]),
                timeout_seconds=deploy_timeout_seconds,
            )
            phases.append(PhaseTiming("cdk-deploy", deploy.duration_seconds, "local-monotonic-clock"))
            phases.append(
                PhaseTiming(
                    "deployment-with-assets",
                    round(time.monotonic() - deployment_pipeline_started, 3),
                    "local-monotonic-clock",
                )
            )
            stack = adapter.describe_stack(str(preflight["stack_name"]))
            if stack is not None:
                stack_id = str(stack["StackId"])
                stack_status = str(stack.get("StackStatus", "unknown"))
                self._update_state(run_id, {"stack_id_hash": fingerprint(stack_id), "stack_status": stack_status})
            if not deploy.ok:
                raise ToolError(_cdk_failure_message(run_dir, "deploy", "CDK deployment command failed"))
            if stack is None or stack_id is None:
                raise ToolError("CDK reported success but the stack does not exist")
            self.progress.info(f"CDK deploy finished in {deploy.duration_seconds:.1f}s")

            phases.extend(adapter.event_phases(stack_id, operation="CREATE"))
            active_phase = "validation"
            deployment_checks, validation_phases = adapter.validate_deployment(
                stack_name_or_id=stack_id,
                run_id=run_id,
                profile=str(preflight["profile"]),
                https_timeout_seconds=readiness_timeout_seconds,
                poll_seconds=poll_seconds,
            )
            phases.extend(validation_phases)
            stack_status = "CREATE_COMPLETE"
            status = "passed"
            self._update_state(run_id, {"status": "validated", "stack_status": stack_status})
            self.progress.info("Validation passed")
        except KeyboardInterrupt as exc:
            status = "interrupted"
            failure = exc
            failure_phase = active_phase
            self.progress.info(f"Interrupted during {active_phase}")
        except Exception as exc:
            status = "failed"
            failure = exc
            failure_phase = active_phase
            self.progress.info(f"Failed during {active_phase}: {type(exc).__name__}: {redact_text(str(exc))}")
        finally:
            active_phase = "cleanup"
            cleanup_started = time.monotonic()
            retain_failed_stack = failure is not None and keep_on_failure
            self.progress.phase(
                "cleanup",
                (
                    "retaining failed stack by explicit request"
                    if retain_failed_stack
                    else "deleting owned stack and sweeping leftovers"
                ),
            )
            if retain_failed_stack:
                try:
                    stack = self._reconcile_attempted_stack(
                        adapter,
                        stack_id or str(preflight["stack_name"]),
                        deployment_attempted=deployment_attempted,
                        poll_seconds=poll_seconds,
                    )
                    if stack is not None:
                        adapter.assert_owned_stack(str(stack["StackId"]), run_id)
                    residual_started = time.monotonic()
                    residuals = _merge_residuals(
                        adapter.residual_resources(run_id),
                        adapter.bootstrap_asset_residuals(run_dir / "cdk.out"),
                    )
                    phases.append(
                        PhaseTiming(
                            "residual-resource-verification",
                            round(time.monotonic() - residual_started, 3),
                            "local-monotonic-clock",
                        )
                    )
                    if stack is not None or residuals:
                        cleanup_status = "retained-on-failure"
                    else:
                        cleanup_status = "not-required"
                except Exception as retention_exc:
                    cleanup_status = "failed"
                    status = "failed"
                    failure_phase = failure_phase or "cleanup"
                    self.progress.info(f"Retention check failed: {type(retention_exc).__name__}")
                phases.append(
                    PhaseTiming(
                        "cleanup-request",
                        round(time.monotonic() - cleanup_started, 3),
                        "explicit-keep-on-failure",
                    )
                )
            else:
                try:
                    stack = self._reconcile_attempted_stack(
                        adapter,
                        stack_id or str(preflight["stack_name"]),
                        deployment_attempted=deployment_attempted,
                        poll_seconds=poll_seconds,
                    )
                    if stack is None:
                        cleanup_status = "not-required"
                    else:
                        stack_id = str(stack["StackId"])
                        rds_cluster_identifiers = adapter.owned_rds_cluster_identifiers(
                            stack_id,
                            run_id,
                        )
                        self._update_state(
                            run_id,
                            {"rds_cluster_identifiers": list(rds_cluster_identifiers)},
                        )
                        cleanup_request_started = time.monotonic()
                        self.progress.info("Requesting owned stack deletion")
                        adapter.delete_owned_stack(stack_id, run_id)
                        phases.append(
                            PhaseTiming(
                                "cleanup-request",
                                round(
                                    time.monotonic() - cleanup_request_started,
                                    3,
                                ),
                                "local-monotonic-clock",
                            )
                        )
                        adapter.wait_for_stack_deleted(
                            stack_id,
                            timeout_seconds=cleanup_timeout_seconds,
                            poll_seconds=poll_seconds,
                        )
                        try:
                            phases.extend(adapter.event_phases(stack_id, operation="DELETE"))
                        except Exception as timing_error:
                            # Stack deletion is authoritative. Timing collection is
                            # best-effort after CloudFormation removes the stack.
                            self._update_state(
                                run_id,
                                {
                                    "delete_timing_status": "unavailable",
                                    "delete_timing_failure_type": type(timing_error).__name__,
                                },
                            )
                    deleted_log_groups = adapter.cleanup_owned_log_groups(
                        str(preflight["stack_name"]),
                        run_id,
                        self._recorded_rds_cluster_identifiers(run_id),
                    )
                    self._update_state(
                        run_id,
                        {"deleted_orphan_log_groups": deleted_log_groups},
                    )
                    tagged_cleanup_started = time.monotonic()
                    tagged_cleanup_passes = adapter.cleanup_owned_tagged_resources(
                        run_id,
                        timeout_seconds=min(900.0, cleanup_timeout_seconds),
                        poll_seconds=max(poll_seconds, 2.0),
                    )
                    phases.append(
                        PhaseTiming(
                            "tagged-resource-cleanup",
                            round(time.monotonic() - tagged_cleanup_started, 3),
                            f"passes={tagged_cleanup_passes}",
                        )
                    )
                    residual_started = time.monotonic()
                    self.progress.info("Verifying residuals after cleanup")
                    asset_residuals: tuple[ResidualResource, ...] = ()
                    if deployment_attempted:
                        asset_residuals = adapter.bootstrap_asset_residuals(run_dir / "cdk.out")
                    residuals = _merge_residuals(
                        adapter.residual_resources(run_id),
                        asset_residuals,
                    )
                    phases.append(
                        PhaseTiming(
                            "residual-resource-verification",
                            round(time.monotonic() - residual_started, 3),
                            "local-monotonic-clock",
                        )
                    )
                    if residuals or stack is not None:
                        cleanup_status = _cleanup_status(residuals)
                    unexpected = unexpected_residuals(residuals)
                    if unexpected:
                        status = "failed"
                        failure_phase = failure_phase or "cleanup"
                        self.progress.info(f"Unexpected residuals remain ({len(unexpected)}); marking cleanup failed")
                    else:
                        self.progress.info(
                            f"Residual verification complete "
                            f"({len(residuals)} expected residual(s), cleanup={cleanup_status})"
                        )
                except Exception as cleanup_exc:
                    cleanup_status = "failed"
                    status = "failed"
                    failure_phase = failure_phase or "cleanup"
                    if failure is None:
                        failure = cleanup_exc
                phases.append(
                    PhaseTiming(
                        "cleanup",
                        round(time.monotonic() - cleanup_started, 3),
                        "local-monotonic-clock",
                    )
                )

        phases.append(
            PhaseTiming(
                "total",
                round(time.monotonic() - total_started, 3),
                "local-monotonic-clock",
            )
        )
        notes: tuple[str, ...] = ()
        if failure is not None:
            notes = ("Live E2E did not pass; inspect the owner-only local run log and state.",)
            self._update_state(
                run_id,
                {
                    "failure_type": type(failure).__name__,
                    "failure_detail": redact_text(str(failure))[:2_000],
                },
            )
        result = RunResult(
            schema_version=SCHEMA_VERSION,
            run_id=run_id,
            started_at=started_at,
            finished_at=utc_now(),
            git_commit=str(preflight["git_commit"]),
            branch=str(preflight["branch"]),
            repository=str(preflight["repository"]),
            account_hash=str(preflight["account_hash"]),
            region=str(preflight["region"]),
            safe_stack_id=("sha256:" + hashlib.sha256(str(preflight["stack_name"]).encode("utf-8")).hexdigest()[:12]),
            profile=str(preflight["profile"]),
            configuration_fingerprint=f"sha256:{preflight['context_fingerprint']}",
            bootstrap_state=f"ready-v{int(preflight['bootstrap_version'])}",
            python_version=str(preflight["versions"]["python_version"]),
            node_version=str(preflight["versions"]["node_version"]),
            cdk_cli_version=str(preflight["versions"]["cdk_cli_version"]),
            cdk_library_version=str(preflight["versions"]["cdk_library_version"]),
            cdk_assets_version=str(preflight["versions"]["cdk_assets_version"]),
            openemr_version=str(preflight["versions"]["openemr_version"]),
            aurora_version=str(preflight["versions"]["aurora_version"]),
            test_runner_version=RUNNER_VERSION,
            status=status,
            stack_status=stack_status,
            cleanup_status=cleanup_status,
            failure_phase=failure_phase,
            phases=tuple(phases),
            checks=checks + deployment_checks,
            residuals=tuple(residuals),
            notes=notes,
            metadata={"timing_schema": "openemr-live-e2e-v1"},
        )
        self._write_raw_result(
            run_id,
            {
                "stack_name": preflight["stack_name"],
                "result": result.to_dict(),
            },
        )
        append_result(self.history_path, self.report_path, result)
        self._update_state(
            run_id,
            {
                "status": status,
                "cleanup_status": cleanup_status,
                "finished_at": result.finished_at,
                "residual_count": len(residuals),
            },
        )
        return result

    def _local_checks(
        self,
        cdk_command: str,
    ) -> tuple[tuple[CheckResult, ...], dict[str, str]]:
        if sys.version_info[:2] != SUPPORTED_PYTHON:
            raise ToolError(
                "Live E2E requires repository-supported Python 3.14.x; "
                f"found {platform.python_version()}. Create the virtual environment with python3.14."
            )
        commands = (
            ("git", ("git", "--version")),
            ("aws-cli", ("aws", "--version")),
            ("node", ("node", "--version")),
            ("docker", ("docker", "info", "--format", "{{.ServerVersion}}")),
            ("cdk", (cdk_command, "--version")),
            (
                "cdk-assets",
                (
                    str(self.root / "node_modules" / ".bin" / "cdk-assets"),
                    "--version",
                ),
            ),
        )
        checks: list[CheckResult] = []
        observed: dict[str, str] = {}
        for name, argv in commands:
            executable = argv[0]
            if shutil.which(executable) is None and not Path(executable).is_file():
                if name in {"cdk", "cdk-assets"}:
                    raise ToolError(
                        f"Pinned local {name} is unavailable at {executable}; run npm ci in the repository root."
                    )
                raise ToolError(f"Required local executable is unavailable: {executable}")
            result = run_command(argv, cwd=self.root, timeout_seconds=30)
            if not result.ok:
                raise ToolError(f"Required local tool check failed: {name}")
            version = (result.stdout or result.stderr).strip().splitlines()[0][:120]
            observed[name] = redact_text(version)
            checks.append(CheckResult(f"local-{name}", "pass", redact_text(version)))
        node_match = re.fullmatch(r"v?(\d+)(?:\.\d+){1,2}", observed["node"])
        if node_match is None or int(node_match.group(1)) != SUPPORTED_NODE_MAJOR:
            raise ToolError(
                "Live E2E requires repository-supported Node.js 24.x; "
                f"found {observed['node']!r}. Install Node.js 24, then run npm ci."
            )
        versions = {
            "python_version": platform.python_version(),
            "node_version": observed["node"],
            "cdk_cli_version": observed["cdk"],
            "cdk_library_version": importlib.metadata.version("aws-cdk-lib"),
            "cdk_assets_version": observed["cdk-assets"],
        }
        return tuple(checks), versions

    def _git_commit_and_clean(self) -> str:
        status = run_command(("git", "status", "--porcelain"), cwd=self.root, timeout_seconds=30)
        if not status.ok:
            raise ToolError("Cannot inspect Git worktree")
        if status.stdout.strip():
            raise ToolError("Live E2E requires a clean Git worktree")
        commit = run_command(("git", "rev-parse", "HEAD"), cwd=self.root, timeout_seconds=30)
        value = commit.stdout.strip()
        if not commit.ok or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ToolError("Cannot resolve the Git commit under test")
        return value

    def _git_commit_for_cleanup(self) -> str:
        status = run_command(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=self.root,
            timeout_seconds=30,
        )
        if not status.ok:
            raise ToolError("Cannot inspect Git worktree")
        allowed_generated = {
            "docs/maintainers/deployment-timing.md",
            "e2e-results/history.json",
        }
        for line in status.stdout.splitlines():
            if len(line) < 4:
                raise ToolError("Cannot safely interpret Git worktree state")
            change, path = line[:2], line[3:]
            if change not in {" M", "M ", "MM"} or path not in allowed_generated:
                raise ToolError(
                    "Live E2E cleanup requires a clean worktree except for its generated timing history and report"
                )
        commit = run_command(
            ("git", "rev-parse", "HEAD"),
            cwd=self.root,
            timeout_seconds=30,
        )
        value = commit.stdout.strip()
        if not commit.ok or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ToolError("Cannot resolve the Git commit used for cleanup")
        return value

    def _git_branch_and_repository(self) -> tuple[str, str]:
        branch = run_command(
            ("git", "branch", "--show-current"),
            cwd=self.root,
            timeout_seconds=30,
        )
        branch_name = branch.stdout.strip()
        if not branch.ok or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", branch_name):
            raise ToolError("Live E2E requires a named Git branch")
        remote = run_command(
            ("git", "remote", "get-url", "origin"),
            cwd=self.root,
            timeout_seconds=30,
        )
        if not remote.ok:
            raise ToolError("Cannot resolve the verified origin repository")
        return branch_name, _repository_slug(remote.stdout.strip())

    def _revalidate_preflight(self, value: dict[str, Any]) -> None:
        created = _parse_timestamp(str(value["created_at"]))
        expires = _parse_timestamp(str(value["expires_at"]))
        now = datetime.now(timezone.utc)
        if created > now + timedelta(minutes=5):
            raise ToolError("Preflight creation time is in the future")
        if expires <= created or expires - created > timedelta(hours=PREFLIGHT_TTL_HOURS, minutes=1):
            raise ToolError("Preflight validity window is invalid")
        if now > expires or now - created > timedelta(hours=PREFLIGHT_TTL_HOURS):
            raise ToolError("Preflight expired; rerun it against current AWS state")
        if value.get("consumed_at") is not None:
            raise ToolError("Preflight was already consumed; create a new approved run")
        if self._git_commit_and_clean() != value["git_commit"]:
            raise ToolError("Git commit changed after preflight")
        if _directory_fingerprint(self._run_dir(str(value["run_id"])) / "cdk.out") != value["assembly_fingerprint"]:
            raise ToolError("Synthesized cloud assembly changed after preflight")
        command = str(value["cdk_command"])
        if not Path(command).is_file() or not os.access(command, os.X_OK):
            raise ToolError("Preflight CDK executable is no longer available")
        _, current_versions = self._local_checks(command)
        current_versions["cdk_executable_sha256"] = _file_sha256(Path(command))
        current_versions["cdk_assets_executable_sha256"] = _file_sha256(
            (self.root / "node_modules" / ".bin" / "cdk-assets").resolve()
        )
        expected_versions = value["versions"]
        for key in (
            "python_version",
            "node_version",
            "cdk_cli_version",
            "cdk_library_version",
            "cdk_executable_sha256",
            "cdk_assets_version",
            "cdk_assets_executable_sha256",
        ):
            if current_versions.get(key) != expected_versions.get(key):
                raise ToolError(f"Local toolchain changed after preflight ({key}); rerun preflight")

    def _assert_approvals(
        self,
        *,
        run_id: str,
        approved_account: str,
        account_id: str,
        confirm_create: str,
        confirm_destroy: str,
        confirm_costs: bool,
        keep_on_failure: bool,
        confirm_keep_on_failure: str | None,
    ) -> None:
        if approved_account != account_id:
            raise ToolError("--approved-account does not match the preflight account")
        if confirm_create != CREATE_CONFIRMATION:
            raise ToolError(f"--confirm-create must equal {CREATE_CONFIRMATION!r}")
        if confirm_destroy != DESTROY_CONFIRMATION:
            raise ToolError(f"--confirm-destroy must equal {DESTROY_CONFIRMATION!r}")
        if not confirm_costs:
            raise ToolError("--confirm-costs is required")
        if keep_on_failure and confirm_keep_on_failure != KEEP_CONFIRMATION:
            raise ToolError(f"--confirm-keep-on-failure must equal {KEEP_CONFIRMATION!r}")
        if not keep_on_failure and confirm_keep_on_failure is not None:
            raise ToolError("--confirm-keep-on-failure is valid only with --keep-on-failure")
        if os.environ.get(APPROVAL_ENVIRONMENT) != run_id:
            raise ToolError(f"{APPROVAL_ENVIRONMENT} must equal the approved run ID")

    def _aws_adapter(self, *, region: str, profile_name: str | None) -> Any:
        configured_endpoint = resolve_emulator_endpoint_url()
        emulated = is_floci_e2e_enabled(configured_endpoint)
        if configured_endpoint and not emulated:
            raise ToolError("AWS endpoint overrides require explicit guarded Floci mode")
        endpoint_url = configured_endpoint if emulated else None
        adapter = self.aws_factory(
            region=region,
            profile_name=profile_name,
            endpoint_url=endpoint_url,
            emulated=emulated,
        )
        # Attach progress after construction so custom test factories need not accept it.
        try:
            adapter.progress = self.progress
        except AttributeError, TypeError:
            pass
        return adapter

    @staticmethod
    def _cdk_emulator_environ() -> dict[str, str]:
        """Force CDK/AWS SDK traffic onto Floci when Floci E2E mode is active."""

        endpoint_url = resolve_emulator_endpoint_url()
        if not is_floci_e2e_enabled(endpoint_url):
            if endpoint_url:
                raise ToolError("AWS endpoint overrides require explicit guarded Floci mode")
            return {}
        safe_endpoint = assert_safe_emulator_endpoint(endpoint_url)
        access_key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip() or "test"
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip() or "test"
        env: dict[str, str] = {
            "AWS_ENDPOINT_URL": safe_endpoint,
            "AWS_ACCESS_KEY_ID": access_key,
            "AWS_SECRET_ACCESS_KEY": secret_key,
            "CDK_DISABLE_CLI_TELEMETRY": "true",
            "JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION": "1",
        }
        session_token = os.environ.get("AWS_SESSION_TOKEN", "").strip()
        if session_token:
            env["AWS_SESSION_TOKEN"] = session_token
        return env

    @staticmethod
    def _assert_local_execution(*, require_tty: bool) -> None:
        endpoint_url = resolve_emulator_endpoint_url()
        if is_floci_e2e_enabled(endpoint_url):
            return
        if endpoint_url:
            raise ToolError("AWS endpoint overrides require explicit guarded Floci mode")
        if any(os.environ.get(name, "").strip().lower() not in {"", "0", "false"} for name in _CI_ENVIRONMENT_SIGNALS):
            raise ToolError("Live E2E is disabled in CI")
        if require_tty and (not sys.stdin.isatty() or not sys.stdout.isatty()):
            raise ToolError("Live E2E requires an interactive local terminal")

    @staticmethod
    def _reconcile_attempted_stack(
        adapter: Any,
        stack_name_or_id: str,
        *,
        deployment_attempted: bool,
        poll_seconds: float,
    ) -> dict[str, Any] | None:
        """Allow an accepted CloudFormation create request to become visible."""

        stack = cast(dict[str, Any] | None, adapter.describe_stack(stack_name_or_id))
        if stack is not None or not deployment_attempted:
            return stack
        deadline = time.monotonic() + min(120.0, max(0.1, poll_seconds * 3))
        while time.monotonic() < deadline:
            time.sleep(min(max(poll_seconds, 0.01), 5.0))
            stack = cast(dict[str, Any] | None, adapter.describe_stack(stack_name_or_id))
            if stack is not None:
                return stack
        return None

    def _cdk_command(
        self,
        *,
        cdk_command: str,
        operation: str,
        run_id: str,
        contexts: dict[str, str],
        run_dir: Path,
        aws_profile: str | None,
        account_id: str,
        region: str,
        timeout_seconds: float,
    ) -> Any:
        if operation not in {"synth", "diff", "publish-assets", "deploy"}:
            raise ValueError("Unsupported CDK operation")
        if operation == "publish-assets":
            manifests = sorted((run_dir / "cdk.out").glob("*.assets.json"))
            if len(manifests) != 1 or manifests[0].is_symlink():
                raise ToolError("Expected exactly one regular CDK asset manifest for the E2E stack")
            argv = [
                str(self.root / "node_modules" / ".bin" / "cdk-assets"),
                "publish",
                "--path",
                str(manifests[0]),
            ]
        else:
            argv = [cdk_command, operation, stack_name(run_id)]
        if operation == "synth":
            argv.extend(
                (
                    "--app",
                    shlex.join((sys.executable, str(self.root / "app.py"))),
                    "--quiet",
                    "--no-lookups",
                    "--output",
                    str(run_dir / "cdk.out"),
                )
            )
        elif operation == "diff":
            argv.extend(
                (
                    "--app",
                    str(run_dir / "cdk.out"),
                    "--no-change-set",
                    "--exclusively",
                )
            )
        elif operation == "deploy":
            argv.extend(
                (
                    "--app",
                    str(run_dir / "cdk.out"),
                    "--require-approval",
                    "never",
                    "--exclusively",
                )
            )
        if operation == "synth":
            for key, value in sorted(contexts.items()):
                argv.extend(("--context", f"{key}={value}"))
        env = {
            "CDK_DEFAULT_ACCOUNT": account_id,
            "CDK_DEFAULT_REGION": region,
            "AWS_REGION": region,
            "AWS_DEFAULT_REGION": region,
            "OPENEMR_LIVE_E2E_RUNNER_RUN_ID": run_id,
        }
        emulator_env = self._cdk_emulator_environ()
        if aws_profile and not emulator_env:
            env["AWS_PROFILE"] = aws_profile
        env.update(emulator_env)
        if operation == "publish-assets":
            real_docker = _resolve_executable("docker")
            timing_path = (run_dir / "docker-timings.jsonl").resolve()
            timing_path.unlink(missing_ok=True)
            env.update(
                {
                    "CDK_DOCKER": str((self.root / "tools" / "live_e2e" / "docker_proxy.py").resolve()),
                    "OPENEMR_E2E_REAL_DOCKER": real_docker,
                    "OPENEMR_E2E_DOCKER_TIMINGS": str(timing_path),
                }
            )
        self.progress.info(f"Starting CDK {operation}")
        with self.progress.pulse(f"CDK {operation} still running"):
            result = run_command(
                argv,
                cwd=self.root,
                timeout_seconds=timeout_seconds,
                env=env,
                umask=0o077,
            )
        log = "\n".join(
            [
                f"command={' '.join(result.argv)}",
                f"returncode={result.returncode}",
                f"duration_seconds={result.duration_seconds:.3f}",
                "",
                redact_text(result.stdout),
                redact_text(result.stderr),
            ]
        )
        atomic_write_text(run_dir / f"{operation}.log", log)
        os.chmod(run_dir / f"{operation}.log", 0o600)
        return result

    def _load_preflight(self, path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise ToolError("Preflight path must be a regular non-symlink file")
        path = path.resolve()
        expected_root = (self.local_root / "preflight").resolve()
        try:
            path.relative_to(expected_root)
        except ValueError as exc:
            raise ToolError("Preflight file must be inside .live-e2e/preflight") from exc
        if not path.is_file():
            raise ToolError("Preflight path must be a regular non-symlink file")
        if path.stat().st_mode & 0o077:
            raise ToolError("Preflight file must be owner-only")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolError(f"Cannot read preflight: {exc}") from exc
        required = {
            "schema_version",
            "run_id",
            "created_at",
            "expires_at",
            "git_commit",
            "branch",
            "repository",
            "account_id",
            "account_hash",
            "region",
            "availability_zones",
            "route53_domain",
            "hosted_zone_id",
            "allowed_ipv4_cidr",
            "profile",
            "cdk_command",
            "stack_name",
            "context_fingerprint",
            "assembly_fingerprint",
            "bootstrap_version",
            "versions",
            "resource_count",
            "resource_types",
            "preflight_phases",
            "checks",
        }
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
            raise ToolError("Unsupported preflight schema")
        if not required.issubset(value):
            raise ToolError("Preflight is missing required fields")
        if not isinstance(value["account_id"], str) or not re.fullmatch(r"\d{12}", value["account_id"]):
            raise ToolError("Preflight account ID is invalid")
        if not isinstance(value["region"], str) or not _REGION.fullmatch(value["region"]):
            raise ToolError("Preflight Region is invalid")
        availability_zones = value["availability_zones"]
        if (
            not isinstance(availability_zones, list)
            or len(availability_zones) != 2
            or any(
                not isinstance(zone, str) or not re.fullmatch(rf"{re.escape(value['region'])}[a-z]", zone)
                for zone in availability_zones
            )
            or len(set(availability_zones)) != len(availability_zones)
        ):
            raise ToolError("Preflight Availability Zone inventory is invalid")
        if not isinstance(value["hosted_zone_id"], str) or not re.fullmatch(r"Z[A-Z0-9]+", value["hosted_zone_id"]):
            raise ToolError("Preflight hosted-zone ID is invalid")
        if value["stack_name"] != stack_name(validate_run_id(str(value["run_id"]))):
            raise ToolError("Preflight stack name is invalid")
        if path != self._preflight_path(str(value["run_id"])).resolve():
            raise ToolError("Preflight filename does not match its run ID")
        versions = value.get("versions")
        version_keys = {
            "python_version",
            "node_version",
            "cdk_cli_version",
            "cdk_library_version",
            "cdk_executable_sha256",
            "cdk_assets_version",
            "cdk_assets_executable_sha256",
            "openemr_version",
            "aurora_version",
        }
        if (
            not isinstance(versions, dict)
            or not version_keys.issubset(versions)
            or any(not isinstance(versions[key], str) or not versions[key] for key in version_keys)
        ):
            raise ToolError("Preflight version inventory is invalid")
        if isinstance(value["bootstrap_version"], bool) or not isinstance(value["bootstrap_version"], int):
            raise ToolError("Preflight bootstrap version is invalid")
        if (
            isinstance(value["resource_count"], bool)
            or not isinstance(value["resource_count"], int)
            or value["resource_count"] < 1
            or not isinstance(value["resource_types"], dict)
        ):
            raise ToolError("Preflight resource inventory is invalid")
        preflight_phases = value["preflight_phases"]
        if not isinstance(preflight_phases, list) or not preflight_phases:
            raise ToolError("Preflight phase timings are invalid")
        for phase in preflight_phases:
            if (
                not isinstance(phase, dict)
                or not isinstance(phase.get("name"), str)
                or isinstance(phase.get("duration_seconds"), bool)
                or not isinstance(phase.get("duration_seconds"), (int, float))
                or phase["duration_seconds"] < 0
                or not isinstance(phase.get("source"), str)
            ):
                raise ToolError("Preflight phase timing entry is invalid")
        return value

    @contextmanager
    def _lock(self) -> Iterator[None]:
        _ensure_owner_only_directory(self.local_root, parents=True)
        lock_path = self.local_root / "live-e2e.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ToolError("Another live E2E operation is already running") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _account_label(self, account_id: str) -> str:
        """Return a stable local HMAC label without exposing a raw account hash."""

        _ensure_owner_only_directory(self.local_root, parents=True)
        key_path = self.local_root / "account-label.key"
        if key_path.is_symlink():
            raise ToolError("Refusing symlinked live E2E account-label key")
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Another local process already created the owner-only key file.
            descriptor = None
        if descriptor is not None:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(os.urandom(32))
                handle.flush()
                os.fsync(handle.fileno())
        if not key_path.is_file() or key_path.stat().st_mode & 0o077:
            raise ToolError("Live E2E account-label key must be an owner-only regular file")
        key = key_path.read_bytes()
        if len(key) != 32:
            raise ToolError("Live E2E account-label key has an invalid length")
        digest = hmac.new(key, account_id.encode("ascii"), hashlib.sha256).hexdigest()[:12]
        return f"sha256:{digest}"

    def _preflight_path(self, run_id: str) -> Path:
        return self.local_root / "preflight" / f"{run_id}.json"

    def _run_dir(self, run_id: str) -> Path:
        return self.local_root / "runs" / run_id

    def _state_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "state.json"

    def _write_state(self, run_id: str, value: dict[str, Any]) -> None:
        path = self._state_path(run_id)
        atomic_write_json(path, value)
        os.chmod(path, 0o600)

    def _write_raw_result(self, run_id: str, value: dict[str, Any]) -> None:
        path = self._run_dir(run_id) / "result.json"
        atomic_write_json(path, value)
        os.chmod(path, 0o600)

    def _update_state(self, run_id: str, updates: dict[str, Any]) -> None:
        path = self._state_path(run_id)
        value: dict[str, Any] = {}
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
        value.update(updates)
        self._write_state(run_id, value)

    def _recorded_rds_cluster_identifiers(
        self,
        run_id: str,
    ) -> tuple[str, ...]:
        path = self._state_path(run_id)
        if not path.exists():
            return ()
        state = json.loads(path.read_text(encoding="utf-8"))
        raw = state.get("rds_cluster_identifiers", [])
        if not isinstance(raw, list) or any(
            not isinstance(item, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,62}", item) for item in raw
        ):
            raise ToolError("Recorded RDS cleanup inventory is malformed")
        identifiers = tuple(sorted(set(raw)))
        # Missing inventory after a stack was observed is unsafe. An explicit empty
        # list is valid for failed creates / rollbacks that never provisioned RDS.
        if state.get("stack_id_hash") and "rds_cluster_identifiers" not in state:
            raise ToolError("Owned stack was observed without a durable RDS cleanup inventory")
        return identifiers


def deployment_contexts(
    *,
    run_id: str,
    account_id: str,
    region: str,
    availability_zones: Sequence[str],
    route53_domain: str,
    hosted_zone_id: str,
    allowed_ipv4_cidr: str,
    profile: str,
    emulated: bool = False,
) -> dict[str, str]:
    """Build the exact context shared by synth and deploy."""

    if profile not in _PROFILES:
        raise ToolError(f"Unsupported live E2E profile: {profile}")
    if not re.fullmatch(r"Z[A-Z0-9]+", hosted_zone_id):
        raise ToolError("Live E2E hosted-zone ID has an invalid format")
    if not re.fullmatch(r"\d{12}", account_id):
        raise ToolError("Live E2E account ID has an invalid format")
    if not _REGION.fullmatch(region):
        raise ToolError("Live E2E Region has an invalid format")
    normalized_zones = tuple(sorted(set(availability_zones)))
    if len(normalized_zones) != 2 or any(
        not re.fullmatch(rf"{re.escape(region)}[a-z]", zone) for zone in normalized_zones
    ):
        raise ToolError("Live E2E requires exactly two standard Availability Zones in the selected Region")
    contexts = {
        "activate_openemr_apis": "false",
        "configure_ses": "false",
        "create_serverless_analytics_environment": "false",
        "disable_rds_deletion_protection_on_destroy": "true",
        "enable_bedrock_integration": "false",
        "enable_data_api": "false",
        "enable_ecs_exec": "false",
        "enable_global_accelerator": "false",
        "enable_long_term_cloudtrail_monitoring": "false",
        "enable_monitoring_alarms": "false",
        "enable_patient_portal": "false",
        "enable_stack_termination_protection": "false",
        "live_e2e_run_id": validate_run_id(run_id),
        "openemr_import_target": "false",
        "openemr_resource_suffix": f"e2e{fingerprint(run_id, length=10)}",
        "openemr_service_fargate_maximum_capacity": "1",
        "openemr_service_fargate_minimum_capacity": "1",
        "rds_deletion_protection": "false",
        "route53_domain": route53_domain,
        "route53_hosted_zone_id": hosted_zone_id,
        "security_group_ip_range_ipv4": allowed_ipv4_cidr,
        "live_e2e_availability_zones": json.dumps(
            normalized_zones,
            separators=(",", ":"),
        ),
    }
    if emulated:
        # Floci lacks several AWS managed IAM policies referenced by the stack.
        contexts["live_e2e_emulated"] = "true"
    contexts.update(_PROFILES[profile])
    return contexts


def validate_inputs(
    *,
    approved_account: str,
    region: str,
    route53_domain: str,
    allowed_ipv4_cidr: str,
    profile: str,
    allow_documentation_values: bool = False,
) -> None:
    """Validate destructive-scope inputs before any AWS request."""

    hash_account_id(approved_account)
    if not _REGION.fullmatch(region):
        raise ToolError("AWS region has an invalid format")
    domain = route53_domain.rstrip(".").lower()
    labels = domain.split(".")
    if len(labels) < 3 or any(not label or len(label) > 63 for label in labels):
        raise ToolError("Use a dedicated E2E hosted-zone subdomain with at least three labels")
    if not allow_documentation_values and any(label in {"example", "invalid", "localhost"} for label in labels):
        raise ToolError("Documentation-only DNS names cannot be used for live E2E")
    try:
        network = ipaddress.ip_network(allowed_ipv4_cidr, strict=True)
    except ValueError as exc:
        raise ToolError("Allowed IPv4 CIDR is invalid") from exc
    if network.version != 4 or network.prefixlen != 32:
        raise ToolError("Live E2E requires one explicit public IPv4 /32")
    address = network.network_address
    if not allow_documentation_values and (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
    ):
        raise ToolError("Live E2E allowed IPv4 address must be a unicast globally routable address")
    if profile not in _PROFILES:
        raise ToolError(f"Profile must be one of: {', '.join(sorted(_PROFILES))}")


def validate_run_id(run_id: str) -> str:
    """Validate and return a bounded run ID."""

    if not _RUN_ID.fullmatch(run_id):
        raise ToolError("Run ID must be 6-48 lowercase letters, digits, or hyphens")
    return run_id


def new_run_id() -> str:
    """Create a collision-resistant, non-sensitive local run ID."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    entropy = fingerprint({"time_ns": time.time_ns(), "pid": os.getpid()}, length=8)
    return f"e2e-{stamp}-{entropy}"


def stack_name(run_id: str) -> str:
    """Return the deterministic CloudFormation stack name."""

    validated = validate_run_id(run_id)
    return f"OpenemrE2E-{hashlib.sha256(validated.encode('utf-8')).hexdigest()[:12]}"


def profiles() -> tuple[str, ...]:
    """Return available maintained profiles."""

    return tuple(sorted(_PROFILES))


def _cleanup_status(residuals: Sequence[Any]) -> str:
    if not residuals:
        return "complete"
    if not unexpected_residuals(residuals):
        return "stack-deleted-with-expected-residuals"
    return "failed"


def _merge_residuals(*groups: Sequence[ResidualResource]) -> tuple[ResidualResource, ...]:
    unique = {(item.resource_type, item.identifier_hash, item.disposition): item for group in groups for item in group}
    return tuple(unique[key] for key in sorted(unique))


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError("Preflight timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ToolError("Preflight timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _ensure_owner_only_directory(
    path: Path,
    *,
    parents: bool = False,
    exist_ok: bool = True,
) -> None:
    ensure_owner_only_directory(
        path,
        parents=parents,
        exist_ok=exist_ok,
        label="live E2E",
    )


def _resolve_executable(command: str) -> str:
    resolved = shutil.which(command)
    if resolved:
        return str(Path(resolved).resolve())
    path = Path(command)
    if path.is_file() and os.access(path, os.X_OK):
        return str(path.resolve())
    raise ToolError(f"Executable is unavailable: {command}")


def _resolve_cdk_executable(root: Path, command: str) -> str:
    """Require the repository-pinned CDK CLI installed by ``npm ci``."""

    pinned = root / "node_modules" / ".bin" / "cdk"
    accepted = {
        "cdk",
        "node_modules/.bin/cdk",
        str(pinned),
        str(pinned.resolve()),
    }
    if command not in accepted:
        raise ToolError(
            "Live E2E accepts only the repository-pinned node_modules/.bin/cdk; "
            "run npm ci instead of selecting a global CDK."
        )
    if not pinned.is_file() or not os.access(pinned, os.X_OK):
        raise ToolError("Pinned local CDK is unavailable at node_modules/.bin/cdk; run npm ci.")
    return str(pinned.resolve())


def _file_sha256(path: Path) -> str:
    """Fingerprint one bounded regular executable without following symlinks."""

    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ToolError("Pinned local executable is not a safe regular file") from exc
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode):
            raise ToolError("Pinned local executable is not a regular file")
        if information.st_size > 100_000_000:
            raise ToolError("Pinned local executable exceeds the fingerprint limit")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return f"sha256:{digest.hexdigest()}"


def _directory_fingerprint(path: Path) -> str:
    if not path.is_dir() or path.is_symlink():
        raise ToolError("Synthesized cloud assembly is missing")
    digest = hashlib.sha256()
    files = sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    if not files:
        raise ToolError("Synthesized cloud assembly is empty")
    for candidate in files:
        if candidate.is_symlink():
            raise ToolError("Synthesized cloud assembly cannot contain symlinks")
        relative = candidate.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with candidate.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _assembly_versions(assembly_dir: Path, expected_stack_name: str) -> dict[str, str]:
    template = _assembly_template(assembly_dir, expected_stack_name)
    try:
        openemr_version = template["Outputs"]["OpenEMRVersion"]["Value"]
    except (KeyError, TypeError) as exc:
        raise ToolError("Cloud assembly is missing the OpenEMR version output") from exc
    aurora_versions = {
        resource.get("Properties", {}).get("EngineVersion")
        for resource in template.get("Resources", {}).values()
        if resource.get("Type") == "AWS::RDS::DBCluster"
    }
    if (
        not isinstance(openemr_version, str)
        or len(aurora_versions) != 1
        or not isinstance(next(iter(aurora_versions)), str)
    ):
        raise ToolError("Cloud assembly has an ambiguous deployment version inventory")
    return {
        "openemr_version": openemr_version,
        "aurora_version": str(next(iter(aurora_versions))),
    }


def _assembly_resource_inventory(
    assembly_dir: Path,
    expected_stack_name: str,
) -> dict[str, int]:
    template = _assembly_template(assembly_dir, expected_stack_name)
    resources = template.get("Resources")
    if not isinstance(resources, dict):
        raise ToolError("Cloud assembly template has no resource inventory")
    inventory: dict[str, int] = {}
    for resource in resources.values():
        if not isinstance(resource, dict) or not isinstance(resource.get("Type"), str):
            raise ToolError("Cloud assembly contains an invalid resource")
        resource_type = str(resource["Type"])
        inventory[resource_type] = inventory.get(resource_type, 0) + 1
    return dict(sorted(inventory.items()))


def _validate_e2e_template(
    template: dict[str, Any],
    *,
    run_id: str,
    hosted_zone_id: str,
    resource_suffix: str,
    profile: str,
) -> None:
    """Fail closed when a synthesized assembly violates live-E2E safety policy."""

    resources = template.get("Resources")
    outputs = template.get("Outputs")
    if not isinstance(resources, dict) or not isinstance(outputs, dict):
        raise ToolError("Synthesized E2E template is missing resources or outputs")
    ownership = outputs.get("LiveE2ERunId")
    if not isinstance(ownership, dict) or ownership.get("Value") != run_id:
        raise ToolError("Synthesized E2E template lacks its exact ownership output")

    by_type: dict[str, list[dict[str, Any]]] = {}
    for resource in resources.values():
        if not isinstance(resource, dict) or not isinstance(resource.get("Type"), str):
            raise ToolError("Synthesized E2E template contains an invalid resource")
        by_type.setdefault(str(resource["Type"]), []).append(resource)

    certificates = by_type.get("AWS::CertificateManager::Certificate", [])
    records = by_type.get("AWS::Route53::RecordSet", [])
    if len(certificates) != 1:
        raise ToolError("Live E2E requires exactly one stack-owned ACM certificate")
    if not any(
        isinstance(record.get("Properties"), dict)
        and record["Properties"].get("HostedZoneId") == hosted_zone_id
        and isinstance(record["Properties"].get("AliasTarget"), dict)
        for record in records
    ):
        raise ToolError("Live E2E DNS alias is not bound to the preflight-verified hosted zone")
    listeners = by_type.get("AWS::ElasticLoadBalancingV2::Listener", [])
    if not listeners or any('"null"' in json.dumps(listener).lower() for listener in listeners):
        raise ToolError("Live E2E HTTPS listener has no valid certificate binding")

    services = by_type.get("AWS::ECS::Service", [])
    if len(services) != 1 or services[0].get("Properties", {}).get("DesiredCount") != 1:
        raise ToolError("Live E2E must synthesize exactly one desired OpenEMR task")
    clusters = by_type.get("AWS::RDS::DBCluster", [])
    if len(clusters) != 1:
        raise ToolError("Live E2E requires exactly one Aurora cluster")
    cluster = clusters[0]
    if (
        cluster.get("Properties", {}).get("DeletionProtection") is not False
        or cluster.get("DeletionPolicy") != "Delete"
        or cluster.get("UpdateReplacePolicy") != "Delete"
    ):
        raise ToolError("Live E2E Aurora lifecycle is not disposable")
    if profile == "api-enabled" and cluster.get("Properties", {}).get("EnableHttpEndpoint") is not True:
        raise ToolError("API-enabled E2E profile did not enable the Aurora Data API")

    for log_group in by_type.get("AWS::Logs::LogGroup", []):
        if log_group.get("DeletionPolicy") != "Delete" or log_group.get("UpdateReplacePolicy") != "Delete":
            raise ToolError("Live E2E log groups must use delete lifecycle policies")
    parameters = by_type.get("AWS::SSM::Parameter", [])
    if not parameters or any(
        not isinstance(parameter.get("Properties", {}).get("Name"), str)
        or not parameter["Properties"]["Name"].endswith(f"_{resource_suffix}")
        for parameter in parameters
    ):
        raise ToolError("Live E2E SSM parameters are not isolated by the run resource suffix")

    forbidden_types = {
        "AWS::CloudTrail::Trail",
        "AWS::EMRServerless::Application",
        "AWS::GlobalAccelerator::Accelerator",
        "AWS::SageMaker::Domain",
        "AWS::SES::EmailIdentity",
        "AWS::SNS::Topic",
    }
    present_forbidden = sorted(forbidden_types & by_type.keys())
    if present_forbidden:
        raise ToolError("Live E2E profile contains forbidden optional resources: " + ", ".join(present_forbidden))


def _assembly_template(
    assembly_dir: Path,
    expected_stack_name: str,
) -> dict[str, Any]:
    manifest_path = assembly_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact = manifest["artifacts"][expected_stack_name]
        template_file = artifact["properties"]["templateFile"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ToolError("Cloud assembly does not contain the expected E2E stack") from exc
    if not isinstance(template_file, str):
        raise ToolError("Cloud assembly template path is invalid")
    template_path = (assembly_dir / template_file).resolve()
    try:
        template_path.relative_to(assembly_dir.resolve())
    except ValueError as exc:
        raise ToolError("Cloud assembly template escapes its output directory") from exc
    try:
        unsafe_template = (
            template_path.is_symlink() or not template_path.is_file() or template_path.stat().st_size > 20_000_000
        )
    except OSError as exc:
        raise ToolError("Cloud assembly template cannot be inspected") from exc
    if unsafe_template:
        raise ToolError("Cloud assembly template is unsafe")
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError("Cloud assembly template is not valid JSON") from exc
    if not isinstance(template, dict):
        raise ToolError("Cloud assembly template must be an object")
    return template


def _repository_slug(remote_url: str) -> str:
    normalized = remote_url.strip().removesuffix(".git").replace("\\", "/")
    if "://" in normalized:
        path = normalized.split("://", 1)[1].split("/", 1)
        normalized = path[1] if len(path) == 2 else ""
    elif ":" in normalized and "@" in normalized.split(":", 1)[0]:
        normalized = normalized.split(":", 1)[1]
    parts = [part for part in normalized.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ToolError("Origin remote is not a supported repository URL")
    slug = "/".join(parts[-2:])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
        raise ToolError("Origin repository slug is invalid")
    return slug


def _cdk_failure_message(run_dir: Path, operation: str, summary: str) -> str:
    """Include a short owner-only CDK log tail so CI failures are diagnosable."""

    log_path = run_dir / f"{operation}.log"
    relative = f".live-e2e/runs/{run_dir.name}/{operation}.log"
    if not log_path.is_file() or log_path.is_symlink():
        return f"{summary}; inspect {relative}"
    try:
        text = redact_text(log_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return f"{summary}; inspect {relative}"
    lines = [line for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-40:])
    if not tail:
        return f"{summary}; inspect {relative}"
    return f"{summary}; inspect {relative}\n{tail}"


def _docker_build_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_symlink() or path.stat().st_size > 1_000_000:
        raise ToolError("Docker timing record is unsafe")
    total = 0.0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError("Cannot read Docker timing record") from exc
    if len(lines) > 1_000:
        raise ToolError("Docker timing record has too many entries")
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ToolError("Docker timing record is invalid JSON") from exc
        duration = item.get("duration_seconds") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or item.get("schema_version") != 1
            or item.get("category") not in {"build", "publish", "other"}
            or isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration < 0
        ):
            raise ToolError("Docker timing record entry is invalid")
        if item["category"] == "build":
            total += float(duration)
    return round(total, 3)
