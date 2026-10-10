"""Command-line interface for offline planning and guarded AWS execution."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from tools._shared import (
    ToolError,
    atomic_write_json,
    atomic_write_private_json,
    ensure_owner_only_directory,
    read_private_json,
    repository_root,
    reserve_private_json,
    snapshot_regular_file,
    utc_now,
)

from .aws import AwsImportError, ImportLockOutcomeUnknown
from .inspect import inspect_source
from .models import ArchiveLimits, ImportPlan
from .plan import (
    TARGET_OPENEMR_VERSION,
    create_plan,
    inspection_from_dict,
    plan_from_dict,
)

MAX_JSON_BYTES = 5 * 1024 * 1024


def _json_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ToolError(f"Input is not a regular JSON file: {path}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ToolError(f"JSON input exceeds {MAX_JSON_BYTES} bytes")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ToolError(f"Invalid UTF-8 JSON input: {path}") from exc
    if not isinstance(value, dict):
        raise ToolError("JSON input must contain one object")
    return value


def _emit(value: dict[str, Any], output: Path | None) -> None:
    if output:
        atomic_write_json(output, value)
    else:
        print(json.dumps(value, indent=2, sort_keys=True))


def _state_path(migration_id: str) -> Path:
    return repository_root() / ".openemr-import" / f"{migration_id}.json"


def _write_state(path: Path, value: dict[str, Any]) -> None:
    atomic_write_private_json(path, value, label="OpenEMR import state")


def _local_cleanup_status(path: Path) -> tuple[str, dict[str, Any]] | None:
    """Return validated resumable or completed cleanup state."""

    if not os.path.lexists(path):
        return None
    data = read_private_json(path, max_bytes=MAX_JSON_BYTES, label="OpenEMR import state")
    if not isinstance(data, dict):
        raise ToolError("OpenEMR import state must contain one JSON object")
    migration_id = data.get("migration_id")
    if not isinstance(migration_id, str) or not re.fullmatch(r"import-[a-f0-9]{16}", migration_id):
        return None
    if data.get("schema_version") == 4 and data.get("cleanup_status") in {
        "in-progress",
        "failed",
    }:
        attempt = data.get("cleanup_attempt", 0)
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ToolError("OpenEMR import cleanup state is malformed")
        return str(data["cleanup_status"]), data
    if data.get("schema_version") == 1 and data.get("status") == "cleanup-complete":
        if (
            data.get("local_receipt_deleted") is not True
            or data.get("cleanup_tombstone_retained") is not True
            or data.get("efs_rollback_copy_deleted") is not True
            or type(data.get("s3_objects_deleted")) is not int
            or data["s3_objects_deleted"] < 0
        ):
            raise ToolError("OpenEMR import cleanup tombstone is malformed")
        return "complete", data
    return None


def _assert_cleanup_confirmations(args: argparse.Namespace, migration_id: str) -> None:
    expected = f"CLEANUP:{migration_id}"
    if not args.allow_aws_execution or not args.confirm_delete_rollback_copy or args.confirmation_token != expected:
        raise ToolError(
            "Cleanup requires --allow-aws-execution, "
            "--confirm-delete-rollback-copy, and --confirmation-token " + expected
        )


def _local_abort_status(path: Path) -> tuple[str, dict[str, Any]] | None:
    """Return validated resumable or completed failed-import abort state."""

    if not os.path.lexists(path):
        return None
    data = read_private_json(path, max_bytes=MAX_JSON_BYTES, label="OpenEMR import state")
    if not isinstance(data, dict):
        raise ToolError("OpenEMR import state must contain one JSON object")
    migration_id = data.get("migration_id")
    if not isinstance(migration_id, str) or not re.fullmatch(
        r"import-[a-f0-9]{16}",
        migration_id,
    ):
        return None
    if data.get("schema_version") == 4 and data.get("abort_status") in {
        "service-restored",
        "cleanup-in-progress",
        "cleanup-failed",
    }:
        attempt = data.get("abort_cleanup_attempt", 0)
        minimum_attempt = 0 if data["abort_status"] == "service-restored" else 1
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < minimum_attempt:
            raise ToolError("OpenEMR import abort state is malformed")
        return str(data["abort_status"]), data
    if data.get("schema_version") == 1 and data.get("status") == "abort-complete":
        if (
            data.get("target_baseline_verified") is not True
            or data.get("service_restored") is not True
            or data.get("staging_deleted") is not True
            or data.get("lock_released") is not True
            or type(data.get("s3_objects_deleted")) is not int
            or data["s3_objects_deleted"] < 0
        ):
            raise ToolError("OpenEMR import abort tombstone is malformed")
        return "complete", data
    return None


def _assert_abort_confirmations(args: argparse.Namespace, migration_id: str) -> None:
    expected = f"ABORT:{migration_id}"
    if not args.allow_aws_execution or not args.confirm_target_baseline_verified or args.confirmation_token != expected:
        raise ToolError(
            "Abort requires --allow-aws-execution, "
            "--confirm-target-baseline-verified, and --confirmation-token " + expected
        )


def _local_recovery_status(path: Path) -> tuple[str, dict[str, Any]] | None:
    """Return validated hard-termination recovery state."""

    if not os.path.lexists(path):
        return None
    data = read_private_json(
        path,
        max_bytes=MAX_JSON_BYTES,
        label="OpenEMR import state",
    )
    if not isinstance(data, dict) or data.get("schema_version") != 4:
        return None
    status = data.get("recovery_status")
    if status not in {"in-progress", "failed", "complete"}:
        return None
    attempt = data.get("recovery_attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ToolError("OpenEMR import recovery state is malformed")
    return str(status), data


def _launch_unknown_state(path: Path) -> dict[str, Any]:
    data = read_private_json(path, max_bytes=MAX_JSON_BYTES, label="OpenEMR import state")
    if not isinstance(data, dict):
        raise ToolError("OpenEMR import state must contain one JSON object")
    required_strings = (
        "migration_id",
        "account_id",
        "region",
        "stack_name",
        "stack_creation_time",
        "cluster_name",
        "service_name",
        "service_url",
        "openemr_version",
        "task_definition_arn",
        "staging_bucket",
        "staging_kms_key_arn",
        "task_security_group_id",
        "database_arn",
        "efs_arn",
        "lock_etag",
        "outcome_unknown_at",
    )
    if (
        data.get("schema_version") != 1
        or data.get("status")
        not in {
            "lock-acquired",
            "source-staged",
            "prelaunch-cleanup-required",
            "quiescing",
            "launch-attempting",
            "launch-outcome-unknown",
        }
        or any(not isinstance(data.get(key), str) or not data[key] for key in required_strings)
        or not isinstance(data.get("original_desired_count"), int)
        or data["original_desired_count"] < 1
        or not isinstance(data.get("recovery_point_arns"), list)
        or len(data["recovery_point_arns"]) != 2
        or not isinstance(data.get("recovery_point_dates"), list)
        or len(data["recovery_point_dates"]) != 2
        or not all(isinstance(value, str) and value for value in data["recovery_point_arns"])
        or not all(isinstance(value, str) and value for value in data["recovery_point_dates"])
        or not isinstance(data.get("private_subnet_ids"), list)
        or not data["private_subnet_ids"]
        or not all(isinstance(value, str) and value for value in data["private_subnet_ids"])
        or (data.get("source_key") is not None and (not isinstance(data["source_key"], str) or not data["source_key"]))
        or (
            data.get("stack_last_updated_time") is not None
            and (not isinstance(data["stack_last_updated_time"], str) or not data["stack_last_updated_time"])
        )
    ):
        raise ToolError("State is not a valid uncertain-launch record")
    return data


def _local_reconcile_status(path: Path) -> dict[str, Any] | None:
    if not os.path.lexists(path):
        return None
    data = read_private_json(path, max_bytes=MAX_JSON_BYTES, label="OpenEMR import state")
    if not isinstance(data, dict):
        raise ToolError("OpenEMR import state must contain one JSON object")
    if data.get("schema_version") != 1 or data.get("status") != "launch-not-observed-cleaned":
        return None
    if (
        data.get("service_restored") is not True
        or data.get("autoscaling_restored") is not True
        or data.get("staging_deleted") is not True
        or data.get("lock_released") is not True
        or type(data.get("s3_objects_deleted")) is not int
        or data["s3_objects_deleted"] < 0
    ):
        raise ToolError("OpenEMR import reconciliation tombstone is malformed")
    return data


def _boto3_session(*, profile: str | None, region: str) -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise ToolError("AWS execution requires the project requirements to be installed") from exc
    return boto3.Session(profile_name=profile, region_name=region)


def _assert_execution_confirmations(args: argparse.Namespace, plan: ImportPlan) -> None:
    required = {
        "--allow-aws-execution": args.allow_aws_execution,
        "--confirm-fresh-target": args.confirm_fresh_target,
        "--confirm-non-production-target": args.confirm_non_production_target,
        "--confirm-downtime": args.confirm_downtime,
        "--confirm-recovery-points": args.confirm_recovery_points,
        "--confirm-destructive-import": args.confirm_destructive_import,
    }
    missing = [flag for flag, accepted in required.items() if not accepted]
    if missing:
        raise ToolError("Execution is locked; explicit acknowledgements are missing: " + ", ".join(missing))
    expected = f"IMPORT:{args.account_id}:{args.region}:{args.stack_name}:{plan.configuration_fingerprint}"
    if args.confirmation_token != expected:
        raise ToolError("Confirmation token mismatch. After reviewing the plan and target, pass: " + expected)


def _matching_plan_source(plan: ImportPlan, source: Path) -> None:
    if plan.target_openemr_version != TARGET_OPENEMR_VERSION:
        raise ToolError("Import plan target does not match the repository OpenEMR version; regenerate it")
    if source.is_symlink() or not source.is_file():
        raise ToolError("Execution source must be one regular native backup file")
    inspection = inspect_source(
        source,
        source_version=plan.source_openemr_version,
    )
    comparisons = {
        "source kind": (inspection.source_kind, plan.source_kind),
        "source fingerprint": (
            inspection.source_fingerprint,
            plan.source_fingerprint,
        ),
        "component checksums": (inspection.checksums, plan.checksums),
        "source version": (
            inspection.source_openemr_version,
            plan.source_openemr_version,
        ),
        "source database version": (
            inspection.source_database_version,
            plan.source_database_version,
        ),
    }
    mismatches = [label for label, (actual, expected) in comparisons.items() if actual != expected]
    if mismatches:
        raise ToolError("Source no longer matches the approved plan: " + ", ".join(mismatches))
    recomputed = create_plan(
        inspection,
        generated_at=plan.created_at,
    )
    if recomputed != plan:
        raise ToolError("Import plan does not match current compatibility policy; regenerate it")


def _context_matches_receipt(context: Any, receipt: Any) -> None:
    comparisons = {
        "account": (context.account_id, receipt.account_id),
        "region": (context.region, receipt.region),
        "stack": (context.stack_name, receipt.stack_name),
        "stack creation time": (
            context.stack_creation_time,
            receipt.stack_creation_time,
        ),
        "stack last update": (
            context.stack_last_updated_time,
            receipt.stack_last_updated_time,
        ),
        "cluster": (context.cluster_name, receipt.cluster_name),
        "service": (context.service_name, receipt.service_name),
        "service URL": (context.service_url, receipt.service_url),
        "OpenEMR version": (context.openemr_version, receipt.openemr_version),
        "task definition": (
            context.task_definition_arn,
            receipt.task_definition_arn,
        ),
        "staging bucket": (context.staging_bucket, receipt.staging_bucket),
        "staging KMS key": (
            context.staging_kms_key_arn,
            receipt.staging_kms_key_arn,
        ),
        "task security group": (
            context.task_security_group_id,
            receipt.task_security_group_id,
        ),
        "private subnets": (
            context.private_subnet_ids,
            receipt.private_subnet_ids,
        ),
        "database": (context.database_arn, receipt.database_arn),
        "EFS file system": (context.efs_arn, receipt.efs_arn),
    }
    mismatches = [name for name, (actual, expected) in comparisons.items() if actual != expected]
    if mismatches:
        raise ToolError("Current stack no longer matches the execution receipt: " + ", ".join(mismatches))


def _inspect_command(args: argparse.Namespace) -> int:
    inspection = inspect_source(
        args.source,
        source_version=args.source_version,
    )
    _emit(inspection.to_dict(), args.output)
    return 0


def _plan_command(args: argparse.Namespace) -> int:
    inspection = inspection_from_dict(_json_file(args.inspection))
    plan = create_plan(inspection)
    _emit(plan.to_dict(), args.output)
    return 0 if plan.execution_allowed else 2


def _execute_command(args: argparse.Namespace) -> int:
    from .aws import (
        ImportLock,
        acquire_import_lock,
        assert_application_healthy,
        assert_import_resource_bindings,
        assert_new_import_target,
        assert_service_autoscaling_active,
        assert_service_stable,
        cleanup_staging_scope,
        read_remote_status,
        recent_recovery_points,
        release_import_lock,
        resolve_stack_context,
        set_service_autoscaling_suspended,
        set_service_desired_count,
        start_import_task,
        upload_source,
        wait_for_task,
        write_receipt,
    )

    plan = plan_from_dict(_json_file(args.plan))
    if not plan.execution_allowed:
        raise ToolError("The approved plan blocks execution: " + "; ".join(plan.blockers))
    if plan.target_mode != "fresh-target-only":
        raise ToolError("Only fresh-target import plans can execute")
    if not re.fullmatch(r"\d{12}", args.account_id):
        raise ToolError("AWS account ID must contain exactly 12 digits")
    if args.maximum_recovery_age_hours < 1 or args.maximum_wait_attempts < 1:
        raise ToolError("Recovery age and waiter attempts must be positive")
    _assert_execution_confirmations(args, plan)
    state_path = args.state or _state_path(plan.migration_id)
    ensure_owner_only_directory(
        state_path.parent,
        parents=True,
        label="OpenEMR import state",
    )
    source_snapshot = snapshot_regular_file(
        args.source,
        state_path.parent,
        max_bytes=ArchiveLimits().max_expanded_bytes,
        label="OpenEMR import source",
    )
    try:
        _matching_plan_source(plan, source_snapshot)
        reserve_private_json(
            state_path,
            {
                "schema_version": 1,
                "migration_id": plan.migration_id,
                "status": "reserved",
            },
            label="OpenEMR import state",
        )
    except FileExistsError as exc:
        source_snapshot.unlink(missing_ok=True)
        raise ToolError(f"Execution state already exists; inspect it before retrying: {state_path}") from exc
    except BaseException:
        source_snapshot.unlink(missing_ok=True)
        raise
    session = _boto3_session(profile=args.profile, region=args.region)
    context = None
    source_key = None
    quiesced = False
    lock_acquired = False
    lock_outcome_unknown = False
    import_lock: ImportLock | None = None
    launch_attempted = False
    receipt = None
    prelaunch_state: dict[str, Any] = {}

    def compensate_prelaunch(failure: BaseException) -> None:
        try:
            if quiesced and context is not None:
                set_service_desired_count(
                    context,
                    session=session,
                    desired_count=original_desired_count,
                )
                set_service_autoscaling_suspended(
                    context,
                    session=session,
                    suspended=False,
                )
            if source_key is not None and context is not None:
                cleanup_staging_scope(
                    region=context.region,
                    staging_bucket=context.staging_bucket,
                    migration_id=plan.migration_id,
                    expected_bucket_owner=context.account_id,
                    session=session,
                )
            if lock_acquired and context is not None and import_lock is not None:
                release_import_lock(
                    context,
                    session=session,
                    lock=import_lock,
                )
        except BaseException as compensation_error:
            _write_state(
                state_path,
                {
                    **prelaunch_state,
                    "status": "prelaunch-cleanup-required",
                    "failure_type": type(failure).__name__,
                    "compensation_failure_type": type(compensation_error).__name__,
                    "service_state_requires_reconciliation": quiesced,
                },
            )
            raise ToolError(
                "Prelaunch compensation did not complete. Preserve the state, "
                "staging scope, and lock; use reconcile-launch before retrying."
            ) from compensation_error
        _write_state(
            state_path,
            {
                **prelaunch_state,
                "status": "prelaunch-failed-cleaned",
                "failure_type": type(failure).__name__,
                "service_remains_stopped": False,
                "autoscaling_remains_suspended": False,
            },
        )

    try:
        context = resolve_stack_context(
            session=session,
            region=args.region,
            expected_account_id=args.account_id,
            stack_name=args.stack_name,
        )
        if context.openemr_version != plan.target_openemr_version:
            raise ToolError("Deployed OpenEMR version does not match the approved import plan")
        if context.import_target_mode != "fresh-target-only":
            raise ToolError(
                "Stack is not marked as a fresh import target; redeploy it with "
                "-c openemr_import_target=true before execution"
            )
        assert_new_import_target(context)
        assert_import_resource_bindings(context, session=session)
        original_desired_count = assert_service_stable(context, session=session)
        assert_service_autoscaling_active(context, session=session)
        recovery_points = recent_recovery_points(
            context,
            session=session,
            maximum_age_hours=args.maximum_recovery_age_hours,
        )
        prelaunch_state = {
            "schema_version": 1,
            "migration_id": plan.migration_id,
            "status": "recovery-verified",
            "account_id": context.account_id,
            "region": context.region,
            "stack_name": context.stack_name,
            "stack_creation_time": context.stack_creation_time,
            "stack_last_updated_time": context.stack_last_updated_time,
            "cluster_name": context.cluster_name,
            "service_name": context.service_name,
            "service_url": context.service_url,
            "openemr_version": context.openemr_version,
            "original_desired_count": original_desired_count,
            "task_definition_arn": context.task_definition_arn,
            "staging_bucket": context.staging_bucket,
            "staging_kms_key_arn": context.staging_kms_key_arn,
            "task_security_group_id": context.task_security_group_id,
            "private_subnet_ids": list(context.private_subnet_ids),
            "database_arn": context.database_arn,
            "efs_arn": context.efs_arn,
            "source_key": None,
            "outcome_unknown_at": utc_now(),
            "recovery_point_arns": [point.recovery_point_arn for point in recovery_points],
            "recovery_point_dates": [point.creation_date for point in recovery_points],
            "started_by": plan.migration_id,
            "service_remains_stopped": False,
            "autoscaling_remains_suspended": False,
        }
        _write_state(state_path, prelaunch_state)
        prelaunch_state["status"] = "lock-acquiring"
        _write_state(state_path, prelaunch_state)
        try:
            import_lock = acquire_import_lock(
                context,
                session=session,
                migration_id=plan.migration_id,
            )
        except ClientError:
            raise
        except BotoCoreError, ImportLockOutcomeUnknown:
            lock_outcome_unknown = True
            raise
        lock_acquired = True
        prelaunch_state["status"] = "lock-acquired"
        prelaunch_state["lock_etag"] = import_lock.etag
        prelaunch_state["lock_version_id"] = import_lock.version_id
        _write_state(state_path, prelaunch_state)
        source_key = f"migrations/{plan.migration_id}/source.tar"
        prelaunch_state["source_key"] = source_key
        _write_state(state_path, prelaunch_state)
        uploaded_key = upload_source(
            context,
            session=session,
            migration_id=plan.migration_id,
            source=source_snapshot,
        )
        if uploaded_key != source_key:
            raise ToolError("Import staging upload returned an unexpected object key")
        source_snapshot.unlink()
        prelaunch_state["status"] = "source-staged"
        _write_state(state_path, prelaunch_state)
        prelaunch_state.update(
            {
                "status": "quiescing",
                "outcome_unknown_at": utc_now(),
                "service_remains_stopped": True,
                "autoscaling_remains_suspended": True,
            }
        )
        _write_state(state_path, prelaunch_state)
        quiesced = True
        set_service_autoscaling_suspended(
            context,
            session=session,
            suspended=True,
        )
        set_service_desired_count(
            context,
            session=session,
            desired_count=0,
        )
        prelaunch_state["status"] = "launch-attempting"
        _write_state(state_path, prelaunch_state)
        launch_attempted = True
        try:
            receipt = start_import_task(
                context,
                session=session,
                plan=plan,
                migration_id=plan.migration_id,
                source_key=source_key,
                original_desired_count=original_desired_count,
                recovery_points=recovery_points,
                lock=import_lock,
            )
        except BotoCoreError:
            receipt = start_import_task(
                context,
                session=session,
                plan=plan,
                migration_id=plan.migration_id,
                source_key=source_key,
                original_desired_count=original_desired_count,
                recovery_points=recovery_points,
                lock=import_lock,
            )
    except (BotoCoreError, KeyboardInterrupt) as exc:
        source_snapshot.unlink(missing_ok=True)
        if lock_outcome_unknown:
            _write_state(
                state_path,
                {
                    **prelaunch_state,
                    "status": "lock-outcome-unknown",
                },
            )
            raise ToolError(
                "S3 import-lock acquisition outcome is unknown. No service mutation "
                "was attempted; inspect locks/active.json before recovery."
            ) from exc
        if launch_attempted:
            _write_state(
                state_path,
                {
                    **prelaunch_state,
                    "status": "launch-outcome-unknown",
                    "outcome_unknown_at": utc_now(),
                    "service_remains_stopped": True,
                    "autoscaling_remains_suspended": True,
                },
            )
            raise ToolError(
                "ECS task launch outcome is unknown. The service remains stopped, "
                "autoscaling remains suspended, and staging data is preserved; "
                f"reconcile ECS startedBy={plan.migration_id} "
                f"before using state file {state_path}"
            ) from exc
        compensate_prelaunch(exc)
        raise
    except Exception as exc:
        source_snapshot.unlink(missing_ok=True)
        if lock_outcome_unknown:
            _write_state(
                state_path,
                {
                    **prelaunch_state,
                    "status": "lock-outcome-unknown",
                },
            )
            raise ToolError(
                "S3 import-lock acquisition outcome is unknown. No service mutation "
                "was attempted; inspect locks/active.json before recovery."
            ) from exc
        if launch_attempted:
            _write_state(
                state_path,
                {
                    **prelaunch_state,
                    "status": "launch-outcome-unknown",
                    "outcome_unknown_at": utc_now(),
                    "service_remains_stopped": True,
                    "autoscaling_remains_suspended": True,
                },
            )
            raise ToolError(
                "ECS task launch outcome is unknown. The service remains stopped, "
                "autoscaling remains suspended, and staging data is preserved; "
                f"reconcile ECS startedBy={plan.migration_id} "
                f"before using state file {state_path}"
            ) from exc
        compensate_prelaunch(exc)
        raise

    if context is None or receipt is None:
        raise ToolError("Import execution state was not initialized")
    write_receipt(state_path, receipt)
    exit_code = wait_for_task(
        context,
        session=session,
        task_arn=receipt.task_arn,
        maximum_attempts=args.maximum_wait_attempts,
    )
    status = read_remote_status(receipt, session=session)
    if exit_code == 0 and _ready_to_restore_service(status):
        set_service_desired_count(
            context,
            session=session,
            desired_count=original_desired_count,
        )
        try:
            assert_application_healthy(context)
        except Exception:
            set_service_desired_count(
                context,
                session=session,
                desired_count=0,
            )
            raise
        set_service_autoscaling_suspended(
            context,
            session=session,
            suspended=False,
        )
        status = read_remote_status(receipt, session=session)
        status["result"] = "import-succeeded-service-restored"
        status["state_file"] = str(state_path)
        _emit(status, None)
        return 0
    status["result"] = "import-failed-service-remains-stopped"
    status["state_file"] = str(state_path)
    _emit(status, None)
    return 3


def _status_command(args: argparse.Namespace) -> int:
    from .aws import read_receipt, read_remote_status

    reconciliation = _local_reconcile_status(args.state)
    if reconciliation is not None:
        _emit({**reconciliation, "already_complete": True}, None)
        return 0
    cleanup_state = _local_cleanup_status(args.state)
    if cleanup_state is not None and cleanup_state[0] == "complete":
        _emit({**cleanup_state[1], "already_complete": True}, None)
        return 0
    abort_state = _local_abort_status(args.state)
    if abort_state is not None and abort_state[0] == "complete":
        _emit({**abort_state[1], "already_complete": True}, None)
        return 0
    recovery_state = _local_recovery_status(args.state)
    receipt = read_receipt(args.state)
    session = _boto3_session(profile=args.profile, region=receipt.region)
    status = read_remote_status(receipt, session=session)
    if cleanup_state is not None:
        status["local_cleanup_status"] = cleanup_state[0]
    if abort_state is not None:
        status["local_abort_status"] = abort_state[0]
    if recovery_state is not None:
        status["local_recovery_status"] = recovery_state[0]
    _emit(status, None)
    return 0


def _finalize_command(args: argparse.Namespace) -> int:
    from .aws import (
        assert_application_healthy,
        read_receipt,
        read_remote_status,
        resolve_stack_context,
        set_service_autoscaling_suspended,
        set_service_desired_count,
    )

    receipt = read_receipt(args.state)
    expected = f"FINALIZE:{receipt.migration_id}"
    if not args.allow_aws_execution or args.confirmation_token != expected:
        raise ToolError("Finalization requires --allow-aws-execution and --confirmation-token " + expected)
    session = _boto3_session(profile=args.profile, region=receipt.region)
    context = resolve_stack_context(
        session=session,
        region=receipt.region,
        expected_account_id=receipt.account_id,
        stack_name=receipt.stack_name,
    )
    _context_matches_receipt(context, receipt)
    status = read_remote_status(receipt, session=session)
    service = status.get("service", {})
    autoscaling = status.get("autoscaling", {})
    stopped = (
        isinstance(service, dict)
        and service.get("desired_count") == 0
        and service.get("running_count") == 0
        and service.get("pending_count") == 0
    )
    already_running = (
        isinstance(service, dict)
        and service.get("desired_count") == receipt.original_desired_count
        and service.get("running_count") == receipt.original_desired_count
        and service.get("pending_count") == 0
    )
    already_finalized = (
        _completed_worker_success(status)
        and already_running
        and isinstance(autoscaling, dict)
        and autoscaling.get("active") is True
        and autoscaling.get("suspended") is False
    )
    if already_finalized:
        assert_application_healthy(context)
        _emit(status, None)
        return 0
    if (
        not _completed_worker_success(status)
        or not isinstance(autoscaling, dict)
        or autoscaling.get("suspended") is not True
        or autoscaling.get("active") is not False
        or not (stopped or already_running)
    ):
        raise ToolError(
            "The successful import, service state, and suspended autoscaling state "
            "do not jointly authorize finalization"
        )
    if stopped:
        set_service_desired_count(
            context,
            session=session,
            desired_count=receipt.original_desired_count,
        )
    try:
        assert_application_healthy(context)
    except Exception:
        set_service_desired_count(
            context,
            session=session,
            desired_count=0,
        )
        raise
    set_service_autoscaling_suspended(
        context,
        session=session,
        suspended=False,
    )
    _emit(read_remote_status(receipt, session=session), None)
    return 0


def _reconcile_launch_command(args: argparse.Namespace) -> int:
    from .aws import (
        IMPORT_LOCK_KEY,
        ExecutionReceipt,
        ImportLock,
        assert_application_healthy,
        assert_service_autoscaling_active,
        assert_service_stable,
        cleanup_staging_scope,
        find_import_tasks,
        read_remote_status,
        release_import_lock,
        resolve_stack_context,
        set_service_autoscaling_suspended,
        set_service_desired_count,
        write_receipt,
    )

    state = _launch_unknown_state(args.state)
    migration_id = str(state["migration_id"])
    expected = f"RECONCILE:{migration_id}"
    if not args.allow_aws_execution or args.confirmation_token != expected:
        raise ToolError(f"Launch reconciliation requires --allow-aws-execution and --confirmation-token {expected}")
    if (
        args.minimum_unknown_age_minutes < 1
        or args.maximum_unknown_age_minutes <= args.minimum_unknown_age_minutes
        or args.maximum_unknown_age_minutes > 55
    ):
        raise ToolError(
            "Uncertain-launch reconciliation requires a positive minimum and a larger maximum of at most 55 minutes"
        )
    try:
        unknown_at = datetime.fromisoformat(str(state["outcome_unknown_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError("Uncertain-launch timestamp is malformed") from exc
    if unknown_at.tzinfo is None:
        raise ToolError("Uncertain-launch timestamp lacks a timezone")
    session = _boto3_session(profile=args.profile, region=str(state["region"]))
    context = resolve_stack_context(
        session=session,
        region=str(state["region"]),
        expected_account_id=str(state["account_id"]),
        stack_name=str(state["stack_name"]),
    )
    for field, expected_value in (
        ("stack_creation_time", context.stack_creation_time),
        ("stack_last_updated_time", context.stack_last_updated_time),
        ("cluster_name", context.cluster_name),
        ("service_name", context.service_name),
        ("service_url", context.service_url),
        ("openemr_version", context.openemr_version),
        ("task_definition_arn", context.task_definition_arn),
        ("staging_bucket", context.staging_bucket),
        ("staging_kms_key_arn", context.staging_kms_key_arn),
        ("task_security_group_id", context.task_security_group_id),
        ("database_arn", context.database_arn),
        ("efs_arn", context.efs_arn),
    ):
        if state[field] != expected_value:
            raise ToolError(f"Uncertain-launch state no longer matches {field}")
    if tuple(state["private_subnet_ids"]) != context.private_subnet_ids:
        raise ToolError("Uncertain-launch state no longer matches private_subnet_ids")
    tasks = find_import_tasks(
        context,
        session=session,
        migration_id=migration_id,
    )
    if tasks:
        reconciled_source_key = state.get("source_key")
        if not isinstance(reconciled_source_key, str) or not reconciled_source_key:
            raise ToolError("An ECS task exists but the durable state has no staged source key")
        task_arn = tasks[0].get("taskArn")
        if not isinstance(task_arn, str) or not task_arn:
            raise ToolError("Reconciled ECS task lacks an ARN")
        lock_version_id = state.get("lock_version_id")
        if lock_version_id is not None and not isinstance(lock_version_id, str):
            raise ToolError("Uncertain-launch lock version is malformed")
        receipt = ExecutionReceipt(
            schema_version=4,
            migration_id=migration_id,
            account_id=context.account_id,
            region=context.region,
            stack_name=context.stack_name,
            stack_creation_time=context.stack_creation_time,
            stack_last_updated_time=context.stack_last_updated_time,
            cluster_name=context.cluster_name,
            service_name=context.service_name,
            service_url=context.service_url,
            openemr_version=context.openemr_version,
            original_desired_count=int(state["original_desired_count"]),
            task_arn=task_arn,
            task_definition_arn=context.task_definition_arn,
            staging_bucket=context.staging_bucket,
            staging_kms_key_arn=context.staging_kms_key_arn,
            task_security_group_id=context.task_security_group_id,
            private_subnet_ids=context.private_subnet_ids,
            database_arn=context.database_arn,
            efs_arn=context.efs_arn,
            source_key=reconciled_source_key,
            started_at=str(state["outcome_unknown_at"]),
            recovery_point_arns=(
                str(state["recovery_point_arns"][0]),
                str(state["recovery_point_arns"][1]),
            ),
            recovery_point_dates=(
                str(state["recovery_point_dates"][0]),
                str(state["recovery_point_dates"][1]),
            ),
            lock_etag=str(state["lock_etag"]),
            lock_version_id=lock_version_id,
        )
        write_receipt(args.state, receipt)
        status = read_remote_status(receipt, session=session)
        status["result"] = "launch-reconciled"
        status["state_file"] = str(args.state)
        _emit(status, None)
        return 0

    age = datetime.now(UTC) - unknown_at.astimezone(UTC)
    if age < timedelta(minutes=args.minimum_unknown_age_minutes):
        raise ToolError(
            "No task is visible yet; wait until the uncertain launch is at least "
            f"{args.minimum_unknown_age_minutes} minutes old"
        )
    if age > timedelta(minutes=args.maximum_unknown_age_minutes):
        raise ToolError(
            "No task is visible and the bounded ECS stopped-task visibility window "
            "has expired; investigate manually without restoring or deleting evidence"
        )
    if not args.confirm_no_task_launched:
        raise ToolError("No ECS task was found. Restoring and cleaning requires --confirm-no-task-launched")
    if find_import_tasks(context, session=session, migration_id=migration_id):
        raise ToolError("An ECS task appeared during reconciliation; rerun reconciliation")
    service_recovery_required = (
        state["status"]
        in {
            "quiescing",
            "launch-attempting",
            "launch-outcome-unknown",
        }
        or state.get("service_state_requires_reconciliation") is True
    )
    if service_recovery_required:
        set_service_desired_count(
            context,
            session=session,
            desired_count=int(state["original_desired_count"]),
        )
        try:
            assert_application_healthy(context)
        except Exception:
            set_service_desired_count(
                context,
                session=session,
                desired_count=0,
            )
            raise
        set_service_autoscaling_suspended(
            context,
            session=session,
            suspended=False,
        )
    else:
        if assert_service_stable(context, session=session) != int(state["original_desired_count"]):
            raise ToolError("Prelaunch cleanup state no longer matches the running service")
        assert_service_autoscaling_active(context, session=session)
    deleted = cleanup_staging_scope(
        region=context.region,
        staging_bucket=context.staging_bucket,
        migration_id=migration_id,
        expected_bucket_owner=context.account_id,
        session=session,
    )
    release_import_lock(
        context,
        session=session,
        lock=ImportLock(
            bucket=context.staging_bucket,
            key=IMPORT_LOCK_KEY,
            migration_id=migration_id,
            etag=str(state["lock_etag"]),
            version_id=(str(state["lock_version_id"]) if state.get("lock_version_id") is not None else None),
        ),
    )
    tombstone = {
        "schema_version": 1,
        "migration_id": migration_id,
        "status": "launch-not-observed-cleaned",
        "reconciled_at": utc_now(),
        "service_restored": service_recovery_required,
        "autoscaling_restored": service_recovery_required,
        "staging_deleted": True,
        "lock_released": True,
        "s3_objects_deleted": deleted,
    }
    _write_state(args.state, tombstone)
    _emit(tombstone, None)
    return 0


def _failed_worker_authorizes_abort(status: dict[str, Any]) -> bool:
    worker = status.get("worker", {})
    task = status.get("task", {})
    if not (
        isinstance(worker, dict)
        and worker.get("status") == "failed"
        and isinstance(task, dict)
        and task.get("last_status") == "STOPPED"
        and task.get("identity_verified") is True
        and task.get("container_exit_code") not in {None, 0}
    ):
        return False
    if worker.get("rollback_status") == "succeeded":
        return True
    return worker.get("rollback_status") is None and worker.get("phase") in {
        "download",
        "source-validation",
        "target-validation",
        "recovery-baseline",
    }


def _interrupted_mutation_authorizes_recovery(status: dict[str, Any]) -> bool:
    worker = status.get("worker", {})
    task = status.get("task", {})
    service = status.get("service", {})
    autoscaling = status.get("autoscaling", {})
    if not (
        isinstance(worker, dict)
        and isinstance(task, dict)
        and task.get("last_status") == "STOPPED"
        and task.get("identity_verified") is True
        and task.get("container_exit_code") not in {None, 0}
        and isinstance(service, dict)
        and service.get("desired_count") == 0
        and service.get("running_count") == 0
        and service.get("pending_count") == 0
        and isinstance(autoscaling, dict)
        and autoscaling.get("suspended") is True
    ):
        return False
    if worker.get("status") == "running" and worker.get("phase") in {
        "database-import",
        "site-import",
        "post-import-validation",
    }:
        return True
    return worker.get("status") == "failed" and worker.get("rollback_status") == "failed"


def _recover_command(args: argparse.Namespace) -> int:
    from .aws import (
        assert_import_resource_bindings,
        read_receipt,
        read_remote_status,
        resolve_stack_context,
        start_recovery_task,
        wait_for_task,
    )

    receipt = read_receipt(args.state)
    expected = f"RECOVER:{receipt.migration_id}"
    if not args.allow_aws_execution or not args.confirm_restore_local_baseline or args.confirmation_token != expected:
        raise ToolError(
            "Recovery requires --allow-aws-execution, "
            "--confirm-restore-local-baseline, and --confirmation-token " + expected
        )
    local = _local_recovery_status(args.state)
    if local is not None and local[0] == "complete":
        _emit({**local[1], "already_complete": True}, None)
        return 0
    session = _boto3_session(profile=args.profile, region=receipt.region)
    context = resolve_stack_context(
        session=session,
        region=receipt.region,
        expected_account_id=receipt.account_id,
        stack_name=receipt.stack_name,
    )
    _context_matches_receipt(context, receipt)
    assert_import_resource_bindings(context, session=session)
    if local is None and not _interrupted_mutation_authorizes_recovery(read_remote_status(receipt, session=session)):
        raise ToolError(
            "Local-baseline recovery requires an identity-verified stopped import "
            "task interrupted during mutation or a failed automatic rollback"
        )
    if local is not None and local[0] == "in-progress":
        attempt = int(local[1]["recovery_attempt"])
    else:
        attempt = int(local[1]["recovery_attempt"]) + 1 if local is not None else 1
        _write_state(
            args.state,
            {
                **asdict(receipt),
                "recovery_status": "in-progress",
                "recovery_attempt": attempt,
            },
        )
    task_arn = start_recovery_task(
        context,
        session=session,
        migration_id=receipt.migration_id,
        attempt=attempt,
    )
    if wait_for_task(context, session=session, task_arn=task_arn) != 0:
        _write_state(
            args.state,
            {
                **asdict(receipt),
                "recovery_status": "failed",
                "recovery_attempt": attempt,
            },
        )
        raise ToolError("Local-baseline recovery task failed")
    recovered = {
        **asdict(receipt),
        "recovery_status": "complete",
        "recovery_attempt": attempt,
    }
    _write_state(args.state, recovered)
    _emit(
        {
            "schema_version": 1,
            "migration_id": receipt.migration_id,
            "status": "local-baseline-recovered",
            "service_remains_stopped": True,
            "autoscaling_remains_suspended": True,
            "next_step": "Verify the target baseline independently, then run abort",
        },
        None,
    )
    return 0


def _abort_command(args: argparse.Namespace) -> int:
    from .aws import (
        IMPORT_LOCK_KEY,
        ImportLock,
        assert_application_healthy,
        cleanup_staging,
        read_receipt,
        read_remote_status,
        release_import_lock,
        resolve_stack_context,
        set_service_autoscaling_suspended,
        set_service_desired_count,
        start_cleanup_task,
        wait_for_task,
    )

    local_status = _local_abort_status(args.state)
    if local_status is not None and local_status[0] == "complete":
        migration_id = str(local_status[1]["migration_id"])
        _assert_abort_confirmations(args, migration_id)
        _emit({**local_status[1], "already_complete": True}, None)
        return 0
    recovery_status = _local_recovery_status(args.state)
    receipt = read_receipt(args.state)
    _assert_abort_confirmations(args, receipt.migration_id)
    session = _boto3_session(profile=args.profile, region=receipt.region)
    context = resolve_stack_context(
        session=session,
        region=receipt.region,
        expected_account_id=receipt.account_id,
        stack_name=receipt.stack_name,
    )
    _context_matches_receipt(context, receipt)
    status = read_remote_status(receipt, session=session)
    if local_status is None:
        recovery_complete = recovery_status is not None and recovery_status[0] == "complete"
        service = status.get("service", {})
        autoscaling = status.get("autoscaling", {})
        baseline_authorized = recovery_complete or _failed_worker_authorizes_abort(status)
        recovered_target_is_quiesced = (
            baseline_authorized
            and isinstance(service, dict)
            and service.get("desired_count") == 0
            and service.get("running_count") == 0
            and service.get("pending_count") == 0
            and isinstance(autoscaling, dict)
            and autoscaling.get("suspended") is True
        )
        restored_service_is_suspended = (
            baseline_authorized
            and isinstance(service, dict)
            and service.get("desired_count") == receipt.original_desired_count
            and service.get("running_count") == receipt.original_desired_count
            and service.get("pending_count") == 0
            and isinstance(autoscaling, dict)
            and autoscaling.get("suspended") is True
        )
        if not recovered_target_is_quiesced and not restored_service_is_suspended:
            raise ToolError(
                "Abort requires one identity-verified failed task, a verified automatic "
                "rollback, completed local-baseline recovery, or pre-mutation failure, "
                "plus suspended autoscaling and either a stopped or fully restored service"
            )
        if recovered_target_is_quiesced:
            set_service_desired_count(
                context,
                session=session,
                desired_count=receipt.original_desired_count,
            )
        try:
            assert_application_healthy(context)
            _write_state(
                args.state,
                {
                    **asdict(receipt),
                    "abort_status": "service-restored",
                    "abort_cleanup_attempt": 0,
                },
            )
        except Exception:
            set_service_desired_count(
                context,
                session=session,
                desired_count=0,
            )
            raise
    else:
        service = status.get("service", {})
        if (
            not isinstance(service, dict)
            or service.get("desired_count") != receipt.original_desired_count
            or service.get("running_count") != receipt.original_desired_count
            or service.get("pending_count") != 0
        ):
            raise ToolError("Resuming abort requires the restored service to be fully running")
        assert_application_healthy(context)
    autoscaling = status.get("autoscaling", {})
    if not isinstance(autoscaling, dict):
        raise ToolError("Unable to verify autoscaling while aborting failed import")
    if autoscaling.get("active") is not True:
        if autoscaling.get("suspended") is not True:
            raise ToolError("Autoscaling is neither active nor fully suspended")
        set_service_autoscaling_suspended(
            context,
            session=session,
            suspended=False,
        )

    previous_attempt = int(local_status[1].get("abort_cleanup_attempt", 0)) if local_status is not None else 0
    cleanup_attempt = (
        previous_attempt
        if local_status is not None and local_status[0] == "cleanup-in-progress"
        else previous_attempt + 1
    )
    _write_state(
        args.state,
        {
            **asdict(receipt),
            "abort_status": "cleanup-in-progress",
            "abort_cleanup_attempt": cleanup_attempt,
        },
    )
    task_arn = start_cleanup_task(
        context,
        session=session,
        migration_id=receipt.migration_id,
        attempt=cleanup_attempt,
    )
    if wait_for_task(context, session=session, task_arn=task_arn) != 0:
        _write_state(
            args.state,
            {
                **asdict(receipt),
                "abort_status": "cleanup-failed",
                "abort_cleanup_attempt": cleanup_attempt,
            },
        )
        raise ToolError("Failed-import EFS artifact cleanup task failed")
    deleted = cleanup_staging(receipt, session=session)
    release_import_lock(
        context,
        session=session,
        lock=ImportLock(
            bucket=receipt.staging_bucket,
            key=IMPORT_LOCK_KEY,
            migration_id=receipt.migration_id,
            etag=receipt.lock_etag,
            version_id=receipt.lock_version_id,
        ),
    )
    tombstone = {
        "schema_version": 1,
        "migration_id": receipt.migration_id,
        "status": "abort-complete",
        "aborted_at": utc_now(),
        "target_baseline_verified": True,
        "service_restored": True,
        "staging_deleted": True,
        "lock_released": True,
        "s3_objects_deleted": deleted,
        "next_step": "Generate a new inspection and plan before retrying",
    }
    _write_state(args.state, tombstone)
    _emit(tombstone, None)
    return 0


def _cleanup_command(args: argparse.Namespace) -> int:
    from .aws import (
        IMPORT_LOCK_KEY,
        ImportLock,
        assert_application_healthy,
        cleanup_staging,
        read_receipt,
        read_remote_status,
        release_import_lock,
        resolve_stack_context,
        start_cleanup_task,
        wait_for_task,
    )

    cleanup_state = _local_cleanup_status(args.state)
    if cleanup_state is not None and cleanup_state[0] == "complete":
        migration_id = str(cleanup_state[1]["migration_id"])
        _assert_cleanup_confirmations(args, migration_id)
        _emit({**cleanup_state[1], "already_complete": True}, None)
        return 0
    receipt = read_receipt(args.state)
    _assert_cleanup_confirmations(args, receipt.migration_id)
    cleanup_resuming = cleanup_state is not None and cleanup_state[0] in {
        "in-progress",
        "failed",
    }
    session = _boto3_session(profile=args.profile, region=receipt.region)
    context = resolve_stack_context(
        session=session,
        region=receipt.region,
        expected_account_id=receipt.account_id,
        stack_name=receipt.stack_name,
    )
    _context_matches_receipt(context, receipt)
    status = read_remote_status(receipt, session=session)
    if not cleanup_resuming and not _completed_worker_success(status):
        raise ToolError("Cleanup is allowed only after a successful import")
    service = status.get("service", {})
    if (
        service.get("desired_count") != receipt.original_desired_count
        or service.get("running_count") != receipt.original_desired_count
    ):
        raise ToolError("Cleanup requires the restored service to be fully running")
    if status.get("autoscaling", {}).get("active") is not True:
        raise ToolError("Cleanup requires ECS service autoscaling to be active")
    assert_application_healthy(context)
    previous_attempt = 0
    if cleanup_resuming and cleanup_state is not None:
        previous_attempt = int(cleanup_state[1].get("cleanup_attempt", 0))
    cleanup_attempt = (
        previous_attempt if cleanup_state is not None and cleanup_state[0] == "in-progress" else previous_attempt + 1
    )
    _write_state(
        args.state,
        {
            **asdict(receipt),
            "cleanup_status": "in-progress",
            "cleanup_attempt": cleanup_attempt,
        },
    )
    task_arn = start_cleanup_task(
        context,
        session=session,
        migration_id=receipt.migration_id,
        attempt=cleanup_attempt,
    )
    if wait_for_task(context, session=session, task_arn=task_arn) != 0:
        _write_state(
            args.state,
            {
                **asdict(receipt),
                "cleanup_status": "failed",
                "cleanup_attempt": cleanup_attempt,
            },
        )
        raise ToolError("EFS import-artifact cleanup task failed")
    deleted = cleanup_staging(receipt, session=session)
    release_import_lock(
        context,
        session=session,
        lock=ImportLock(
            bucket=receipt.staging_bucket,
            key=IMPORT_LOCK_KEY,
            migration_id=receipt.migration_id,
            etag=receipt.lock_etag,
            version_id=receipt.lock_version_id,
        ),
    )
    tombstone = {
        "schema_version": 1,
        "migration_id": receipt.migration_id,
        "status": "cleanup-complete",
        "s3_objects_deleted": deleted,
        "local_receipt_deleted": True,
        "cleanup_tombstone_retained": True,
        "efs_rollback_copy_deleted": True,
    }
    _write_state(args.state, tombstone)
    _emit(tombstone, None)
    return 0


def _completed_worker_success(status: dict[str, Any]) -> bool:
    worker = status.get("worker", {})
    task = status.get("task", {})
    return (
        isinstance(worker, dict)
        and worker.get("status") == "succeeded"
        and isinstance(task, dict)
        and task.get("last_status") == "STOPPED"
        and task.get("container_exit_code") == 0
        and task.get("identity_verified") is True
    )


def _ready_to_restore_service(status: dict[str, Any]) -> bool:
    service = status.get("service", {})
    autoscaling = status.get("autoscaling", {})
    return (
        _completed_worker_success(status)
        and isinstance(service, dict)
        and service.get("desired_count") == 0
        and service.get("running_count") == 0
        and service.get("pending_count") == 0
        and isinstance(autoscaling, dict)
        and autoscaling.get("suspended") is True
        and autoscaling.get("active") is False
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.openemr_import",
        description=(
            "Inspect and plan offline by default; AWS mutations require explicit "
            "execution subcommands and confirmations."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("source", type=Path)
    inspect_parser.add_argument("--source-version")
    inspect_parser.add_argument("--output", type=Path)
    inspect_parser.set_defaults(handler=_inspect_command)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("inspection", type=Path)
    plan_parser.add_argument("--output", type=Path)
    plan_parser.set_defaults(handler=_plan_command)

    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--plan", type=Path, required=True)
    execute_parser.add_argument("--source", type=Path, required=True)
    execute_parser.add_argument("--account-id", required=True)
    execute_parser.add_argument("--region", required=True)
    execute_parser.add_argument("--stack-name", required=True)
    execute_parser.add_argument("--profile")
    execute_parser.add_argument("--state", type=Path)
    execute_parser.add_argument("--maximum-recovery-age-hours", type=int, default=36)
    execute_parser.add_argument("--maximum-wait-attempts", type=int, default=960)
    execute_parser.add_argument("--allow-aws-execution", action="store_true")
    execute_parser.add_argument("--confirm-fresh-target", action="store_true")
    execute_parser.add_argument(
        "--confirm-non-production-target",
        action="store_true",
    )
    execute_parser.add_argument("--confirm-downtime", action="store_true")
    execute_parser.add_argument("--confirm-recovery-points", action="store_true")
    execute_parser.add_argument("--confirm-destructive-import", action="store_true")
    execute_parser.add_argument("--confirmation-token", required=True)
    execute_parser.set_defaults(handler=_execute_command)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--state", type=Path, required=True)
    status_parser.add_argument("--profile")
    status_parser.set_defaults(handler=_status_command)

    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--state", type=Path, required=True)
    finalize_parser.add_argument("--profile")
    finalize_parser.add_argument("--allow-aws-execution", action="store_true")
    finalize_parser.add_argument("--confirmation-token", required=True)
    finalize_parser.set_defaults(handler=_finalize_command)

    reconcile_parser = subparsers.add_parser("reconcile-launch")
    reconcile_parser.add_argument("--state", type=Path, required=True)
    reconcile_parser.add_argument("--profile")
    reconcile_parser.add_argument("--allow-aws-execution", action="store_true")
    reconcile_parser.add_argument("--confirm-no-task-launched", action="store_true")
    reconcile_parser.add_argument("--minimum-unknown-age-minutes", type=int, default=15)
    reconcile_parser.add_argument("--maximum-unknown-age-minutes", type=int, default=45)
    reconcile_parser.add_argument("--confirmation-token", required=True)
    reconcile_parser.set_defaults(handler=_reconcile_launch_command)

    recover_parser = subparsers.add_parser("recover")
    recover_parser.add_argument("--state", type=Path, required=True)
    recover_parser.add_argument("--profile")
    recover_parser.add_argument("--allow-aws-execution", action="store_true")
    recover_parser.add_argument(
        "--confirm-restore-local-baseline",
        action="store_true",
    )
    recover_parser.add_argument("--confirmation-token", required=True)
    recover_parser.set_defaults(handler=_recover_command)

    abort_parser = subparsers.add_parser("abort")
    abort_parser.add_argument("--state", type=Path, required=True)
    abort_parser.add_argument("--profile")
    abort_parser.add_argument("--allow-aws-execution", action="store_true")
    abort_parser.add_argument(
        "--confirm-target-baseline-verified",
        action="store_true",
    )
    abort_parser.add_argument("--confirmation-token", required=True)
    abort_parser.set_defaults(handler=_abort_command)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--state", type=Path, required=True)
    cleanup_parser.add_argument("--profile")
    cleanup_parser.add_argument("--allow-aws-execution", action="store_true")
    cleanup_parser.add_argument(
        "--confirm-delete-rollback-copy",
        action="store_true",
    )
    cleanup_parser.add_argument("--confirmation-token", required=True)
    cleanup_parser.set_defaults(handler=_cleanup_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the import CLI with redacted, actionable failures."""

    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (AwsImportError, ToolError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "unknown")
        print(f"error: AWS API request failed ({code})", file=sys.stderr)
        return 2
    except BotoCoreError:
        print("error: AWS SDK operation failed", file=sys.stderr)
        return 2
