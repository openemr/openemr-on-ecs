"""Tests for defensive OpenEMR import inspection and planning."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools._shared import (
    ToolError,
    atomic_write_private_json,
    read_private_json,
    snapshot_regular_file,
)
from tools.openemr_import import cli
from tools.openemr_import.aws import (
    AwsImportError,
    ExecutionReceipt,
    StackContext,
    read_receipt,
    write_receipt,
)
from tools.openemr_import.cli import main
from tools.openemr_import.inspect import (
    _inspect_sql,
    _resolve_manifest_artifact,
    inspect_source,
)
from tools.openemr_import.models import ArchiveLimits
from tools.openemr_import.plan import (
    TARGET_OPENEMR_VERSION,
    create_plan,
    plan_from_dict,
)


def test_service_restore_requires_worker_task_and_quiescence_state() -> None:
    status = {
        "worker": {"status": "succeeded"},
        "task": {
            "last_status": "STOPPED",
            "container_exit_code": 0,
            "identity_verified": True,
        },
        "service": {"desired_count": 0, "running_count": 0, "pending_count": 0},
        "autoscaling": {"suspended": True, "active": False},
    }

    assert cli._ready_to_restore_service(status)
    status["service"]["desired_count"] = 1
    assert not cli._ready_to_restore_service(status)


def test_private_state_io_rejects_symlink_and_permissive_files(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    outside.chmod(0o600)
    linked = private / "state.json"
    linked.symlink_to(outside)

    with pytest.raises(ToolError):
        atomic_write_private_json(linked, {"status": "unsafe"})
    assert outside.read_text(encoding="utf-8") == "{}"

    linked.unlink()
    linked.write_text("{}", encoding="utf-8")
    linked.chmod(0o644)
    with pytest.raises(ToolError, match="private regular file"):
        read_private_json(linked)


def test_manifest_artifact_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "openemr.sql"
    outside.write_bytes(_sql_dump())
    linked = source / "openemr.sql"
    linked.symlink_to(outside)

    with pytest.raises(ToolError, match="may not be symlinks"):
        _resolve_manifest_artifact(
            source,
            {
                "path": linked.name,
                "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            },
        )

    linked.unlink()
    real = source / "real"
    real.mkdir()
    nested = real / "openemr.sql"
    nested.write_bytes(_sql_dump())
    alias = source / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(ToolError, match="may not be symlinks"):
        _resolve_manifest_artifact(
            source,
            {
                "path": "alias/openemr.sql",
                "sha256": hashlib.sha256(nested.read_bytes()).hexdigest(),
            },
        )


def test_execution_source_snapshot_is_private_and_detects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.tar"
    source.write_bytes(b"original-source")
    private = tmp_path / "private"
    snapshot = snapshot_regular_file(
        source,
        private,
        max_bytes=1024,
        label="test source",
    )
    assert snapshot.read_bytes() == b"original-source"
    assert snapshot.stat().st_mode & 0o777 == 0o600

    source.write_bytes(b"mutable-source")
    original_read = os.read
    mutated = False

    def mutate_after_read(descriptor: int, length: int) -> bytes:
        nonlocal mutated
        payload = original_read(descriptor, length)
        if payload and not mutated:
            mutated = True
            source.write_bytes(b"changed-during-copy")
        return payload

    monkeypatch.setattr("tools._shared.os.read", mutate_after_read)
    with pytest.raises(ToolError, match="changed while"):
        snapshot_regular_file(
            source,
            private,
            max_bytes=1024,
            label="test source",
        )


def test_completed_cleanup_status_and_cleanup_are_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = tmp_path / "cleanup.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "migration_id": "import-0123456789abcdef",
                "status": "cleanup-complete",
                "s3_objects_deleted": 3,
                "local_receipt_deleted": True,
                "cleanup_tombstone_retained": True,
                "efs_rollback_copy_deleted": True,
            }
        ),
        encoding="utf-8",
    )
    state.chmod(0o600)

    def deny_session(*args: object, **kwargs: object) -> None:
        raise AssertionError("completed cleanup must not create an AWS session")

    monkeypatch.setattr(cli, "_boto3_session", deny_session)
    assert cli._status_command(argparse.Namespace(state=state, profile=None)) == 0
    cleanup_args = argparse.Namespace(
        state=state,
        profile=None,
        allow_aws_execution=True,
        confirm_delete_rollback_copy=True,
        confirmation_token="CLEANUP:import-0123456789abcdef",
    )
    assert cli._cleanup_command(cleanup_args) == 0
    assert cli._cleanup_command(cleanup_args) == 0
    emitted = capsys.readouterr().out
    assert emitted.count('"already_complete": true') == 3


def _add_bytes(
    archive: tarfile.TarFile,
    name: str,
    content: bytes,
    *,
    mode: int = 0o600,
) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    archive.addfile(member, io.BytesIO(content))


def _sql_dump(
    patient_marker: bytes = b"private-patient-name",
    *,
    version: str = TARGET_OPENEMR_VERSION,
    database_version: int = 541,
) -> bytes:
    major, minor, patch = version.split(".")
    return b"".join(
        (
            b"-- MySQL dump 10.13\n",
            b"CREATE TABLE `version` (`v_major` int, `v_minor` int, `v_patch` int, "
            b"`v_realpatch` int, `v_tag` varchar(31), `v_database` int, `v_acl` int);\n",
            (f"INSERT INTO `version` VALUES ({major},{minor},{patch},0,'',{database_version},13);\n").encode(),
            b"CREATE TABLE `patient_data` (`id` bigint, `name` text);\n",
            b"INSERT INTO `patient_data` VALUES (1,'" + patient_marker + b"');\n",
        )
    )


def _sites_archive(
    *,
    version: str = TARGET_OPENEMR_VERSION,
    include_keys: bool = True,
    include_version: bool = True,
    executable_document: bool = False,
    active_document: tuple[str, bytes] | None = None,
    invalid_keys: bool = False,
    second_site: bool = False,
) -> bytes:
    major, minor, patch = version.split(".")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        if include_version:
            _add_bytes(
                archive,
                "version.php",
                (
                    "<?php\n"
                    f"$v_major = '{major}';\n"
                    f"$v_minor = '{minor}';\n"
                    f"$v_patch = '{patch}';\n"
                    "$v_tag = '';\n"
                    "$v_realpatch = '0';\n"
                    "$v_database = 541;\n"
                ).encode(),
            )
        _add_bytes(archive, "sites/default/sqlconf.php", b"<?php secret ?>")
        _add_bytes(
            archive,
            "sites/default/config.php",
            b"<?php // official application configuration ?>",
        )
        _add_bytes(
            archive,
            "sites/default/documents/1/patient.pdf",
            b"private-document-content",
        )
        if include_keys:
            key_a = b"invalid" if invalid_keys else b"007" + base64.b64encode(b"a" * 96)
            key_b = b"invalid" if invalid_keys else b"007" + base64.b64encode(b"b" * 96)
            _add_bytes(
                archive,
                "sites/default/documents/logs_and_misc/methods/sevena",
                key_a,
            )
            _add_bytes(
                archive,
                "sites/default/documents/logs_and_misc/methods/sevenb",
                key_b,
            )
        if executable_document:
            _add_bytes(
                archive,
                "sites/default/documents/upload.php",
                b"<?php echo 'unsafe';",
            )
        if active_document is not None:
            _add_bytes(
                archive,
                f"sites/default/documents/{active_document[0]}",
                active_document[1],
            )
        if second_site:
            _add_bytes(archive, "sites/other/sqlconf.php", b"<?php ?>")
            _add_bytes(archive, "sites/other/documents/file.pdf", b"other")
            _add_bytes(
                archive,
                "sites/other/documents/logs_and_misc/methods/sevena",
                b"007" + base64.b64encode(b"c" * 96),
            )
            _add_bytes(
                archive,
                "sites/other/documents/logs_and_misc/methods/sevenb",
                b"007" + base64.b64encode(b"d" * 96),
            )
    return output.getvalue()


def _native_backup(
    path: Path,
    *,
    sites: bytes | None = None,
    patient_marker: bytes = b"private-patient-name",
    sql_version: str = TARGET_OPENEMR_VERSION,
) -> Path:
    sql = gzip.compress(_sql_dump(patient_marker, version=sql_version))
    with tarfile.open(path, mode="w") as archive:
        _add_bytes(archive, "openemr.sql.gz", sql)
        _add_bytes(archive, "openemr.tar.gz", sites or _sites_archive())
        _add_bytes(archive, "backup-metadata.txt", b"ignored")
    return path


def _execution_context_and_receipt() -> tuple[StackContext, ExecutionReceipt]:
    context = StackContext(
        account_id="111122223333",
        region="us-east-1",
        stack_name="OpenemrEcsStack",
        stack_creation_time="2026-08-01T00:00:00+00:00",
        stack_last_updated_time=None,
        cluster_name="openemr-cluster",
        service_name="openemr-service",
        service_url="https://openemr.example.test",
        openemr_version=TARGET_OPENEMR_VERSION,
        import_target_mode="fresh-target-only",
        task_definition_arn="arn:aws:ecs:us-east-1:111122223333:task-definition/import:1",
        staging_bucket="private-staging-bucket",
        staging_kms_key_arn="arn:aws:kms:us-east-1:111122223333:key/example",
        task_security_group_id="sg-import",
        private_subnet_ids=("subnet-one", "subnet-two"),
        database_arn="arn:aws:rds:us-east-1:111122223333:cluster:openemr",
        efs_arn=("arn:aws:elasticfilesystem:us-east-1:111122223333:file-system/fs-openemr"),
    )
    receipt = ExecutionReceipt(
        schema_version=4,
        migration_id="import-0123456789abcdef",
        account_id=context.account_id,
        region=context.region,
        stack_name=context.stack_name,
        stack_creation_time=context.stack_creation_time,
        stack_last_updated_time=None,
        cluster_name=context.cluster_name,
        service_name=context.service_name,
        service_url=context.service_url,
        openemr_version=context.openemr_version,
        original_desired_count=1,
        task_arn="arn:aws:ecs:us-east-1:111122223333:task/openemr/task",
        task_definition_arn=context.task_definition_arn,
        staging_bucket=context.staging_bucket,
        staging_kms_key_arn=context.staging_kms_key_arn,
        task_security_group_id=context.task_security_group_id,
        private_subnet_ids=context.private_subnet_ids,
        database_arn=context.database_arn,
        efs_arn=context.efs_arn,
        source_key="migrations/import-0123456789abcdef/source.tar",
        started_at="2026-08-01T00:05:00+00:00",
        recovery_point_arns=("arn:backup:db", "arn:backup:efs"),
        recovery_point_dates=(
            "2026-08-01T00:00:00+00:00",
            "2026-08-01T00:00:00+00:00",
        ),
        lock_etag='"lock-etag"',
        lock_version_id="lock-version",
    )
    return context, receipt


def test_native_backup_inspection_is_aggregate_and_plan_is_executable(
    tmp_path: Path,
) -> None:
    source = _native_backup(tmp_path / "native-backup.tar")

    inspection = inspect_source(source)
    rendered = json.dumps(inspection.to_dict())
    plan = create_plan(inspection, generated_at="2026-07-31T00:00:00Z")

    assert inspection.source_kind == "native-openemr-backup"
    assert inspection.source_openemr_version == TARGET_OPENEMR_VERSION
    assert inspection.database_type == "mysql"
    assert inspection.sites[0].document_count == 3
    assert inspection.ignored_application_file_count >= 1
    assert not inspection.custom_code_detected
    assert "private-patient-name" not in rendered
    assert "private-document-content" not in rendered
    assert plan.execution_allowed
    assert plan.source_kind == inspection.source_kind
    assert plan.checksums == inspection.checksums
    assert plan_from_dict(plan.to_dict()) == plan


def test_new_plan_uses_new_migration_scope_for_safe_retry(tmp_path: Path) -> None:
    inspection = inspect_source(_native_backup(tmp_path / "backup.tar"))

    first = create_plan(inspection, generated_at="2026-08-01T00:00:00Z")
    retry = create_plan(inspection, generated_at="2026-08-01T00:01:00Z")

    assert first.configuration_fingerprint == retry.configuration_fingerprint
    assert first.migration_id != retry.migration_id


def test_execution_rejects_plan_with_forged_target_version(tmp_path: Path) -> None:
    source = _native_backup(tmp_path / "backup.tar")
    plan = create_plan(inspect_source(source))

    with pytest.raises(ToolError, match="repository OpenEMR version"):
        cli._matching_plan_source(
            replace(plan, target_openemr_version="8.2.1"),
            source,
        )


@pytest.mark.parametrize(
    ("sites", "sql_version", "expected"),
    [
        (
            _sites_archive(version="8.1.0"),
            "8.1.0",
            "same-version source",
        ),
        (
            _sites_archive(include_keys=False),
            TARGET_OPENEMR_VERSION,
            "encryption key",
        ),
        (
            _sites_archive(second_site=True),
            TARGET_OPENEMR_VERSION,
            "exactly one site",
        ),
        (
            _sites_archive(executable_document=True),
            TARGET_OPENEMR_VERSION,
            "Custom executable code",
        ),
    ],
    ids=("version-mismatch", "missing-keys", "multisite", "custom-code"),
)
def test_conservative_plan_blocks_unsupported_sources(
    tmp_path: Path,
    sites: bytes,
    sql_version: str,
    expected: str,
) -> None:
    inspection = inspect_source(_native_backup(tmp_path / "backup.tar", sites=sites, sql_version=sql_version))

    plan = create_plan(inspection)

    assert not plan.execution_allowed
    assert expected.lower() in " ".join(plan.blockers).lower()


def test_inspection_binds_sql_site_and_schema_versions(tmp_path: Path) -> None:
    source = _native_backup(
        tmp_path / "mismatched.tar",
        sites=_sites_archive(version="8.1.0"),
        sql_version=TARGET_OPENEMR_VERSION,
    )

    with pytest.raises(ToolError, match="versions do not match"):
        inspect_source(source)


@pytest.mark.parametrize(
    "unsafe_sql",
    (
        b"CREATE/**/PROCEDURE unsafe_proc() SELECT 1;\n",
        b"CREATE/*x*/TRIGGER unsafe_trigger BEFORE INSERT ON patient_data FOR EACH ROW SET NEW.pid = NEW.pid;\n",
        b"/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`%`*/ "
        b"/*!50003 TRIGGER unsafe_trigger BEFORE INSERT ON patient_data "
        b"FOR EACH ROW SET NEW.pid = NEW.pid */;\n",
        b"CREATE TABLE patient_data (pid bigint); system touch /tmp/unsafe\n",
    ),
)
def test_offline_inspection_rejects_comment_split_stored_code_and_commands(
    unsafe_sql: bytes,
) -> None:
    dump = io.BytesIO(_sql_dump() + unsafe_sql)

    with pytest.raises(ToolError, match="unsafe|executable"):
        _inspect_sql(
            dump,
            name="openemr.sql",
            compressed_bytes=len(dump.getvalue()),
            limits=ArchiveLimits(),
        )


def test_offline_inspection_ignores_block_commented_version_row() -> None:
    commented_version = _sql_dump().split(b"INSERT INTO", 1)[1]
    dump = io.BytesIO(
        b"-- MySQL dump\nCREATE TABLE `version` (`v_major` tinyint);\n/* INSERT INTO" + commented_version + b" */\n"
    )

    with pytest.raises(ToolError, match="authoritative OpenEMR version"):
        _inspect_sql(
            dump,
            name="openemr.sql",
            compressed_bytes=len(dump.getvalue()),
            limits=ArchiveLimits(),
        )


def test_offline_inspection_ignores_version_row_inside_quoted_value() -> None:
    fake_version = _sql_dump().splitlines()[2].replace(b"'", b"\\'")
    dump = io.BytesIO(
        b"-- MySQL dump\nCREATE TABLE notes (body text);\n"
        b"INSERT INTO notes VALUES ('prefix\n" + fake_version + b"\nsuffix');\n"
    )

    with pytest.raises(ToolError, match="authoritative OpenEMR version"):
        _inspect_sql(
            dump,
            name="openemr.sql",
            compressed_bytes=len(dump.getvalue()),
            limits=ArchiveLimits(),
        )


def test_inspection_rejects_malformed_drive_keys_before_execution(tmp_path: Path) -> None:
    inspection = inspect_source(
        _native_backup(
            tmp_path / "malformed-keys.tar",
            sites=_sites_archive(invalid_keys=True),
        )
    )

    assert not create_plan(inspection).execution_allowed
    assert "encryption key" in " ".join(create_plan(inspection).blockers).lower()


@pytest.mark.parametrize(
    ("name", "content"),
    (
        ("payload.phtml", b"not-even-php"),
        ("disguised.jpg", b"#!/bin/sh\necho unsafe\n"),
        ("binary.dat", b"\x7fELF\x02\x01"),
        ("active-document.html", b"<p>plain HTML</p>"),
        ("event-handler.jpg", b"<svg onload=alert(1)>"),
    ),
)
def test_inspection_rejects_broader_active_content(
    tmp_path: Path,
    name: str,
    content: bytes,
) -> None:
    inspection = inspect_source(
        _native_backup(
            tmp_path / f"{name.replace('.', '-')}.tar",
            sites=_sites_archive(active_document=(name, content)),
        )
    )

    assert not create_plan(inspection).execution_allowed
    assert inspection.custom_code_detected is True


def test_native_backup_outer_compression_ratio_is_bounded(tmp_path: Path) -> None:
    source = tmp_path / "compressed-native.tar.gz"
    with tarfile.open(source, mode="w:gz") as archive:
        _add_bytes(archive, "openemr.sql.gz", gzip.compress(_sql_dump()))
        _add_bytes(archive, "openemr.tar.gz", _sites_archive())
        _add_bytes(archive, "padding.bin", b"\0" * (2 * 1024 * 1024))

    with pytest.raises(ToolError, match="compression-ratio"):
        inspect_source(source)


def test_directory_source_is_inspectable_but_not_executable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "openemr.sql.gz").write_bytes(gzip.compress(_sql_dump()))
    (source / "openemr.tar.gz").write_bytes(_sites_archive())

    inspection = inspect_source(source)
    plan = create_plan(inspection)

    assert inspection.source_kind == "sql-and-sites"
    assert not plan.execution_allowed
    assert "inspection-only" in " ".join(plan.blockers)


def test_operator_declared_version_cannot_authorize_native_execution(
    tmp_path: Path,
) -> None:
    source = _native_backup(
        tmp_path / "backup.tar",
        sites=_sites_archive(include_version=False),
    )

    inspection = inspect_source(source, source_version=TARGET_OPENEMR_VERSION)
    plan = create_plan(inspection)

    assert not plan.execution_allowed
    assert "must contain version.php" in " ".join(plan.blockers)


def test_archive_path_traversal_and_links_are_rejected(tmp_path: Path) -> None:
    traversal = io.BytesIO()
    with tarfile.open(fileobj=traversal, mode="w:gz") as archive:
        _add_bytes(archive, "../version.php", b"unsafe")
    with pytest.raises(ToolError, match="traversal"):
        inspect_source(
            _native_backup(
                tmp_path / "traversal.tar",
                sites=traversal.getvalue(),
            )
        )

    linked = io.BytesIO()
    with tarfile.open(fileobj=linked, mode="w:gz") as archive:
        member = tarfile.TarInfo("sites/default/documents/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    with pytest.raises(ToolError, match="links"):
        inspect_source(
            _native_backup(
                tmp_path / "link.tar",
                sites=linked.getvalue(),
            )
        )


def test_archive_limits_are_enforced_before_planning(tmp_path: Path) -> None:
    source = _native_backup(tmp_path / "backup.tar")

    with pytest.raises(ToolError, match="limits exceeded"):
        inspect_source(source, limits=ArchiveLimits(max_members=2))


def test_cli_inspect_and_plan_are_offline_and_do_not_leak_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _native_backup(
        tmp_path / "backup.tar",
        patient_marker=b"do-not-print-this-patient",
    )
    inspection_path = tmp_path / "inspection.json"
    plan_path = tmp_path / "plan.json"

    def deny_session(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline commands must not create an AWS session")

    monkeypatch.setattr("boto3.Session", deny_session)

    assert main(["inspect", str(source), "--output", str(inspection_path)]) == 0
    assert (
        main(
            [
                "plan",
                str(inspection_path),
                "--output",
                str(plan_path),
            ]
        )
        == 0
    )
    output = inspection_path.read_text() + plan_path.read_text()
    assert "do-not-print-this-patient" not in output
    assert json.loads(plan_path.read_text())["execution_allowed"] is True


def test_execute_stays_locked_without_all_confirmations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _native_backup(tmp_path / "backup.tar")
    plan = create_plan(inspect_source(source))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

    def deny_session(*args: object, **kwargs: object) -> None:
        raise AssertionError("locked execution must not create an AWS session")

    monkeypatch.setattr("boto3.Session", deny_session)

    exit_code = main(
        [
            "execute",
            "--plan",
            str(plan_path),
            "--source",
            str(source),
            "--account-id",
            "123456789012",
            "--region",
            "us-east-1",
            "--stack-name",
            "OpenEMR",
            "--confirmation-token",
            "wrong",
        ]
    )

    assert exit_code == 2


def test_ambiguous_task_launch_preserves_stopped_service_and_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _native_backup(tmp_path / "backup.tar")
    plan = create_plan(inspect_source(source))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    state_path = tmp_path / "state" / "receipt.json"
    account_id = "111122223333"
    region = "us-east-1"
    stack_name = "OpenemrEcsStack"
    context = SimpleNamespace(
        account_id=account_id,
        openemr_version=plan.target_openemr_version,
        import_target_mode="fresh-target-only",
        region=region,
        stack_name=stack_name,
        stack_creation_time="2026-08-01T00:00:00+00:00",
        stack_last_updated_time=None,
        cluster_name="openemr-cluster",
        service_name="openemr-service",
        service_url="https://openemr.example.test",
        task_definition_arn="arn:aws:ecs:us-east-1:111122223333:task-definition/import:1",
        staging_bucket="private-staging-bucket",
        staging_kms_key_arn="arn:aws:kms:us-east-1:111122223333:key/test",
        task_security_group_id="sg-0123456789abcdef0",
        private_subnet_ids=("subnet-11111111", "subnet-22222222"),
        database_arn="arn:aws:rds:us-east-1:111122223333:cluster:openemr",
        efs_arn="arn:aws:elasticfilesystem:us-east-1:111122223333:file-system/fs-12345678",
    )
    desired_counts: list[int] = []
    scaling_suspension: list[bool] = []
    cleanup_called = False

    monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: object())
    monkeypatch.setattr("tools.openemr_import.aws.resolve_stack_context", lambda **kwargs: context)
    monkeypatch.setattr("tools.openemr_import.aws.assert_import_resource_bindings", lambda *args, **kwargs: None)
    monkeypatch.setattr("tools.openemr_import.aws.assert_new_import_target", lambda *args, **kwargs: None)
    monkeypatch.setattr("tools.openemr_import.aws.assert_service_stable", lambda *args, **kwargs: 1)
    monkeypatch.setattr("tools.openemr_import.aws.assert_service_autoscaling_active", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "tools.openemr_import.aws.recent_recovery_points",
        lambda *args, **kwargs: (
            SimpleNamespace(recovery_point_arn="arn:backup:rds", creation_date="2026-07-31T00:00:00+00:00"),
            SimpleNamespace(recovery_point_arn="arn:backup:efs", creation_date="2026-07-31T00:00:00+00:00"),
        ),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.acquire_import_lock",
        lambda *args, **kwargs: SimpleNamespace(
            etag='"lock-etag"',
            version_id="lock-version",
        ),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.upload_source",
        lambda *args, **kwargs: f"migrations/{plan.migration_id}/source.tar",
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.set_service_desired_count",
        lambda *args, desired_count, **kwargs: desired_counts.append(desired_count),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.set_service_autoscaling_suspended",
        lambda *args, suspended, **kwargs: scaling_suspension.append(suspended),
    )

    def ambiguous_launch(*args: object, **kwargs: object) -> None:
        raise AwsImportError("malformed ECS launch response")

    def unexpected_cleanup(*args: object, **kwargs: object) -> None:
        nonlocal cleanup_called
        cleanup_called = True

    monkeypatch.setattr("tools.openemr_import.aws.start_import_task", ambiguous_launch)
    monkeypatch.setattr("tools.openemr_import.aws.cleanup_staging_scope", unexpected_cleanup)

    args = argparse.Namespace(
        plan=plan_path,
        source=source,
        account_id=account_id,
        region=region,
        stack_name=stack_name,
        maximum_recovery_age_hours=24,
        maximum_wait_attempts=1,
        allow_aws_execution=True,
        confirm_fresh_target=True,
        confirm_non_production_target=True,
        confirm_downtime=True,
        confirm_recovery_points=True,
        confirm_destructive_import=True,
        confirmation_token=f"IMPORT:{account_id}:{region}:{stack_name}:{plan.configuration_fingerprint}",
        state=state_path,
        profile=None,
    )

    with pytest.raises(ToolError, match="launch outcome is unknown"):
        cli._execute_command(args)

    assert desired_counts == [0]
    assert scaling_suspension == [True]
    assert cleanup_called is False
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "launch-outcome-unknown"
    assert state["original_desired_count"] == 1
    assert state["service_remains_stopped"] is True
    assert state["autoscaling_remains_suspended"] is True


def test_failed_import_abort_restores_service_and_cleans_retry_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, receipt = _execution_context_and_receipt()
    state_path = tmp_path / "state" / "receipt.json"
    write_receipt(state_path, receipt)
    desired_counts: list[int] = []
    autoscaling: list[bool] = []
    cleanup_attempts: list[int] = []
    released: list[object] = []
    status = {
        "worker": {
            "status": "failed",
            "phase": "post-import-validation",
            "rollback_status": "succeeded",
        },
        "task": {
            "last_status": "STOPPED",
            "container_exit_code": 1,
            "identity_verified": True,
        },
        "service": {"desired_count": 0, "running_count": 0, "pending_count": 0},
        "autoscaling": {"active": False, "suspended": True},
    }
    monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: object())
    monkeypatch.setattr(
        "tools.openemr_import.aws.resolve_stack_context",
        lambda **kwargs: context,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.read_remote_status",
        lambda *args, **kwargs: status,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.set_service_desired_count",
        lambda *args, desired_count, **kwargs: desired_counts.append(desired_count),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.set_service_autoscaling_suspended",
        lambda *args, suspended, **kwargs: autoscaling.append(suspended),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.assert_application_healthy",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.start_cleanup_task",
        lambda *args, attempt, **kwargs: cleanup_attempts.append(attempt) or "cleanup-task",
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.wait_for_task",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.cleanup_staging",
        lambda *args, **kwargs: 4,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.release_import_lock",
        lambda *args, lock, **kwargs: released.append(lock),
    )

    result = cli._abort_command(
        argparse.Namespace(
            state=state_path,
            profile=None,
            allow_aws_execution=True,
            confirm_target_baseline_verified=True,
            confirmation_token=f"ABORT:{receipt.migration_id}",
        )
    )

    assert result == 0
    assert desired_counts == [1]
    assert autoscaling == [False]
    assert cleanup_attempts == [1]
    assert released
    tombstone = json.loads(state_path.read_text(encoding="utf-8"))
    assert tombstone["status"] == "abort-complete"
    assert tombstone["next_step"].startswith("Generate a new")


def test_abort_retry_accepts_service_restored_before_state_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, receipt = _execution_context_and_receipt()
    state_path = tmp_path / "state" / "receipt.json"
    write_receipt(state_path, receipt)
    desired_counts: list[int] = []
    monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: object())
    monkeypatch.setattr(
        "tools.openemr_import.aws.resolve_stack_context",
        lambda **kwargs: context,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.read_remote_status",
        lambda *args, **kwargs: {
            "worker": {
                "status": "failed",
                "phase": "post-import-validation",
                "rollback_status": "succeeded",
            },
            "task": {
                "last_status": "STOPPED",
                "identity_verified": True,
                "container_exit_code": 1,
            },
            "service": {
                "desired_count": receipt.original_desired_count,
                "running_count": receipt.original_desired_count,
                "pending_count": 0,
            },
            "autoscaling": {"active": False, "suspended": True},
        },
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.set_service_desired_count",
        lambda *args, desired_count, **kwargs: desired_counts.append(desired_count),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.assert_application_healthy",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.set_service_autoscaling_suspended",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.start_cleanup_task",
        lambda *args, **kwargs: "cleanup-task",
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.wait_for_task",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.cleanup_staging",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.release_import_lock",
        lambda *args, **kwargs: None,
    )

    result = cli._abort_command(
        argparse.Namespace(
            state=state_path,
            profile=None,
            allow_aws_execution=True,
            confirm_target_baseline_verified=True,
            confirmation_token=f"ABORT:{receipt.migration_id}",
        )
    )

    assert result == 0
    assert desired_counts == []
    assert read_private_json(state_path, label="test state")["status"] == ("abort-complete")


def test_interrupted_mutation_recovery_restores_local_baseline_and_stays_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, receipt = _execution_context_and_receipt()
    state_path = tmp_path / "state" / "receipt.json"
    write_receipt(state_path, receipt)
    monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: object())
    monkeypatch.setattr(
        "tools.openemr_import.aws.resolve_stack_context",
        lambda **kwargs: context,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.assert_import_resource_bindings",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.read_remote_status",
        lambda *args, **kwargs: {
            "worker": {"status": "running", "phase": "database-import"},
            "task": {
                "last_status": "STOPPED",
                "identity_verified": True,
                "container_exit_code": 137,
            },
            "service": {
                "desired_count": 0,
                "running_count": 0,
                "pending_count": 0,
            },
            "autoscaling": {"suspended": True},
        },
    )
    launched: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "tools.openemr_import.aws.start_recovery_task",
        lambda context, *, session, migration_id, attempt: (
            launched.append((migration_id, attempt)) or "arn:aws:ecs:us-east-1:111122223333:task/recovery"
        ),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.wait_for_task",
        lambda *args, **kwargs: 0,
    )

    result = cli._recover_command(
        argparse.Namespace(
            state=state_path,
            profile=None,
            allow_aws_execution=True,
            confirm_restore_local_baseline=True,
            confirmation_token=f"RECOVER:{receipt.migration_id}",
        )
    )

    recovered = read_private_json(state_path, label="test state")
    assert result == 0
    assert launched == [(receipt.migration_id, 1)]
    assert recovered["recovery_status"] == "complete"
    assert recovered["recovery_attempt"] == 1


def test_finalize_retry_accepts_already_active_autoscaling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, receipt = _execution_context_and_receipt()
    state_path = tmp_path / "state" / "receipt.json"
    write_receipt(state_path, receipt)
    status = {
        "worker": {"status": "succeeded"},
        "task": {
            "last_status": "STOPPED",
            "identity_verified": True,
            "container_exit_code": 0,
        },
        "service": {
            "desired_count": receipt.original_desired_count,
            "running_count": receipt.original_desired_count,
            "pending_count": 0,
        },
        "autoscaling": {"active": True, "suspended": False},
    }
    monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: object())
    monkeypatch.setattr(
        "tools.openemr_import.aws.resolve_stack_context",
        lambda **kwargs: context,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.read_remote_status",
        lambda *args, **kwargs: status,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.assert_application_healthy",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.set_service_autoscaling_suspended",
        lambda *args, **kwargs: pytest.fail("already-active autoscaling must not be mutated"),
    )

    assert (
        cli._finalize_command(
            argparse.Namespace(
                state=state_path,
                profile=None,
                allow_aws_execution=True,
                confirmation_token=f"FINALIZE:{receipt.migration_id}",
            )
        )
        == 0
    )


def test_cleanup_reuses_uncertain_attempt_then_increments_known_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, receipt = _execution_context_and_receipt()
    state_path = tmp_path / "state" / "receipt.json"
    atomic_write_private_json(
        state_path,
        {
            **asdict(receipt),
            "cleanup_status": "in-progress",
            "cleanup_attempt": 2,
        },
        label="test state",
    )
    status = {
        "worker": {"status": "succeeded"},
        "task": {
            "last_status": "STOPPED",
            "identity_verified": True,
            "container_exit_code": 0,
        },
        "service": {
            "desired_count": receipt.original_desired_count,
            "running_count": receipt.original_desired_count,
            "pending_count": 0,
        },
        "autoscaling": {"active": True, "suspended": False},
    }
    attempts: list[int] = []
    wait_results = iter((1, 0))
    monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: object())
    monkeypatch.setattr(
        "tools.openemr_import.aws.resolve_stack_context",
        lambda **kwargs: context,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.read_remote_status",
        lambda *args, **kwargs: status,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.assert_application_healthy",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.start_cleanup_task",
        lambda *args, attempt, **kwargs: attempts.append(attempt) or "cleanup-task",
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.wait_for_task",
        lambda *args, **kwargs: next(wait_results),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.cleanup_staging",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.release_import_lock",
        lambda *args, **kwargs: None,
    )
    args = argparse.Namespace(
        state=state_path,
        profile=None,
        allow_aws_execution=True,
        confirm_delete_rollback_copy=True,
        confirmation_token=f"CLEANUP:{receipt.migration_id}",
    )

    with pytest.raises(ToolError, match="cleanup task failed"):
        cli._cleanup_command(args)
    failed = read_private_json(state_path, label="test state")
    assert failed["cleanup_status"] == "failed"
    assert failed["cleanup_attempt"] == 2

    assert cli._cleanup_command(args) == 0
    assert attempts == [2, 3]


def test_uncertain_launch_reconciliation_recovers_task_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, receipt = _execution_context_and_receipt()
    state_path = tmp_path / "state" / "unknown.json"
    atomic_write_private_json(
        state_path,
        {
            "schema_version": 1,
            "migration_id": receipt.migration_id,
            "status": "launch-outcome-unknown",
            "account_id": context.account_id,
            "region": context.region,
            "stack_name": context.stack_name,
            "stack_creation_time": context.stack_creation_time,
            "stack_last_updated_time": context.stack_last_updated_time,
            "cluster_name": context.cluster_name,
            "service_name": context.service_name,
            "service_url": context.service_url,
            "openemr_version": context.openemr_version,
            "original_desired_count": receipt.original_desired_count,
            "task_definition_arn": context.task_definition_arn,
            "staging_bucket": context.staging_bucket,
            "staging_kms_key_arn": context.staging_kms_key_arn,
            "task_security_group_id": context.task_security_group_id,
            "private_subnet_ids": list(context.private_subnet_ids),
            "database_arn": context.database_arn,
            "efs_arn": context.efs_arn,
            "source_key": receipt.source_key,
            "recovery_point_arns": list(receipt.recovery_point_arns),
            "recovery_point_dates": list(receipt.recovery_point_dates),
            "lock_etag": receipt.lock_etag,
            "lock_version_id": receipt.lock_version_id,
            "outcome_unknown_at": "2026-08-01T00:05:00+00:00",
        },
    )
    monkeypatch.setattr(cli, "_boto3_session", lambda **kwargs: object())
    monkeypatch.setattr(
        "tools.openemr_import.aws.resolve_stack_context",
        lambda **kwargs: context,
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.find_import_tasks",
        lambda *args, **kwargs: (
            {
                "taskArn": receipt.task_arn,
                "taskDefinitionArn": receipt.task_definition_arn,
                "startedBy": receipt.migration_id,
            },
        ),
    )
    monkeypatch.setattr(
        "tools.openemr_import.aws.read_remote_status",
        lambda *args, **kwargs: {"worker": {"status": "running"}},
    )

    result = cli._reconcile_launch_command(
        argparse.Namespace(
            state=state_path,
            profile=None,
            allow_aws_execution=True,
            confirm_no_task_launched=False,
            minimum_unknown_age_minutes=15,
            maximum_unknown_age_minutes=45,
            confirmation_token=f"RECONCILE:{receipt.migration_id}",
        )
    )

    assert result == 0
    reconciled = read_receipt(state_path)
    assert reconciled.task_arn == receipt.task_arn
    assert reconciled.lock_etag == receipt.lock_etag


def test_plan_json_rejects_truthy_strings_for_safety_flags(tmp_path: Path) -> None:
    source = _native_backup(tmp_path / "backup.tar")
    data = create_plan(inspect_source(source)).to_dict()
    data["execution_allowed"] = "false"

    with pytest.raises(ToolError, match="malformed"):
        plan_from_dict(data)


def test_policy_target_uses_constants_without_importing_stack() -> None:
    """The planner follows the deployment pin without constructing a CDK stack."""

    repository = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from openemr_ecs.constants import StackConstants; "
                "from tools.openemr_import.plan import TARGET_OPENEMR_VERSION; "
                "assert TARGET_OPENEMR_VERSION == StackConstants.OPENEMR_VERSION; "
                "assert 'openemr_ecs.stack' not in sys.modules"
            ),
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
