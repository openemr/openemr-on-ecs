"""Unit tests for the isolated import worker's filesystem boundaries."""

from __future__ import annotations

import base64
import gzip
import importlib.util
import io
import json
import os
import re
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_VALID_SEVEN_KEY = b"007" + base64.b64encode(b"k" * 112)
_VERSION_PHP = b"<?php $v_major='8'; $v_minor='2'; $v_patch='0'; " b"$v_tag=''; $v_realpatch='0'; $v_database=541;"
_VERSION_SQL = (
    b"CREATE TABLE `version` (`v_major` int, `v_minor` int, `v_patch` int, "
    b"`v_realpatch` int, `v_tag` varchar(31), `v_database` int, `v_acl` int);\n"
    b"INSERT INTO `version` VALUES (8,2,0,0,'',541,13);\n"
)


def _worker() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "tools" / "openemr-import-worker" / "worker.py"
    spec = importlib.util.spec_from_file_location("openemr_import_worker", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _add(
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


def _sites(
    path: Path,
    *,
    unsafe_upload: bool = False,
    active_document: tuple[str, bytes] | None = None,
    invalid_keys: bool = False,
) -> Path:
    key_material = b"not-an-openemr-key" if invalid_keys else _VALID_SEVEN_KEY
    with tarfile.open(path, mode="w:gz") as archive:
        _add(
            archive,
            "version.php",
            _VERSION_PHP,
        )
        _add(archive, "sites/default/sqlconf.php", b"source-credential")
        _add(archive, "sites/default/config.php", b"source-config")
        _add(archive, "sites/default/documents/patient.pdf", b"patient")
        _add(
            archive,
            "sites/default/documents/logs_and_misc/methods/sevena",
            key_material,
        )
        _add(
            archive,
            "sites/default/documents/logs_and_misc/methods/sevenb",
            key_material,
        )
        _add(archive, "sites/default/images/logo.png", b"logo")
        _add(archive, "sites/default/referral_template.html", b"template")
        if unsafe_upload:
            _add(
                archive,
                "sites/default/documents/payload.php",
                b"<?php echo 1;",
            )
        if active_document is not None:
            _add(
                archive,
                f"sites/default/documents/{active_document[0]}",
                active_document[1],
            )
    return path


def test_worker_extracts_only_data_allowlist_and_never_source_credentials(
    tmp_path: Path,
) -> None:
    worker = _worker()
    destination = tmp_path / "import" / "default"
    destination.mkdir(parents=True)

    version = worker._validate_and_extract_sites(
        _sites(tmp_path / "sites.tar.gz"),
        destination,
    )

    assert version == ("8.2.0", 541)
    assert (destination / "documents" / "patient.pdf").read_bytes() == b"patient"
    assert (destination / "images" / "logo.png").read_bytes() == b"logo"
    assert (destination / "referral_template.html").read_bytes() == b"template"
    assert not (destination / "sqlconf.php").exists()
    assert not (destination / "config.php").exists()
    assert b"source-credential" not in b"".join(path.read_bytes() for path in destination.rglob("*") if path.is_file())


def test_worker_rejects_script_like_content_in_imported_data(tmp_path: Path) -> None:
    worker = _worker()
    destination = tmp_path / "import" / "default"
    destination.mkdir(parents=True)

    with pytest.raises(worker.ImportFailure, match="custom-executable-content"):
        worker._validate_and_extract_sites(
            _sites(tmp_path / "sites.tar.gz", unsafe_upload=True),
            destination,
        )


@pytest.mark.parametrize(
    ("name", "content"),
    (
        ("payload.phtml", b"plain"),
        ("disguised.jpg", b"#!/bin/sh\n"),
        ("binary.dat", b"\x7fELF\x02\x01"),
        ("windows.exe", b"plain"),
        ("active-document.html", b"<p>plain HTML</p>"),
        ("event-handler.jpg", b"<svg onload=alert(1)>"),
        ("late-marker.pdf", b"A" * 5000 + b"\n<?php echo 1;"),
        (
            "boundary-handler.jpg",
            b"A" * (1024 * 1024 - 30) + b"<svg onabcdefghijklmnopqrstuvwxy=alert(1)>",
        ),
    ),
)
def test_worker_rejects_broader_active_content(
    tmp_path: Path,
    name: str,
    content: bytes,
) -> None:
    worker = _worker()
    destination = tmp_path / "import" / "default"
    destination.mkdir(parents=True)

    with pytest.raises(worker.ImportFailure, match="custom-executable-content"):
        worker._validate_and_extract_sites(
            _sites(
                tmp_path / "sites.tar.gz",
                active_document=(name, content),
            ),
            destination,
        )


def test_worker_rejects_native_outer_compression_bomb(tmp_path: Path) -> None:
    worker = _worker()
    source = tmp_path / "source.tar.gz"
    with tarfile.open(source, mode="w:gz") as archive:
        _add(archive, "openemr.sql.gz", gzip.compress(b"-- MySQL dump\n" + _VERSION_SQL))
        _add(archive, "openemr.tar.gz", b"sites")
        _add(archive, "padding.bin", b"\0" * (2 * 1024 * 1024))
    work = tmp_path / "work"
    work.mkdir()

    with pytest.raises(worker.ImportFailure, match="native-backup-compression-ratio"):
        worker._unpack_native_source(source, work)


def test_worker_rejects_malformed_current_encryption_keys(tmp_path: Path) -> None:
    worker = _worker()
    destination = tmp_path / "import" / "default"
    destination.mkdir(parents=True)

    with pytest.raises(worker.ImportFailure, match="invalid-document-encryption-keys"):
        worker._validate_and_extract_sites(
            _sites(tmp_path / "sites.tar.gz", invalid_keys=True),
            destination,
        )


def test_site_swap_preserves_target_configuration_and_rollback_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    mount_root = tmp_path / "mount"
    target = mount_root / "default"
    (target / "documents" / "certificates").mkdir(parents=True)
    (target / "config.php").write_bytes(b"target-config")
    (target / "sqlconf.php").write_bytes(b"target-credential")
    (target / "documents" / "certificates" / "mysql-ca").write_bytes(b"target-ca")
    (target / "documents" / "fresh-placeholder").write_bytes(b"old")

    imported = tmp_path / "imported"
    (imported / "documents" / "logs_and_misc" / "methods").mkdir(parents=True)
    (imported / "documents" / "patient.pdf").write_bytes(b"patient")
    (imported / "documents" / "logs_and_misc" / "methods" / "sevena").write_bytes(_VALID_SEVEN_KEY)
    (imported / "documents" / "logs_and_misc" / "methods" / "sevenb").write_bytes(_VALID_SEVEN_KEY)

    worker._stage_and_swap_sites(imported, mount_root, "import-0123456789abcdef")

    assert (target / "config.php").read_bytes() == b"target-config"
    assert (target / "sqlconf.php").read_bytes() == b"target-credential"
    assert (target / "documents" / "patient.pdf").read_bytes() == b"patient"
    assert (target / "documents").stat().st_mode & 0o777 == 0o770
    assert (target / "documents" / "patient.pdf").stat().st_mode & 0o777 == 0o660
    assert (target / "documents" / "certificates" / "mysql-ca").read_bytes() == b"target-ca"
    assert not (target / "documents" / "fresh-placeholder").exists()
    backup = mount_root / ".openemr-import-backup" / "import-0123456789abcdef" / "default"
    assert (backup / "documents" / "fresh-placeholder").read_bytes() == b"old"

    monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(mount_root))
    worker._cleanup_site_artifacts(
        "import-0123456789abcdef",
        delete_backup=True,
    )
    worker._cleanup_site_artifacts(
        "import-0123456789abcdef",
        delete_backup=True,
    )
    assert not backup.exists()


def test_sql_validation_is_bounded_and_recognizes_native_dump(tmp_path: Path) -> None:
    worker = _worker()
    source = tmp_path / "openemr.sql.gz"
    source.write_bytes(
        gzip.compress(
            b"-- MySQL dump\n"
            + _VERSION_SQL
            + b"CREATE TABLE `patient_data` (`id` int);\n"
            + b"INSERT INTO `patient_data` VALUES (1);\n"
        )
    )
    output = tmp_path / "validated.sql"

    assert worker._validate_sql(source, output) == ("8.2.0", 541)

    assert output.read_bytes().startswith(b"-- MySQL dump")


def test_fresh_target_check_rejects_rows_in_every_non_seed_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()

    def run_mysql(*args: object, **kwargs: object) -> str:
        command = " ".join(str(value) for value in args)
        if "SHOW TABLES" in command:
            return "documents\nform_encounter\npatient_data\nusers\npatient_tracker\n"
        if "FROM version" in command:
            return "8\t3\t0\t0\t\t541\n"
        if "patient_tracker" in command:
            return "1\n"
        return "0\n"

    monkeypatch.setattr(worker, "_run_mysql", run_mysql)

    with pytest.raises(worker.ImportFailure, match="target-is-not-empty"):
        worker._assert_empty_target("8.3.0", 541)


def test_fresh_target_check_rejects_modified_seed_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()

    def run_mysql(*args: object, **kwargs: object) -> str:
        command = " ".join(str(value) for value in args)
        if "SHOW TABLES" in command:
            return "documents\nform_encounter\nglobals\npatient_data\nusers\n"
        if "FROM version" in command:
            return "8\t3\t0\t0\t\t541\n"
        if "COUNT(*) FROM `globals`" in command:
            return "487\n"
        return "0\n"

    monkeypatch.setattr(worker, "_run_mysql", run_mysql)
    monkeypatch.setattr(
        worker,
        "_seed_table_fingerprint",
        lambda database, table: "0" * 64,
    )

    with pytest.raises(
        worker.ImportFailure,
        match="target-seed-content-mismatch",
    ):
        worker._assert_empty_target("8.3.0", 541)


def test_seed_manifest_excludes_nondeterministic_globals_values() -> None:
    worker = _worker()
    baseline = worker.FRESH_SEED_BASELINE["globals"]

    assert baseline.get("exclude_columns") == ["gl_value"]
    assert isinstance(baseline.get("sha256"), str)


def test_seed_manifest_limits_unfingerprinted_tables_to_bootstrap_identity() -> None:
    worker = _worker()

    unfingerprinted = {
        table for table, baseline in worker.FRESH_SEED_BASELINE.items() if baseline.get("sha256") is None
    }

    assert unfingerprinted == {
        "facility",
        "gacl_aro",
        "groups",
        "users",
        "users_secure",
    }


def test_fresh_target_check_rejects_seed_row_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()

    def run_mysql(*args: object, **kwargs: object) -> str:
        command = " ".join(str(value) for value in args)
        if "SHOW TABLES" in command:
            return "documents\nform_encounter\nlist_options\npatient_data\nusers\n"
        if "FROM version" in command:
            return "8\t3\t0\t0\t\t541\n"
        if "COUNT(*) FROM `list_options`" in command:
            return "5604\n"
        return "0\n"

    monkeypatch.setattr(worker, "_run_mysql", run_mysql)

    with pytest.raises(
        worker.ImportFailure,
        match="target-seed-row-count-mismatch",
    ):
        worker._assert_empty_target("8.3.0", 541)


def test_baseline_dump_rejects_stored_code_and_skips_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    for name, value in {
        "MYSQL_HOST": "database",
        "MYSQL_PORT": "3306",
        "MYSQL_USERNAME": "importer",
        "MYSQL_PASSWORD": "secret",
        "MYSQL_SSL_CA": "/certs/ca.pem",
        "MYSQL_DATABASE": "openemr",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: "1")

    with pytest.raises(
        worker.ImportFailure,
        match="target-baseline-has-stored-code",
    ):
        worker._dump_target_database(tmp_path / "baseline.sql")

    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        destination = kwargs["stdout"]
        assert isinstance(destination, io.BufferedWriter)
        destination.write(b"-- baseline\n")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: "0")
    monkeypatch.setattr(worker.subprocess, "run", run)
    worker._dump_target_database(tmp_path / "clean-baseline.sql")

    assert "--skip-routines" in commands[0]
    assert "--skip-triggers" in commands[0]
    assert "--skip-events" in commands[0]
    assert "--routines" not in commands[0]
    assert "--triggers" not in commands[0]
    assert "--events" not in commands[0]


