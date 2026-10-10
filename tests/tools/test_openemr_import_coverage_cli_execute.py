"""Guarded execute and uncertain-launch reconciliation with mocked AWS calls."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from tools._shared import ToolError, atomic_write_private_json, read_private_json
from tools.openemr_import import cli
from tools.openemr_import.aws import (
    AwsImportError,
    ExecutionReceipt,
    ImportLock,
    ImportLockOutcomeUnknown,
    RecoveryPoint,
    StackContext,
    read_receipt,
)
from tools.openemr_import.models import SCHEMA_VERSION, ImportPlan, SiteInventory, SourceInspection
from tools.openemr_import.plan import TARGET_OPENEMR_VERSION, create_plan

ACCOUNT = "111122223333"
REGION = "us-east-1"
STACK = "OpenemrEcsStack"
AWS = "tools.openemr_import.aws"
MIGRATION = "import-0123456789abcdef"


def _context(**overrides: Any) -> StackContext:
    values: dict[str, Any] = {
        "account_id": ACCOUNT,
        "region": REGION,
        "stack_name": STACK,
        "stack_creation_time": "2026-08-01T00:00:00+00:00",
        "stack_last_updated_time": None,
        "cluster_name": "openemr-cluster",
        "service_name": "openemr-service",
        "service_url": "https://openemr.example.test",
        "openemr_version": TARGET_OPENEMR_VERSION,
        "import_target_mode": "fresh-target-only",
        "task_definition_arn": "arn:aws:ecs:us-east-1:111122223333:task-definition/import:1",
        "staging_bucket": "private-staging-bucket",
        "staging_kms_key_arn": "arn:aws:kms:us-east-1:111122223333:key/example",
        "task_security_group_id": "sg-import",
        "private_subnet_ids": ("subnet-one", "subnet-two"),
        "database_arn": "arn:aws:rds:us-east-1:111122223333:cluster:openemr",
        "efs_arn": "arn:aws:elasticfilesystem:us-east-1:111122223333:file-system/fs-openemr",
    }
    values.update(overrides)
    return StackContext(**values)


def _plan(**overrides: Any) -> ImportPlan:
    inspection = SourceInspection(
        schema_version=SCHEMA_VERSION,
        source_kind="native-openemr-backup",
        source_fingerprint="f" * 32,
        source_bytes=10,
        source_openemr_version=TARGET_OPENEMR_VERSION,
        source_database_version=541,
        database_type="mysql",
        sql_compressed=True,
        sql_bytes=5,
        sites_archive_bytes=5,
        expanded_site_bytes=5,
        archive_member_count=3,
        sites=(SiteInventory("default", True, True, 1, True, 0, 0, 0),),
        ignored_application_file_count=0,
        nested_archive_count=0,
        custom_code_detected=False,
        checksums={"source": "sha256:" + "a" * 64},
    )
    plan = create_plan(inspection, generated_at="2026-08-01T00:00:00Z")
    data = plan.to_dict() | overrides
    return ImportPlan(**data)


class _Execution:
    """Fake AWS adapter plus a ready-to-run execute invocation."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: ImportPlan | None = None) -> None:
        self.plan = plan or _plan()
        self.plan_path = tmp_path / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan.to_dict()), encoding="utf-8")
        self.source = tmp_path / "backup.tar"
        self.source.write_bytes(b"native-backup-bytes")
        self.state_path = tmp_path / "state" / "receipt.json"
        self.context = _context()
        self.events: list[tuple[Any, ...]] = []
        self.failures: dict[str, list[BaseException | None]] = {}
        self.wait_exit_code = 0
        self.uploaded_key: str | None = None
        self.receipt_override: Any = "unset"
        self.healthy = True
        self.statuses: list[dict[str, Any]] = []
        self.lock = ImportLock(
            bucket=self.context.staging_bucket,
            key="locks/active.json",
            migration_id=self.plan.migration_id,
            etag='"lock-etag"',
            version_id="lock-version",
        )
        monkeypatch.setattr(cli, "_matching_plan_source", self._matching_plan_source)
        monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: "session")
        patches = {
            "resolve_stack_context": lambda **kwargs: self._call("resolve") or self.context,
            "assert_new_import_target": lambda context: self._call("fresh"),
            "assert_import_resource_bindings": lambda context, *, session: self._call("bindings"),
            "assert_service_stable": lambda context, *, session: self._call("stable") or 2,
            "assert_service_autoscaling_active": lambda context, *, session: self._call("autoscaling-active"),
            "recent_recovery_points": lambda context, *, session, maximum_age_hours: (
                self._call("recovery-points")
                or (
                    RecoveryPoint("db", "arn:backup:db", "2026-08-01T00:00:00+00:00"),
                    RecoveryPoint("efs", "arn:backup:efs", "2026-08-01T00:00:00+00:00"),
                )
            ),
            "acquire_import_lock": lambda context, *, session, migration_id: self._call("lock") or self.lock,
            "upload_source": self._upload,
            "set_service_autoscaling_suspended": lambda context, *, session, suspended: self._call(
                "suspended", suspended
            ),
            "set_service_desired_count": lambda context, *, session, desired_count: self._call(
                "desired", desired_count
            ),
            "start_import_task": self._start,
            "wait_for_task": lambda context, *, session, task_arn, maximum_attempts: (
                self._call("wait") or self.wait_exit_code
            ),
            "read_remote_status": self._read_status,
            "assert_application_healthy": self._health,
            "cleanup_staging_scope": lambda **kwargs: self._call("staging-deleted", kwargs["migration_id"]) or 0,
            "release_import_lock": lambda context, *, session, lock: self._call("released", lock.etag),
        }
        for name, replacement in patches.items():
            monkeypatch.setattr(f"{AWS}.{name}", replacement)

    def fail(self, operation: str, *errors: BaseException | None) -> None:
        self.failures[operation] = list(errors)

    def _call(self, operation: str, *details: Any) -> None:
        self.events.append((operation, *details))
        pending = self.failures.get(operation)
        if pending:
            error = pending.pop(0)
            if error is not None:
                raise error

    def _matching_plan_source(self, plan: ImportPlan, source: Path) -> None:
        self.events.append(("verified-snapshot", source.read_bytes()))
        self._call("verify")

    def _upload(self, context: Any, *, session: Any, migration_id: str, source: Path) -> str:
        self._call("upload")
        return self.uploaded_key or f"migrations/{migration_id}/source.tar"

    def _start(self, context: Any, *, session: Any, plan: ImportPlan, **kwargs: Any) -> Any:
        self._call("start", kwargs["migration_id"])
        if self.receipt_override != "unset":
            return self.receipt_override
        return ExecutionReceipt(
            schema_version=4,
            migration_id=plan.migration_id,
            account_id=context.account_id,
            region=context.region,
            stack_name=context.stack_name,
            stack_creation_time=context.stack_creation_time,
            stack_last_updated_time=None,
            cluster_name=context.cluster_name,
            service_name=context.service_name,
            service_url=context.service_url,
            openemr_version=context.openemr_version,
            original_desired_count=kwargs["original_desired_count"],
            task_arn="arn:aws:ecs:us-east-1:111122223333:task/openemr/import",
            task_definition_arn=context.task_definition_arn,
            staging_bucket=context.staging_bucket,
            staging_kms_key_arn=context.staging_kms_key_arn,
            task_security_group_id=context.task_security_group_id,
            private_subnet_ids=context.private_subnet_ids,
            database_arn=context.database_arn,
            efs_arn=context.efs_arn,
            source_key=kwargs["source_key"],
            started_at="2026-08-01T00:05:00+00:00",
            recovery_point_arns=("arn:backup:db", "arn:backup:efs"),
            recovery_point_dates=("2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
            lock_etag=kwargs["lock"].etag,
            lock_version_id=kwargs["lock"].version_id,
        )

    def _read_status(self, receipt: Any, *, session: Any) -> dict[str, Any]:
        self.events.append(("status",))
        return self.statuses.pop(0) if self.statuses else _ready_status()

    def _health(self, context: Any) -> None:
        self._call("health")
        if not self.healthy:
            raise AwsImportError("login page unhealthy")

    def args(self, **overrides: Any) -> argparse.Namespace:
        values: dict[str, Any] = {
            "plan": self.plan_path,
            "source": self.source,
            "account_id": ACCOUNT,
            "region": REGION,
            "stack_name": STACK,
            "maximum_recovery_age_hours": 24,
            "maximum_wait_attempts": 3,
            "allow_aws_execution": True,
            "confirm_fresh_target": True,
            "confirm_non_production_target": True,
            "confirm_downtime": True,
            "confirm_recovery_points": True,
            "confirm_destructive_import": True,
            "confirmation_token": f"IMPORT:{ACCOUNT}:{REGION}:{STACK}:{self.plan.configuration_fingerprint}",
            "state": self.state_path,
            "profile": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run(self, **overrides: Any) -> int:
        return cli._execute_command(self.args(**overrides))

    def state(self) -> dict[str, Any]:
        return read_private_json(self.state_path, label="test state")

    def snapshots(self) -> list[Path]:
        return sorted(self.state_path.parent.glob("*.snapshot"))

    def operations(self) -> list[tuple[Any, ...]]:
        ignored = {"verify", "verified-snapshot", "resolve", "fresh", "bindings", "stable", "autoscaling-active"}
        return [event for event in self.events if event[0] not in ignored and event[0] != "recovery-points"]


def _ready_status(**worker: Any) -> dict[str, Any]:
    return {
        "worker": {"status": "succeeded", **worker},
        "task": {"last_status": "STOPPED", "container_exit_code": 0, "identity_verified": True},
        "service": {"desired_count": 0, "running_count": 0, "pending_count": 0},
        "autoscaling": {"active": False, "suspended": True},
    }


# --- preconditions -----------------------------------------------------------


@pytest.mark.parametrize(
    ("plan_overrides", "arg_overrides", "message"),
    (
        ({"execution_allowed": False, "blockers": ("multisite",)}, {}, "blocks execution: multisite"),
        ({"target_mode": "in-place"}, {}, "Only fresh-target import plans"),
        ({}, {"account_id": "12345"}, "exactly 12 digits"),
        ({}, {"maximum_recovery_age_hours": 0}, "must be positive"),
        ({}, {"maximum_wait_attempts": 0}, "must be positive"),
    ),
    ids=("blocked-plan", "target-mode", "account-id", "recovery-age", "wait-attempts"),
)
def test_execute_rejects_unsafe_inputs_before_any_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan_overrides: dict[str, Any],
    arg_overrides: dict[str, Any],
    message: str,
) -> None:
    execution = _Execution(tmp_path, monkeypatch, _plan(**plan_overrides))

    with pytest.raises(ToolError, match=message):
        execution.run(**arg_overrides)
    assert execution.events == []
    assert not execution.state_path.parent.exists()


def test_execute_refuses_to_reuse_existing_state_and_discards_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    atomic_write_private_json(execution.state_path, {"status": "prior-run"}, label="test state")

    with pytest.raises(ToolError, match="Execution state already exists"):
        execution.run()

    assert execution.state() == {"status": "prior-run"}
    assert execution.snapshots() == []
    assert execution.operations() == []


def test_execute_discards_snapshot_when_plan_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.fail("verify", ToolError("Source no longer matches the approved plan"))

    with pytest.raises(ToolError, match="no longer matches"):
        execution.run()

    assert ("verified-snapshot", b"native-backup-bytes") in execution.events
    assert execution.snapshots() == []
    assert not execution.state_path.exists()


@pytest.mark.parametrize(
    ("context", "message"),
    (
        (_context(openemr_version="7.0.0"), "Deployed OpenEMR version does not match"),
        (_context(import_target_mode="in-place"), "not marked as a fresh import target"),
    ),
    ids=("version", "mode"),
)
def test_execute_rejects_mismatched_stack_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context: StackContext,
    message: str,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.context = context

    with pytest.raises(ToolError, match=message):
        execution.run()

    assert execution.operations() == []
    assert execution.state()["status"] == "prelaunch-failed-cleaned"
    assert execution.snapshots() == []


# --- success and post-launch outcomes ----------------------------------------


def test_execute_success_restores_service_after_healthy_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    migration_id = execution.plan.migration_id

    assert execution.run() == 0

    assert execution.operations() == [
        ("lock",),
        ("upload",),
        ("suspended", True),
        ("desired", 0),
        ("start", migration_id),
        ("wait",),
        ("status",),
        ("desired", 2),
        ("health",),
        ("suspended", False),
        ("status",),
    ]
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["result"] == "import-succeeded-service-restored"
    assert emitted["state_file"] == str(execution.state_path)
    assert read_receipt(execution.state_path).lock_etag == '"lock-etag"'
    assert execution.snapshots() == []


def test_execute_failed_import_keeps_service_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.wait_exit_code = 1

    assert execution.run() == 3

    assert ("desired", 2) not in execution.operations()
    assert ("suspended", False) not in execution.operations()
    assert json.loads(capsys.readouterr().out)["result"] == "import-failed-service-remains-stopped"


def test_execute_stops_service_again_when_post_import_health_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.healthy = False

    with pytest.raises(AwsImportError, match="login page unhealthy"):
        execution.run()

    desired = [event[1] for event in execution.operations() if event[0] == "desired"]
    assert desired == [0, 2, 0]
    assert ("suspended", False) not in execution.operations()


def test_execute_retries_launch_once_after_sdk_transport_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.fail("start", BotoCoreError(), None)

    assert execution.run() == 0

    starts = [event for event in execution.events if event[0] == "start"]
    assert starts == [("start", execution.plan.migration_id)] * 2


def test_execute_refuses_uninitialized_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.receipt_override = None

    with pytest.raises(ToolError, match="execution state was not initialized"):
        execution.run()
    assert ("wait",) not in execution.events


# --- prelaunch failures and compensation -------------------------------------


def test_lock_client_error_is_compensated_without_releasing_foreign_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.fail("lock", ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject"))

    with pytest.raises(ClientError):
        execution.run()

    assert execution.operations() == [("lock",)]
    state = execution.state()
    assert state["status"] == "prelaunch-failed-cleaned"
    assert state["failure_type"] == "ClientError"


@pytest.mark.parametrize(
    "error",
    (BotoCoreError(), ImportLockOutcomeUnknown("no identity")),
    ids=("botocore", "outcome-unknown"),
)
def test_unknown_lock_outcome_preserves_state_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.fail("lock", error)

    with pytest.raises(ToolError, match="import-lock acquisition outcome is unknown"):
        execution.run()

    assert execution.operations() == [("lock",)]
    state = execution.state()
    assert state["status"] == "lock-outcome-unknown"
    assert state["original_desired_count"] == 2
    assert execution.snapshots() == []


def test_unexpected_upload_key_releases_lock_and_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.uploaded_key = "migrations/other/source.tar"

    with pytest.raises(ToolError, match="unexpected object key"):
        execution.run()

    assert execution.operations() == [
        ("lock",),
        ("upload",),
        ("staging-deleted", execution.plan.migration_id),
        ("released", '"lock-etag"'),
    ]
    state = execution.state()
    assert state["status"] == "prelaunch-failed-cleaned"
    assert state["service_remains_stopped"] is False
    assert execution.snapshots() == []


def test_quiesce_failure_restores_service_autoscaling_staging_and_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.fail("desired", AwsImportError("service did not stabilize"))

    with pytest.raises(AwsImportError, match="did not stabilize"):
        execution.run()

    assert execution.operations()[2:] == [
        ("suspended", True),
        ("desired", 0),
        ("desired", 2),
        ("suspended", False),
        ("staging-deleted", execution.plan.migration_id),
        ("released", '"lock-etag"'),
    ]
    state = execution.state()
    assert state["status"] == "prelaunch-failed-cleaned"
    assert state["autoscaling_remains_suspended"] is False


def test_failed_compensation_preserves_evidence_for_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.fail("desired", AwsImportError("service did not stabilize"))
    execution.fail("staging-deleted", ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObjects"))

    with pytest.raises(ToolError, match="Prelaunch compensation did not complete"):
        execution.run()

    assert ("released", '"lock-etag"') not in execution.operations()
    state = execution.state()
    assert state["status"] == "prelaunch-cleanup-required"
    assert state["failure_type"] == "AwsImportError"
    assert state["compensation_failure_type"] == "ClientError"
    assert state["service_state_requires_reconciliation"] is True
    assert state["lock_etag"] == '"lock-etag"'


def test_sdk_error_before_lock_is_compensated_and_reraised(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.fail("stable", BotoCoreError())

    with pytest.raises(BotoCoreError):
        execution.run()

    assert execution.operations() == []
    assert execution.state()["status"] == "prelaunch-failed-cleaned"


@pytest.mark.parametrize(
    "error",
    (KeyboardInterrupt(), AwsImportError("malformed launch response")),
    ids=("interrupt", "malformed-response"),
)
def test_uncertain_launch_keeps_service_stopped_and_staging_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    execution = _Execution(tmp_path, monkeypatch)
    execution.fail("start", error)

    with pytest.raises(ToolError, match="launch outcome is unknown"):
        execution.run()

    assert execution.operations()[-1] == ("start", execution.plan.migration_id)
    assert not any(event[0] in {"staging-deleted", "released"} for event in execution.operations())
    state = execution.state()
    assert state["status"] == "launch-outcome-unknown"
    assert state["service_remains_stopped"] is True
    assert state["autoscaling_remains_suspended"] is True


# --- reconcile-launch --------------------------------------------------------


def _unknown_state(**overrides: Any) -> dict[str, Any]:
    context = _context()
    state: dict[str, Any] = {
        "schema_version": 1,
        "migration_id": MIGRATION,
        "status": "launch-outcome-unknown",
        "account_id": ACCOUNT,
        "region": REGION,
        "stack_name": STACK,
        "stack_creation_time": context.stack_creation_time,
        "stack_last_updated_time": None,
        "cluster_name": context.cluster_name,
        "service_name": context.service_name,
        "service_url": context.service_url,
        "openemr_version": context.openemr_version,
        "original_desired_count": 2,
        "task_definition_arn": context.task_definition_arn,
        "staging_bucket": context.staging_bucket,
        "staging_kms_key_arn": context.staging_kms_key_arn,
        "task_security_group_id": context.task_security_group_id,
        "private_subnet_ids": list(context.private_subnet_ids),
        "database_arn": context.database_arn,
        "efs_arn": context.efs_arn,
        "source_key": f"migrations/{MIGRATION}/source.tar",
        "recovery_point_arns": ["arn:backup:db", "arn:backup:efs"],
        "recovery_point_dates": ["d1", "d2"],
        "lock_etag": '"lock-etag"',
        "lock_version_id": "lock-version",
        "outcome_unknown_at": (datetime.now(UTC) - timedelta(minutes=20)).isoformat(),
    }
    state.update(overrides)
    return state


class _Reconcile:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]) -> None:
        self.state_path = tmp_path / "state" / "unknown.json"
        atomic_write_private_json(self.state_path, state, label="test state")
        self.context = _context()
        self.task_scans: list[tuple[dict[str, Any], ...]] = [()]
        self.events: list[tuple[Any, ...]] = []
        self.healthy = True
        self.stable_count = 2
        monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: "session")
        patches = {
            "resolve_stack_context": lambda **kwargs: self.context,
            "find_import_tasks": self._find,
            "read_remote_status": lambda receipt, *, session: {"worker": {"status": "running"}},
            "set_service_desired_count": lambda c, *, session, desired_count: self.events.append(
                ("desired", desired_count)
            ),
            "set_service_autoscaling_suspended": lambda c, *, session, suspended: self.events.append(
                ("suspended", suspended)
            ),
            "assert_application_healthy": self._health,
            "assert_service_stable": lambda c, *, session: self.events.append(("stable",)) or self.stable_count,
            "assert_service_autoscaling_active": lambda c, *, session: self.events.append(("autoscaling-active",)),
            "cleanup_staging_scope": lambda **kwargs: (
                self.events.append(("staging-deleted", kwargs["migration_id"])) or 2
            ),
            "release_import_lock": lambda c, *, session, lock: self.events.append(
                ("released", lock.etag, lock.version_id)
            ),
        }
        for name, replacement in patches.items():
            monkeypatch.setattr(f"{AWS}.{name}", replacement)

    def _find(self, context: Any, *, session: Any, migration_id: str) -> tuple[dict[str, Any], ...]:
        self.events.append(("find",))
        return self.task_scans.pop(0) if len(self.task_scans) > 1 else self.task_scans[0]

    def _health(self, context: Any) -> None:
        self.events.append(("health",))
        if not self.healthy:
            raise AwsImportError("login page unhealthy")

    def run(self, **overrides: Any) -> int:
        values: dict[str, Any] = {
            "state": self.state_path,
            "profile": None,
            "allow_aws_execution": True,
            "confirm_no_task_launched": True,
            "minimum_unknown_age_minutes": 15,
            "maximum_unknown_age_minutes": 45,
            "confirmation_token": f"RECONCILE:{MIGRATION}",
        }
        values.update(overrides)
        return cli._reconcile_launch_command(argparse.Namespace(**values))

    def state(self) -> dict[str, Any]:
        return read_private_json(self.state_path, label="test state")


@pytest.mark.parametrize(
    ("state", "args", "message"),
    (
        ({}, {"confirmation_token": "RECONCILE:other"}, f"RECONCILE:{MIGRATION}"),
        ({}, {"minimum_unknown_age_minutes": 0}, "positive minimum"),
        ({}, {"maximum_unknown_age_minutes": 15}, "positive minimum"),
        ({}, {"maximum_unknown_age_minutes": 56}, "at most 55 minutes"),
        ({"outcome_unknown_at": "not-a-time"}, {}, "timestamp is malformed"),
        ({"outcome_unknown_at": "2026-08-01T00:00:00"}, {}, "lacks a timezone"),
    ),
    ids=("token", "minimum", "window", "maximum", "malformed-time", "naive-time"),
)
def test_reconcile_rejects_unsafe_arguments_before_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
    args: dict[str, Any],
    message: str,
) -> None:
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state(**state))

    with pytest.raises(ToolError, match=message):
        reconcile.run(**args)
    assert reconcile.events == []


