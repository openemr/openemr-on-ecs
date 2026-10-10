"""CLI local-state validation and post-launch commands with mocked AWS calls."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from tools._shared import ToolError, atomic_write_private_json, read_private_json, repository_root
from tools.openemr_import import cli
from tools.openemr_import.aws import ExecutionReceipt, StackContext
from tools.openemr_import.models import SCHEMA_VERSION, SiteInventory, SourceInspection
from tools.openemr_import.plan import TARGET_OPENEMR_VERSION, create_plan

MIGRATION = "import-0123456789abcdef"
AWS = "tools.openemr_import.aws"


def _context(**overrides: Any) -> StackContext:
    values: dict[str, Any] = {
        "account_id": "111122223333",
        "region": "us-east-1",
        "stack_name": "OpenemrEcsStack",
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


def _receipt() -> ExecutionReceipt:
    context = _context()
    return ExecutionReceipt(
        schema_version=4,
        migration_id=MIGRATION,
        account_id=context.account_id,
        region=context.region,
        stack_name=context.stack_name,
        stack_creation_time=context.stack_creation_time,
        stack_last_updated_time=None,
        cluster_name=context.cluster_name,
        service_name=context.service_name,
        service_url=context.service_url,
        openemr_version=context.openemr_version,
        original_desired_count=2,
        task_arn="arn:aws:ecs:us-east-1:111122223333:task/openemr/task",
        task_definition_arn=context.task_definition_arn,
        staging_bucket=context.staging_bucket,
        staging_kms_key_arn=context.staging_kms_key_arn,
        task_security_group_id=context.task_security_group_id,
        private_subnet_ids=context.private_subnet_ids,
        database_arn=context.database_arn,
        efs_arn=context.efs_arn,
        source_key=f"migrations/{MIGRATION}/source.tar",
        started_at="2026-08-01T00:05:00+00:00",
        recovery_point_arns=("arn:backup:db", "arn:backup:efs"),
        recovery_point_dates=("2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
        lock_etag='"lock-etag"',
        lock_version_id="lock-version",
    )


def _state(tmp_path: Path, value: Any) -> Path:
    path = tmp_path / "state" / "receipt.json"
    atomic_write_private_json(path, value, label="test state")
    return path


def _receipt_state(tmp_path: Path, **extra: Any) -> Path:
    return _state(tmp_path, asdict(_receipt()) | extra)


def _status(
    *,
    worker: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
    service: Any = None,
    autoscaling: Any = None,
) -> dict[str, Any]:
    return {
        "worker": worker if worker is not None else {"status": "succeeded"},
        "task": task
        if task is not None
        else {"last_status": "STOPPED", "container_exit_code": 0, "identity_verified": True},
        "service": service if service is not None else {"desired_count": 2, "running_count": 2, "pending_count": 0},
        "autoscaling": autoscaling if autoscaling is not None else {"active": True, "suspended": False},
    }


_STOPPED = {"desired_count": 0, "running_count": 0, "pending_count": 0}
_RUNNING = {"desired_count": 2, "running_count": 2, "pending_count": 0}
_SUSPENDED = {"active": False, "suspended": True}
_ACTIVE = {"active": True, "suspended": False}
_FAILED_TASK = {"last_status": "STOPPED", "container_exit_code": 1, "identity_verified": True}


class _Aws:
    """Records guarded AWS adapter calls made by CLI commands."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, status: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.statuses = status if isinstance(status, list) else [status]
        self.healthy = True
        self.wait_exit_codes = [0]
        self.context = _context()
        monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: self.events.append(("session", kwargs)) or "s")
        patches = {
            "resolve_stack_context": lambda **kwargs: self.context,
            "read_remote_status": self._read_status,
            "set_service_desired_count": lambda c, *, session, desired_count: self.events.append(
                ("desired", desired_count)
            ),
            "set_service_autoscaling_suspended": lambda c, *, session, suspended: self.events.append(
                ("suspended", suspended)
            ),
            "assert_application_healthy": self._health,
            "assert_import_resource_bindings": lambda c, *, session: self.events.append(("bindings",)),
            "start_cleanup_task": lambda c, *, session, migration_id, attempt: self._launch("cleanup", attempt),
            "start_recovery_task": lambda c, *, session, migration_id, attempt: self._launch("recovery", attempt),
            "wait_for_task": lambda c, *, session, task_arn, **kwargs: self.wait_exit_codes.pop(0),
            "cleanup_staging": lambda receipt, *, session: self.events.append(("staging-deleted",)) or 3,
            "release_import_lock": lambda c, *, session, lock: self.events.append(("released", lock.etag)),
        }
        for name, replacement in patches.items():
            monkeypatch.setattr(f"{AWS}.{name}", replacement)

    def _read_status(self, receipt: Any, *, session: Any) -> dict[str, Any]:
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return json.loads(json.dumps(status))

    def _health(self, context: Any) -> None:
        self.events.append(("health",))
        if not self.healthy:
            raise ToolError("login page unhealthy")

    def _launch(self, kind: str, attempt: int) -> str:
        self.events.append((kind, attempt))
        return f"arn:{kind}:{attempt}"


