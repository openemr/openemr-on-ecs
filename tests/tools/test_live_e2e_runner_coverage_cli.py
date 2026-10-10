"""CLI dispatch tests for live E2E with a fake runner (never touches AWS)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools._shared import ToolError
from tools.live_e2e import cli
from tools.live_e2e.models import PhaseTiming
from tools.live_e2e.runner import (
    ACCOUNT_CONFIRMATION,
    CREATE_CONFIRMATION,
    DESTROY_CONFIRMATION,
    ZONE_CONFIRMATION,
)

_SCOPE = ["--approved-account", "123456789012", "--region", "us-east-1"]
_PREFLIGHT = [
    *_SCOPE,
    "--route53-domain",
    "e2e.openemr.org",
    "--allowed-ipv4-cidr",
    "8.8.8.8/32",
    "--confirm-dedicated-zone",
    ZONE_CONFIRMATION,
    "--confirm-non-production-account",
    ACCOUNT_CONFIRMATION,
]
_RUN = [
    "run",
    "--preflight",
    ".live-e2e/preflight/e2e-cli-run.json",
    "--approved-account",
    "123456789012",
    "--confirm-create",
    CREATE_CONFIRMATION,
    "--confirm-destroy",
    DESTROY_CONFIRMATION,
    "--confirm-costs",
    "--noninteractive",
]
_CLEANUP = [
    "cleanup",
    *_SCOPE,
    "--run-id",
    "e2e-cli-run",
    "--confirm-destroy",
    DESTROY_CONFIRMATION,
    "--noninteractive",
]


class _FakeRunner:
    instances: list[_FakeRunner] = []
    result: Any = None
    cleanup_result: tuple[str, int] = ("complete", 0)
    error: BaseException | None = None

    def __init__(self, *, root: Path, progress: Any) -> None:
        self.root = root
        self.progress = progress
        self.calls: list[tuple[str, dict[str, Any]]] = []
        _FakeRunner.instances.append(self)

    def _maybe_fail(self) -> None:
        if _FakeRunner.error is not None:
            raise _FakeRunner.error

    def preflight(self, **kwargs: Any) -> Path:
        self.calls.append(("preflight", kwargs))
        self._maybe_fail()
        path = self.root / ".live-e2e" / "preflight" / "e2e-cli-run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"run_id": "e2e-cli-run", "resource_count": 42, "checks": [{}, {}, {}]}),
            encoding="utf-8",
        )
        return path

    def run(self, **kwargs: Any) -> Any:
        self.calls.append(("run", kwargs))
        self._maybe_fail()
        return _FakeRunner.result

    def cleanup(self, **kwargs: Any) -> tuple[str, int]:
        self.calls.append(("cleanup", kwargs))
        self._maybe_fail()
        return _FakeRunner.cleanup_result

    def regenerate_report(self) -> None:
        self.calls.append(("report", {}))
        history = self.root / "e2e-results" / "history.json"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text('{"runs": [], "schema_version": 1}\n\n', encoding="utf-8")


@pytest.fixture
def fake_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> type[_FakeRunner]:
    _FakeRunner.instances = []
    _FakeRunner.result = None
    _FakeRunner.cleanup_result = ("complete", 0)
    _FakeRunner.error = None
    monkeypatch.setattr(cli, "LiveE2ERunner", _FakeRunner)
    monkeypatch.setattr(cli, "repository_root", lambda: tmp_path)
    return _FakeRunner


def _run_result(status: str, phases: tuple[PhaseTiming, ...]) -> Any:
    return SimpleNamespace(
        run_id="e2e-cli-run",
        status=status,
        cleanup_status="complete",
        residuals=(),
        phases=phases,
        to_dict=lambda: {"run_id": "e2e-cli-run", "status": status},
    )


def test_preflight_json_reports_relative_path_and_forwards_options(
    fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["preflight", *_PREFLIGHT, "--run-id", "e2e-cli-run", "--noninteractive", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "preflight_path": ".live-e2e/preflight/e2e-cli-run.json",
        "resource_count": 42,
        "run_id": "e2e-cli-run",
        "status": "passed",
    }
    name, kwargs = fake_runner.instances[0].calls[0]
    assert name == "preflight"
    assert kwargs == {
        "approved_account": "123456789012",
        "region": "us-east-1",
        "route53_domain": "e2e.openemr.org",
        "allowed_ipv4_cidr": "8.8.8.8/32",
        "profile": "default",
        "aws_profile": None,
        "cdk_command": "node_modules/.bin/cdk",
        "bootstrap_stack_name": "CDKToolkit",
        "confirm_dedicated_zone": ZONE_CONFIRMATION,
        "confirm_non_production_account": ACCOUNT_CONFIRMATION,
        "run_id": "e2e-cli-run",
        "require_tty": False,
    }


def test_plan_human_output_with_verbose_counts(
    fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["plan", *_PREFLIGHT, "--verbose"])

    assert code == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "Preflight passed for run e2e-cli-run.",
        "Owner-only approval file: .live-e2e/preflight/e2e-cli-run.json",
        "No application stack or other AWS resource was created.",
        "Validated 3 checks and 42 synthesized resources.",
    ]
    assert fake_runner.instances[0].calls[0][1]["require_tty"] is True
    assert fake_runner.instances[0].progress.verbose is True


def test_run_json_returns_status_exit_code(fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]) -> None:
    fake_runner.result = _run_result("failed", ())
    assert cli.main([*_RUN, "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"run_id": "e2e-cli-run", "status": "failed"}

    fake_runner.result = _run_result("passed", ())
    assert cli.main([*_RUN, "--json", "--keep-on-failure", "--confirm-keep-on-failure", "x"]) == 0
    kwargs = fake_runner.instances[-1].calls[0][1]
    assert kwargs["keep_on_failure"] is True
    assert kwargs["confirm_keep_on_failure"] == "x"
    assert kwargs["preflight_path"] == Path(".live-e2e/preflight/e2e-cli-run.json")
    assert kwargs["deploy_timeout_seconds"] == 90 * 60
    assert kwargs["poll_seconds"] == 20
    assert kwargs["require_tty"] is False


def test_run_human_output_prints_measured_and_unmeasured_timings(
    fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_runner.result = _run_result(
        "passed",
        (
            PhaseTiming("total", 100.0, "clock"),
            PhaseTiming("cdk-deploy", 50.5, "clock"),
            PhaseTiming("cleanup", 12.25, "clock"),
        ),
    )

    assert cli.main([*_RUN, "--verbose"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out == [
        "Run e2e-cli-run: passed; cleanup=complete; residuals=0",
        "Timing (seconds): total=100.000; deploy=50.500; ecs-create=not-measured; "
        "https-ready=not-measured; cleanup=12.250",
        "Owner-only diagnostics: .live-e2e/runs/e2e-cli-run/",
    ]


def test_run_human_output_prefers_deployment_with_assets_and_reports_failure(
    fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_runner.result = _run_result(
        "failed",
        (
            PhaseTiming("deployment-with-assets", 70.0, "clock"),
            PhaseTiming("cdk-deploy", 50.0, "clock"),
        ),
    )
    assert cli.main(_RUN) == 1
    out = capsys.readouterr().out
    assert "deploy=70.000" in out
    assert "total=not-measured" in out
    assert "Owner-only diagnostics" not in out


def test_run_rejects_non_positive_timeouts_before_running(
    fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([*_RUN, "--poll-seconds", "0"]) == 2
    assert "ERROR: poll interval must be positive" in capsys.readouterr().err
    assert fake_runner.instances[0].calls == []


@pytest.mark.parametrize(
    ("status", "code"),
    (
        ("complete", 0),
        ("stack-deleted-with-expected-residuals", 0),
        ("not-required", 0),
        ("failed", 1),
    ),
)
def test_cleanup_json_exit_codes(
    fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str], status: str, code: int
) -> None:
    fake_runner.cleanup_result = (status, 2)
    assert cli.main([*_CLEANUP, "--json"]) == code
    assert json.loads(capsys.readouterr().out) == {
        "cleanup_status": status,
        "residual_count": 2,
        "run_id": "e2e-cli-run",
    }
    kwargs = fake_runner.instances[0].calls[0][1]
    assert kwargs == {
        "run_id": "e2e-cli-run",
        "approved_account": "123456789012",
        "region": "us-east-1",
        "aws_profile": None,
        "confirm_destroy": DESTROY_CONFIRMATION,
        "timeout_seconds": 60 * 60,
        "poll_seconds": 20,
        "require_tty": False,
    }


def test_cleanup_human_output_verbose(fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([*_CLEANUP, "--verbose"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Cleanup e2e-cli-run: complete; residuals=0",
        "Owner-only diagnostics: .live-e2e/runs/e2e-cli-run/",
    ]
    assert cli.main(_CLEANUP) == 0
    assert capsys.readouterr().out.splitlines() == ["Cleanup e2e-cli-run: complete; residuals=0"]


def test_report_command_prints_history_or_summary(
    fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["report", "--json"]) == 0
    assert capsys.readouterr().out == '{"runs": [], "schema_version": 1}\n'
    assert cli.main(["report"]) == 0
    assert capsys.readouterr().out == "Regenerated docs/maintainers/deployment-timing.md.\n"
    assert [instance.calls for instance in fake_runner.instances] == [[("report", {})], [("report", {})]]


def test_profiles_command_lists_profiles(fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["profiles", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"profiles": ["api-enabled", "default"]}
    assert cli.main(["profiles"]) == 0
    assert capsys.readouterr().out == "api-enabled\ndefault\n"


@pytest.mark.parametrize("error", (ToolError("boom tool"), ValueError("boom value")))
def test_tool_and_value_errors_exit_with_code_two(
    fake_runner: type[_FakeRunner], capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    fake_runner.error = error
    assert cli.main(_CLEANUP) == 2
    assert capsys.readouterr().err.strip().endswith(f"ERROR: {error}")


def test_unknown_parsed_command_is_a_programming_error(
    fake_runner: type[_FakeRunner], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Parser(argparse.ArgumentParser):
        def parse_args(self, *_a: Any, **_k: Any) -> Any:  # type: ignore[override]
            return argparse.Namespace(command="unexpected")

    monkeypatch.setattr(cli, "build_parser", Parser)
    with pytest.raises(AssertionError, match="unreachable"):
        cli.main([])
    assert fake_runner.instances[0].calls == []


def test_timing_helpers() -> None:
    assert cli._positive(1.5, "x") == 1.5
    with pytest.raises(ToolError, match="cleanup timeout must be positive"):
        cli._positive(-1, "cleanup timeout")
    assert cli._timing({"total": 1.0}, "total") == "1.000"
    assert cli._timing({}, "total") == "not-measured"
    assert cli._timing_first({"b": 2.0}, "a", "b") == "2.000"
    assert cli._timing_first({}, "a", "b") == "not-measured"