@pytest.mark.parametrize(
    ("context", "message"),
    (
        (_context(staging_bucket="other-bucket"), "no longer matches staging_bucket"),
        (_context(private_subnet_ids=("subnet-three",)), "no longer matches private_subnet_ids"),
    ),
    ids=("bucket", "subnets"),
)
def test_reconcile_requires_state_to_match_current_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context: StackContext,
    message: str,
) -> None:
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state())
    reconcile.context = context

    with pytest.raises(ToolError, match=message):
        reconcile.run()
    assert reconcile.events == []


@pytest.mark.parametrize(
    ("state", "task", "message"),
    (
        ({"source_key": None}, {"taskArn": "arn:task"}, "no staged source key"),
        ({}, {"taskArn": ""}, "lacks an ARN"),
        ({"lock_version_id": 7}, {"taskArn": "arn:task"}, "lock version is malformed"),
    ),
    ids=("source-key", "task-arn", "lock-version"),
)
def test_reconcile_found_task_requires_complete_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
    task: dict[str, Any],
    message: str,
) -> None:
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state(**state))
    reconcile.task_scans = [(task,)]

    with pytest.raises(ToolError, match=message):
        reconcile.run()
    assert reconcile.state()["status"] == "launch-outcome-unknown"


@pytest.mark.parametrize(
    ("minutes", "message"),
    ((5, "wait until the uncertain launch is at least 15 minutes old"), (50, "visibility window has expired")),
    ids=("too-young", "too-old"),
)
def test_reconcile_without_task_only_acts_inside_visibility_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minutes: int,
    message: str,
) -> None:
    unknown_at = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state(outcome_unknown_at=unknown_at))

    with pytest.raises(ToolError, match=message):
        reconcile.run()
    assert reconcile.events == [("find",)]