# --- JSON input and local state helpers -------------------------------------


def test_json_input_must_be_a_bounded_regular_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ToolError, match="not a regular JSON file"):
        cli._json_file(tmp_path)

    target = tmp_path / "data.json"
    link = tmp_path / "link.json"
    target.write_text("{}", encoding="utf-8")
    link.symlink_to(target)
    with pytest.raises(ToolError, match="not a regular JSON file"):
        cli._json_file(link)

    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError, match="Invalid UTF-8 JSON"):
        cli._json_file(target)

    target.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ToolError, match="must contain one object"):
        cli._json_file(target)

    monkeypatch.setattr(cli, "MAX_JSON_BYTES", 2)
    target.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(ToolError, match="exceeds 2 bytes"):
        cli._json_file(target)


def test_default_state_path_is_private_repository_directory() -> None:
    assert cli._state_path(MIGRATION) == repository_root() / ".openemr-import" / f"{MIGRATION}.json"


@pytest.mark.parametrize(
    "reader",
    (cli._local_cleanup_status, cli._local_abort_status, cli._local_reconcile_status, cli._launch_unknown_state),
)
def test_local_state_readers_reject_non_object_json(tmp_path: Path, reader: Any) -> None:
    path = _state(tmp_path, ["not", "an", "object"])

    with pytest.raises(ToolError, match="must contain one JSON object|not a valid uncertain-launch"):
        reader(path)


@pytest.mark.parametrize(
    "reader",
    (cli._local_cleanup_status, cli._local_abort_status, cli._local_recovery_status, cli._local_reconcile_status),
)
def test_local_state_readers_treat_missing_state_as_absent(tmp_path: Path, reader: Any) -> None:
    assert reader(tmp_path / "missing.json") is None


def test_cleanup_state_classification(tmp_path: Path) -> None:
    assert cli._local_cleanup_status(_state(tmp_path, {"migration_id": "../escape"})) is None
    assert cli._local_cleanup_status(_state(tmp_path, {"migration_id": MIGRATION, "status": "reserved"})) is None
    state = {"schema_version": 4, "migration_id": MIGRATION, "cleanup_status": "failed", "cleanup_attempt": 2}
    assert cli._local_cleanup_status(_state(tmp_path, state)) == ("failed", state)

    with pytest.raises(ToolError, match="cleanup state is malformed"):
        cli._local_cleanup_status(_state(tmp_path, state | {"cleanup_attempt": 0}))
    with pytest.raises(ToolError, match="cleanup tombstone is malformed"):
        cli._local_cleanup_status(
            _state(tmp_path, {"schema_version": 1, "migration_id": MIGRATION, "status": "cleanup-complete"})
        )


def _abort_tombstone(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "migration_id": MIGRATION,
        "status": "abort-complete",
        "target_baseline_verified": True,
        "service_restored": True,
        "staging_deleted": True,
        "lock_released": True,
        "s3_objects_deleted": 0,
    } | overrides


