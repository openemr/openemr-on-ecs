"""Validation of persisted inspection/plan JSON and the conservative planner."""

from __future__ import annotations

import runpy
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tools._shared import ToolError
from tools.openemr_import import cli
from tools.openemr_import.models import SCHEMA_VERSION, SiteInventory, SourceInspection
from tools.openemr_import.plan import (
    TARGET_OPENEMR_VERSION,
    create_plan,
    inspection_from_dict,
    plan_from_dict,
)


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
        "sites": (
            SiteInventory(
                site_id="default",
                has_sqlconf=True,
                has_documents=True,
                document_count=1,
                has_encryption_keys=True,
                certificate_count=0,
                edi_file_count=0,
                executable_file_count=0,
            ),
        ),
        "ignored_application_file_count": 0,
        "nested_archive_count": 0,
        "custom_code_detected": False,
        "checksums": {"source": "sha256:" + "a" * 64},
    }
    values.update(overrides)
    return SourceInspection(**values)


def test_inspection_round_trips_through_json_dict() -> None:
    inspection = _inspection(source_database_version=None)

    assert inspection_from_dict(inspection.to_dict()) == inspection


def test_inspection_rejects_unknown_schema_version() -> None:
    data = _inspection().to_dict() | {"schema_version": 99}

    with pytest.raises(ToolError, match="Unsupported source inspection schema"):
        inspection_from_dict(data)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda data: data.update(sites={"default": {}}),
        lambda data: data.update(sites=["not-an-object"]),
        lambda data: data["sites"][0].update(site_id=7),
        lambda data: data["sites"][0].update(has_sqlconf="true"),
        lambda data: data["sites"][0].update(document_count=-1),
        lambda data: data.update(source_openemr_version=8),
        lambda data: data.update(upstream_reference=None),
        lambda data: data.update(unsupported_content=[1]),
        lambda data: data.update(checksums={"source": 1}),
        lambda data: data.pop("database_type"),
    ),
    ids=(
        "sites-not-array",
        "site-not-object",
        "site-id-type",
        "truthy-string-boolean",
        "negative-count",
        "version-type",
        "upstream-type",
        "strings-type",
        "checksum-type",
        "missing-key",
    ),
)
def test_inspection_rejects_malformed_fields(mutate: Any) -> None:
    data = _inspection().to_dict()
    data["sites"] = [dict(site) for site in data["sites"]]
    mutate(data)

    with pytest.raises(ToolError, match="incomplete or malformed"):
        inspection_from_dict(data)


def test_plan_rejects_unknown_schema_version() -> None:
    data = create_plan(_inspection()).to_dict() | {"schema_version": 0}

    with pytest.raises(ToolError, match="Unsupported import plan schema"):
        plan_from_dict(data)


def test_unknown_source_versions_block_execution() -> None:
    plan = create_plan(_inspection(source_openemr_version=None, source_database_version=None))

    assert plan.execution_allowed is False
    assert plan.source_openemr_version == "unknown"
    assert plan.source_database_version == 0
    assert "Source version must be known before execution" in plan.blockers
    assert "Source database schema version must be verified before execution" in plan.blockers


def test_newer_source_than_target_blocks_execution() -> None:
    plan = create_plan(_inspection(), target_version="8.0.0")

    assert plan.execution_allowed is False
    assert "Source OpenEMR version is newer than the target" in plan.blockers


def test_invalid_version_strings_are_refused() -> None:
    with pytest.raises(ToolError, match="version is invalid"):
        create_plan(_inspection(source_openemr_version="not-a-version"))


def test_blockers_and_warnings_are_deduplicated() -> None:
    inspection = replace(
        _inspection(),
        unsupported_content=("dup", "dup"),
        manual_review=("warn", "warn"),
    )

    plan = create_plan(inspection)

    assert plan.blockers == ("dup",)
    assert plan.warnings == ("warn",)


def test_module_entry_point_runs_cli_and_reports_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["openemr_import", "plan", str(tmp_path / "missing.json")])
    monkeypatch.delitem(sys.modules, "tools.openemr_import.__main__", raising=False)

    with pytest.raises(SystemExit) as exited:
        runpy.run_module("tools.openemr_import", run_name="__main__")

    assert exited.value.code == 2
    assert "Input is not a regular JSON file" in capsys.readouterr().err
    assert sys.modules["tools.openemr_import.cli"].main is cli.main