def test_reconcile_without_task_requires_operator_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state())

    with pytest.raises(ToolError, match="--confirm-no-task-launched"):
        reconcile.run(confirm_no_task_launched=False)
    assert reconcile.events == [("find",)]


def test_reconcile_aborts_when_task_appears_during_rescan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state())
    reconcile.task_scans = [(), ({"taskArn": "arn:late"},)]

    with pytest.raises(ToolError, match="appeared during reconciliation"):
        reconcile.run()
    assert reconcile.events == [("find",), ("find",)]


def test_reconcile_restores_stopped_service_then_cleans_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state())

    assert reconcile.run() == 0

    assert reconcile.events == [
        ("find",),
        ("find",),
        ("desired", 2),
        ("health",),
        ("suspended", False),
        ("staging-deleted", MIGRATION),
        ("released", '"lock-etag"', "lock-version"),
    ]
    tombstone = reconcile.state()
    assert tombstone["status"] == "launch-not-observed-cleaned"
    assert tombstone["service_restored"] is True
    assert tombstone["s3_objects_deleted"] == 2
    assert json.loads(capsys.readouterr().out) == tombstone


def test_reconcile_stops_service_again_when_health_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state())
    reconcile.healthy = False

    with pytest.raises(AwsImportError, match="login page unhealthy"):
        reconcile.run()

    assert reconcile.events[2:] == [("desired", 2), ("health",), ("desired", 0)]
    assert reconcile.state()["status"] == "launch-outcome-unknown"


def test_reconcile_prelaunch_cleanup_verifies_running_service_instead_of_restoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconcile = _Reconcile(
        tmp_path,
        monkeypatch,
        _unknown_state(status="prelaunch-cleanup-required", source_key=None, lock_version_id=None),
    )

    assert reconcile.run() == 0

    assert reconcile.events[2:] == [
        ("stable",),
        ("autoscaling-active",),
        ("staging-deleted", MIGRATION),
        ("released", '"lock-etag"', None),
    ]
    assert reconcile.state()["service_restored"] is False


def test_reconcile_prelaunch_cleanup_refuses_changed_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reconcile = _Reconcile(tmp_path, monkeypatch, _unknown_state(status="lock-acquired"))
    reconcile.stable_count = 1

    with pytest.raises(ToolError, match="no longer matches the running service"):
        reconcile.run()
    assert not any(event[0] in {"staging-deleted", "released"} for event in reconcile.events)
