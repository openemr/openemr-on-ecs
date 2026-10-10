"""Unit tests for the fresh-seed manifest regeneration utility."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

_WORKER_DIR = Path(__file__).resolve().parents[2] / "tools" / "openemr-import-worker"


def _utility() -> ModuleType:
    had_worker = "worker" in sys.modules
    saved_worker = sys.modules.get("worker")
    sys.path.insert(0, str(_WORKER_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "ci_update_seed_manifest",
            _WORKER_DIR / "ci_update_seed_manifest.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(_WORKER_DIR))
        if had_worker:
            sys.modules["worker"] = saved_worker  # type: ignore[assignment]
        else:
            sys.modules.pop("worker", None)
    return module


def _policy(
    utility: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tables: dict[str, dict[str, object]],
) -> None:
    monkeypatch.setattr(utility.import_worker, "FRESH_SEEDED_TABLES", set(tables))
    monkeypatch.setattr(utility.import_worker, "FRESH_SEED_BASELINE", tables)


def test_render_manifest_matches_canonical_format() -> None:
    utility = _utility()
    manifest = {
        "openemr_version": "8.4.1",
        "database_version": 543,
        "tables": {
            "version": {"rows": 1, "sha256": "a" * 64},
            "background_services": {
                "rows": 5,
                "sha256": "b" * 64,
                "exclude_columns": ["next_run", "lock_expires_at"],
            },
            "facility": {"rows": 1, "sha256": None},
        },
    }

    rendered = utility._render_manifest(manifest)

    assert rendered == (
        "{\n"
        '  "openemr_version": "8.4.1",\n'
        '  "database_version": 543,\n'
        '  "tables": {\n'
        f'    "background_services": {{"rows": 5, "sha256": "{"b" * 64}", '
        '"exclude_columns": ["next_run", "lock_expires_at"]},\n'
        f'    "facility": {{"rows": 1, "sha256": null}},\n'
        f'    "version": {{"rows": 1, "sha256": "{"a" * 64}"}}\n'
        "  }\n"
        "}\n"
    )


def test_render_manifest_rejects_non_mapping_tables() -> None:
    utility = _utility()

    with pytest.raises(ValueError, match="tables must be a mapping"):
        utility._render_manifest({"openemr_version": "8.4.1", "database_version": 543, "tables": []})


def test_generate_collects_live_versions_rows_and_fingerprints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    utility = _utility()
    _policy(
        utility,
        monkeypatch,
        {
            "codes": {"rows": 0, "sha256": "0" * 64},
            "users": {"rows": 0, "sha256": None},
        },
    )
    monkeypatch.setattr(utility.import_worker, "_database_version_identity", lambda database: ("9.9.9", 999))
    monkeypatch.setattr(
        utility.import_worker,
        "_run_mysql",
        lambda database, *args: "7\n" if "codes" in " ".join(args) else "4\n",
    )
    monkeypatch.setattr(
        utility.import_worker,
        "_seed_table_fingerprint",
        lambda database, table: f"{table}-digest",
    )
    output = tmp_path / "fresh-seed-manifest.json"

    assert utility.main(["--output", str(output)]) == 0

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["openemr_version"] == "9.9.9"
    assert manifest["database_version"] == 999
    assert manifest["tables"] == {
        "codes": {"rows": 7, "sha256": "codes-digest"},
        "users": {"rows": 4, "sha256": None},
    }
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status == {"status": "passed", "database_version": 999, "openemr_version": "9.9.9", "tables": 2}


def test_generate_preserves_exclude_columns_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    utility = _utility()
    _policy(
        utility,
        monkeypatch,
        {"globals": {"rows": 0, "sha256": "0" * 64, "exclude_columns": ["gl_value"]}},
    )
    monkeypatch.setattr(utility.import_worker, "_database_version_identity", lambda database: ("8.4.1", 543))
    monkeypatch.setattr(utility.import_worker, "_run_mysql", lambda database, *args: "487\n")
    monkeypatch.setattr(utility.import_worker, "_seed_table_fingerprint", lambda database, table: "g" * 64)
    output = tmp_path / "fresh-seed-manifest.json"

    assert utility.main(["--output", str(output)]) == 0

    rendered = output.read_text(encoding="utf-8")
    assert f'"globals": {{"rows": 487, "sha256": "{"g" * 64}", "exclude_columns": ["gl_value"]}}' in rendered


def test_generate_writes_a_manifest_the_calling_uid_can_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container writes as uid 1000 but the harness reads back as another uid."""
    utility = _utility()
    _policy(utility, monkeypatch, {"version": {"rows": 1, "sha256": "0" * 64}})
    monkeypatch.setattr(utility.import_worker, "_database_version_identity", lambda database: ("8.4.1", 543))
    monkeypatch.setattr(utility.import_worker, "_run_mysql", lambda database, *args: "1\n")
    monkeypatch.setattr(utility.import_worker, "_seed_table_fingerprint", lambda database, table: "v" * 64)
    output = tmp_path / "fresh-seed-manifest.json"

    assert utility.main(["--output", str(output)]) == 0

    mode = stat.S_IMODE(output.stat().st_mode)
    assert mode & stat.S_IROTH, f"manifest mode {mode:04o} is not world-readable"
    assert not mode & (stat.S_IWGRP | stat.S_IWOTH), f"manifest mode {mode:04o} is group/world writable"


