"""Validation and rendering edge cases for the live E2E timing history."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tools._shared import ToolError
from tools.live_e2e.models import SCHEMA_VERSION, CheckResult, PhaseTiming, RunResult
from tools.live_e2e.report import (
    _display_first_phase,
    _validate_run,
    append_result,
    load_history,
    phase_names,
    regenerate_report,
    render_markdown,
    update_cleanup_result,
    validate_history,
)


def _result(run_id: str = "e2e-report-coverage", **overrides: Any) -> RunResult:
    base = RunResult(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        started_at="2026-08-01T12:00:00Z",
        finished_at="2026-08-01T12:30:00Z",
        git_commit="b" * 40,
        branch="feature/report",
        repository="openemr/openemr-on-ecs",
        account_hash="sha256:abcdef012345",
        region="us-east-1",
        safe_stack_id="sha256:012345abcdef",
        profile="default",
        configuration_fingerprint="sha256:0123456789abcdef",
        bootstrap_state="ready-v27",
        python_version="3.14.0",
        node_version="v24.1.0",
        cdk_cli_version="2.1135.1",
        cdk_library_version="2.264.0",
        cdk_assets_version="4.7.0",
        openemr_version="8.2.0",
        aurora_version="8.0.mysql_aurora.3.12.0",
        test_runner_version="1.0.0",
        status="passed",
        stack_status="CREATE_COMPLETE",
        cleanup_status="complete",
        failure_phase=None,
        phases=(PhaseTiming("total", 65.0, "local-monotonic-clock"),),
        checks=(CheckResult("application-https", "pass", "ok"),),
    )
    return replace(base, **overrides)


def _run(**overrides: Any) -> dict[str, Any]:
    value = _result().to_dict()
    value.update(overrides)
    return value


def test_load_history_rejects_unreadable_json(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError, match="Cannot read timing history"):
        load_history(path)


@pytest.mark.parametrize(
    ("value", "message"),
    (
        ([], "schema version"),
        ({"schema_version": 99, "runs": []}, "schema version"),
        ({"schema_version": SCHEMA_VERSION, "runs": {}}, "runs must be a list"),
    ),
)
def test_validate_history_rejects_bad_documents(value: Any, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        validate_history(value)


def test_validate_history_rejects_duplicate_run_ids_and_sorts_runs() -> None:
    later = _run(run_id="e2e-report-later", started_at="2026-08-02T00:00:00Z")
    earlier = _run(run_id="e2e-report-earlier", started_at="2026-08-01T00:00:00Z")
    normalized = validate_history({"schema_version": SCHEMA_VERSION, "runs": [later, earlier]})
    assert [run["run_id"] for run in normalized["runs"]] == ["e2e-report-earlier", "e2e-report-later"]

    with pytest.raises(ToolError, match="Duplicate timing run ID: e2e-report-later"):
        validate_history({"schema_version": SCHEMA_VERSION, "runs": [later, dict(later)]})


def test_append_rejects_conflicting_measurements_for_same_run(tmp_path: Path) -> None:
    history = tmp_path / "history.json"
    report = tmp_path / "report.md"
    append_result(history, report, _result())
    with pytest.raises(ToolError, match="already exists with different measurements"):
        append_result(history, report, _result(status="failed", failure_phase="validation"))
    assert load_history(history)["runs"][0]["status"] == "passed"


def test_regenerate_report_rewrites_markdown_from_history(tmp_path: Path) -> None:
    history = tmp_path / "history.json"
    report = tmp_path / "report.md"
    append_result(history, report, _result())
    report.write_text("stale\n", encoding="utf-8")

    regenerate_report(history, report)

    content = report.read_text(encoding="utf-8")
    assert content == render_markdown(load_history(history))
    assert "1m 05s" in content


def test_update_cleanup_result_returns_false_for_unknown_run(tmp_path: Path) -> None:
    history = tmp_path / "history.json"
    report = tmp_path / "report.md"
    updated = update_cleanup_result(
        history,
        report,
        run_id="e2e-missing-run",
        cleanup_status="complete",
        residuals=(),
        phase=PhaseTiming("cleanup-retry", 1.0, "local-monotonic-clock"),
        finished_at="2026-08-01T13:00:00Z",
    )
    assert updated is False
    assert not history.exists()
    assert not report.exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"run_id": ""}, "run_id must be a non-empty string"),
        ({"schema_version": 2}, "schema version"),
        ({"run_id": "BAD_ID"}, "run ID has an invalid format"),
        ({"git_commit": "not-hex"}, "hexadecimal commit"),
        ({"branch": "-leading-dash"}, "branch has an invalid format"),
        ({"repository": "no-slash"}, "repository has an invalid format"),
        ({"safe_stack_id": "OpenemrE2E-abc"}, "stack identifier must be a one-way hash"),
        ({"configuration_fingerprint": "sha256:short"}, "configuration fingerprint is invalid"),
        ({"status": "skipped"}, "Unsupported run status: skipped"),
        ({"cleanup_status": "partial"}, "Unsupported cleanup status: partial"),
        ({"failure_phase": ""}, "failure phase must be null"),
        ({"metadata": []}, "metadata must be an object"),
        ({"phases": "total"}, "phases must be a list"),
        ({"phases": ["total"]}, "phase must be an object"),
        ({"phases": [{"name": "", "duration_seconds": 1, "source": "x"}]}, "phase name is required"),
        ({"phases": [{"name": "total", "duration_seconds": True, "source": "x"}]}, "duration must be non-negative"),
        ({"phases": [{"name": "total", "duration_seconds": -1, "source": "x"}]}, "duration must be non-negative"),
        ({"phases": [{"name": "total", "duration_seconds": 1, "source": ""}]}, "phase source is required"),
        ({"checks": ["ok"]}, "check must be an object"),
        ({"checks": [{"name": "a", "status": "pass", "detail": 3}]}, "check detail must be a string"),
        ({"residuals": ["s3"]}, "Residual resource must be an object"),
        (
            {"residuals": [{"resource_type": "s3", "identifier_hash": "", "disposition": "x"}]},
            "identifier_hash must be a non-empty string",
        ),
        ({"notes": ["ok", 3]}, "notes must contain only strings"),
        ({"notes": "free text"}, "notes must contain only strings"),
    ),
)
def test_validate_run_rejects_malformed_fields(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ToolError, match=message):
        _validate_run(_run(**overrides))


def test_validate_run_rejects_non_object() -> None:
    with pytest.raises(ToolError, match="Each timing run must be an object"):
        _validate_run(["not", "a", "run"])


def test_validate_run_normalizes_phase_durations() -> None:
    run = _validate_run(_run(phases=[{"name": "total", "duration_seconds": 1.23456, "source": "clock"}]))
    assert run["phases"] == [{"name": "total", "duration_seconds": 1.235, "source": "clock"}]


def test_report_falls_back_to_dash_without_deploy_phases_and_lists_phase_names() -> None:
    run = _validate_run(_run())
    assert _display_first_phase(run, "deployment-with-assets", "cdk-deploy") == "—"
    assert phase_names([run]) == {"total"}
    markdown = render_markdown({"schema_version": SCHEMA_VERSION, "runs": [run]})
    assert "- Total deployment time (including assets): —" in markdown


def test_report_renders_failed_cleanup_residuals_as_unknown() -> None:
    run = _run(status="failed", cleanup_status="failed", failure_phase="cleanup")
    markdown = render_markdown({"schema_version": SCHEMA_VERSION, "runs": [run]})
    assert "No successful live E2E measurement has been recorded." in markdown
    assert "| failed (cleanup) |" in markdown
    assert markdown.rstrip().splitlines()[-1] == "```"
    row = next(line for line in markdown.splitlines() if line.startswith("| e2e-report-coverage |"))
    assert row.endswith("| failed | unknown |")