def test_sql_validation_rejects_client_commands_anywhere_in_dump(tmp_path: Path) -> None:
    worker = _worker()
    source = tmp_path / "openemr.sql"
    source.write_bytes(
        b"-- MySQL dump\nCREATE TABLE `patient_data` (`id` int);\n"
        + (b"-- padding\n" * (worker.CHUNK_SIZE // 5))
        + b"\\! touch /tmp/escaped\n"
    )

    with pytest.raises(worker.ImportFailure, match="unsafe-sql-client-command"):
        worker._validate_sql(source, tmp_path / "validated.sql")


@pytest.mark.parametrize(
    "stored_code",
    (
        b"CREATE DEFINER=`root`@`%` TRIGGER patient_trigger "
        b"BEFORE INSERT ON patient_data FOR EACH ROW SET NEW.pid = NEW.pid;\n",
        b"CREATE/**/PROCEDURE unsafe_proc() SELECT 1;\n",
        b"CREATE/* split keyword */TRIGGER unsafe_trigger "
        b"BEFORE INSERT ON patient_data FOR EACH ROW SET NEW.pid = NEW.pid;\n",
        b"/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`%`*/ "
        b"/*!50003 TRIGGER unsafe_trigger BEFORE INSERT ON patient_data "
        b"FOR EACH ROW SET NEW.pid = NEW.pid */;\n",
    ),
)
def test_sql_validation_rejects_stored_executable_definitions(
    tmp_path: Path,
    stored_code: bytes,
) -> None:
    worker = _worker()
    source = tmp_path / "openemr.sql"
    source.write_bytes(b"-- MySQL dump\n" + _VERSION_SQL + stored_code)

    with pytest.raises(worker.ImportFailure, match="unsupported-sql-stored-code"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_validation_rejects_client_command_after_semicolon(
    tmp_path: Path,
) -> None:
    worker = _worker()
    source = tmp_path / "openemr.sql"
    source.write_bytes(
        b"-- MySQL dump\n" + _VERSION_SQL + b"CREATE TABLE patient_data (pid bigint); system touch /tmp/unsafe\n"
    )

    with pytest.raises(worker.ImportFailure, match="unsafe-sql-client-command"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_version_identity_ignores_commented_insert(tmp_path: Path) -> None:
    worker = _worker()
    source = tmp_path / "openemr.sql"
    source.write_bytes(
        b"-- MySQL dump\n-- " + _VERSION_SQL.splitlines()[1] + b"\nCREATE TABLE patient_data (pid bigint);\n"
    )

    with pytest.raises(worker.ImportFailure, match="missing-sql-version-row"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_version_identity_ignores_block_commented_insert(
    tmp_path: Path,
) -> None:
    worker = _worker()
    source = tmp_path / "openemr.sql"
    source.write_bytes(
        b"-- MySQL dump\n/* hidden version row\n" + _VERSION_SQL + b"*/\nCREATE TABLE patient_data (pid bigint);\n"
    )

    with pytest.raises(worker.ImportFailure, match="missing-sql-version-row"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_version_identity_ignores_insert_inside_quoted_value(
    tmp_path: Path,
) -> None:
    worker = _worker()
    source = tmp_path / "openemr.sql"
    source.write_bytes(
        b"-- MySQL dump\nCREATE TABLE notes (body text);\n"
        b"INSERT INTO notes VALUES ('prefix\n" + _VERSION_SQL.replace(b"'", b"\\'") + b"suffix');\n"
    )

    with pytest.raises(worker.ImportFailure, match="missing-sql-version-row"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_site_validation_requires_canonical_nonempty_encryption_keys(tmp_path: Path) -> None:
    worker = _worker()
    source = tmp_path / "sites.tar.gz"
    with tarfile.open(source, mode="w:gz") as archive:
        _add(
            archive,
            "version.php",
            _VERSION_PHP,
        )
        _add(archive, "sites/default/sqlconf.php", b"source-credential")
        _add(archive, "sites/default/documents/patient.pdf", b"patient")
        _add(archive, "sites/default/documents/decoy/sevena", b"a")
        _add(archive, "sites/default/documents/decoy/sevenb", b"b")

    destination = tmp_path / "import" / "default"
    destination.mkdir(parents=True)
    with pytest.raises(worker.ImportFailure, match="missing-document-encryption-keys"):
        worker._validate_and_extract_sites(source, destination)


def test_efs_mutability_probe_is_removed_after_atomic_rename(tmp_path: Path) -> None:
    worker = _worker()
    mount_root = tmp_path / "mount"
    (mount_root / "default").mkdir(parents=True)

    worker._assert_efs_mutable(mount_root, "import-0123456789abcdef")

    assert not list(mount_root.glob(".openemr-import-probe-*"))


def test_fresh_efs_check_rejects_existing_documents_but_allows_baseline_files(
    tmp_path: Path,
) -> None:
    worker = _worker()
    mount_root = tmp_path / "mount"
    methods = mount_root / "default" / "documents" / "logs_and_misc" / "methods"
    methods.mkdir(parents=True)
    (methods / "sevena").write_bytes(_VALID_SEVEN_KEY)
    certificates = mount_root / "default" / "documents" / "certificates"
    certificates.mkdir()
    (certificates / "mysql-ca").write_text("ca", encoding="utf-8")

    worker._assert_fresh_efs_target(mount_root)

    (mount_root / "default" / "documents" / "patient.pdf").write_bytes(b"patient")
    with pytest.raises(worker.ImportFailure, match="target-site-is-not-empty"):
        worker._assert_fresh_efs_target(mount_root)


def test_automatic_rollback_restores_site_and_database_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    migration_id = "import-0123456789abcdef"
    mount_root = tmp_path / "mount"
    target = mount_root / "default"
    target.mkdir(parents=True)
    (target / "state").write_text("imported", encoding="utf-8")
    backup = mount_root / ".openemr-import-backup" / migration_id / "default"
    backup.mkdir(parents=True)
    (backup / "state").write_text("original", encoding="utf-8")
    baseline = backup.parent / "target-baseline.sql"
    baseline.write_text("-- baseline", encoding="utf-8")
    restored: list[Path] = []
    monkeypatch.setattr(
        worker,
        "_restore_baseline_database",
        lambda path: restored.append(path),
    )
    monkeypatch.setattr(worker, "_drop_import_user", lambda migration_id: None)

    assert worker._attempt_automatic_rollback(
        mount_root=mount_root,
        migration_id=migration_id,
        baseline_sql=baseline,
        database_mutation_started=True,
    )
    assert (target / "state").read_text(encoding="utf-8") == "original"
    assert restored == [baseline]


def test_hard_termination_recovery_restores_and_revalidates_local_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    migration_id = "import-0123456789abcdef"
    mount_root = tmp_path / "mount"
    baseline = mount_root / ".openemr-import-backup" / migration_id / "target-baseline.sql"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("-- baseline", encoding="utf-8")
    monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(mount_root))
    monkeypatch.setattr(
        worker,
        "_attempt_automatic_rollback",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        worker,
        "_database_version_identity",
        lambda database: ("8.2.0", 541),
    )
    checked: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        worker,
        "_assert_empty_target",
        lambda *args: checked.append(args),
    )
    monkeypatch.setattr(
        worker,
        "_assert_fresh_efs_target",
        lambda path: checked.append((path,)),
    )

    worker._recover_local_baseline(migration_id)

    assert checked == [("8.2.0", 541), (mount_root.resolve(),)]


def test_site_recovery_resumes_after_interruption_between_atomic_renames(
    tmp_path: Path,
) -> None:
    worker = _worker()
    migration_id = "import-0123456789abcdef"
    mount_root = tmp_path / "mount"
    backup = mount_root / ".openemr-import-backup" / migration_id / "default"
    failed = mount_root / ".openemr-import-staging" / migration_id / "failed-default"
    backup.mkdir(parents=True)
    failed.mkdir(parents=True)
    (backup / "state").write_text("original", encoding="utf-8")
    (failed / "state").write_text("imported", encoding="utf-8")

    worker._restore_site_backup(mount_root, migration_id)

    assert (mount_root / "default" / "state").read_text(encoding="utf-8") == "original"
    assert (failed / "state").read_text(encoding="utf-8") == "imported"


def test_worker_status_records_timestamped_phase_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    monkeypatch.setenv("IMPORT_STAGING_BUCKET_OWNER", "123456789012")
    monkeypatch.setenv(
        "IMPORT_STAGING_KMS_KEY_ARN",
        "arn:aws:kms:us-east-1:123456789012:key/example",
    )

    class StatusS3:
        arguments: dict[str, Any] | None = None

        def put_object(self, **kwargs: Any) -> None:
            self.arguments = kwargs

    client = StatusS3()
    worker._put_status(
        client,
        bucket="private-bucket",
        migration_id="import-0123456789abcdef",
        status="running",
        phase="source-validation",
    )

    assert client.arguments is not None
    payload = json.loads(client.arguments["Body"])
    assert payload["phase"] == "source-validation"
    assert payload["status"] == "running"
    assert datetime.fromisoformat(payload["updated_at"]).tzinfo is not None


def test_database_command_never_contains_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    monkeypatch.setenv("MYSQL_HOST", "db.internal")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USERNAME", "admin")
    monkeypatch.setenv("MYSQL_PASSWORD", "must-not-appear")
    monkeypatch.setenv("MYSQL_SSL_CA", "/ca.pem")

    command = worker._mysql_command("openemr")

    assert "must-not-appear" not in " ".join(command)
    assert "--binary-mode" in command
    assert "--local-infile=0" in command
    assert "--sandbox" in command
    assert "--ssl-verify-server-cert" in command
    assert os.environ["MYSQL_PASSWORD"] == "must-not-appear"


def test_status_object_binds_bucket_owner_and_customer_managed_kms_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    key_arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
    monkeypatch.setenv("IMPORT_STAGING_BUCKET_OWNER", "123456789012")
    monkeypatch.setenv("IMPORT_STAGING_KMS_KEY_ARN", key_arn)

    class S3:
        arguments: dict[str, Any] | None = None

        def put_object(self, **kwargs: Any) -> None:
            self.arguments = kwargs

    s3 = S3()
    worker._put_status(
        s3,
        bucket="staging",
        migration_id="import-0123456789abcdef",
        status="running",
        phase="download",
    )

    assert s3.arguments is not None
    assert s3.arguments["ExpectedBucketOwner"] == "123456789012"
    assert s3.arguments["ServerSideEncryption"] == "aws:kms"
    assert s3.arguments["SSEKMSKeyId"] == key_arn


def test_database_dump_runs_as_temporary_database_scoped_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    sql = tmp_path / "validated.sql"
    sql.write_bytes(b"CREATE TABLE patient_data (id int);\n")
    calls: list[dict[str, object]] = []

    def fake_run_mysql(
        *extra: str,
        stdin: Any = None,
        username: str | None = None,
        password: str | None = None,
    ) -> str:
        body = stdin.read() if hasattr(stdin, "read") else b""
        calls.append(
            {
                "extra": extra,
                "body": body,
                "username": username,
                "password": password,
            }
        )
        return ""

    monkeypatch.setattr(worker, "_run_mysql", fake_run_mysql)
    monkeypatch.setenv("MYSQL_DATABASE", "openemr")

    worker._replace_database(sql, "import-0123456789abcdef")

    assert len(calls) == 4
    setup = calls[1]
    setup_body = setup["body"]
    assert isinstance(setup_body, bytes)
    assert b"GRANT ALL PRIVILEGES ON `openemr`.*" in setup_body
    assert b"REQUIRE SSL" in setup_body
    import_call = calls[2]
    assert import_call["extra"] == ("openemr",)
    assert import_call["username"] == "oe_import_0123456789abcdef"
    assert import_call["password"]
    assert import_call["body"] == sql.read_bytes()
    assert calls[3]["body"] == b"DROP USER IF EXISTS 'oe_import_0123456789abcdef'@'%';\n"


def test_temporary_database_user_cleanup_runs_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    sql = tmp_path / "validated.sql"
    sql.write_bytes(b"CREATE TABLE patient_data (id int);\n")
    calls: list[bytes] = []

    def fake_run_mysql(
        *extra: str,
        stdin: Any = None,
        username: str | None = None,
        password: str | None = None,
    ) -> str:
        del extra, username, password
        body = stdin.read() if hasattr(stdin, "read") else b""
        calls.append(body)
        if len(calls) == 2:
            raise worker.ImportFailure("database-command-failed")
        return ""

    monkeypatch.setattr(worker, "_run_mysql", fake_run_mysql)
    monkeypatch.setenv("MYSQL_DATABASE", "openemr")

    with pytest.raises(worker.ImportFailure, match="database-command-failed"):
        worker._replace_database(sql, "import-0123456789abcdef")

    assert len(calls) == 3
    assert calls[-1] == b"DROP USER IF EXISTS 'oe_import_0123456789abcdef'@'%';\n"


_DOCKERFILE = Path(__file__).resolve().parents[2] / "tools" / "openemr-import-worker" / "Dockerfile"


def _dockerfile_instructions() -> list[str]:
    """Return Dockerfile instructions with comments dropped and continuations joined."""
    instructions: list[str] = []
    pending = ""
    for raw in _DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        instructions.append((pending + line).strip())
        pending = ""
    assert not pending, "Dockerfile ends with a dangling line continuation"
    return instructions


def _apk_add_packages(instructions: list[str]) -> list[str]:
    packages: list[str] = []
    for instruction in instructions:
        if not instruction.startswith("RUN "):
            continue
        for command in re.split(r"\s*(?:&&|\|\||;)\s*", instruction[len("RUN ") :]):
            tokens = command.split()
            if tokens[:2] != ["apk", "add"]:
                continue
            packages.extend(token for token in tokens[2:] if not token.startswith("-"))
    return packages


def test_import_worker_base_image_is_digest_pinned() -> None:
    instructions = _dockerfile_instructions()
    base = [line for line in instructions if line.startswith("FROM ") and " AS base" in line]

    assert len(base) == 1
    assert re.search(r"@sha256:[0-9a-f]{64}\b", base[0]), base[0]


def test_import_worker_apk_packages_are_not_exactly_pinned() -> None:
    """Alpine mirrors drop superseded package builds, so exact pins break the build.

    The base image digest fixes the Alpine release branch; within it, apk only
    ever resolves the branch's current security/bugfix rebuild of each package.
    An exact ``package=version`` pin adds no reproducibility and fails the build
    the moment upstream ships a rebuild (see the September 2026 curl breakage).
    """
    packages = _apk_add_packages(_dockerfile_instructions())

    assert set(packages) == {"ca-certificates", "curl", "mariadb-client"}
    pinned = [package for package in packages if any(operator in package for operator in ("=", "<", ">", "~"))]
    assert not pinned, f"apk version constraints break once Alpine mirrors drop the superseded build: {pinned}"