def test_abort_state_classification(tmp_path: Path) -> None:
    assert cli._local_abort_status(_state(tmp_path, {"migration_id": 5})) is None
    assert cli._local_abort_status(_state(tmp_path, {"schema_version": 4, "migration_id": MIGRATION})) is None
    restored = {"schema_version": 4, "migration_id": MIGRATION, "abort_status": "service-restored"}
    assert cli._local_abort_status(_state(tmp_path, restored)) == ("service-restored", restored)
    assert cli._local_abort_status(_state(tmp_path, _abort_tombstone()))[0] == "complete"  # type: ignore[index]

    with pytest.raises(ToolError, match="abort state is malformed"):
        cli._local_abort_status(_state(tmp_path, restored | {"abort_status": "cleanup-failed"}))
    with pytest.raises(ToolError, match="abort tombstone is malformed"):
        cli._local_abort_status(_state(tmp_path, _abort_tombstone(s3_objects_deleted=-1)))


def test_recovery_state_classification(tmp_path: Path) -> None:
    assert cli._local_recovery_status(_state(tmp_path, {"schema_version": 1})) is None
    assert cli._local_recovery_status(_state(tmp_path, {"schema_version": 4, "recovery_status": "x"})) is None
    state = {"schema_version": 4, "recovery_status": "in-progress", "recovery_attempt": 1}
    assert cli._local_recovery_status(_state(tmp_path, state)) == ("in-progress", state)

    with pytest.raises(ToolError, match="recovery state is malformed"):
        cli._local_recovery_status(_state(tmp_path, state | {"recovery_attempt": True}))


def _reconcile_tombstone(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "migration_id": MIGRATION,
        "status": "launch-not-observed-cleaned",
        "service_restored": True,
        "autoscaling_restored": True,
        "staging_deleted": True,
        "lock_released": True,
        "s3_objects_deleted": 1,
    } | overrides


def test_reconcile_tombstone_classification(tmp_path: Path) -> None:
    assert cli._local_reconcile_status(_state(tmp_path, {"schema_version": 1, "status": "reserved"})) is None
    assert cli._local_reconcile_status(_state(tmp_path, _reconcile_tombstone())) == _reconcile_tombstone()

    with pytest.raises(ToolError, match="reconciliation tombstone is malformed"):
        cli._local_reconcile_status(_state(tmp_path, _reconcile_tombstone(lock_released=False)))


def test_launch_unknown_state_rejects_unexpected_status(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="not a valid uncertain-launch record"):
        cli._launch_unknown_state(_state(tmp_path, {"schema_version": 1, "status": "succeeded"}))


def test_boto3_session_binds_profile_and_region(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("boto3.Session", lambda **kwargs: calls.append(kwargs) or "session")

    assert cli._boto3_session(profile="ops", region="us-west-2") == "session"
    assert calls == [{"profile_name": "ops", "region_name": "us-west-2"}]


def test_boto3_session_requires_installed_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)

    with pytest.raises(ToolError, match="requires the project requirements"):
        cli._boto3_session(profile=None, region="us-east-1")


# --- plan/source and receipt binding ----------------------------------------


def _inspection(**overrides: Any) -> SourceInspection:
    values: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_kind": "native-openemr-backup",
        "source_fingerprint": "f" * 32,
        "source_bytes": 10,
        "source_openemr_version": TARGET_OPENEMR_VERSION,
        "source_database_version": 541,
        "database_type": "mysql",
        "sql_compressed": True,
        "sql_bytes": 5,
        "sites_archive_bytes": 5,
        "expanded_site_bytes": 5,
        "archive_member_count": 3,
        "sites": (SiteInventory("default", True, True, 1, True, 0, 0, 0),),
        "ignored_application_file_count": 0,
        "nested_archive_count": 0,
        "custom_code_detected": False,
        "checksums": {"source": "sha256:" + "a" * 64},
    }
    values.update(overrides)
    return SourceInspection(**values)


