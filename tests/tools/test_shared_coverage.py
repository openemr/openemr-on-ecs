"""Security-boundary tests for the dependency-free shared tool helpers."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools import _shared
from tools._shared import (
    CommandResult,
    ToolError,
    atomic_write_private_json,
    atomic_write_text,
    canonical_json,
    ensure_owner_only_directory,
    fingerprint,
    hash_account_id,
    read_private_json,
    redact_text,
    repository_root,
    reserve_private_json,
    resolve_repo_path,
    run_command,
    safe_read_text,
    snapshot_regular_file,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_command_result_ok_reflects_return_code() -> None:
    assert CommandResult(("true",), 0, "", "", 0.1).ok is True
    assert CommandResult(("false",), 1, "", "", 0.1).ok is False


def test_repository_root_fails_outside_a_repository(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="Unable to locate repository root"):
        repository_root(tmp_path)


def test_repository_root_accepts_a_file_start(tmp_path: Path) -> None:
    (tmp_path / "cdk.json").write_text("{}", encoding="utf-8")
    (tmp_path / "openemr_ecs").mkdir()
    nested = tmp_path / "tools" / "module.py"
    nested.parent.mkdir()
    nested.write_text("", encoding="utf-8")

    assert repository_root(nested) == tmp_path.resolve()


def test_owner_only_directory_creates_private_parents(tmp_path: Path) -> None:
    target = tmp_path / "state" / "nested"

    ensure_owner_only_directory(target, parents=True)

    assert target.is_dir()
    assert _mode(target) == 0o700


def test_owner_only_directory_tightens_existing_permissions(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir(mode=0o755)

    ensure_owner_only_directory(target)

    assert _mode(target) == 0o700


def test_owner_only_directory_accepts_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    ensure_owner_only_directory(Path("relative-state"))

    assert _mode(tmp_path / "relative-state") == 0o700


def test_owner_only_directory_requires_missing_parents_flag(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="Refusing symlinked state or unsafe directory"):
        ensure_owner_only_directory(tmp_path / "missing" / "child")
    assert not (tmp_path / "missing").exists()


def test_owner_only_directory_refuses_existing_when_exclusive(tmp_path: Path) -> None:
    target = tmp_path / "taken"
    target.mkdir()

    with pytest.raises(FileExistsError):
        ensure_owner_only_directory(target, exist_ok=False)
    ensure_owner_only_directory(tmp_path / "fresh", exist_ok=False)
    assert (tmp_path / "fresh").is_dir()


def test_owner_only_directory_refuses_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)

    with pytest.raises(ToolError, match="Refusing symlinked import or unsafe directory"):
        ensure_owner_only_directory(tmp_path / "link", label="import")


def test_owner_only_directory_refuses_foreign_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_shared.os, "geteuid", lambda: os.getuid() + 1)

    with pytest.raises(ToolError, match="Cache path is not an owned private directory"):
        ensure_owner_only_directory(tmp_path, label="cache")


def test_owner_only_directory_root_is_never_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    root_owner = os.stat("/").st_uid
    monkeypatch.setattr(_shared.os, "geteuid", lambda: root_owner)

    with pytest.raises(FileExistsError):
        ensure_owner_only_directory(Path("/"), exist_ok=False)


def test_owner_only_directory_rejects_anchorless_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class RelativeCwdPath(type(tmp_path)):  # type: ignore[misc]
        @classmethod
        def cwd(cls) -> Path:
            return Path("not-absolute")

    monkeypatch.setattr(_shared, "Path", RelativeCwdPath)

    with pytest.raises(ToolError, match="Invalid state directory"):
        ensure_owner_only_directory(Path("child"))


def test_safe_open_unsupported_platforms_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_shared, "_SUPPORTS_SAFE_OPEN", False)
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ToolError, match="cannot safely open the state directory"):
        ensure_owner_only_directory(tmp_path)
    with pytest.raises(ToolError, match="cannot safely snapshot the source"):
        snapshot_regular_file(tmp_path / "file.txt", tmp_path / "snap", max_bytes=10)
    with pytest.raises(ToolError, match="does not support safe descriptor-relative"):
        safe_read_text(tmp_path, "file.txt")


def test_private_files_reject_parent_directory_names(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="Invalid state filename"):
        reserve_private_json(tmp_path / "..", {})


def test_reserve_private_json_is_exclusive_and_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "private" / "lock.json"

    reserve_private_json(target, {"b": 1, "a": 2})

    assert target.read_text(encoding="utf-8") == '{"a": 2, "b": 1}\n'
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700
    with pytest.raises(FileExistsError):
        reserve_private_json(target, {"replaced": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2, "b": 1}


def test_atomic_private_json_round_trips_and_replaces(tmp_path: Path) -> None:
    target = tmp_path / "state" / "run.json"

    atomic_write_private_json(target, {"phase": "one", "name": "café"})
    atomic_write_private_json(target, {"phase": "two"})

    assert read_private_json(target) == {"phase": "two"}
    assert _mode(target) == 0o600
    assert sorted(path.name for path in target.parent.iterdir()) == ["run.json"]


def test_atomic_private_json_refuses_group_readable_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o644)

    with pytest.raises(ToolError, match="Lock file is not an owned private regular file"):
        atomic_write_private_json(target, {"x": 1}, label="lock")
    assert target.read_text(encoding="utf-8") == "{}"


def test_atomic_private_json_refuses_symlinked_destination(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    real.chmod(0o600)
    (tmp_path / "link.json").symlink_to(real)

    with pytest.raises(ToolError, match="State file is symlinked or unsafe"):
        atomic_write_private_json(tmp_path / "link.json", {"x": 1})
    assert real.read_text(encoding="utf-8") == "{}"


def test_atomic_private_json_removes_temporary_file_on_failure(tmp_path: Path) -> None:
    target = tmp_path / "state.json"

    with pytest.raises(TypeError):
        atomic_write_private_json(target, {"value": object()})

    assert list(tmp_path.iterdir()) == []


def test_atomic_private_json_tolerates_vanished_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"

    def replace_then_fail(source: str, destination: str, **kwargs: Any) -> None:
        os.unlink(source, dir_fd=kwargs["src_dir_fd"])
        raise OSError("simulated rename failure")

    monkeypatch.setattr(_shared.os, "replace", replace_then_fail)

    with pytest.raises(OSError, match="simulated rename failure"):
        atomic_write_private_json(target, {"x": 1})
    assert list(tmp_path.iterdir()) == []


def _private_file(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def test_read_private_json_enforces_size_limit(tmp_path: Path) -> None:
    target = _private_file(tmp_path / "state.json", b'{"a": "0123456789"}')

    with pytest.raises(ToolError, match="exceeds the 5-byte limit"):
        read_private_json(target, max_bytes=5)


def test_read_private_json_detects_growth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _private_file(tmp_path / "state.json", b"{}")
    original_read = os.read
    grew = False

    def grow_then_read(descriptor: int, size: int) -> bytes:
        nonlocal grew
        if not grew:
            grew = True
            with target.open("ab") as handle:
                handle.write(b" " * 64)
        return original_read(descriptor, size)

    monkeypatch.setattr(_shared.os, "read", grow_then_read)

    with pytest.raises(ToolError, match="grew beyond the 10-byte limit"):
        read_private_json(target, max_bytes=10)


def test_read_private_json_rejects_invalid_json(tmp_path: Path) -> None:
    target = _private_file(tmp_path / "state.json", b"\xff not json")

    with pytest.raises(ToolError, match="State file is not valid JSON"):
        read_private_json(target)


def test_read_private_json_rejects_symlinks(tmp_path: Path) -> None:
    real = _private_file(tmp_path / "real.json", b"{}")
    (tmp_path / "link.json").symlink_to(real)

    with pytest.raises(ToolError, match="State file is symlinked or unsafe"):
        read_private_json(tmp_path / "link.json")


def test_snapshot_copies_regular_file_into_private_directory(tmp_path: Path) -> None:
    source = tmp_path / "backup.sql"
    source.write_bytes(b"CREATE TABLE demo;\n")

    snapshot = snapshot_regular_file(source, tmp_path / "snapshots", max_bytes=100, label="import source")

    assert snapshot.parent == tmp_path / "snapshots"
    assert snapshot.name.startswith(".import-source.")
    assert snapshot.name.endswith(".snapshot")
    assert snapshot.read_bytes() == b"CREATE TABLE demo;\n"
    assert _mode(snapshot) == 0o600
    assert _mode(snapshot.parent) == 0o700


def test_snapshot_accepts_relative_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "relative.txt").write_bytes(b"data")
    monkeypatch.chdir(tmp_path)

    snapshot = snapshot_regular_file(Path("relative.txt"), tmp_path / "snap", max_bytes=10)

    assert snapshot.read_bytes() == b"data"


def test_snapshot_validates_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        snapshot_regular_file(tmp_path / "x", tmp_path / "snap", max_bytes=0)
    with pytest.raises(ToolError, match="Invalid source path"):
        snapshot_regular_file(Path("/"), tmp_path / "snap", max_bytes=10)


@pytest.mark.parametrize("content", [b"", b"x" * 11])
def test_snapshot_requires_bounded_nonempty_file(tmp_path: Path, content: bytes) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(content)

    with pytest.raises(ToolError, match="Source is not one bounded regular file"):
        snapshot_regular_file(source, tmp_path / "snap", max_bytes=10)
    assert not (tmp_path / "snap").exists()


def test_snapshot_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="Unable to safely snapshot source"):
        snapshot_regular_file(tmp_path / "missing.bin", tmp_path / "snap", max_bytes=10)


def _grow_during_read(monkeypatch: pytest.MonkeyPatch, source: Path, extra: bytes) -> None:
    original_read = os.read
    grew = False

    def grow_then_read(descriptor: int, size: int) -> bytes:
        nonlocal grew
        if not grew:
            grew = True
            with source.open("ab") as handle:
                handle.write(extra)
        return original_read(descriptor, size)

    monkeypatch.setattr(_shared.os, "read", grow_then_read)


def test_snapshot_rejects_growth_beyond_limit_and_removes_partial_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"12345")
    _grow_during_read(monkeypatch, source, b"x" * 20)

    with pytest.raises(ToolError, match="Source grew beyond the size limit"):
        snapshot_regular_file(source, tmp_path / "snap", max_bytes=10)
    assert list((tmp_path / "snap").iterdir()) == []


def test_snapshot_rejects_source_changed_during_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"12345")
    _grow_during_read(monkeypatch, source, b"67")

    with pytest.raises(ToolError, match="Source changed while it was being snapshotted"):
        snapshot_regular_file(source, tmp_path / "snap", max_bytes=10)
    assert list((tmp_path / "snap").iterdir()) == []


def test_canonical_json_and_fingerprint_are_deterministic() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    assert len(fingerprint({"a": 1}, length=8)) == 8
    for length in (7, 65):
        with pytest.raises(ValueError, match="between 8 and 64"):
            fingerprint({}, length=length)


def test_hash_account_id_requires_twelve_digits() -> None:
    hashed = hash_account_id("123456789012")

    assert hashed.startswith("sha256:")
    assert len(hashed) == len("sha256:") + 12
    assert "123456789012" not in hashed
    with pytest.raises(ToolError, match="exactly 12 digits"):
        hash_account_id("12345")


def test_redaction_of_nested_containers_with_escaped_quotes() -> None:
    text = 'secret = {"k": "a\\"b", "n": [1, {"x": 2}]} tail'

    assert redact_text(text) == 'secret = "<redacted>" tail'


@pytest.mark.parametrize("text", ["token = {]", 'token = {"a": 1'])
def test_redaction_of_malformed_containers_still_hides_values(text: str) -> None:
    redacted = redact_text(text)

    assert "<redacted>" in redacted
    assert redacted.startswith("token =")


def test_redaction_leaves_non_sensitive_camel_case_keys() -> None:
    assert redact_text("XToken=visible") == "XToken=visible"


def test_resolve_repo_path_policy_errors(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text("x", encoding="utf-8")
    (root / "folder").mkdir()

    with pytest.raises(ToolError, match="not a directory"):
        resolve_repo_path(root / "notes.md", "x")
    with pytest.raises(ToolError, match="escapes the repository root"):
        resolve_repo_path(root, tmp_path / "outside.md")
    with pytest.raises(ToolError, match="escapes the repository root"):
        resolve_repo_path(root, "folder/../../outside.md")
    with pytest.raises(ToolError, match="File extension .md is not allowed"):
        resolve_repo_path(root, "notes.md", allowed_extensions={".TXT"})
    with pytest.raises(ToolError, match="File extension <none> is not allowed"):
        resolve_repo_path(root, "folder", allowed_extensions={".txt"})
    with pytest.raises(ToolError, match="not a regular file"):
        resolve_repo_path(root, "folder")
    assert resolve_repo_path(root, "folder", require_file=False) == root.resolve() / "folder"


def test_resolve_repo_path_rechecks_containment_after_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    outside = tmp_path / "outside.md"

    class SwappedPath(type(tmp_path)):  # type: ignore[misc]
        def resolve(self, strict: bool = False) -> Path:
            return outside

    monkeypatch.setattr(_shared, "Path", SwappedPath)

    with pytest.raises(ToolError, match="escapes the repository root"):
        resolve_repo_path(root, str(root / "notes.md"))


def test_atomic_write_text_cleans_up_on_failure(tmp_path: Path) -> None:
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "child").write_text("keep", encoding="utf-8")

    with pytest.raises(OSError):
        atomic_write_text(destination, "content")

    assert sorted(path.name for path in tmp_path.iterdir()) == ["occupied"]
    assert (destination / "child").read_text(encoding="utf-8") == "keep"


class _FakeProcess:
    def __init__(self, outcomes: list[Any], returncode: int = 0):
        self.pid = 4242
        self.returncode = returncode
        self.outcomes = outcomes
        self.timeouts: list[float | None] = []

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _patch_popen(monkeypatch: pytest.MonkeyPatch, process: _FakeProcess) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def popen(argv: list[str], **kwargs: Any) -> _FakeProcess:
        calls.append({"argv": argv, **kwargs})
        return process

    monkeypatch.setattr(_shared.subprocess, "Popen", popen)
    return calls


def test_run_command_validates_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="argv cannot be empty"):
        run_command([], cwd=tmp_path, timeout_seconds=1)
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        run_command(["echo"], cwd=tmp_path, timeout_seconds=0)


def test_run_command_returns_structured_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess([("out", "err")], returncode=3)
    calls = _patch_popen(monkeypatch, process)

    result = run_command(["tool", Path("arg")], cwd=tmp_path, timeout_seconds=5, env={"EXTRA": "1"}, umask=0o077)

    assert (result.argv, result.returncode, result.stdout, result.stderr) == (("tool", "arg"), 3, "out", "err")
    assert result.ok is False
    assert result.duration_seconds >= 0
    assert calls[0]["argv"] == ["tool", "arg"]
    assert calls[0]["env"]["EXTRA"] == "1"
    assert calls[0]["umask"] == 0o077
    assert calls[0]["start_new_session"] is True
    assert process.timeouts == [5]


def test_run_command_terminates_timed_out_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess([subprocess.TimeoutExpired("tool", 1), ("", "")])
    calls = _patch_popen(monkeypatch, process)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(_shared.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(ToolError, match="Command timed out after 1.5s: tool --flag"):
        run_command(["tool", "--flag"], cwd=tmp_path, timeout_seconds=1.5)

    assert calls[0]["umask"] == -1
    assert signals == [(4242, signal.SIGTERM)]
    assert process.timeouts == [1.5, 5]


def test_run_command_kills_process_group_that_ignores_sigterm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([subprocess.TimeoutExpired("tool", 1), subprocess.TimeoutExpired("tool", 5), ("", "")])
    _patch_popen(monkeypatch, process)
    signals: list[int] = []
    monkeypatch.setattr(_shared.os, "killpg", lambda pid, sig: signals.append(sig))

    with pytest.raises(ToolError, match="timed out"):
        run_command(["tool"], cwd=tmp_path, timeout_seconds=1)

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.timeouts == [1, 5, None]


def test_safe_read_text_rejects_bad_limits_and_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        safe_read_text(tmp_path, "file.txt", max_bytes=0)
    with pytest.raises(ToolError, match="not a regular file"):
        safe_read_text(tmp_path, ".")
