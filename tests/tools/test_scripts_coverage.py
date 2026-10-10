"""Tests for hyphenated helper scripts loaded directly from their file paths."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPOSITORY / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def extractor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, Path]:
    module = _load("extract_startup_script_under_test", "scripts/extract-startup-script.py")
    output = tmp_path / "startup_script.sh"
    real_open = open

    def redirected_open(path: str, *args: Any, **kwargs: Any) -> Any:
        if path == "/tmp/startup_script.sh":  # noqa: S108
            return real_open(output, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(module, "open", redirected_open, raising=False)
    monkeypatch.chdir(tmp_path)
    return module, output


def _write_compute(root: Path, content: str) -> None:
    (root / "openemr_ecs").mkdir(exist_ok=True)
    (root / "openemr_ecs" / "compute.py").write_text(content, encoding="utf-8")


def test_extract_startup_script_writes_string_commands(
    extractor: tuple[ModuleType, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module, output = extractor
    _write_compute(
        tmp_path,
        "other = ['ignored']\n"
        "def build():\n"
        "    startup_commands = ['echo one', 42, 'echo two']\n"
        "    later = startup_commands\n",
    )

    assert module.extract_startup_script() is True

    assert output.read_text(encoding="utf-8") == "#!/bin/sh\nset -e\nset -x\necho one\necho two\n"
    assert capsys.readouterr().out == "Extracted 2 commands to /tmp/startup_script.sh\n"


def test_extract_startup_script_requires_compute_file(
    extractor: tuple[ModuleType, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    module, output = extractor

    assert module.extract_startup_script() is False
    assert "openemr_ecs/compute.py not found" in capsys.readouterr().err
    assert not output.exists()


def test_extract_startup_script_reports_syntax_errors(
    extractor: tuple[ModuleType, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module, _ = extractor
    _write_compute(tmp_path, "def broken(:\n")

    assert module.extract_startup_script() is False
    assert "Failed to parse compute.py" in capsys.readouterr().err


def test_extract_startup_script_requires_a_list_literal(
    extractor: tuple[ModuleType, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module, output = extractor
    _write_compute(tmp_path, "startup_commands = 'echo not a list'\n")

    assert module.extract_startup_script() is False
    assert "Could not find startup_commands list" in capsys.readouterr().err
    assert not output.exists()


def test_extract_startup_script_reports_write_errors(
    extractor: tuple[ModuleType, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module, _ = extractor
    _write_compute(tmp_path, "startup_commands = ['echo one']\n")
    real_open = module.open

    def deny_output(path: str, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if "w" in mode:
            raise PermissionError("read-only filesystem")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(module, "open", deny_output)

    assert module.extract_startup_script() is False
    assert "Failed to write startup script: read-only filesystem" in capsys.readouterr().err


@pytest.fixture
def synthesis() -> ModuleType:
    return _load("cdk_synthesis_matrix_under_test", "scripts/test-cdk-synthesis.py")


class _Runner:
    """Record subprocess.run calls and return queued results."""

    def __init__(self, *results: subprocess.CompletedProcess[str]):
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return self.results.pop(0)


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _run_configuration(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    runner: _Runner,
    *,
    verbose: bool = False,
) -> tuple[bool, str]:
    monkeypatch.setattr(synthesis, "subprocess", SimpleNamespace(run=runner))
    return synthesis.test_configuration(
        "demo",
        "Demo configuration",
        {"enable_data_api": True, "openemr_service_fargate_cpu": 512},
        root,
        root / "cdk",
        root / "cfn-lint",
        verbose,
    )


def test_context_values_render_booleans_in_lowercase(synthesis: ModuleType) -> None:
    assert synthesis._context_value(True) == "true"
    assert synthesis._context_value(False) == "false"
    assert synthesis._context_value(512) == "512"


def test_configuration_synthesizes_and_lints_templates(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "cdk.out").mkdir()
    (tmp_path / "cdk.out" / "Stack.template.json").write_text("{}", encoding="utf-8")
    runner = _Runner(_result(stdout="  ERROR: indented script text\n"), _result())

    assert _run_configuration(synthesis, monkeypatch, tmp_path, runner) == (True, "")

    synth_argv, synth_kwargs = runner.calls[0]
    assert synth_argv[:2] == [str(tmp_path / "cdk"), "synth"]
    assert synth_argv[-4:] == [
        "--context",
        "enable_data_api=true",
        "--context",
        "openemr_service_fargate_cpu=512",
    ]
    assert synth_kwargs == {"cwd": tmp_path, "capture_output": True, "text": True}
    assert runner.calls[1][0] == [str(tmp_path / "cfn-lint"), str(tmp_path / "cdk.out" / "Stack.template.json")]
    assert "Synthesis and cfn-lint successful for demo" in capsys.readouterr().out


def test_configuration_fails_on_unacknowledged_nag_errors(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _Runner(_result(stderr="ERROR AwsSolutions-IAM5 wildcard\n"))

    result = _run_configuration(synthesis, monkeypatch, tmp_path, runner, verbose=True)

    assert result == (False, "CDK Nag errors found in output")
    output = capsys.readouterr().out
    assert "--- CDK Nag Errors ---\nERROR AwsSolutions-IAM5 wildcard\n" in output
    assert len(runner.calls) == 1


def test_configuration_requires_a_template(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _Runner(_result())

    assert _run_configuration(synthesis, monkeypatch, tmp_path, runner) == (
        False,
        "Synthesis produced no CloudFormation template",
    )


def test_configuration_reports_cfn_lint_failures(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "cdk.out").mkdir()
    (tmp_path / "cdk.out" / "Stack.template.json").write_text("{}", encoding="utf-8")
    runner = _Runner(_result(), _result(returncode=2, stdout="E3012 bad type", stderr="lint stderr"))

    result = _run_configuration(synthesis, monkeypatch, tmp_path, runner, verbose=True)

    assert result == (False, "cfn-lint failed for demo")
    output = capsys.readouterr().out
    assert "--- cfn-lint Output ---\nE3012 bad type\nlint stderr\n" in output


def test_configuration_reports_synthesis_failures(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _Runner(_result(returncode=1, stdout="partial", stderr="Traceback: boom"))

    result = _run_configuration(synthesis, monkeypatch, tmp_path, runner, verbose=True)

    assert result == (False, "Traceback: boom")
    assert "--- Error Output ---\nTraceback: boom\npartial\n" in capsys.readouterr().out


def _prepare_main(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    *,
    cdk_json: bool = True,
    cdk: bool = True,
    cfn_lint: bool = True,
) -> Path:
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    if cdk_json:
        (root / "cdk.json").write_text("{}", encoding="utf-8")
    if cdk:
        (root / "node_modules" / ".bin").mkdir(parents=True)
        (root / "node_modules" / ".bin" / "cdk").write_text("", encoding="utf-8")
    python_bin = tmp_path / "venv" / "bin"
    python_bin.mkdir(parents=True)
    if cfn_lint:
        (python_bin / "cfn-lint").write_text("", encoding="utf-8")
    monkeypatch.setattr(synthesis, "__file__", str(root / "scripts" / "test-cdk-synthesis.py"))
    monkeypatch.setattr(sys, "executable", str(python_bin / "python"))
    monkeypatch.setattr(sys, "argv", ["test-cdk-synthesis.py", *argv])
    monkeypatch.setattr(
        synthesis,
        "TEST_CONFIGURATIONS",
        [
            {"name": "first", "description": "First", "config": {}},
            {"name": "second", "description": "Second", "config": {"enable_ecs_exec": True}},
        ],
    )
    return root


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("cdk_json", "cdk.json not found"),
        ("cdk", "Pinned CDK CLI is missing"),
        ("cfn_lint", "Pinned cfn-lint is missing"),
    ],
)
def test_main_requires_pinned_inputs(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    missing: str,
    message: str,
) -> None:
    _prepare_main(synthesis, monkeypatch, tmp_path, [], **{missing: False})
    monkeypatch.setattr(synthesis, "test_configuration", lambda *args: pytest.fail("must not synthesize"))

    assert synthesis.main() == 1
    assert message in capsys.readouterr().out


def test_main_runs_every_configuration(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _prepare_main(synthesis, monkeypatch, tmp_path, [])
    calls: list[tuple[Any, ...]] = []

    def fake_configuration(*args: Any) -> tuple[bool, str]:
        calls.append(args)
        return True, ""

    monkeypatch.setattr(synthesis, "test_configuration", fake_configuration)

    assert synthesis.main() == 0

    assert [call[0] for call in calls] == ["first", "second"]
    assert calls[1][3:] == (
        root,
        root / "node_modules" / ".bin" / "cdk",
        tmp_path / "venv" / "bin" / "cfn-lint",
        False,
    )
    assert "All tests passed!" in capsys.readouterr().out


def test_main_stops_on_first_failure_and_prints_errors(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare_main(synthesis, monkeypatch, tmp_path, ["--fail-fast", "--verbose"])
    calls: list[str] = []

    def fake_configuration(name: str, *args: Any) -> tuple[bool, str]:
        calls.append(name)
        return False, "x" * 250

    monkeypatch.setattr(synthesis, "test_configuration", fake_configuration)

    assert synthesis.main() == 1

    output = capsys.readouterr().out
    assert calls == ["first"]
    assert "Stopping on first failure (--fail-fast)" in output
    assert f"  - first\n    Error: {'x' * 200}\n" in output


def test_main_continues_after_failures_without_fail_fast(
    synthesis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare_main(synthesis, monkeypatch, tmp_path, [])
    results = iter([(False, "first failed"), (True, "")])
    monkeypatch.setattr(synthesis, "test_configuration", lambda *args: next(results))

    assert synthesis.main() == 1

    output = capsys.readouterr().out
    assert "Some tests failed:\n  - first\n" in output
    assert "Error:" not in output
    assert "Passed:\x1b[0m 1" in output


def test_log_helpers_use_ansi_prefixes(synthesis: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    synthesis.log("info")
    synthesis.warning("careful")

    assert capsys.readouterr().out == "\x1b[0;34m[INFO]\x1b[0m info\n\x1b[1;33m⚠\x1b[0m careful\n"