def test_execution_token_must_bind_account_region_stack_and_plan(tmp_path: Path) -> None:
    plan = create_plan(_inspection())
    args = argparse.Namespace(
        allow_aws_execution=True,
        confirm_fresh_target=True,
        confirm_non_production_target=True,
        confirm_downtime=True,
        confirm_recovery_points=True,
        confirm_destructive_import=True,
        account_id="111122223333",
        region="us-east-1",
        stack_name="Stack",
        confirmation_token="IMPORT:111122223333:us-west-2:Stack:" + plan.configuration_fingerprint,
    )

    with pytest.raises(ToolError, match="Confirmation token mismatch.*us-east-1"):
        cli._assert_execution_confirmations(args, plan)


def test_plan_source_must_be_regular_file(tmp_path: Path) -> None:
    plan = create_plan(_inspection())

    with pytest.raises(ToolError, match="one regular native backup file"):
        cli._matching_plan_source(plan, tmp_path)


def test_plan_source_must_match_reinspection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "backup.tar"
    source.write_bytes(b"x")
    plan = create_plan(_inspection(), generated_at="2026-08-01T00:00:00Z")
    monkeypatch.setattr(
        cli,
        "inspect_source",
        lambda path, source_version: _inspection(source_fingerprint="0" * 32, checksums={"source": "sha256:b"}),
    )

    with pytest.raises(ToolError, match="no longer matches the approved plan: source fingerprint, component checksums"):
        cli._matching_plan_source(plan, source)


def test_plan_must_match_current_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "backup.tar"
    source.write_bytes(b"x")
    plan = replace(create_plan(_inspection(), generated_at="2026-08-01T00:00:00Z"), phases=("skip-recovery",))
    monkeypatch.setattr(cli, "inspect_source", lambda path, source_version: _inspection())

    with pytest.raises(ToolError, match="does not match current compatibility policy"):
        cli._matching_plan_source(plan, source)


def test_context_must_match_receipt() -> None:
    with pytest.raises(ToolError, match="no longer matches the execution receipt: cluster, private subnets"):
        cli._context_matches_receipt(
            _context(cluster_name="other", private_subnet_ids=("subnet-three",)),
            _receipt(),
        )


# --- authorization predicates -----------------------------------------------


def test_failed_worker_abort_authorization_requires_safe_rollback_or_pre_mutation_phase() -> None:
    def status(**worker: Any) -> dict[str, Any]:
        return _status(worker={"status": "failed", **worker}, task=_FAILED_TASK)

    assert cli._failed_worker_authorizes_abort(status(phase="download")) is True
    assert cli._failed_worker_authorizes_abort(status(rollback_status="succeeded")) is True
    assert cli._failed_worker_authorizes_abort(status(phase="database-import")) is False
    assert cli._failed_worker_authorizes_abort(status(phase="download", rollback_status="failed")) is False
    assert (
        cli._failed_worker_authorizes_abort(_status(worker={"status": "failed"}, task={"last_status": "RUNNING"}))
        is False
    )


