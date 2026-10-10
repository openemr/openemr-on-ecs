"""Additional fail-closed and CLI tests for the npm audit deferral gate."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import check_npm_audit
from scripts.check_npm_audit import ALLOWED_ADVISORIES, ALLOWED_NODE, ALLOWED_VERSION, validate_report

BEFORE_REVIEW = date(2026, 10, 3)


def _allowed_entry() -> dict[str, Any]:
    return {
        "severity": "high",
        "isDirect": False,
        "nodes": [ALLOWED_NODE],
        "via": [{"source": source, "url": url} for source, url in ALLOWED_ADVISORIES.items()],
    }


def _install(root: Path, version: str = ALLOWED_VERSION) -> None:
    package = root / ALLOWED_NODE / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": version}), encoding="utf-8")
    (root / "package-lock.json").write_text(
        json.dumps({"packages": {ALLOWED_NODE: {"version": version}}}),
        encoding="utf-8",
    )


def test_missing_installed_and_locked_package_fails_closed(tmp_path: Path) -> None:
    failures = validate_report({"vulnerabilities": {}}, tmp_path, today=BEFORE_REVIEW)

    assert len(failures) == 2
    assert failures[0].startswith("cannot verify installed deferred package:")
    assert failures[1].startswith("cannot verify locked deferred package:")


def test_lock_without_deferred_entry_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, version="5.0.12")
    (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {}}), encoding="utf-8")

    failures = validate_report({"vulnerabilities": {}}, tmp_path, today=BEFORE_REVIEW)

    assert failures[0].startswith("cannot verify locked deferred package:")
    assert "install/lock mismatch: installed 5.0.12, locked None" in failures[1]


def test_non_object_advisory_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path)

    failures = validate_report({"vulnerabilities": {"brace-expansion": "high"}}, tmp_path, today=BEFORE_REVIEW)

    assert failures == ["brace-expansion advisory is not an object"]


def test_changed_severity_or_directness_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path)
    entry = _allowed_entry()
    entry["isDirect"] = True

    failures = validate_report({"vulnerabilities": {"brace-expansion": entry}}, tmp_path, today=BEFORE_REVIEW)

    assert failures == ["brace-expansion severity/directness changed"]


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    completed: subprocess.CompletedProcess[str],
) -> tuple[int, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return completed

    class FixedDatetime:
        @staticmethod
        def now(tz: timezone) -> datetime:
            return datetime(2026, 10, 3, tzinfo=tz)

    script = tmp_path / "scripts" / "check_npm_audit.py"
    monkeypatch.setattr(check_npm_audit, "__file__", str(script))
    monkeypatch.setattr(check_npm_audit, "subprocess", SimpleNamespace(run=fake_run))
    monkeypatch.setattr(check_npm_audit, "datetime", FixedDatetime)
    return check_npm_audit.main(), calls


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["npm", "audit", "--json"], returncode, stdout, stderr)


def test_main_reports_clean_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install(tmp_path, version="5.0.12")

    exit_code, calls = _run_main(monkeypatch, tmp_path, _completed(json.dumps({"vulnerabilities": {}})))

    assert exit_code == 0
    assert capsys.readouterr().out == "npm audit: no vulnerabilities\n"
    assert calls == [
        {
            "argv": ["npm", "audit", "--json"],
            "cwd": tmp_path.resolve(),
            "check": False,
            "capture_output": True,
            "text": True,
        }
    ]


def test_main_reports_active_deferral(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install(tmp_path)
    report = {"vulnerabilities": {"brace-expansion": _allowed_entry()}}

    exit_code, _ = _run_main(monkeypatch, tmp_path, _completed(json.dumps(report), returncode=1))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert output.startswith("npm audit: temporarily deferred ")
    assert ALLOWED_NODE in output
    assert "review by 2026-11-05" in output


def test_main_lists_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install(tmp_path, version="5.0.12")
    report = {"vulnerabilities": {"left-pad": {}}}

    exit_code, _ = _run_main(monkeypatch, tmp_path, _completed(json.dumps(report), returncode=1))

    assert exit_code == 1
    assert capsys.readouterr().err == "- unexpected vulnerable package: left-pad\n"


def test_main_treats_empty_output_as_empty_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, _ = _run_main(monkeypatch, tmp_path, _completed(""))

    assert exit_code == 1
    assert "npm audit report has no vulnerabilities object" in capsys.readouterr().err


@pytest.mark.parametrize(("returncode", "expected"), [(7, 7), (0, 1)])
def test_main_passes_through_unparseable_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    returncode: int,
    expected: int,
) -> None:
    exit_code, _ = _run_main(
        monkeypatch,
        tmp_path,
        _completed("npm ERR! network", returncode=returncode, stderr="details\n"),
    )

    assert exit_code == expected
    assert capsys.readouterr().err == "npm ERR! networkdetails\n"
