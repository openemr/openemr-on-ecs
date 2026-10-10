"""Unit tests for live E2E runner helpers and guarded local methods (no AWS, network, or Docker)."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import tools.live_e2e.runner as runner_module
from tools._shared import CommandResult, ToolError
from tools.live_e2e.emulator import AWS_ENDPOINT_ENVIRONMENT, FLOCI_E2E_ENVIRONMENT
from tools.live_e2e.models import ResidualResource
from tools.live_e2e.runner import (
    _CI_ENVIRONMENT_SIGNALS,
    LiveE2ERunner,
    _assembly_resource_inventory,
    _assembly_template,
    _assembly_versions,
    _cdk_failure_message,
    _cleanup_status,
    _directory_fingerprint,
    _docker_build_duration,
    _file_sha256,
    _merge_residuals,
    _parse_timestamp,
    _repository_slug,
    _resolve_cdk_executable,
    _resolve_executable,
    _timestamp,
    _validate_e2e_template,
    deployment_contexts,
    new_run_id,
    stack_name,
    validate_inputs,
    validate_run_id,
)


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*_CI_ENVIRONMENT_SIGNALS, FLOCI_E2E_ENVIRONMENT, AWS_ENDPOINT_ENVIRONMENT, "AWS_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)


def _root(path: Path) -> Path:
    (path / "cdk.json").write_text("{}\n", encoding="utf-8")
    (path / "openemr_ecs").mkdir()
    return path


def _executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _no_aws(**_: Any) -> Any:
    pytest.fail("AWS must not be called")


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def time_ns(self) -> int:
        return 1


class _ReadFailPath(type(Path())):  # type: ignore[misc]
    """Path whose reads fail for names in ``fail_names`` while metadata calls still work."""

    fail_names: frozenset[str] = frozenset()

    def read_text(self, *args: Any, **kwargs: Any) -> str:
        if self.name in self.fail_names:
            raise OSError("read failed")
        return super().read_text(*args, **kwargs)

    def stat(self, *, follow_symlinks: bool = True) -> Any:
        if f"stat:{self.name}" in self.fail_names:
            raise OSError("stat failed")
        return super().stat(follow_symlinks=follow_symlinks)


def _fail_path(path: Path, *names: str) -> Path:
    cls = type("FailPath", (_ReadFailPath,), {"fail_names": frozenset(names)})
    return cls(path)


# --- local toolchain checks --------------------------------------------------------------


def _tool_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = _root(tmp_path)
    cdk = _executable(root / "node_modules" / ".bin" / "cdk")
    assets = _executable(root / "node_modules" / ".bin" / "cdk-assets")
    return root, cdk, assets


def _tool_outputs(cdk: Path, assets: Path) -> dict[str, CommandResult]:
    def ok(argv: tuple[str, ...], stdout: str, stderr: str = "") -> CommandResult:
        return CommandResult(argv, 0, stdout, stderr, 0.01)

    return {
        "git": ok(("git",), "git version 2.50.0\n"),
        "aws": ok(("aws",), "", "aws-cli/2.27.0 Python/3.13\nsecond line\n"),
        "node": ok(("node",), "v24.11.0\n"),
        "docker": ok(("docker",), "28.0.0\n"),
        str(cdk): ok((str(cdk),), "2.1135.1 (build abc)\n"),
        str(assets): ok((str(assets),), "4.7.0\n"),
    }


def test_local_checks_record_redacted_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, cdk, assets = _tool_root(tmp_path)
    outputs = _tool_outputs(cdk, assets)
    seen: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...], *, cwd: Path, timeout_seconds: float) -> CommandResult:
        seen.append(tuple(argv))
        assert cwd == root
        assert timeout_seconds == 30
        return outputs[str(argv[0])]

    monkeypatch.setattr(runner_module, "run_command", fake_run)
    monkeypatch.setattr(runner_module, "shutil", SimpleNamespace(which=lambda command: f"/usr/bin/{command}"))

    checks, versions = LiveE2ERunner(root=root)._local_checks(str(cdk))

    assert [check.name for check in checks] == [
        "local-git",
        "local-aws-cli",
        "local-node",
        "local-docker",
        "local-cdk",
        "local-cdk-assets",
    ]
    assert checks[1].detail == "aws-cli/2.27.0 Python/3.13"
    assert versions["node_version"] == "v24.11.0"
    assert versions["cdk_cli_version"] == "2.1135.1 (build abc)"
    assert versions["cdk_assets_version"] == "4.7.0"
    assert versions["python_version"].startswith("3.14")
    assert versions["cdk_library_version"]
    assert seen[3] == ("docker", "info", "--format", "{{.ServerVersion}}")


def test_local_checks_require_supported_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, cdk, _ = _tool_root(tmp_path)
    monkeypatch.setattr(runner_module, "SUPPORTED_PYTHON", (2, 7))
    monkeypatch.setattr(runner_module, "run_command", lambda *_a, **_k: pytest.fail("no tool checks"))
    with pytest.raises(ToolError, match=r"Python 3\.14\.x"):
        LiveE2ERunner(root=root)._local_checks(str(cdk))


def test_local_checks_reject_missing_pinned_cdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    monkeypatch.setattr(
        runner_module,
        "shutil",
        SimpleNamespace(which=lambda command: None if command.startswith("/") else f"/usr/bin/{command}"),
    )
    monkeypatch.setattr(
        runner_module,
        "run_command",
        lambda argv, **_: CommandResult(tuple(argv), 0, "v24.0.0\n", "", 0.01),
    )
    missing = root / "node_modules" / ".bin" / "cdk"
    with pytest.raises(ToolError, match="Pinned local cdk is unavailable .* run npm ci"):
        LiveE2ERunner(root=root)._local_checks(str(missing))


def test_local_checks_reject_missing_system_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, cdk, _ = _tool_root(tmp_path)
    monkeypatch.setattr(runner_module, "shutil", SimpleNamespace(which=lambda command: None))
    with pytest.raises(ToolError, match="Required local executable is unavailable: git"):
        LiveE2ERunner(root=root)._local_checks(str(cdk))


def test_local_checks_reject_failing_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, cdk, _ = _tool_root(tmp_path)
    monkeypatch.setattr(runner_module, "shutil", SimpleNamespace(which=lambda command: f"/usr/bin/{command}"))
    monkeypatch.setattr(
        runner_module,
        "run_command",
        lambda argv, **_: CommandResult(tuple(argv), 0 if argv[0] == "git" else 1, "x\n", "", 0.01),
    )
    with pytest.raises(ToolError, match="Required local tool check failed: aws-cli"):
        LiveE2ERunner(root=root)._local_checks(str(cdk))


# --- git helpers --------------------------------------------------------------------------


def _git(monkeypatch: pytest.MonkeyPatch, responses: dict[str, CommandResult]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...], **kwargs: Any) -> CommandResult:
        calls.append(tuple(argv))
        assert kwargs["timeout_seconds"] == 30
        return responses[argv[1]]

    monkeypatch.setattr(runner_module, "run_command", fake_run)
    return calls


def _cmd(returncode: int = 0, stdout: str = "") -> CommandResult:
    return CommandResult(("git",), returncode, stdout, "", 0.01)


def test_git_commit_and_clean_returns_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _git(monkeypatch, {"status": _cmd(stdout=""), "rev-parse": _cmd(stdout="c" * 40 + "\n")})
    assert LiveE2ERunner(root=_root(tmp_path))._git_commit_and_clean() == "c" * 40
    assert calls == [("git", "status", "--porcelain"), ("git", "rev-parse", "HEAD")]


@pytest.mark.parametrize(
    ("responses", "message"),
    (
        ({"status": _cmd(returncode=128)}, "Cannot inspect Git worktree"),
        ({"status": _cmd(stdout=" M app.py\n")}, "clean Git worktree"),
        ({"status": _cmd(), "rev-parse": _cmd(stdout="not-a-sha\n")}, "Cannot resolve the Git commit under test"),
        ({"status": _cmd(), "rev-parse": _cmd(returncode=1, stdout="c" * 40)}, "Cannot resolve the Git commit"),
    ),
)
def test_git_commit_and_clean_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, responses: dict[str, CommandResult], message: str
) -> None:
    _git(monkeypatch, responses)
    with pytest.raises(ToolError, match=message):
        LiveE2ERunner(root=_root(tmp_path))._git_commit_and_clean()


@pytest.mark.parametrize(
    ("responses", "message"),
    (
        ({"status": _cmd(returncode=1)}, "Cannot inspect Git worktree"),
        ({"status": _cmd(stdout="M\n")}, "Cannot safely interpret Git worktree state"),
        ({"status": _cmd(stdout=""), "rev-parse": _cmd(stdout="xyz")}, "commit used for cleanup"),
    ),
)
def test_git_commit_for_cleanup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, responses: dict[str, CommandResult], message: str
) -> None:
    _git(monkeypatch, responses)
    with pytest.raises(ToolError, match=message):
        LiveE2ERunner(root=_root(tmp_path))._git_commit_for_cleanup()


def test_git_branch_and_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = LiveE2ERunner(root=_root(tmp_path))
    calls = _git(
        monkeypatch,
        {
            "branch": _cmd(stdout="feature/live-e2e\n"),
            "remote": _cmd(stdout="ssh://git@github.com/openemr/openemr-on-ecs.git\n"),
        },
    )
    assert runner._git_branch_and_repository() == ("feature/live-e2e", "openemr/openemr-on-ecs")
    assert calls == [("git", "branch", "--show-current"), ("git", "remote", "get-url", "origin")]

    _git(monkeypatch, {"branch": _cmd(stdout="\n")})
    with pytest.raises(ToolError, match="named Git branch"):
        runner._git_branch_and_repository()

    _git(monkeypatch, {"branch": _cmd(stdout="main\n"), "remote": _cmd(returncode=2)})
    with pytest.raises(ToolError, match="verified origin repository"):
        runner._git_branch_and_repository()


# --- adapter / environment guards --------------------------------------------------------


def test_aws_adapter_refuses_unguarded_endpoint_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AWS_ENDPOINT_ENVIRONMENT, "http://localhost:4566")
    runner = LiveE2ERunner(root=_root(tmp_path), aws_factory=_no_aws)
    with pytest.raises(ToolError, match="explicit guarded Floci mode"):
        runner._aws_adapter(region="us-east-1", profile_name=None)


def test_aws_adapter_floci_mode_passes_emulator_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLOCI_E2E_ENVIRONMENT, "1")
    monkeypatch.setenv(AWS_ENDPOINT_ENVIRONMENT, "http://localhost:4566")
    captured: dict[str, Any] = {}

    class Slotted:
        __slots__ = ()

    def factory(**kwargs: Any) -> Slotted:
        captured.update(kwargs)
        return Slotted()

    adapter = LiveE2ERunner(root=_root(tmp_path), aws_factory=factory)._aws_adapter(
        region="us-west-2", profile_name="e2e"
    )

    assert isinstance(adapter, Slotted)
    assert captured == {
        "region": "us-west-2",
        "profile_name": "e2e",
        "endpoint_url": "http://localhost:4566",
        "emulated": True,
    }


def test_aws_adapter_attaches_progress_reporter(tmp_path: Path) -> None:
    adapter = SimpleNamespace()
    runner = LiveE2ERunner(root=_root(tmp_path), aws_factory=lambda **_: adapter)
    assert runner._aws_adapter(region="us-east-1", profile_name=None) is adapter
    assert adapter.progress is runner.progress


def test_cdk_emulator_environment_forwards_floci_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLOCI_E2E_ENVIRONMENT, "true")
    monkeypatch.setenv(AWS_ENDPOINT_ENVIRONMENT, "http://localhost:4566")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "floci-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "floci-token")

    env = LiveE2ERunner._cdk_emulator_environ()

    assert env == {
        "AWS_ENDPOINT_URL": "http://localhost:4566",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "floci-secret",
        "AWS_SESSION_TOKEN": "floci-token",
        "CDK_DISABLE_CLI_TELEMETRY": "true",
        "JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION": "1",
    }


def test_cdk_emulator_environment_is_empty_outside_floci() -> None:
    assert LiveE2ERunner._cdk_emulator_environ() == {}


def test_local_execution_requires_interactive_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(ToolError, match="interactive local terminal"):
        LiveE2ERunner._assert_local_execution(require_tty=True)
    LiveE2ERunner._assert_local_execution(require_tty=False)


def test_local_execution_rejects_unguarded_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    with pytest.raises(ToolError, match="explicit guarded Floci mode"):
        LiveE2ERunner._assert_local_execution(require_tty=False)


def test_local_execution_allows_ci_in_guarded_floci_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv(FLOCI_E2E_ENVIRONMENT, "1")
    monkeypatch.setenv(AWS_ENDPOINT_ENVIRONMENT, "http://localhost:4566")
    LiveE2ERunner._assert_local_execution(require_tty=True)


# --- stack reconciliation ----------------------------------------------------------------


class _DescribeAdapter:
    def __init__(self, results: list[dict[str, Any] | None]) -> None:
        self.results = results
        self.calls: list[str] = []

    def describe_stack(self, name: str) -> dict[str, Any] | None:
        self.calls.append(name)
        return self.results.pop(0) if self.results else None


def test_reconcile_returns_immediately_when_deployment_not_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(runner_module, "time", clock)
    adapter = _DescribeAdapter([None])
    assert LiveE2ERunner._reconcile_attempted_stack(adapter, "s", deployment_attempted=False, poll_seconds=1) is None
    assert adapter.calls == ["s"]
    assert clock.sleeps == []


def test_reconcile_waits_for_accepted_create_to_become_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(runner_module, "time", clock)
    stack = {"StackId": "arn:stack"}
    adapter = _DescribeAdapter([None, None, stack])
    assert LiveE2ERunner._reconcile_attempted_stack(adapter, "s", deployment_attempted=True, poll_seconds=1) is stack
    assert clock.sleeps == [1, 1]


def test_reconcile_gives_up_after_bounded_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(runner_module, "time", clock)
    adapter = _DescribeAdapter([])
    assert LiveE2ERunner._reconcile_attempted_stack(adapter, "s", deployment_attempted=True, poll_seconds=10) is None
    assert clock.sleeps == [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
    assert len(adapter.calls) == 7


# --- CDK command construction ------------------------------------------------------------


def _capture_run(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> CommandResult:
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return CommandResult(
            tuple(argv),
            returncode,
            "account 123456789012 deployed\n",
            "password=hunter2\n",
            1.25,
        )

    monkeypatch.setattr(runner_module, "run_command", fake_run)
    return captured


def _cdk_kwargs(run_dir: Path, operation: str, **overrides: Any) -> dict[str, Any]:
    kwargs = {
        "cdk_command": "/opt/cdk",
        "operation": operation,
        "run_id": "e2e-cdk-command",
        "contexts": {},
        "run_dir": run_dir,
        "aws_profile": None,
        "account_id": "123456789012",
        "region": "us-east-1",
        "timeout_seconds": 60,
    }
    kwargs.update(overrides)
    return kwargs


def test_cdk_command_rejects_unsupported_operation(tmp_path: Path) -> None:
    runner = LiveE2ERunner(root=_root(tmp_path))
    with pytest.raises(ValueError, match="Unsupported CDK operation"):
        runner._cdk_command(**_cdk_kwargs(tmp_path, "destroy"))


@pytest.mark.parametrize("operation", ("diff", "deploy"))
def test_cdk_diff_and_deploy_use_preflight_assembly_and_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root = _root(tmp_path)
    run_dir = root / "run"
    run_dir.mkdir()
    captured = _capture_run(monkeypatch)

    result = LiveE2ERunner(root=root)._cdk_command(
        **_cdk_kwargs(run_dir, operation, aws_profile="e2e-profile", contexts={"ignored": "x"})
    )

    assert result.duration_seconds == 1.25
    argv = captured["argv"]
    assert argv[:3] == ["/opt/cdk", operation, stack_name("e2e-cdk-command")]
    assert argv[argv.index("--app") + 1] == str(run_dir / "cdk.out")
    assert "--exclusively" in argv
    assert "--context" not in argv
    if operation == "diff":
        assert "--no-change-set" in argv
    else:
        assert argv[argv.index("--require-approval") + 1] == "never"
    env = captured["kwargs"]["env"]
    assert env["AWS_PROFILE"] == "e2e-profile"
    assert env["CDK_DEFAULT_ACCOUNT"] == "123456789012"
    assert captured["kwargs"]["umask"] == 0o077
    log = run_dir / f"{operation}.log"
    content = log.read_text(encoding="utf-8")
    assert "returncode=0" in content
    assert "duration_seconds=1.250" in content
    assert "123456789012" not in content
    assert "hunter2" not in content
    assert log.stat().st_mode & 0o777 == 0o600


def test_cdk_publish_assets_routes_docker_through_timing_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    run_dir = root / "run"
    (run_dir / "cdk.out").mkdir(parents=True)
    manifest = run_dir / "cdk.out" / "Stack.assets.json"
    manifest.write_text("{}", encoding="utf-8")
    stale_timing = run_dir / "docker-timings.jsonl"
    stale_timing.write_text("stale\n", encoding="utf-8")
    docker = _executable(tmp_path / "bin" / "docker")
    monkeypatch.setattr(runner_module, "shutil", SimpleNamespace(which=lambda command: str(docker)))
    captured = _capture_run(monkeypatch, returncode=3)

    result = LiveE2ERunner(root=root)._cdk_command(**_cdk_kwargs(run_dir, "publish-assets", aws_profile="ignored"))

    assert not result.ok
    assert captured["argv"] == [
        str(root / "node_modules" / ".bin" / "cdk-assets"),
        "publish",
        "--path",
        str(manifest),
    ]
    env = captured["kwargs"]["env"]
    assert env["CDK_DOCKER"] == str((root / "tools" / "live_e2e" / "docker_proxy.py").resolve())
    assert env["OPENEMR_E2E_REAL_DOCKER"] == str(docker.resolve())
    assert env["OPENEMR_E2E_DOCKER_TIMINGS"] == str(stale_timing.resolve())
    assert env["AWS_PROFILE"] == "ignored"
    assert not stale_timing.exists()
    assert "returncode=3" in (run_dir / "publish-assets.log").read_text(encoding="utf-8")


def test_cdk_publish_assets_requires_exactly_one_regular_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    run_dir = root / "run"
    (run_dir / "cdk.out").mkdir(parents=True)
    monkeypatch.setattr(runner_module, "run_command", lambda *_a, **_k: pytest.fail("must not publish"))
    runner = LiveE2ERunner(root=root)
    with pytest.raises(ToolError, match="exactly one regular CDK asset manifest"):
        runner._cdk_command(**_cdk_kwargs(run_dir, "publish-assets"))

    target = tmp_path / "real.assets.json"
    target.write_text("{}", encoding="utf-8")
    (run_dir / "cdk.out" / "Stack.assets.json").symlink_to(target)
    with pytest.raises(ToolError, match="exactly one regular CDK asset manifest"):
        runner._cdk_command(**_cdk_kwargs(run_dir, "publish-assets"))


# --- local state helpers -----------------------------------------------------------------


def test_lock_rejects_concurrent_operation(tmp_path: Path) -> None:
    runner = LiveE2ERunner(root=_root(tmp_path))
    with runner._lock():
        pass
    with (runner.local_root / "live-e2e.lock").open("a+", encoding="utf-8") as other:
        fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ToolError, match="Another live E2E operation is already running"):
            with runner._lock():
                pytest.fail("lock must not be acquired twice")
        fcntl.flock(other.fileno(), fcntl.LOCK_UN)


def test_account_label_is_stable_owner_only_hmac(tmp_path: Path) -> None:
    runner = LiveE2ERunner(root=_root(tmp_path))
    first = runner._account_label("123456789012")
    second = runner._account_label("123456789012")
    other = runner._account_label("210987654321")

    assert first == second
    assert first != other
    assert first.startswith("sha256:") and len(first) == len("sha256:") + 12
    key = runner.local_root / "account-label.key"
    assert key.stat().st_mode & 0o777 == 0o600
    assert len(key.read_bytes()) == 32

    key.chmod(0o644)
    with pytest.raises(ToolError, match="owner-only regular file"):
        runner._account_label("123456789012")
    key.chmod(0o600)
    key.write_bytes(b"short")
    with pytest.raises(ToolError, match="invalid length"):
        runner._account_label("123456789012")


def test_account_label_refuses_symlinked_key(tmp_path: Path) -> None:
    runner = LiveE2ERunner(root=_root(tmp_path))
    runner.local_root.mkdir(mode=0o700)
    target = tmp_path / "elsewhere.key"
    target.write_bytes(b"k" * 32)
    (runner.local_root / "account-label.key").symlink_to(target)
    with pytest.raises(ToolError, match="symlinked live E2E account-label key"):
        runner._account_label("123456789012")


def test_recorded_rds_cluster_identifiers(tmp_path: Path) -> None:
    runner = LiveE2ERunner(root=_root(tmp_path))
    run_id = "e2e-rds-inventory"
    assert runner._recorded_rds_cluster_identifiers(run_id) == ()

    runner._update_state(run_id, {"rds_cluster_identifiers": ["db-b", "db-a", "db-b"]})
    assert runner._recorded_rds_cluster_identifiers(run_id) == ("db-a", "db-b")

    runner._write_state(run_id, {"rds_cluster_identifiers": ["bad_identifier!"]})
    with pytest.raises(ToolError, match="Recorded RDS cleanup inventory is malformed"):
        runner._recorded_rds_cluster_identifiers(run_id)

    runner._write_state(run_id, {"stack_id_hash": "abc"})
    with pytest.raises(ToolError, match="without a durable RDS cleanup inventory"):
        runner._recorded_rds_cluster_identifiers(run_id)


# --- context and input validation --------------------------------------------------------


def _contexts(**overrides: Any) -> dict[str, str]:
    values: dict[str, Any] = {
        "run_id": "e2e-context-run",
        "account_id": "123456789012",
        "region": "us-east-1",
        "availability_zones": ("us-east-1b", "us-east-1a"),
        "route53_domain": "e2e.openemr.org",
        "hosted_zone_id": "ZTEST123",
        "allowed_ipv4_cidr": "8.8.8.8/32",
        "profile": "default",
    }
    values.update(overrides)
    return deployment_contexts(**values)


def test_deployment_contexts_mark_emulated_runs() -> None:
    contexts = _contexts(emulated=True)
    assert contexts["live_e2e_emulated"] == "true"
    assert contexts["live_e2e_availability_zones"] == '["us-east-1a","us-east-1b"]'
    assert "live_e2e_emulated" not in _contexts()


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"profile": "everything"}, "Unsupported live E2E profile: everything"),
        ({"hosted_zone_id": "/hostedzone/Z1"}, "hosted-zone ID"),
        ({"account_id": "1234"}, "account ID"),
        ({"region": "mars-1"}, "Region"),
        ({"availability_zones": ("us-east-1a",)}, "exactly two standard Availability Zones"),
        ({"availability_zones": ("us-east-1a", "us-west-2b")}, "exactly two standard Availability Zones"),
    ),
)
def test_deployment_contexts_reject_invalid_scope(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ToolError, match=message):
        _contexts(**overrides)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"region": "global"}, "AWS region has an invalid format"),
        ({"allowed_ipv4_cidr": "not-a-cidr"}, "Allowed IPv4 CIDR is invalid"),
        ({"allowed_ipv4_cidr": "8.8.8.0/24"}, "one explicit public IPv4 /32"),
        ({"allowed_ipv4_cidr": "2001:4860::8888/128"}, "one explicit public IPv4 /32"),
        ({"allowed_ipv4_cidr": "10.0.0.1/32"}, "globally routable"),
        ({"profile": "custom"}, "Profile must be one of: api-enabled, default"),
    ),
)
def test_validate_inputs_rejects_unsafe_scope(overrides: dict[str, Any], message: str) -> None:
    values: dict[str, Any] = {
        "approved_account": "123456789012",
        "region": "us-east-1",
        "route53_domain": "e2e.openemr.org",
        "allowed_ipv4_cidr": "8.8.8.8/32",
        "profile": "default",
    }
    values.update(overrides)
    with pytest.raises(ToolError, match=message):
        validate_inputs(**values)


def test_validate_inputs_accepts_documentation_values_for_cleanup() -> None:
    validate_inputs(
        approved_account="123456789012",
        region="us-east-1",
        route53_domain="e2e.invalid.example",
        allowed_ipv4_cidr="192.0.2.1/32",
        profile="default",
        allow_documentation_values=True,
    )


def test_run_id_generation_and_validation() -> None:
    with pytest.raises(ToolError, match="Run ID must be 6-48"):
        validate_run_id("Upper_Case")
    generated = new_run_id()
    assert re.fullmatch(r"e2e-\d{8}t\d{6}z-[0-9a-f]{8}", generated), generated
    assert validate_run_id(generated) == generated


# --- residual / timestamp / path helpers -------------------------------------------------


def test_cleanup_status_and_residual_merge() -> None:
    expected = ResidualResource("ecr", "sha256:aaaaaaaaaaaa", "shared-content-addressed-asset")
    unexpected = ResidualResource("s3", "sha256:bbbbbbbbbbbb", "unexpected-residual")
    assert _cleanup_status(()) == "complete"
    assert _cleanup_status((expected,)) == "stack-deleted-with-expected-residuals"
    assert _cleanup_status((expected, unexpected)) == "failed"
    assert _merge_residuals((unexpected, expected), (expected,)) == (expected, unexpected)


def test_timestamps_round_trip_in_utc() -> None:
    value = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    assert _timestamp(value) == "2026-08-01T12:00:00Z"
    assert _parse_timestamp("2026-08-01T12:00:00Z") == value
    with pytest.raises(ToolError, match="Preflight timestamp is invalid"):
        _parse_timestamp("yesterday")
    with pytest.raises(ToolError, match="must include a timezone"):
        _parse_timestamp("2026-08-01T12:00:00")


def test_resolve_executable_prefers_path_then_explicit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _executable(tmp_path / "tool")
    monkeypatch.setattr(runner_module, "shutil", SimpleNamespace(which=lambda command: str(tool)))
    assert _resolve_executable("tool") == str(tool.resolve())

    monkeypatch.setattr(runner_module, "shutil", SimpleNamespace(which=lambda command: None))
    assert _resolve_executable(str(tool)) == str(tool.resolve())
    with pytest.raises(ToolError, match="Executable is unavailable: missing-tool"):
        _resolve_executable("missing-tool")


def test_resolve_cdk_executable_requires_installed_pinned_cli(tmp_path: Path) -> None:
    root = _root(tmp_path)
    with pytest.raises(ToolError, match="Pinned local CDK is unavailable"):
        _resolve_cdk_executable(root, "node_modules/.bin/cdk")


def test_file_sha256_rejects_unsafe_files(tmp_path: Path) -> None:
    regular = tmp_path / "cdk"
    regular.write_bytes(b"cdk")
    assert _file_sha256(regular) == "sha256:" + hashlib.sha256(b"cdk").hexdigest()

    link = tmp_path / "link"
    link.symlink_to(regular)
    with pytest.raises(ToolError, match="not a safe regular file"):
        _file_sha256(link)

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ToolError, match="is not a regular file"):
        _file_sha256(directory)

    huge = tmp_path / "huge"
    with huge.open("wb") as handle:
        handle.truncate(100_000_001)
    with pytest.raises(ToolError, match="exceeds the fingerprint limit"):
        _file_sha256(huge)


def test_directory_fingerprint_rejects_missing_empty_and_symlinked_assemblies(tmp_path: Path) -> None:
    assembly = tmp_path / "cdk.out"
    with pytest.raises(ToolError, match="assembly is missing"):
        _directory_fingerprint(assembly)
    assembly.mkdir()
    with pytest.raises(ToolError, match="assembly is empty"):
        _directory_fingerprint(assembly)
    (assembly / "manifest.json").write_text("{}", encoding="utf-8")
    first = _directory_fingerprint(assembly)
    assert first == _directory_fingerprint(assembly)
    (assembly / "manifest.json").write_text("{ }", encoding="utf-8")
    assert _directory_fingerprint(assembly) != first

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (assembly / "linked.json").symlink_to(outside)
    with pytest.raises(ToolError, match="cannot contain symlinks"):
        _directory_fingerprint(assembly)


# --- cloud assembly parsing --------------------------------------------------------------

_STACK = stack_name("e2e-assembly-run")


def _assembly(tmp_path: Path, template: Any, template_file: Any = "stack.template.json") -> Path:
    assembly = tmp_path / "cdk.out"
    assembly.mkdir(exist_ok=True)
    (assembly / "manifest.json").write_text(
        json.dumps({"artifacts": {_STACK: {"properties": {"templateFile": template_file}}}}),
        encoding="utf-8",
    )
    if isinstance(template_file, str) and template is not None:
        target = assembly / template_file
        target.write_text(template if isinstance(template, str) else json.dumps(template), encoding="utf-8")
    return assembly


def test_assembly_template_rejects_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="does not contain the expected E2E stack"):
        _assembly_template(tmp_path, _STACK)


@pytest.mark.parametrize(
    ("template", "template_file", "message"),
    (
        ({}, 7, "template path is invalid"),
        (None, "../escape.template.json", "escapes its output directory"),
        (None, "missing.template.json", "template is unsafe"),
        ("{oops", "stack.template.json", "not valid JSON"),
        ([], "stack.template.json", "must be an object"),
    ),
)
def test_assembly_template_rejects_unsafe_templates(
    tmp_path: Path, template: Any, template_file: Any, message: str
) -> None:
    assembly = _assembly(tmp_path, template, template_file)
    with pytest.raises(ToolError, match=message):
        _assembly_template(assembly, _STACK)


def test_assembly_template_reports_uninspectable_template(tmp_path: Path) -> None:
    assembly = _assembly(tmp_path, {"Resources": {}})
    with pytest.raises(ToolError, match="cannot be inspected"):
        _assembly_template(_fail_path(assembly, "stat:stack.template.json"), _STACK)


def test_assembly_template_reports_unreadable_template(tmp_path: Path) -> None:
    assembly = _assembly(tmp_path, {"Resources": {}})
    with pytest.raises(ToolError, match="not valid JSON"):
        _assembly_template(_fail_path(assembly, "stack.template.json"), _STACK)


def test_assembly_versions_require_unambiguous_inventory(tmp_path: Path) -> None:
    assembly = _assembly(tmp_path, {"Resources": {}})
    with pytest.raises(ToolError, match="missing the OpenEMR version output"):
        _assembly_versions(assembly, _STACK)

    two_clusters = {
        "Outputs": {"OpenEMRVersion": {"Value": "8.2.0"}},
        "Resources": {
            "A": {"Type": "AWS::RDS::DBCluster", "Properties": {"EngineVersion": "8.0.a"}},
            "B": {"Type": "AWS::RDS::DBCluster", "Properties": {"EngineVersion": "8.0.b"}},
        },
    }
    assembly = _assembly(tmp_path, two_clusters)
    with pytest.raises(ToolError, match="ambiguous deployment version inventory"):
        _assembly_versions(assembly, _STACK)


def test_assembly_resource_inventory_counts_types(tmp_path: Path) -> None:
    assembly = _assembly(
        tmp_path,
        {
            "Resources": {
                "B": {"Type": "AWS::S3::Bucket"},
                "A": {"Type": "AWS::ECS::Cluster"},
                "C": {"Type": "AWS::S3::Bucket"},
            }
        },
    )
    inventory = _assembly_resource_inventory(assembly, _STACK)
    assert inventory == {"AWS::ECS::Cluster": 1, "AWS::S3::Bucket": 2}
    assert list(inventory) == ["AWS::ECS::Cluster", "AWS::S3::Bucket"]

    assembly = _assembly(tmp_path, {"Resources": []})
    with pytest.raises(ToolError, match="no resource inventory"):
        _assembly_resource_inventory(assembly, _STACK)

    assembly = _assembly(tmp_path, {"Resources": {"A": {"Type": 3}}})
    with pytest.raises(ToolError, match="contains an invalid resource"):
        _assembly_resource_inventory(assembly, _STACK)


def _safe_template() -> dict[str, Any]:
    return {
        "Outputs": {"LiveE2ERunId": {"Value": "e2e-safe-run"}},
        "Resources": {
            "Certificate": {"Type": "AWS::CertificateManager::Certificate", "Properties": {}},
            "Dns": {
                "Type": "AWS::Route53::RecordSet",
                "Properties": {"HostedZoneId": "ZSAFE123", "AliasTarget": {"DNSName": "alb"}},
            },
            "Listener": {
                "Type": "AWS::ElasticLoadBalancingV2::Listener",
                "Properties": {"Certificates": [{"CertificateArn": {"Ref": "Certificate"}}]},
            },
            "Service": {"Type": "AWS::ECS::Service", "Properties": {"DesiredCount": 1}},
            "Database": {
                "Type": "AWS::RDS::DBCluster",
                "Properties": {"DeletionProtection": False},
                "DeletionPolicy": "Delete",
                "UpdateReplacePolicy": "Delete",
            },
            "Logs": {
                "Type": "AWS::Logs::LogGroup",
                "Properties": {},
                "DeletionPolicy": "Delete",
                "UpdateReplacePolicy": "Delete",
            },
            "Parameter": {"Type": "AWS::SSM::Parameter", "Properties": {"Name": "swarm_mode_safe123"}},
        },
    }


def _validate(template: dict[str, Any], profile: str = "default") -> None:
    _validate_e2e_template(
        template,
        run_id="e2e-safe-run",
        hosted_zone_id="ZSAFE123",
        resource_suffix="safe123",
        profile=profile,
    )


def _mutate(path: str, value: Any) -> dict[str, Any]:
    template = copy.deepcopy(_safe_template())
    *parents, leaf = path.split(".")
    target: Any = template
    for part in parents:
        target = target[part]
    if value is _DELETE:
        del target[leaf]
    else:
        target[leaf] = value
    return template


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        ("Resources", [], "missing resources or outputs"),
        ("Outputs.LiveE2ERunId", {"Value": "e2e-other-run"}, "exact ownership output"),
        ("Resources.Broken", "not-a-resource", "contains an invalid resource"),
        ("Resources.Certificate", _DELETE, "exactly one stack-owned ACM certificate"),
        ("Resources.Dns.Properties.HostedZoneId", "ZOTHER", "not bound to the preflight-verified hosted zone"),
        ("Resources.Listener.Properties", {"Certificates": [{"CertificateArn": "null"}]}, "no valid certificate"),
        ("Resources.Listener", _DELETE, "no valid certificate"),
        ("Resources.Service.Properties.DesiredCount", 2, "exactly one desired OpenEMR task"),
        ("Resources.Database", _DELETE, "exactly one Aurora cluster"),
        ("Resources.Database.Properties.DeletionProtection", True, "Aurora lifecycle is not disposable"),
        ("Resources.Database.DeletionPolicy", "Snapshot", "Aurora lifecycle is not disposable"),
        ("Resources.Logs.DeletionPolicy", "Retain", "log groups must use delete lifecycle"),
        ("Resources.Parameter", _DELETE, "SSM parameters are not isolated"),
        ("Resources.Topic", {"Type": "AWS::SNS::Topic"}, "forbidden optional resources: AWS::SNS::Topic"),
    ),
)
def test_e2e_template_policy_rejects_drift(path: str, value: Any, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        _validate(_mutate(path, value))


def test_api_enabled_profile_requires_aurora_data_api() -> None:
    template = _safe_template()
    with pytest.raises(ToolError, match="did not enable the Aurora Data API"):
        _validate(template, profile="api-enabled")
    template["Resources"]["Database"]["Properties"]["EnableHttpEndpoint"] = True
    _validate(template, profile="api-enabled")


@pytest.mark.parametrize(
    ("remote", "slug"),
    (
        ("https://github.com/openemr/openemr-on-ecs", "openemr/openemr-on-ecs"),
        ("git@github.com:org/repo.git", "org/repo"),
        ("/srv/git/org/repo.git", "org/repo"),
    ),
)
def test_repository_slug_accepts_supported_remotes(remote: str, slug: str) -> None:
    assert _repository_slug(remote) == slug


@pytest.mark.parametrize(
    ("remote", "message"),
    (
        ("https://github.com", "not a supported repository URL"),
        ("repo", "not a supported repository URL"),
        ("https://github.com/org/re po", "slug is invalid"),
    ),
)
def test_repository_slug_rejects_unsupported_remotes(remote: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        _repository_slug(remote)


# --- failure diagnostics and Docker timing -----------------------------------------------


def test_cdk_failure_message_includes_redacted_log_tail(tmp_path: Path) -> None:
    run_dir = tmp_path / "e2e-failure-run"
    run_dir.mkdir()
    summary = "CDK deployment command failed; inspect .live-e2e/runs/e2e-failure-run/deploy.log"
    assert _cdk_failure_message(run_dir, "deploy", "CDK deployment command failed") == summary

    log = run_dir / "deploy.log"
    log.write_text("  \n\n", encoding="utf-8")
    assert _cdk_failure_message(run_dir, "deploy", "CDK deployment command failed") == summary

    log.write_text(
        "\n".join(f"line {index}" for index in range(50)) + "\naccount 123456789012 failed\n",
        encoding="utf-8",
    )
    message = _cdk_failure_message(run_dir, "deploy", "CDK deployment command failed")
    lines = message.splitlines()
    assert lines[0] == summary
    assert len(lines) == 41
    assert lines[1] == "line 11"
    assert lines[-1] == "account <account-id> failed"

    assert _cdk_failure_message(_fail_path(run_dir, "deploy.log"), "deploy", "CDK deployment command failed") == (
        summary
    )


def test_cdk_failure_message_ignores_symlinked_log(tmp_path: Path) -> None:
    run_dir = tmp_path / "e2e-failure-run"
    run_dir.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("secret details\n", encoding="utf-8")
    (run_dir / "deploy.log").symlink_to(outside)
    assert "secret details" not in _cdk_failure_message(run_dir, "deploy", "failed")


def _timing_line(**overrides: Any) -> str:
    value: dict[str, Any] = {"schema_version": 1, "category": "build", "duration_seconds": 1.0}
    value.update(overrides)
    return json.dumps(value)


def test_docker_build_duration_absent_record_is_zero(tmp_path: Path) -> None:
    assert _docker_build_duration(tmp_path / "missing.jsonl") == 0.0


def test_docker_build_duration_sums_only_builds(tmp_path: Path) -> None:
    path = tmp_path / "timing.jsonl"
    path.write_text(
        "\n".join(
            (
                _timing_line(duration_seconds=1.2346),
                _timing_line(duration_seconds=2),
                _timing_line(category="other", duration_seconds=50),
            )
        ),
        encoding="utf-8",
    )
    assert _docker_build_duration(path) == 3.235


def test_docker_build_duration_rejects_unsafe_records(tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text(_timing_line(), encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    with pytest.raises(ToolError, match="Docker timing record is unsafe"):
        _docker_build_duration(link)

    big = tmp_path / "big.jsonl"
    big.write_text("x" * 1_000_001, encoding="utf-8")
    with pytest.raises(ToolError, match="Docker timing record is unsafe"):
        _docker_build_duration(big)

    many = tmp_path / "many.jsonl"
    many.write_text("\n".join(["{}"] * 1_001), encoding="utf-8")
    with pytest.raises(ToolError, match="too many entries"):
        _docker_build_duration(many)

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ToolError, match="invalid JSON"):
        _docker_build_duration(invalid)

    for line in ("[]", _timing_line(schema_version=2), _timing_line(duration_seconds=True)):
        invalid.write_text(line, encoding="utf-8")
        with pytest.raises(ToolError, match="entry is invalid"):
            _docker_build_duration(invalid)

    with pytest.raises(ToolError, match="Cannot read Docker timing record"):
        _docker_build_duration(_fail_path(target, "target.jsonl"))