def test_interrupted_mutation_recovery_authorization() -> None:
    def status(worker: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return _status(worker=worker, task=_FAILED_TASK, service=_STOPPED, autoscaling=_SUSPENDED) | kwargs

    assert cli._interrupted_mutation_authorizes_recovery(status({"status": "failed", "rollback_status": "failed"}))
    assert cli._interrupted_mutation_authorizes_recovery(status({"status": "running", "phase": "site-import"}))
    assert not cli._interrupted_mutation_authorizes_recovery(status({"status": "running", "phase": "download"}))
    assert not cli._interrupted_mutation_authorizes_recovery(
        status({"status": "running", "phase": "site-import"}, service=_RUNNING)
    )


# --- status -----------------------------------------------------------------


def test_status_reports_completed_reconciliation_without_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_boto3_session", lambda **_: pytest.fail("no AWS session"))
    path = _state(tmp_path, _reconcile_tombstone())

    assert cli._status_command(argparse.Namespace(state=path, profile=None)) == 0
    assert json.loads(capsys.readouterr().out)["already_complete"] is True


def test_status_reports_completed_abort_without_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_boto3_session", lambda **_: pytest.fail("no AWS session"))
    path = _state(tmp_path, _abort_tombstone())

    assert cli._status_command(argparse.Namespace(state=path, profile=None)) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["status"] == "abort-complete"
    assert emitted["already_complete"] is True


def test_status_merges_remote_and_local_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    aws = _Aws(monkeypatch, _status())
    path = _receipt_state(
        tmp_path,
        cleanup_status="failed",
        cleanup_attempt=1,
        abort_status="cleanup-failed",
        abort_cleanup_attempt=1,
        recovery_status="complete",
        recovery_attempt=1,
    )

    assert cli._status_command(argparse.Namespace(state=path, profile="ops")) == 0

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["local_cleanup_status"] == "failed"
    assert emitted["local_abort_status"] == "cleanup-failed"
    assert emitted["local_recovery_status"] == "complete"
    assert aws.events == [("session", {"profile": "ops", "region": "us-east-1"})]


# --- finalize ---------------------------------------------------------------


def _finalize_args(path: Path, token: str = f"FINALIZE:{MIGRATION}") -> argparse.Namespace:
    return argparse.Namespace(state=path, profile=None, allow_aws_execution=True, confirmation_token=token)


def test_finalize_requires_exact_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _status())

    with pytest.raises(ToolError, match=f"FINALIZE:{MIGRATION}"):
        cli._finalize_command(_finalize_args(_receipt_state(tmp_path), token="FINALIZE:other"))
    assert aws.events == []


def test_finalize_restores_stopped_service_then_resumes_autoscaling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    aws = _Aws(monkeypatch, [_status(service=_STOPPED, autoscaling=_SUSPENDED), _status()])

    assert cli._finalize_command(_finalize_args(_receipt_state(tmp_path))) == 0

    assert aws.events[1:] == [("desired", 2), ("health",), ("suspended", False)]
    assert json.loads(capsys.readouterr().out)["autoscaling"] == _ACTIVE


def test_finalize_stops_service_again_when_health_check_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _status(service=_STOPPED, autoscaling=_SUSPENDED))
    aws.healthy = False

    with pytest.raises(ToolError, match="login page unhealthy"):
        cli._finalize_command(_finalize_args(_receipt_state(tmp_path)))
    assert aws.events[1:] == [("desired", 2), ("health",), ("desired", 0)]


@pytest.mark.parametrize(
    "status",
    (
        _status(worker={"status": "failed"}, service=_STOPPED, autoscaling=_SUSPENDED),
        _status(service=_STOPPED, autoscaling=_ACTIVE),
        _status(service={"desired_count": 1, "running_count": 0, "pending_count": 1}, autoscaling=_SUSPENDED),
    ),
    ids=("worker-failed", "autoscaling-active", "service-in-transition"),
)
def test_finalize_refuses_unauthorized_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, Any],
) -> None:
    aws = _Aws(monkeypatch, status)

    with pytest.raises(ToolError, match="do not jointly authorize finalization"):
        cli._finalize_command(_finalize_args(_receipt_state(tmp_path)))
    assert [event for event in aws.events if event[0] != "session"] == []


# --- recover ----------------------------------------------------------------


def _recover_args(path: Path, *, confirmed: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        state=path,
        profile=None,
        allow_aws_execution=True,
        confirm_restore_local_baseline=confirmed,
        confirmation_token=f"RECOVER:{MIGRATION}",
    )


_INTERRUPTED = _status(
    worker={"status": "running", "phase": "database-import"},
    task=_FAILED_TASK,
    service=_STOPPED,
    autoscaling=_SUSPENDED,
)


def test_recover_requires_baseline_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _INTERRUPTED)

    with pytest.raises(ToolError, match="--confirm-restore-local-baseline"):
        cli._recover_command(_recover_args(_receipt_state(tmp_path), confirmed=False))
    assert aws.events == []


def test_recover_is_idempotent_once_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    aws = _Aws(monkeypatch, _INTERRUPTED)
    path = _receipt_state(tmp_path, recovery_status="complete", recovery_attempt=2)

    assert cli._recover_command(_recover_args(path)) == 0
    assert json.loads(capsys.readouterr().out)["already_complete"] is True
    assert aws.events == []


