"""Edge-case tests for version-audit classification, deferrals, rendering, and CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools._shared import ToolError
from tools.version_audit import audit
from tools.version_audit.__main__ import (
    EXIT_ALL_SOURCES_FAILED,
    EXIT_AUDIT_ERROR,
    EXIT_OK,
    main,
)
from tools.version_audit.models import AuditReport, Declaration, Finding, Status
from tools.version_audit.render import render_human
from tools.version_audit.sources import Resolution

MAIN = "tools.version_audit.__main__"


def _declaration(**overrides: Any) -> Declaration:
    values: dict[str, Any] = {
        "identifier": "python:demo",
        "name": "demo",
        "category": "python-production",
        "current": "1.0.0",
        "definition": "requirements.txt:1",
        "source_kind": "pypi",
        "constraint": "==1.0.0",
        "metadata": {"normalized_name": "demo"},
    }
    values.update(overrides)
    return Declaration(**values)


def _finding(**overrides: Any) -> Finding:
    values: dict[str, Any] = {
        "identifier": "python:demo",
        "name": "demo",
        "category": "python-production",
        "current": "1.0.0",
        "latest": "2.0.0",
        "status": Status.STABLE_UPDATE,
        "definition": "requirements.txt:1",
        "source_kind": "pypi",
        "source_url": "https://pypi.org/pypi/demo/json",
        "note": "original note",
    }
    values.update(overrides)
    return Finding(**values)


def _resolution(latest: str | None, **overrides: Any) -> Resolution:
    return Resolution(latest=latest, source_url="https://example.test", **overrides)


def test_compatible_alias_rejects_non_numeric_current_and_invalid_latest() -> None:
    assert audit._compatible_alias("latest", "1.0.0") is False
    assert audit._compatible_alias("3", "not-a-version") is False
    assert audit._compatible_alias("v3", "3.9.1") is True
    assert audit._compatible_alias("3.9", "3.10.0") is False


def test_classify_flags_conflicting_declarations() -> None:
    declaration = _declaration(metadata={"conflicting_pins": True})

    status, note = audit._classify(declaration, _resolution("1.0.0"))

    assert status is Status.MANUAL_REVIEW
    assert "conflicting" in str(note)


def test_classify_without_latest_is_unable() -> None:
    status, note = audit._classify(_declaration(), _resolution(None, note="lookup skipped"))

    assert (status, note) == (Status.UNABLE, "lookup skipped")


@pytest.mark.parametrize(
    ("constraint", "latest"),
    [("==not a spec!!", "1.0.0"), (">=1.0", "not-a-version")],
)
def test_classify_rejects_uncomparable_python_constraints(constraint: str, latest: str) -> None:
    status, note = audit._classify(_declaration(constraint=constraint), _resolution(latest))

    assert status is Status.MANUAL_REVIEW
    assert "could not be compared" in str(note)


def test_classify_non_semantic_values() -> None:
    same = _declaration(current="stable-tag", constraint=None, source_kind="manual")
    different = _declaration(current="stable-tag", constraint=None, source_kind="manual")

    assert audit._classify(same, _resolution("stable-tag", note="matches"))[0] is Status.CURRENT
    status, note = audit._classify(different, _resolution("other-tag"))
    assert status is Status.MANUAL_REVIEW
    assert "Non-semantic" in str(note)


def test_classify_equal_versions_is_current() -> None:
    declaration = _declaration(current="emr-7.13.0", constraint=None, source_kind="emr-serverless")

    status, note = audit._classify(declaration, _resolution("emr-7.13.0", note="docs"))

    assert (status, note) == (Status.CURRENT, "docs")


def _write_deferrals(root: Path, payload: Any) -> None:
    path = root / "tools" / "version_audit"
    path.mkdir(parents=True)
    (path / "deferrals.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_deferrals_file_means_no_deferrals(tmp_path: Path) -> None:
    assert audit._load_deferrals(tmp_path) == {}


def test_deferrals_preserve_prerequisite_text(tmp_path: Path) -> None:
    _write_deferrals(
        tmp_path,
        {
            "python:demo": {
                "status": "incompatible",
                "reason": " Breaks API ",
                "prerequisite": " Upstream fix ",
                "review_date": "2999-12-31",
            }
        },
    )

    assert audit._load_deferrals(tmp_path) == {
        "python:demo": {
            "status": "incompatible",
            "reason": "Breaks API",
            "prerequisite": "Upstream fix",
            "review_date": "2999-12-31",
        }
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (["not", "an", "object"], "must be a JSON object"),
        ({"python:demo": "text"}, "named JSON object"),
        ({"": {}}, "named JSON object"),
        ({"python:demo": {"status": "deferred", "extra": 1}}, "unknown fields: extra"),
        ({"python:demo": {"status": "current", "reason": "x"}}, "invalid status"),
        (
            {"python:demo": {"status": "deferred", "reason": "x", "prerequisite": 5}},
            "prerequisite must be text",
        ),
        ({"python:demo": {"status": "deferred", "reason": "x"}}, "ISO review_date"),
        (
            {"python:demo": {"status": "deferred", "reason": "x", "review_date": "soon"}},
            "invalid review_date",
        ),
    ],
)
def test_malformed_deferrals_fail_closed(tmp_path: Path, payload: Any, message: str) -> None:
    _write_deferrals(tmp_path, payload)

    with pytest.raises(ValueError, match=message):
        audit._load_deferrals(tmp_path)


def test_apply_deferral_ignores_current_findings() -> None:
    finding = _finding(status=Status.CURRENT)
    deferrals = {"python:demo": {"status": "deferred", "reason": "x"}}

    assert audit._apply_deferral(_declaration(), finding, deferrals) is finding


@pytest.mark.parametrize(
    ("deferral", "expected_status", "expected_note"),
    [
        (
            {"status": "incompatible", "reason": "Breaks", "prerequisite": "Fix", "review_date": "2999-01-01"},
            Status.INCOMPATIBLE,
            "Breaks; Fix; Review by 2999-01-01",
        ),
        ({"status": "bogus", "reason": "Unknown status"}, Status.DEFERRED, "Unknown status"),
        ({"status": "current"}, Status.DEFERRED, "original note"),
    ],
)
def test_apply_deferral_normalizes_status_and_note(
    deferral: dict[str, str],
    expected_status: Status,
    expected_note: str,
) -> None:
    finding = _finding(status=Status.MANUAL_REVIEW)

    deferred = audit._apply_deferral(_declaration(), finding, {"python:demo": deferral})

    assert deferred.status is expected_status
    assert deferred.note == expected_note
    assert deferred.latest == finding.latest
    assert deferred.source_url == finding.source_url


def _run_with_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolution: Resolution,
    declaration: Declaration,
) -> Finding:
    monkeypatch.setattr(audit, "collect_declarations", lambda root: (declaration,))
    monkeypatch.setattr(audit, "_load_deferrals", lambda root: {})

    class FakeSources:
        def __init__(self, client: object):
            pass

        def resolve(self, item: Declaration) -> Resolution:
            return resolution

    monkeypatch.setattr(audit, "VersionSources", FakeSources)
    report = audit.run_audit(tmp_path, generated_at="2026-01-01T00:00:00Z")
    return report.findings[0]


def test_run_audit_reports_newer_prerelease(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    declaration = _declaration(current="3.14", constraint=None, source_kind="python-toolchain")

    finding = _run_with_resolution(
        monkeypatch,
        tmp_path,
        _resolution("3.14.2", latest_prerelease="3.15.0rc1"),
        declaration,
    )

    assert finding.status is Status.PRERELEASE_ONLY
    assert finding.note == "Newer prerelease exists: 3.15.0rc1"


def test_run_audit_keeps_current_when_prerelease_is_uncomparable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    declaration = _declaration(current="3.14", constraint=None, source_kind="python-toolchain")

    finding = _run_with_resolution(
        monkeypatch,
        tmp_path,
        _resolution("3.14.2", latest_prerelease="nightly-build"),
        declaration,
    )

    assert finding.status is Status.CURRENT


def test_render_human_groups_findings_with_errors_and_notes() -> None:
    report = AuditReport(
        generated_at="2026-01-01T00:00:00Z",
        repository_root=".",
        findings=(
            _finding(category="b", name="beta", status=Status.MANUAL_REVIEW),
            _finding(category="a", name="alpha", latest=None, status=Status.UNABLE, error="timeout", note=None),
            _finding(category="a", name="gamma", status=Status.CURRENT, latest="1.0.0", note=None),
        ),
        selected_categories=("a", "b"),
    )

    output = render_human(report)

    assert output.index("[a]") < output.index("[b]")
    assert "- alpha: 1.0.0 -> unknown (Unable to determine)" in output
    assert "  source error: timeout" in output
    assert "  note: original note" in output
    assert "- gamma: 1.0.0 -> 1.0.0 (Current)\n\n[b]" in output
    assert "Total: 3 | actionable: 1" in output
    assert output.endswith("Partial source failure: yes\n")


def _patch_cli(monkeypatch: pytest.MonkeyPatch, root: Path, report: AuditReport) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{MAIN}.repository_root", lambda: root)
    monkeypatch.setattr(
        f"{MAIN}.collect_declarations",
        lambda root: (_declaration(category="python-dev"), _declaration(category="node")),
    )

    def fake_run_audit(*args: Any, **kwargs: Any) -> AuditReport:
        calls.append(kwargs)
        return report

    monkeypatch.setattr(f"{MAIN}.run_audit", fake_run_audit)
    return calls


def _report(*findings: Finding) -> AuditReport:
    return AuditReport(
        generated_at="2026-01-01T00:00:00Z",
        repository_root=".",
        findings=findings,
        selected_categories=("node",),
    )


def test_cli_lists_categories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _patch_cli(monkeypatch, tmp_path, _report())

    assert main(["--list-categories"]) == EXIT_OK
    assert capsys.readouterr().out == "node\npython-dev\n"
    assert calls == []


def test_cli_rejects_unknown_category(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli(monkeypatch, tmp_path, _report())

    with pytest.raises(SystemExit) as raised:
        main(["--category", "node,unknown"])

    assert raised.value.code == 2
    assert "unknown category: unknown; available: node, python-dev" in capsys.readouterr().err


def test_cli_rejects_non_positive_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli(monkeypatch, tmp_path, _report())

    with pytest.raises(SystemExit):
        main(["--timeout", "0"])

    assert "--timeout must be positive" in capsys.readouterr().err


def test_cli_prints_human_report_and_writes_absolute_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _patch_cli(monkeypatch, tmp_path, _report(_finding(category="node")))
    json_path = tmp_path / "out" / "report.json"

    assert main(["--category", "node", "--json", str(json_path), "--timeout", "3", "--timestamp", "T"]) == EXIT_OK

    assert "OpenEMR on ECS version audit" in capsys.readouterr().out
    assert json.loads(json_path.read_text(encoding="utf-8"))["findings"][0]["identifier"] == "python:demo"
    assert calls == [{"categories": ("node",), "timeout_seconds": 3.0, "online": True, "generated_at": "T"}]


def test_cli_fails_when_every_source_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_cli(monkeypatch, tmp_path, _report(_finding(latest=None, status=Status.UNABLE)))

    assert main(["--quiet", "--fail-if-all-sources-fail"]) == EXIT_ALL_SOURCES_FAILED


def test_cli_reports_audit_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail() -> Path:
        raise ToolError("no repository here")

    monkeypatch.setattr(f"{MAIN}.repository_root", fail)

    assert main(["--quiet"]) == EXIT_AUDIT_ERROR
    assert "version audit failed: no repository here" in capsys.readouterr().err
