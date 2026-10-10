"""Coverage for the import worker's database, EFS, rollback, and entrypoint guards."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_VALID_SEVEN_KEY = b"007" + base64.b64encode(b"k" * 112)
_WORKER_PATH = Path(__file__).resolve().parents[2] / "tools" / "openemr-import-worker" / "worker.py"
_MIGRATION_ID = "import-0123456789abcdef"
_KMS_KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
_DATABASE_ENV = {
    "MYSQL_HOST": "db.internal",
    "MYSQL_PORT": "3306",
    "MYSQL_USERNAME": "admin",
    "MYSQL_PASSWORD": "must-not-appear",
    "MYSQL_SSL_CA": "/certs/ca.pem",
    "MYSQL_DATABASE": "openemr",
}


@pytest.fixture
def worker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("openemr_import_worker", _WORKER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _DATABASE_ENV.items():
        monkeypatch.setenv(name, value)


def _write_keys(default_site: Path) -> None:
    methods = default_site / "documents" / "logs_and_misc" / "methods"
    methods.mkdir(parents=True, exist_ok=True)
    (methods / "sevena").write_bytes(_VALID_SEVEN_KEY)
    (methods / "sevenb").write_bytes(_VALID_SEVEN_KEY)


class _Recorder:
    """Fake ``subprocess.run`` recording each invocation."""

    def __init__(self, *, returncode: int = 0, stdout: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, b"stderr-not-published")


# mysql client invocation


@pytest.mark.parametrize("missing", ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USERNAME", "MYSQL_PASSWORD", "MYSQL_SSL_CA"))
def test_mysql_command_requires_complete_database_configuration(
    worker: ModuleType,
    database_env: None,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.setenv(missing, "")

    with pytest.raises(worker.ImportFailure, match="missing-database-configuration"):
        worker._mysql_command()


def test_mysql_command_uses_username_override(worker: ModuleType, database_env: None) -> None:
    command = worker._mysql_command("openemr", username="oe_import_x")

    assert command[0] == "mariadb"
    assert "--user=oe_import_x" in command
    assert "--user=admin" not in command
    assert command[-1] == "openemr"


def test_run_mysql_without_stdin_passes_password_only_via_environment(
    worker: ModuleType,
    database_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _Recorder(stdout=b"  result\n")
    monkeypatch.setattr(worker.subprocess, "run", run)
    monkeypatch.setenv("HOME", "/home/test")

    assert worker._run_mysql("openemr", "--execute=SELECT 1") == "result"

    command, kwargs = run.calls[0]
    assert command[-2:] == ["openemr", "--execute=SELECT 1"]
    assert "must-not-appear" not in " ".join(command)
    assert kwargs["env"]["MYSQL_PWD"] == "must-not-appear"
    assert kwargs["env"]["HOME"] == "/home/test"
    assert set(kwargs["env"]) == {"HOME", "PATH", "MYSQL_PWD"}
    assert "stdin" not in kwargs
    assert "input" not in kwargs
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 7200


def test_run_mysql_sends_in_memory_stdin_as_input_with_override_credentials(
    worker: ModuleType,
    database_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _Recorder()
    monkeypatch.setattr(worker.subprocess, "run", run)

    result = worker._run_mysql(stdin=io.BytesIO(b"SELECT 1;\n"), username="oe_import_x", password="temporary")

    command, kwargs = run.calls[0]
    assert result == ""
    assert kwargs["input"] == b"SELECT 1;\n"
    assert "stdin" not in kwargs
    assert kwargs["env"]["MYSQL_PWD"] == "temporary"
    assert "--user=oe_import_x" in command


def test_run_mysql_streams_real_file_handle_as_stdin(
    worker: ModuleType,
    database_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _Recorder(stdout=b"ok")
    monkeypatch.setattr(worker.subprocess, "run", run)
    sql = tmp_path / "validated.sql"
    sql.write_bytes(b"CREATE TABLE t (id int);\n")

    with sql.open("rb") as handle:
        assert worker._run_mysql("openemr", stdin=handle) == "ok"
        assert run.calls[0][1]["stdin"] is handle
    assert "input" not in run.calls[0][1]


def test_run_mysql_redacts_failed_command(
    worker: ModuleType,
    database_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker.subprocess, "run", _Recorder(returncode=1))

    with pytest.raises(worker.ImportFailure, match=r"^database-command-failed$"):
        worker._run_mysql("--execute=SELECT 1")


def test_run_mysql_raw_returns_unstripped_bytes(
    worker: ModuleType,
    database_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _Recorder(stdout=b"  a\tb\n")
    monkeypatch.setattr(worker.subprocess, "run", run)

    assert worker._run_mysql_raw("openemr", "--execute=SELECT 1") == b"  a\tb\n"
    assert run.calls[0][1]["env"]["MYSQL_PWD"] == "must-not-appear"

    monkeypatch.setattr(worker.subprocess, "run", _Recorder(returncode=2))
    with pytest.raises(worker.ImportFailure, match="database-command-failed"):
        worker._run_mysql_raw("openemr")


# Seed fingerprints and target baseline


def test_seed_fingerprint_selects_non_excluded_columns_in_binary_order(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_queries: list[tuple[str, ...]] = []
    content_queries: list[tuple[str, ...]] = []

    def run_mysql(*extra: str, **kwargs: Any) -> str:
        schema_queries.append(extra)
        return "gl_name\ngl_index\ngl_value"

    def run_mysql_raw(*extra: str) -> bytes:
        content_queries.append(extra)
        return b"rows"

    monkeypatch.setattr(worker, "_run_mysql", run_mysql)
    monkeypatch.setattr(worker, "_run_mysql_raw", run_mysql_raw)

    assert worker._seed_table_fingerprint("openemr", "globals") == hashlib.sha256(b"rows").hexdigest()
    assert schema_queries[0][0] == "information_schema"
    assert "TABLE_SCHEMA = 'openemr' AND TABLE_NAME = 'globals'" in schema_queries[0][1]
    assert content_queries == [
        (
            "openemr",
            "--execute=SELECT `gl_name`,`gl_index` FROM `globals` ORDER BY BINARY `gl_name`,BINARY `gl_index`",
        )
    ]


@pytest.mark.parametrize(
    ("baseline", "columns", "error"),
    (
        ({"exclude_columns": "gl_value"}, "gl_value", "target-seed-manifest-invalid"),
        ({"exclude_columns": ["gl_value`; DROP"]}, "gl_value", "target-seed-manifest-invalid"),
        ({"exclude_columns": [1]}, "gl_value", "target-seed-manifest-invalid"),
        ({"exclude_columns": ["missing"]}, "gl_value", "target-seed-schema-check-failed"),
        ({"exclude_columns": ["gl_value"]}, "gl_value", "target-seed-schema-check-failed"),
    ),
)
def test_seed_fingerprint_rejects_invalid_manifest_or_schema(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    baseline: dict[str, object],
    columns: str,
    error: str,
) -> None:
    monkeypatch.setitem(worker.FRESH_SEED_BASELINE, "globals", baseline)
    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: columns)
    monkeypatch.setattr(worker, "_run_mysql_raw", lambda *args: pytest.fail("content must not be queried"))

    with pytest.raises(worker.ImportFailure, match=error):
        worker._seed_table_fingerprint("openemr", "globals")


def test_baseline_dump_rejects_unsafe_database_name(
    worker: ModuleType,
    database_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_DATABASE", "openemr`; DROP")
    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: pytest.fail("must not query"))

    with pytest.raises(worker.ImportFailure, match="invalid-database-name"):
        worker._dump_target_database(tmp_path / "baseline.sql")


def test_baseline_dump_requires_database_configuration(
    worker: ModuleType,
    database_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MYSQL_SSL_CA")

    with pytest.raises(worker.ImportFailure, match="missing-database-configuration"):
        worker._dump_target_database(tmp_path / "baseline.sql")


def test_baseline_dump_writes_private_file_without_password_in_command(
    worker: ModuleType,
    database_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        kwargs["stdout"].write(b"-- baseline\n")
        return subprocess.CompletedProcess(command, 0, None, b"")

    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: "0")
    monkeypatch.setattr(worker.subprocess, "run", run)
    output = tmp_path / "baseline.sql"

    worker._dump_target_database(output)

    command, kwargs = calls[0]
    assert command[0] == "mariadb-dump"
    assert command[-1] == "openemr"
    assert "--single-transaction" in command
    assert "--ssl-verify-server-cert" in command
    assert "must-not-appear" not in " ".join(command)
    assert kwargs["env"]["MYSQL_PWD"] == "must-not-appear"
    assert output.read_bytes() == b"-- baseline\n"
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ("oserror", "timeout", "returncode", "empty"))
def test_baseline_dump_removes_partial_output_on_failure(
    worker: ModuleType,
    database_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if failure != "empty":
            kwargs["stdout"].write(b"partial")
            kwargs["stdout"].flush()
        if failure == "oserror":
            raise OSError("exec failed")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 7200)
        return subprocess.CompletedProcess(command, 1 if failure == "returncode" else 0, None, b"")

    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: "0")
    monkeypatch.setattr(worker.subprocess, "run", run)
    output = tmp_path / "baseline.sql"

    with pytest.raises(worker.ImportFailure, match="target-baseline-dump-failed"):
        worker._dump_target_database(output)

    assert not output.exists()


def test_baseline_dump_refuses_to_overwrite_existing_file(
    worker: ModuleType,
    database_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "baseline.sql"
    output.write_bytes(b"earlier baseline")
    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: "0")
    monkeypatch.setattr(worker.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not dump"))

    with pytest.raises(worker.ImportFailure, match="target-baseline-dump-failed"):
        worker._dump_target_database(output)

    assert not output.exists()


@pytest.mark.parametrize(
    ("rows", "expected"),
    (
        ("8\t4\t1\t0\t\t543", ("8.4.1", 543)),
        ("8\t4\t1\tNULL\tNULL\t543", ("8.4.1", 543)),
        ("8\t4\t1\t2\t.post1\t543", ("8.4.1.2.post1", 543)),
    ),
)
def test_database_version_identity_normalizes_version_row(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    rows: str,
    expected: tuple[str, int],
) -> None:
    queries: list[tuple[str, ...]] = []
    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: queries.append(args) or rows)

    assert worker._database_version_identity("openemr") == expected
    assert queries[0][0] == "openemr"


@pytest.mark.parametrize(
    ("rows", "error"),
    (
        ("", "target-version-row-is-not-unique"),
        ("8\t4\t1\t0\t\t543\n8\t4\t1\t0\t\t543", "target-version-row-is-not-unique"),
        ("8\t4\t1\t0\t543", "target-version-row-is-malformed"),
        ("8\t4\t1\t0\t!!\t543", "target-version-row-is-malformed"),
        ("8\t4\t1\t0\t\tabc", "target-version-row-is-malformed"),
    ),
)
def test_database_version_identity_rejects_ambiguous_or_malformed_rows(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    rows: str,
    error: str,
) -> None:
    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: rows)

    with pytest.raises(worker.ImportFailure, match=error):
        worker._database_version_identity("openemr")


def _fresh_target(worker: ModuleType, tables: dict[str, str], version: str = "8\t4\t1\t0\t\t543"):
    def run_mysql(*args: str, **kwargs: Any) -> str:
        command = " ".join(args)
        if "SHOW TABLES" in command:
            return "\n".join(tables)
        if "FROM version;" in command:
            return version
        for table, count in tables.items():
            if f"COUNT(*) FROM `{table}`" in command:
                return count
        raise AssertionError(command)

    return run_mysql


def test_empty_target_check_accepts_pristine_seeded_target(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = {name: "0" for name in ("documents", "form_encounter", "patient_data")}
    for seeded in ("globals", "users", "version"):
        tables[seeded] = f"header\n{worker.FRESH_SEED_BASELINE[seeded]['rows']}"
    fingerprinted: list[tuple[str, str]] = []

    def fingerprint(database: str, table: str) -> str:
        fingerprinted.append((database, table))
        return str(worker.FRESH_SEED_BASELINE[table]["sha256"])

    monkeypatch.setattr(worker, "_run_mysql", _fresh_target(worker, tables))
    monkeypatch.setattr(worker, "_seed_table_fingerprint", fingerprint)

    worker._assert_empty_target("8.4.1", 543)

    assert fingerprinted == [("openemr", "globals"), ("openemr", "version")]


def test_empty_target_check_refuses_manifest_for_other_release(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: pytest.fail("must not query"))

    with pytest.raises(worker.ImportFailure, match="target-seed-manifest-version-mismatch"):
        worker._assert_empty_target("8.2.0", 543)
    with pytest.raises(worker.ImportFailure, match="target-seed-manifest-version-mismatch"):
        worker._assert_empty_target("8.4.1", 541)


@pytest.mark.parametrize(
    ("tables", "error"),
    (
        ({"documents": "0", "users": "4"}, "target-schema-is-not-initialized"),
        (
            {"bad-name": "0", "documents": "0", "form_encounter": "0", "patient_data": "0", "users": "4"},
            "target-schema-has-unsafe-table-name",
        ),
        ({"documents": "", "form_encounter": "0", "patient_data": "0", "users": "4"}, "target-emptiness-check-failed"),
        (
            {"documents": "many", "form_encounter": "0", "patient_data": "0", "users": "4"},
            "target-emptiness-check-failed",
        ),
    ),
)
def test_empty_target_check_rejects_uninitialized_or_unsafe_schema(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tables: dict[str, str],
    error: str,
) -> None:
    monkeypatch.setattr(worker, "_run_mysql", _fresh_target(worker, tables))

    with pytest.raises(worker.ImportFailure, match=error):
        worker._assert_empty_target("8.4.1", 543)


def test_empty_target_check_rejects_schema_version_mismatch(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = {"documents": "0", "form_encounter": "0", "patient_data": "0", "users": "4"}
    monkeypatch.setattr(worker, "_run_mysql", _fresh_target(worker, tables, version="8\t4\t0\t0\t\t543"))

    with pytest.raises(worker.ImportFailure, match="target-schema-version-mismatch"):
        worker._assert_empty_target("8.4.1", 543)


def test_empty_target_check_rejects_malformed_manifest_fingerprint(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = {"documents": "0", "form_encounter": "0", "globals": "489", "patient_data": "0", "users": "4"}
    monkeypatch.setitem(worker.FRESH_SEED_BASELINE, "globals", {"rows": 489, "sha256": "NOT-HEX"})
    monkeypatch.setattr(worker, "_run_mysql", _fresh_target(worker, tables))
    monkeypatch.setattr(worker, "_seed_table_fingerprint", lambda *args: pytest.fail("must not fingerprint"))

    with pytest.raises(worker.ImportFailure, match="target-seed-content-mismatch:globals"):
        worker._assert_empty_target("8.4.1", 543)


# EFS target checks


def test_efs_mutability_requires_default_site(worker: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(worker.ImportFailure, match="target-default-site-missing"):
        worker._assert_efs_mutable(tmp_path / "missing", _MIGRATION_ID)
    tmp_path.joinpath("mount").mkdir()
    with pytest.raises(worker.ImportFailure, match="target-default-site-missing"):
        worker._assert_efs_mutable(tmp_path / "mount", _MIGRATION_ID)


@pytest.mark.parametrize("existing", (".openemr-import-probe-", ".openemr-import-probe-renamed-"))
def test_efs_mutability_refuses_preexisting_probe_paths(worker: ModuleType, tmp_path: Path, existing: str) -> None:
    (tmp_path / "default").mkdir()
    probe = tmp_path / f"{existing}{_MIGRATION_ID}"
    probe.mkdir()
    (probe / "keep").write_text("operator data", encoding="utf-8")

    with pytest.raises(worker.ImportFailure, match="migration-probe-path-already-exists"):
        worker._assert_efs_mutable(tmp_path, _MIGRATION_ID)

    assert (probe / "keep").read_text(encoding="utf-8") == "operator data"


def test_efs_mutability_reports_unwritable_target_and_cleans_probe(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "default").mkdir()

    def replace(source: object, destination: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(worker.os, "replace", replace)

    with pytest.raises(worker.ImportFailure, match="target-sites-not-writable"):
        worker._assert_efs_mutable(tmp_path, _MIGRATION_ID)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["default"]


def test_fresh_efs_check_requires_documents_directory(worker: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "default").mkdir()

    with pytest.raises(worker.ImportFailure, match="target-documents-directory-missing"):
        worker._assert_fresh_efs_target(tmp_path)


def test_fresh_efs_check_rejects_symlinks_and_allows_target_only_files(worker: ModuleType, tmp_path: Path) -> None:
    documents = tmp_path / "default" / "documents"
    (documents / "nested").mkdir(parents=True)
    (documents / "nested" / ".htaccess").write_text("deny", encoding="utf-8")
    (documents / "logs_and_misc" / "methods").mkdir(parents=True)
    (documents / "logs_and_misc" / "methods" / "one").write_text("key", encoding="utf-8")

    worker._assert_fresh_efs_target(tmp_path)

    (documents / "link").symlink_to(tmp_path)
    with pytest.raises(worker.ImportFailure, match="unsafe-target-site-path"):
        worker._assert_fresh_efs_target(tmp_path)


def test_fresh_efs_check_rejects_key_names_outside_methods_directory(worker: ModuleType, tmp_path: Path) -> None:
    documents = tmp_path / "default" / "documents"
    documents.mkdir(parents=True)
    (documents / "sevena").write_text("not a baseline key location", encoding="utf-8")

    with pytest.raises(worker.ImportFailure, match="target-site-is-not-empty"):
        worker._assert_fresh_efs_target(tmp_path)


# Database replacement and restore


@pytest.mark.parametrize("migration_id", ("import-" + "a" * 40, "import-abc-def", "import-abc'def"))
def test_import_username_rejects_unsafe_identity(worker: ModuleType, migration_id: str) -> None:
    with pytest.raises(worker.ImportFailure, match="invalid-migration-identity"):
        worker._import_username(migration_id)


def test_drop_import_user_targets_only_migration_account(worker: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[bytes] = []
    monkeypatch.setattr(
        worker,
        "_run_mysql",
        lambda *args, stdin=None, **kwargs: bodies.append(stdin.read()) or "",
    )

    worker._drop_import_user(_MIGRATION_ID)

    assert bodies == [b"DROP USER IF EXISTS 'oe_import_0123456789abcdef'@'%';\n"]


@pytest.mark.parametrize("operation", ("_replace_database", "_restore_baseline_database"))
def test_destructive_database_operations_reject_unsafe_database_name(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    monkeypatch.setenv("MYSQL_DATABASE", "openemr`; DROP DATABASE mysql; --")
    monkeypatch.setattr(worker, "_run_mysql", lambda *args, **kwargs: pytest.fail("must not run SQL"))
    sql = tmp_path / "validated.sql"
    sql.write_bytes(b"SELECT 1;\n")

    with pytest.raises(worker.ImportFailure, match="invalid-database-name"):
        if operation == "_replace_database":
            worker._replace_database(sql, _MIGRATION_ID)
        else:
            worker._restore_baseline_database(sql)


def test_restore_baseline_recreates_database_and_streams_baseline(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_DATABASE", "openemr")
    baseline = tmp_path / "target-baseline.sql"
    baseline.write_bytes(b"-- baseline\n")
    calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def run_mysql(*extra: str, stdin: Any = None, **kwargs: Any) -> str:
        calls.append((extra, stdin.read() if stdin is not None else None))
        return ""

    monkeypatch.setattr(worker, "_run_mysql", run_mysql)

    worker._restore_baseline_database(baseline)

    assert calls == [
        (
            (
                "--execute=DROP DATABASE `openemr`; "
                "CREATE DATABASE `openemr` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
            ),
            None,
        ),
        (("openemr",), b"-- baseline\n"),
    ]


# Site staging and swap


def test_overlay_replaces_existing_entries_with_imported_data(worker: ModuleType, tmp_path: Path) -> None:
    imported = tmp_path / "imported"
    (imported / "documents").mkdir(parents=True)
    (imported / "documents" / "patient.pdf").write_bytes(b"patient")
    (imported / "clickoptions.txt").write_bytes(b"imported-options")
    staged = tmp_path / "staged"
    (staged / "documents").mkdir(parents=True)
    (staged / "documents" / "placeholder").write_bytes(b"old")
    (staged / "clickoptions.txt").write_bytes(b"old-options")
    (staged / "config.php").write_bytes(b"target-config")

    worker._overlay_import_data(imported, staged)

    assert sorted(path.name for path in (staged / "documents").iterdir()) == ["patient.pdf"]
    assert (staged / "clickoptions.txt").read_bytes() == b"imported-options"
    assert (staged / "config.php").read_bytes() == b"target-config"


def test_restore_target_only_files_requires_target_sqlconf(worker: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()

    with pytest.raises(worker.ImportFailure, match="target-sqlconf-missing"):
        worker._restore_target_only_files(tmp_path / "source", tmp_path / "staged")


def test_restore_target_only_files_copies_nested_access_controls(worker: ModuleType, tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "documents" / "deep").mkdir(parents=True)
    (source / "sqlconf.php").write_bytes(b"target-credential")
    (source / "documents" / "deep" / ".htaccess").write_bytes(b"Deny from all")
    (source / "documents" / "deep" / "other.txt").write_bytes(b"not target-only")
    staged = tmp_path / "staged"
    staged.mkdir()

    worker._restore_target_only_files(source, staged)

    assert (staged / "documents" / "deep" / ".htaccess").read_bytes() == b"Deny from all"
    assert not (staged / "documents" / "deep" / "other.txt").exists()
    assert not (staged / "documents" / "certificates").exists()


@pytest.mark.parametrize("kind", ("root", "directory", "file"))
def test_normalize_permissions_rejects_symlinks(worker: ModuleType, tmp_path: Path, kind: str) -> None:
    real = tmp_path / "real"
    (real / "documents").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"x")
    (outside / "secret").chmod(0o600)
    default_site = real
    if kind == "root":
        default_site = tmp_path / "linked-default"
        default_site.symlink_to(real, target_is_directory=True)
    elif kind == "directory":
        (real / "documents" / "linked").symlink_to(outside, target_is_directory=True)
    else:
        (real / "documents" / "linked").symlink_to(outside / "secret")

    with pytest.raises(worker.ImportFailure, match="unsafe-staged-site-path"):
        worker._normalize_site_permissions(default_site)

    assert (outside / "secret").stat().st_mode & 0o777 == 0o600


def test_stage_and_swap_requires_target_default_site(worker: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(worker.ImportFailure, match="target-default-site-missing"):
        worker._stage_and_swap_sites(tmp_path / "imported", tmp_path, _MIGRATION_ID)


@pytest.mark.parametrize("existing", ("staging", "backup"))
def test_stage_and_swap_refuses_existing_migration_paths(worker: ModuleType, tmp_path: Path, existing: str) -> None:
    (tmp_path / "default").mkdir()
    if existing == "staging":
        (tmp_path / ".openemr-import-staging" / _MIGRATION_ID).mkdir(parents=True)
    else:
        (tmp_path / ".openemr-import-backup" / _MIGRATION_ID / "default").mkdir(parents=True)

    with pytest.raises(worker.ImportFailure, match="migration-path-already-exists"):
        worker._stage_and_swap_sites(tmp_path / "imported", tmp_path, _MIGRATION_ID)


def test_stage_and_swap_restores_original_site_when_final_rename_fails(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "default"
    target.mkdir()
    (target / "sqlconf.php").write_bytes(b"target-credential")
    (target / "state").write_text("original", encoding="utf-8")
    imported = tmp_path / "imported"
    imported.mkdir()
    (imported / "state").write_text("imported", encoding="utf-8")
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def replace(source: Path, destination: Path) -> None:
        replacements.append((Path(source), Path(destination)))
        if len(replacements) == 2:
            raise OSError("rename interrupted")
        real_replace(source, destination)

    monkeypatch.setattr(worker.os, "replace", replace)

    with pytest.raises(OSError, match="rename interrupted"):
        worker._stage_and_swap_sites(imported, tmp_path, _MIGRATION_ID)

    backup = tmp_path / ".openemr-import-backup" / _MIGRATION_ID / "default"
    assert (target / "state").read_text(encoding="utf-8") == "original"
    assert not backup.exists()
    assert replacements[-1] == (backup, target)


# Rollback


def test_restore_site_backup_is_noop_without_backup(worker: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "default").mkdir()

    worker._restore_site_backup(tmp_path, _MIGRATION_ID)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["default"]


def test_restore_site_backup_refuses_ambiguous_partial_rollback(worker: ModuleType, tmp_path: Path) -> None:
    backup = tmp_path / ".openemr-import-backup" / _MIGRATION_ID / "default"
    backup.mkdir(parents=True)
    (tmp_path / ".openemr-import-staging" / _MIGRATION_ID / "failed-default").mkdir(parents=True)
    (tmp_path / "default").mkdir()

    with pytest.raises(worker.ImportFailure, match="site-rollback-state-is-ambiguous"):
        worker._restore_site_backup(tmp_path, _MIGRATION_ID)

    assert backup.is_dir()


def test_restore_site_backup_without_current_target_restores_backup(worker: ModuleType, tmp_path: Path) -> None:
    backup = tmp_path / ".openemr-import-backup" / _MIGRATION_ID / "default"
    backup.mkdir(parents=True)
    (backup / "state").write_text("original", encoding="utf-8")

    worker._restore_site_backup(tmp_path, _MIGRATION_ID)

    assert (tmp_path / "default" / "state").read_text(encoding="utf-8") == "original"
    assert not (tmp_path / ".openemr-import-staging" / _MIGRATION_ID / "failed-default").exists()


@pytest.mark.parametrize("baseline", (None, "missing"))
def test_rollback_fails_without_baseline_but_still_drops_import_user(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    baseline: str | None,
) -> None:
    dropped: list[str] = []
    monkeypatch.setattr(worker, "_restore_baseline_database", lambda path: pytest.fail("no baseline to restore"))
    monkeypatch.setattr(worker, "_drop_import_user", dropped.append)

    assert not worker._attempt_automatic_rollback(
        mount_root=tmp_path,
        migration_id=_MIGRATION_ID,
        baseline_sql=None if baseline is None else tmp_path / baseline,
        database_mutation_started=True,
    )
    assert dropped == [_MIGRATION_ID]


def test_rollback_reports_failure_when_import_user_cannot_be_dropped(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def drop(migration_id: str) -> None:
        raise worker.ImportFailure("database-command-failed")

    monkeypatch.setattr(worker, "_restore_baseline_database", lambda path: pytest.fail("database untouched"))
    monkeypatch.setattr(worker, "_drop_import_user", drop)

    assert not worker._attempt_automatic_rollback(
        mount_root=tmp_path,
        migration_id=_MIGRATION_ID,
        baseline_sql=None,
        database_mutation_started=False,
    )


# Post-import validation


def _validation_mysql(table_count: int, version: str = "8\t4\t1\t0\t\t543"):
    def run_mysql(*args: str, **kwargs: Any) -> str:
        if "--execute=SHOW TABLES" in args:
            return "\n".join(f"table_{index}" for index in range(table_count))
        return version

    return run_mysql


def test_import_validation_accepts_complete_import(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_keys(tmp_path / "default")
    monkeypatch.setattr(worker, "_run_mysql", _validation_mysql(50))

    worker._validate_import(tmp_path, "8.4.1", 543)


@pytest.mark.parametrize(
    ("table_count", "version", "keys", "error"),
    (
        (49, "8\t4\t1\t0\t\t543", True, "database-validation-failed"),
        (50, "8\t4\t0\t0\t\t543", True, "database-version-validation-failed"),
        (50, "8\t4\t1\t0\t\t542", True, "database-version-validation-failed"),
        (50, "8\t4\t1\t0\t\t543", False, "site-validation-failed"),
    ),
)
def test_import_validation_rejects_incomplete_import(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    table_count: int,
    version: str,
    keys: bool,
    error: str,
) -> None:
    if keys:
        _write_keys(tmp_path / "default")
    monkeypatch.setattr(worker, "_run_mysql", _validation_mysql(table_count, version))

    with pytest.raises(worker.ImportFailure, match=error):
        worker._validate_import(tmp_path, "8.4.1", 543)


# Cleanup and recovery


def test_cleanup_refuses_paths_escaping_mount_root(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mount_root = tmp_path / "mount"
    mount_root.mkdir()
    outside = tmp_path / "outside"
    (outside / _MIGRATION_ID).mkdir(parents=True)
    (mount_root / ".openemr-import-staging").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(mount_root))

    with pytest.raises(worker.ImportFailure, match="unsafe-cleanup-path"):
        worker._cleanup_site_artifacts(_MIGRATION_ID, delete_backup=False)

    assert (outside / _MIGRATION_ID).is_dir()


def test_cleanup_refuses_symlinked_migration_directory_inside_mount(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mount_root = tmp_path / "mount"
    (mount_root / "default").mkdir(parents=True)
    (mount_root / ".openemr-import-staging").mkdir()
    (mount_root / ".openemr-import-staging" / _MIGRATION_ID).symlink_to(
        mount_root / "default", target_is_directory=True
    )
    monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(mount_root))

    with pytest.raises(worker.ImportFailure, match="unsafe-cleanup-path"):
        worker._cleanup_site_artifacts(_MIGRATION_ID, delete_backup=False)

    assert (mount_root / "default").is_dir()


@pytest.mark.parametrize("state", ("missing", "symlink"))
def test_recovery_requires_regular_local_baseline(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    baseline = tmp_path / ".openemr-import-backup" / _MIGRATION_ID / "target-baseline.sql"
    if state == "symlink":
        baseline.parent.mkdir(parents=True)
        real = tmp_path / "elsewhere.sql"
        real.write_bytes(b"-- baseline")
        baseline.symlink_to(real)
    monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(tmp_path))
    monkeypatch.setattr(worker, "_attempt_automatic_rollback", lambda **kwargs: pytest.fail("must not roll back"))

    with pytest.raises(worker.ImportFailure, match="target-baseline-dump-missing"):
        worker._recover_local_baseline(_MIGRATION_ID)


def test_recovery_reports_failed_rollback_without_revalidating(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = tmp_path / ".openemr-import-backup" / _MIGRATION_ID / "target-baseline.sql"
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(b"-- baseline")
    monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(tmp_path))
    monkeypatch.setattr(worker, "_attempt_automatic_rollback", lambda **kwargs: False)
    monkeypatch.setattr(worker, "_database_version_identity", lambda database: pytest.fail("must not revalidate"))

    with pytest.raises(worker.ImportFailure, match="local-baseline-recovery-failed"):
        worker._recover_local_baseline(_MIGRATION_ID)


# Entrypoint


def _main(worker: ModuleType, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["worker.py", *argv])
    return worker.main()


def _records(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


@pytest.mark.parametrize(
    ("argv", "message"),
    (
        (("--migration-id", "import-XYZ"), "invalid migration identifier"),
        (("--migration-id", f"{_MIGRATION_ID}0"), "invalid migration identifier"),
        (("--operation", "cleanup", "--migration-id", _MIGRATION_ID), "cleanup requires explicit"),
        (("--migration-id", _MIGRATION_ID), "import source arguments are required"),
    ),
)
def test_main_rejects_invalid_invocations(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(SystemExit, match=message):
        _main(worker, monkeypatch, *argv)


def test_main_cleanup_deletes_migration_artifacts(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for root in (".openemr-import-staging", ".openemr-import-backup"):
        (tmp_path / root / _MIGRATION_ID / "default").mkdir(parents=True)
    (tmp_path / ".openemr-import-backup" / "import-ffffffffffffffff").mkdir()
    monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(tmp_path))

    assert (
        _main(worker, monkeypatch, "--operation", "cleanup", "--migration-id", _MIGRATION_ID, "--delete-site-backup")
        == 0
    )

    assert _records(capsys.readouterr().out) == [{"phase": "cleanup"}, {"phase": "cleanup-complete"}]
    assert not (tmp_path / ".openemr-import-staging" / _MIGRATION_ID).exists()
    assert not (tmp_path / ".openemr-import-backup" / _MIGRATION_ID).exists()
    assert (tmp_path / ".openemr-import-backup" / "import-ffffffffffffffff").is_dir()


def test_main_cleanup_reports_unsafe_paths(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(migration_id: str, *, delete_backup: bool) -> None:
        raise worker.ImportFailure("unsafe-cleanup-path")

    monkeypatch.setattr(worker, "_cleanup_site_artifacts", refuse)

    assert (
        _main(worker, monkeypatch, "--operation", "cleanup", "--migration-id", _MIGRATION_ID, "--delete-site-backup")
        == 1
    )

    assert _records(capsys.readouterr().out)[-1] == {
        "error": "unsafe-cleanup-path",
        "phase": "cleanup",
        "status": "failed",
    }


@pytest.mark.parametrize("fails", (False, True))
def test_main_recover_reports_outcome(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fails: bool,
) -> None:
    recovered: list[str] = []

    def recover(migration_id: str) -> None:
        recovered.append(migration_id)
        if fails:
            raise worker.ImportFailure("local-baseline-recovery-failed")

    monkeypatch.setattr(worker, "_recover_local_baseline", recover)

    assert _main(worker, monkeypatch, "--operation", "recover", "--migration-id", _MIGRATION_ID) == int(fails)

    records = _records(capsys.readouterr().out)
    assert recovered == [_MIGRATION_ID]
    assert records[0] == {"phase": "recovery"}
    if fails:
        assert records[-1] == {"error": "local-baseline-recovery-failed", "phase": "recovery", "status": "failed"}
    else:
        assert records[-1] == {"phase": "recovery-complete"}


_SOURCE_BYTES = b"native-backup-archive"
_SQL_HASH = "a" * 64
_SITES_HASH = "b" * 64


class _FakeS3:
    def __init__(self, *, fail_put: str | None = None) -> None:
        self.fail_put = fail_put
        self.downloads: list[tuple[str, str, str, dict[str, str]]] = []
        self.statuses: list[dict[str, Any]] = []
        self.put_arguments: list[dict[str, Any]] = []

    def download_file(self, bucket: str, key: str, filename: str, ExtraArgs: dict[str, str]) -> None:  # noqa: N803
        self.downloads.append((bucket, key, filename, ExtraArgs))
        Path(filename).write_bytes(_SOURCE_BYTES)

    def put_object(self, **kwargs: Any) -> None:
        payload = json.loads(kwargs["Body"])
        if self.fail_put == "always" or (self.fail_put == "failed" and payload["status"] == "failed"):
            raise RuntimeError("s3 unavailable")
        self.put_arguments.append(kwargs)
        self.statuses.append(payload)


class _Pipeline:
    """Replaces every heavy worker step with a recorder so main()'s orchestration can be asserted."""

    def __init__(self, worker: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.worker = worker
        self.calls: list[str] = []
        self.failures: dict[str, BaseException] = {}
        self.rollback_result = True
        self.rollback_kwargs: list[dict[str, Any]] = []
        self.sql_identity = ("8.4.1", 543)
        self.work_root = tmp_path / "work"
        self.work_root.mkdir()
        self.mount_root = tmp_path / "mount"
        self.mount_root.mkdir()
        self.s3 = _FakeS3()
        self.client_names: list[str] = []
        for name, value in {
            **_DATABASE_ENV,
            "IMPORT_STAGING_BUCKET": "staging-bucket",
            "IMPORT_STAGING_BUCKET_OWNER": "123456789012",
            "IMPORT_STAGING_KMS_KEY_ARN": _KMS_KEY_ARN,
            "OPENEMR_SITES_MOUNT_ROOT": str(self.mount_root),
            "TARGET_OPENEMR_VERSION": "8.4.1",
        }.items():
            monkeypatch.setenv(name, value)
        real_path = Path

        def path(*parts: Any) -> Path:
            if parts == ("/work",):
                return self.work_root
            return real_path(*parts)

        monkeypatch.setattr(worker, "Path", path)
        monkeypatch.setattr(worker.boto3, "client", self._client)
        for name in (
            "_unpack_native_source",
            "_validate_and_extract_sites",
            "_validate_sql",
            "_assert_efs_mutable",
            "_assert_fresh_efs_target",
            "_assert_empty_target",
            "_dump_target_database",
            "_replace_database",
            "_stage_and_swap_sites",
            "_validate_import",
        ):
            monkeypatch.setattr(worker, name, self._step(name))
        monkeypatch.setattr(worker, "_attempt_automatic_rollback", self._rollback)

    def _client(self, name: str) -> _FakeS3:
        self.client_names.append(name)
        return self.s3

    def _rollback(self, **kwargs: Any) -> bool:
        self.rollback_kwargs.append(kwargs)
        return self.rollback_result

    def _step(self, name: str):
        def run(*args: Any) -> Any:
            self.calls.append(name)
            if name in self.failures:
                raise self.failures[name]
            if name == "_unpack_native_source":
                work = args[1]
                (work / "openemr.sql").write_bytes(b"sql")
                (work / "openemr.tar.gz").write_bytes(b"sites")
                return work / "openemr.sql", work / "openemr.tar.gz", {"sql": _SQL_HASH, "sites": _SITES_HASH}
            if name == "_validate_and_extract_sites":
                return ("8.4.1", 543)
            if name == "_validate_sql":
                return self.sql_identity
            return None

        return run

    def argv(self, **overrides: str) -> list[str]:
        source_sha = hashlib.sha256(_SOURCE_BYTES).hexdigest()
        values = {
            "--migration-id": _MIGRATION_ID,
            "--source-key": f"migrations/{_MIGRATION_ID}/source.tar",
            "--source-sha256": source_sha,
            "--source-fingerprint": self.worker._canonical_fingerprint(
                {
                    "source": f"sha256:{source_sha}",
                    "sql": f"sha256:{_SQL_HASH}",
                    "sites": f"sha256:{_SITES_HASH}",
                }
            ),
            "--source-openemr-version": "8.4.1",
            "--source-database-version": "543",
        }
        values.update(overrides)
        return [*(item for pair in values.items() for item in pair), "--recovery-verified"]


@pytest.fixture
def pipeline(worker: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Pipeline:
    return _Pipeline(worker, monkeypatch, tmp_path)


def test_main_import_runs_guarded_phases_in_order(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _main(worker, monkeypatch, *pipeline.argv()) == 0

    phases = [
        "download",
        "source-validation",
        "target-validation",
        "recovery-baseline",
        "database-import",
        "site-import",
        "post-import-validation",
    ]
    assert [(status["status"], status["phase"]) for status in pipeline.s3.statuses] == [
        *(("running", phase) for phase in phases),
        ("succeeded", "complete"),
    ]
    assert all(arguments["ExpectedBucketOwner"] == "123456789012" for arguments in pipeline.s3.put_arguments)
    assert pipeline.client_names == ["s3"]
    assert pipeline.s3.downloads == [
        (
            "staging-bucket",
            f"migrations/{_MIGRATION_ID}/source.tar",
            str(pipeline.work_root / _MIGRATION_ID / "source.tar"),
            {"ExpectedBucketOwner": "123456789012"},
        )
    ]
    assert pipeline.calls == [
        "_unpack_native_source",
        "_validate_and_extract_sites",
        "_validate_sql",
        "_assert_efs_mutable",
        "_assert_fresh_efs_target",
        "_assert_empty_target",
        "_dump_target_database",
        "_replace_database",
        "_stage_and_swap_sites",
        "_validate_import",
    ]
    assert pipeline.rollback_kwargs == []
    assert (pipeline.mount_root / ".openemr-import-backup" / _MIGRATION_ID).is_dir()
    assert not (pipeline.work_root / _MIGRATION_ID).exists()
    assert [record["phase"] for record in _records(capsys.readouterr().out)] == [*phases, "complete"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"--source-key": "migrations/other/source.tar"}, "invalid staging key"),
        ({"--source-database-version": "0"}, "import source arguments are required"),
    ),
)
def test_main_import_rejects_unbound_source_arguments(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(SystemExit, match=message):
        _main(worker, monkeypatch, *pipeline.argv(**overrides))

    assert pipeline.client_names == []


def test_main_import_requires_bucket_and_recovery_verification(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = pipeline.argv()
    with pytest.raises(SystemExit, match="required import safeguards are missing"):
        _main(worker, monkeypatch, *argv[:-1])

    monkeypatch.setenv("IMPORT_STAGING_BUCKET", "")
    with pytest.raises(SystemExit, match="required import safeguards are missing"):
        _main(worker, monkeypatch, *argv)
    assert pipeline.client_names == []


def test_main_import_rejects_invalid_staging_safeguards(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IMPORT_STAGING_KMS_KEY_ARN", "arn:aws:kms:us-east-1:999999999999:key/other")

    with pytest.raises(SystemExit, match="invalid-staging-kms-key"):
        _main(worker, monkeypatch, *pipeline.argv())
    assert pipeline.client_names == []


def test_main_import_reports_aws_client_failure_before_creating_workspace(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken_client(name: str) -> None:
        raise RuntimeError("no credentials")

    monkeypatch.setattr(worker.boto3, "client", broken_client)

    assert _main(worker, monkeypatch, *pipeline.argv()) == 1

    assert _records(capsys.readouterr().out) == [
        {"error": "aws-client-initialization-failed", "phase": "initialization", "status": "failed"}
    ]
    assert list(pipeline.work_root.iterdir()) == []


@pytest.mark.parametrize(
    ("setup", "error", "phase", "last_call"),
    (
        ("checksum", "source-checksum-mismatch", "download", None),
        ("fingerprint", "source-fingerprint-mismatch", "source-validation", "_unpack_native_source"),
        ("target-version", "source-target-version-mismatch", "source-validation", "_validate_and_extract_sites"),
        ("source-version", "source-target-version-mismatch", "source-validation", "_validate_and_extract_sites"),
        ("database-version", "source-target-version-mismatch", "source-validation", "_validate_and_extract_sites"),
        ("sql-version", "source-component-version-mismatch", "source-validation", "_validate_sql"),
        ("sql-database-version", "source-component-version-mismatch", "source-validation", "_validate_sql"),
        ("target-not-empty", "target-is-not-empty", "target-validation", "_assert_empty_target"),
        ("rollback-root-exists", "migration-path-already-exists", "recovery-baseline", "_assert_empty_target"),
        ("baseline-dump", "target-baseline-dump-failed", "recovery-baseline", "_dump_target_database"),
    ),
)
def test_main_import_failures_before_database_mutation_skip_rollback(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    setup: str,
    error: str,
    phase: str,
    last_call: str | None,
) -> None:
    overrides: dict[str, str] = {}
    if setup == "checksum":
        overrides["--source-sha256"] = "0" * 64
    elif setup == "fingerprint":
        overrides["--source-fingerprint"] = "0" * 32
    elif setup == "target-version":
        monkeypatch.setenv("TARGET_OPENEMR_VERSION", "8.4.2")
    elif setup == "source-version":
        overrides["--source-openemr-version"] = "8.4.2"
    elif setup == "database-version":
        overrides["--source-database-version"] = "544"
    elif setup == "sql-version":
        pipeline.sql_identity = ("8.4.0", 543)
    elif setup == "sql-database-version":
        pipeline.sql_identity = ("8.4.1", 542)
    elif setup == "target-not-empty":
        pipeline.failures["_assert_empty_target"] = worker.ImportFailure("target-is-not-empty")
    elif setup == "rollback-root-exists":
        (pipeline.mount_root / ".openemr-import-backup" / _MIGRATION_ID).mkdir(parents=True)
    elif setup == "baseline-dump":
        pipeline.failures["_dump_target_database"] = worker.ImportFailure("target-baseline-dump-failed")

    assert _main(worker, monkeypatch, *pipeline.argv(**overrides)) == 1

    assert (pipeline.calls[-1] if pipeline.calls else None) == last_call
    assert "_replace_database" not in pipeline.calls
    assert pipeline.rollback_kwargs == []
    final = pipeline.s3.statuses[-1]
    assert (final["status"], final["phase"], final["error"], final["rollback_status"]) == (
        "failed",
        phase,
        error,
        None,
    )
    assert _records(capsys.readouterr().out)[-1] == {
        "error": error,
        "phase": phase,
        "rollback_status": None,
        "status": "failed",
    }
    assert not (pipeline.work_root / _MIGRATION_ID).exists()


@pytest.mark.parametrize(
    ("failing_step", "exception", "rollback_result", "error", "phase", "rollback_status"),
    (
        ("_replace_database", "import", True, "database-command-failed", "database-import", "succeeded"),
        ("_replace_database", "import", False, "automatic-rollback-failed", "database-import", "failed"),
        ("_validate_import", "import", True, "database-validation-failed", "post-import-validation", "succeeded"),
        ("_stage_and_swap_sites", "runtime", True, "internal-worker-error", "site-import", "succeeded"),
        ("_stage_and_swap_sites", "runtime", False, "automatic-rollback-failed", "site-import", "failed"),
    ),
)
def test_main_import_rolls_back_after_database_mutation(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failing_step: str,
    exception: str,
    rollback_result: bool,
    error: str,
    phase: str,
    rollback_status: str,
) -> None:
    pipeline.failures[failing_step] = (
        worker.ImportFailure(
            "database-validation-failed" if failing_step == "_validate_import" else "database-command-failed"
        )
        if exception == "import"
        else RuntimeError("unexpected secret-bearing detail")
    )
    pipeline.rollback_result = rollback_result

    assert _main(worker, monkeypatch, *pipeline.argv()) == 1

    assert pipeline.rollback_kwargs == [
        {
            "mount_root": pipeline.mount_root,
            "migration_id": _MIGRATION_ID,
            "baseline_sql": pipeline.mount_root / ".openemr-import-backup" / _MIGRATION_ID / "target-baseline.sql",
            "database_mutation_started": True,
        }
    ]
    final = pipeline.s3.statuses[-1]
    assert (final["status"], final["phase"], final["error"], final["rollback_status"]) == (
        "failed",
        phase,
        error,
        rollback_status,
    )
    output = capsys.readouterr().out
    assert "secret-bearing" not in output
    assert _records(output)[-1] == {
        "error": error,
        "phase": phase,
        "rollback_status": rollback_status,
        "status": "failed",
    }


def test_main_import_unexpected_error_before_mutation_skips_rollback(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline.failures["_assert_efs_mutable"] = RuntimeError("boom")

    assert _main(worker, monkeypatch, *pipeline.argv()) == 1

    assert pipeline.rollback_kwargs == []
    final = pipeline.s3.statuses[-1]
    assert (final["phase"], final["error"], final["rollback_status"]) == (
        "target-validation",
        "internal-worker-error",
        None,
    )


def test_main_import_emits_local_record_when_failure_status_cannot_be_published(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pipeline.s3.fail_put = "failed"
    pipeline.failures["_replace_database"] = worker.ImportFailure("database-command-failed")

    assert _main(worker, monkeypatch, *pipeline.argv()) == 1

    records = _records(capsys.readouterr().out)
    assert records[-2:] == [
        {
            "error": "status-publish-failed",
            "original_error": "database-command-failed",
            "phase": "database-import",
            "rollback_status": "succeeded",
            "status": "failed",
        },
        {
            "error": "database-command-failed",
            "phase": "database-import",
            "rollback_status": "succeeded",
            "status": "failed",
        },
    ]


@pytest.mark.parametrize("mutated", (False, True))
def test_main_import_emits_local_record_when_status_bucket_is_unavailable(
    worker: ModuleType,
    pipeline: _Pipeline,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutated: bool,
) -> None:
    if mutated:
        pipeline.s3.fail_put = "failed"
        pipeline.failures["_stage_and_swap_sites"] = RuntimeError("boom")
    else:
        pipeline.s3.fail_put = "always"

    assert _main(worker, monkeypatch, *pipeline.argv()) == 1

    phase = "site-import" if mutated else "download"
    rollback_status = "succeeded" if mutated else None
    assert pipeline.calls[-1:] == (["_stage_and_swap_sites"] if mutated else [])
    assert _records(capsys.readouterr().out)[-2:] == [
        {
            "error": "status-publish-failed",
            "original_error": "internal-worker-error",
            "phase": phase,
            "rollback_status": rollback_status,
            "status": "failed",
        },
        {
            "error": "internal-worker-error",
            "phase": phase,
            "rollback_status": rollback_status,
            "status": "failed",
        },
    ]
    assert not (pipeline.work_root / _MIGRATION_ID).exists()