def test_recover_refuses_without_interrupted_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _status(service=_STOPPED, autoscaling=_SUSPENDED))

    with pytest.raises(ToolError, match="requires an identity-verified stopped import task"):
        cli._recover_command(_recover_args(_receipt_state(tmp_path)))
    assert ("recovery", 1) not in aws.events


def test_recover_reuses_in_progress_attempt_and_records_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aws = _Aws(monkeypatch, _INTERRUPTED)
    aws.wait_exit_codes = [1]
    path = _receipt_state(tmp_path, recovery_status="in-progress", recovery_attempt=2)

    with pytest.raises(ToolError, match="Local-baseline recovery task failed"):
        cli._recover_command(_recover_args(path))

    assert ("recovery", 2) in aws.events
    failed = read_private_json(path, label="test state")
    assert (failed["recovery_status"], failed["recovery_attempt"]) == ("failed", 2)


def test_recover_retries_failed_attempt_with_next_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _INTERRUPTED)
    path = _receipt_state(tmp_path, recovery_status="failed", recovery_attempt=2)

    assert cli._recover_command(_recover_args(path)) == 0

    assert ("recovery", 3) in aws.events
    assert read_private_json(path, label="test state")["recovery_status"] == "complete"


# --- abort ------------------------------------------------------------------


def _abort_args(path: Path, *, token: str = f"ABORT:{MIGRATION}") -> argparse.Namespace:
    return argparse.Namespace(
        state=path,
        profile=None,
        allow_aws_execution=True,
        confirm_target_baseline_verified=True,
        confirmation_token=token,
    )


_ROLLED_BACK = _status(
    worker={"status": "failed", "rollback_status": "succeeded"},
    task=_FAILED_TASK,
    service=_STOPPED,
    autoscaling=_SUSPENDED,
)


def test_completed_abort_is_idempotent_but_still_requires_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    aws = _Aws(monkeypatch, _ROLLED_BACK)
    path = _state(tmp_path, _abort_tombstone())

    with pytest.raises(ToolError, match=f"ABORT:{MIGRATION}"):
        cli._abort_command(_abort_args(path, token="ABORT:wrong"))
    assert cli._abort_command(_abort_args(path)) == 0
    assert json.loads(capsys.readouterr().out)["already_complete"] is True
    assert aws.events == []


def test_abort_refuses_without_failed_task_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _status(service=_STOPPED, autoscaling=_SUSPENDED))

    with pytest.raises(ToolError, match="Abort requires one identity-verified failed task"):
        cli._abort_command(_abort_args(_receipt_state(tmp_path)))
    assert not any(event[0] in {"desired", "cleanup", "released"} for event in aws.events)


def test_abort_stops_service_again_when_health_check_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _ROLLED_BACK)
    aws.healthy = False
    path = _receipt_state(tmp_path)

    with pytest.raises(ToolError, match="login page unhealthy"):
        cli._abort_command(_abort_args(path))

    assert aws.events[1:] == [("desired", 2), ("health",), ("desired", 0)]
    assert "abort_status" not in read_private_json(path, label="test state")


def test_abort_after_completed_recovery_and_failed_cleanup_records_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aws = _Aws(monkeypatch, _status(worker={"status": "running"}, service=_STOPPED, autoscaling=_SUSPENDED))
    aws.wait_exit_codes = [1]
    path = _receipt_state(tmp_path, recovery_status="complete", recovery_attempt=1)

    with pytest.raises(ToolError, match="Failed-import EFS artifact cleanup task failed"):
        cli._abort_command(_abort_args(path))

    assert aws.events[1:] == [("desired", 2), ("health",), ("suspended", False), ("cleanup", 1)]
    failed = read_private_json(path, label="test state")
    assert (failed["abort_status"], failed["abort_cleanup_attempt"]) == ("cleanup-failed", 1)


def test_abort_resume_requires_fully_running_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _status(service=_STOPPED, autoscaling=_ACTIVE))
    path = _receipt_state(tmp_path, abort_status="cleanup-failed", abort_cleanup_attempt=1)

    with pytest.raises(ToolError, match="restored service to be fully running"):
        cli._abort_command(_abort_args(path))
    assert ("health",) not in aws.events