def test_generate_expect_version_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    utility = _utility()
    monkeypatch.setattr(utility.import_worker, "_database_version_identity", lambda database: ("8.4.1", 543))
    output = tmp_path / "fresh-seed-manifest.json"

    assert utility.main(["--output", str(output), "--expect-version", "8.2.0"]) == 1

    assert not output.exists()
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status["status"] == "failed"
    assert "8.4.1" in status["error"]


def test_generate_fails_closed_on_worker_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    utility = _utility()

    def raise_identity(database: str) -> tuple[str, int]:
        raise utility.import_worker.ImportFailure("missing-database-configuration")

    monkeypatch.setattr(utility.import_worker, "_database_version_identity", raise_identity)

    assert utility.main(["--output", str(tmp_path / "out.json")]) == 1

    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status == {"status": "failed", "error": "missing-database-configuration"}


def test_verify_delegates_to_worker_fresh_target_assertion(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    utility = _utility()
    checked: list[tuple[str, int]] = []
    monkeypatch.setattr(
        utility.import_worker,
        "_seed_manifest",
        {"openemr_version": "8.4.1", "database_version": 543, "tables": {}},
    )
    monkeypatch.setattr(
        utility.import_worker,
        "_assert_empty_target",
        lambda version, database_version: checked.append((version, database_version)),
    )

    assert utility.main(["--verify"]) == 0

    assert checked == [("8.4.1", 543)]
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status["status"] == "passed"
    assert status["verified"] is True


def test_verify_fails_closed_on_assertion_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    utility = _utility()
    monkeypatch.setattr(
        utility.import_worker,
        "_seed_manifest",
        {"openemr_version": "8.4.1", "database_version": 543, "tables": {}},
    )

    def raise_assertion(version: str, database_version: int) -> None:
        raise utility.import_worker.ImportFailure("target-seed-row-count-mismatch")

    monkeypatch.setattr(utility.import_worker, "_assert_empty_target", raise_assertion)

    assert utility.main(["--verify"]) == 1

    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status == {"status": "failed", "error": "target-seed-row-count-mismatch"}


def test_collect_tables_covers_sorted_policy_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    utility = _utility()
    _policy(
        utility,
        monkeypatch,
        {
            "zeta": {"rows": 0, "sha256": "0" * 64},
            "alpha": {"rows": 0, "sha256": "0" * 64},
        },
    )
    monkeypatch.setattr(utility.import_worker, "_run_mysql", lambda database, *args: "0\n")
    monkeypatch.setattr(utility.import_worker, "_seed_table_fingerprint", lambda database, table: "h" * 64)

    tables = utility._collect_tables("openemr")

    assert list(tables) == ["alpha", "zeta"]