@pytest.mark.parametrize(
    ("autoscaling", "message"),
    (
        ("corrupt", "Unable to verify autoscaling"),
        ({"active": False, "suspended": False}, "neither active nor fully suspended"),
    ),
    ids=("not-object", "mixed"),
)
def test_abort_resume_requires_verifiable_autoscaling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    autoscaling: Any,
    message: str,
) -> None:
    aws = _Aws(monkeypatch, _status(service=_RUNNING, autoscaling=autoscaling))
    path = _receipt_state(tmp_path, abort_status="cleanup-failed", abort_cleanup_attempt=1)

    with pytest.raises(ToolError, match=message):
        cli._abort_command(_abort_args(path))
    assert not any(event[0] == "cleanup" for event in aws.events)


def test_abort_resume_retries_cleanup_with_next_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aws = _Aws(monkeypatch, _status(service=_RUNNING, autoscaling=_SUSPENDED))
    path = _receipt_state(tmp_path, abort_status="cleanup-failed", abort_cleanup_attempt=1)

    assert cli._abort_command(_abort_args(path)) == 0

    assert aws.events[1:] == [
        ("health",),
        ("suspended", False),
        ("cleanup", 2),
        ("staging-deleted",),
        ("released", '"lock-etag"'),
    ]
    assert read_private_json(path, label="test state")["status"] == "abort-complete"


# --- cleanup ----------------------------------------------------------------


def _cleanup_args(path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        state=path,
        profile=None,
        allow_aws_execution=True,
        confirm_delete_rollback_copy=True,
        confirmation_token=f"CLEANUP:{MIGRATION}",
    )


@pytest.mark.parametrize(
    "overrides",
    ({"confirm_delete_rollback_copy": False}, {"allow_aws_execution": False}, {"confirmation_token": "CLEANUP:x"}),
    ids=("rollback-copy", "aws-execution", "token"),
)
def test_cleanup_requires_every_confirmation_before_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
) -> None:
    aws = _Aws(monkeypatch, _status())
    args = _cleanup_args(_receipt_state(tmp_path))
    for key, value in overrides.items():
        setattr(args, key, value)

    with pytest.raises(ToolError, match=f"--confirmation-token CLEANUP:{MIGRATION}"):
        cli._cleanup_command(args)
    assert aws.events == []


@pytest.mark.parametrize(
    ("status", "message"),
    (
        (_status(worker={"status": "failed"}), "only after a successful import"),
        (_status(service=_STOPPED), "restored service to be fully running"),
        (_status(autoscaling=_SUSPENDED), "autoscaling to be active"),
    ),
    ids=("not-successful", "service-stopped", "autoscaling-suspended"),
)
def test_cleanup_refuses_until_import_is_finalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, Any],
    message: str,
) -> None:
    aws = _Aws(monkeypatch, status)
    path = _receipt_state(tmp_path)

    with pytest.raises(ToolError, match=message):
        cli._cleanup_command(_cleanup_args(path))
    assert not any(event[0] in {"cleanup", "staging-deleted", "released"} for event in aws.events)
    assert "cleanup_status" not in read_private_json(path, label="test state")


# --- main error translation -------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (ValueError("bad value"), "error: bad value"),
        (
            ClientError({"Error": {"Code": "ThrottlingException"}}, "DescribeStacks"),
            "error: AWS API request failed (ThrottlingException)",
        ),
        (ClientError({}, "DescribeStacks"), "error: AWS API request failed (unknown)"),
        (BotoCoreError(), "error: AWS SDK operation failed"),
    ),
    ids=("value", "client", "client-unknown", "botocore"),
)
def test_main_translates_failures_into_redacted_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected: str,
) -> None:
    def fail(args: argparse.Namespace) -> int:
        raise error

    monkeypatch.setattr(cli, "_status_command", fail)

    assert cli.main(["status", "--state", str(tmp_path / "state.json")]) == 2
    assert capsys.readouterr().err.strip() == expected
