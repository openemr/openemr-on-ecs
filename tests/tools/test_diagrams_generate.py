"""Tests for the cdk-dia architecture diagram generator without running Node tools."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture
def generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "diagrams_generate_under_test", REPOSITORY / "diagrams" / "generate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output = tmp_path / "diagrams"
    output.mkdir()
    binaries = tmp_path / "node_modules" / ".bin"
    binaries.mkdir(parents=True)
    for name in ("cdk", "cdk-dia"):
        (binaries / name).write_text("#!/bin/sh\n", encoding="utf-8")
        (binaries / name).chmod(0o755)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "OUTPUT_DIR", output)
    monkeypatch.setattr(module, "CDK_OUT", output / ".cdk.out")
    monkeypatch.setattr(module, "CDK_COMMAND", binaries / "cdk")
    monkeypatch.setattr(module, "CDK_DIA_COMMAND", binaries / "cdk-dia")
    monkeypatch.setattr(module, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/dot", rmtree=shutil.rmtree))
    return module


class _FakeTools:
    """Simulate cdk synth and cdk-dia by writing their expected artifacts."""

    def __init__(self, module: ModuleType, *, fail_on: str | None = None):
        self.module = module
        self.fail_on = fail_on
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, kwargs))
        if self.fail_on is not None and Path(cmd[0]).name == self.fail_on:
            raise subprocess.CalledProcessError(1, cmd, output="synth exploded")
        if cmd[1] == "synth":
            self.module.CDK_OUT.mkdir()
            (self.module.CDK_OUT / "tree.json").write_text("{}", encoding="utf-8")
        else:
            target = Path(cmd[cmd.index("--target-path") + 1])
            target.write_bytes(b"png")
            target.with_suffix(".dot").write_text("digraph {}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", None)


def _install_tools(generator: ModuleType, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> _FakeTools:
    tools = _FakeTools(generator, **kwargs)
    monkeypatch.setattr(
        generator,
        "subprocess",
        SimpleNamespace(
            run=tools,
            PIPE=subprocess.PIPE,
            STDOUT=subprocess.STDOUT,
            CalledProcessError=subprocess.CalledProcessError,
        ),
    )
    return tools


def test_main_synthesizes_and_renders_both_diagrams(
    generator: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CDK_DEFAULT_ACCOUNT", "999999999999")
    tools = _install_tools(generator, monkeypatch)

    generator.main()

    synth_cmd, synth_kwargs = tools.calls[0]
    assert synth_cmd[:2] == [str(generator.CDK_COMMAND), "synth"]
    assert "--no-lookups" in synth_cmd
    assert synth_cmd[synth_cmd.index("--output") + 1] == str(generator.CDK_OUT)
    assert synth_cmd[-6:] == [
        "--context",
        f"certificate_arn={generator.DUMMY_CERT_ARN}",
        "--context",
        "route53_domain=null",
        "--context",
        "security_group_ip_range_ipv4=10.0.0.0/8",
    ]
    assert synth_kwargs["cwd"] == tmp_path
    assert synth_kwargs["check"] is True
    assert synth_kwargs["env"]["AWS_REGION"] == "us-east-1"
    assert "CDK_DEFAULT_ACCOUNT" not in synth_kwargs["env"]

    dia_flags = [cmd[-1] for cmd, _ in tools.calls[1:]]
    assert dia_flags == ["--collapse", "--no-collapse"]
    for name in ("architecture", "architecture-full"):
        assert (tmp_path / "diagrams" / f"{name}.png").read_bytes() == b"png"
        assert not (tmp_path / "diagrams" / f"{name}.dot").exists()
    assert not generator.CDK_OUT.exists()
    output = capsys.readouterr().out
    assert "Generating compact view -> diagrams/architecture.png" in output
    assert output.endswith("Done.\n")


def test_generate_diagrams_requires_tree_json(generator: ModuleType) -> None:
    with pytest.raises(FileNotFoundError, match="did not produce a tree.json"):
        generator.generate_diagrams()


def test_main_reports_failed_commands_and_cleans_up(
    generator: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    generator.CDK_OUT.mkdir()
    _install_tools(generator, monkeypatch, fail_on="cdk")

    with pytest.raises(SystemExit) as raised:
        generator.main()

    assert str(raised.value.code).startswith(f"ERROR: command failed: {generator.CDK_COMMAND} synth")
    assert "synth exploded" in capsys.readouterr().err
    assert not generator.CDK_OUT.exists()


@pytest.mark.parametrize(
    ("broken", "message"),
    [
        ("CDK_COMMAND", "pinned CDK CLI not found"),
        ("CDK_DIA_COMMAND", "pinned cdk-dia CLI not found"),
    ],
)
def test_main_requires_executable_pinned_tools(
    generator: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
    message: str,
) -> None:
    getattr(generator, broken).chmod(0o644)
    _install_tools(generator, monkeypatch)

    with pytest.raises(SystemExit, match=message):
        generator.main()


def test_main_requires_graphviz(generator: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generator, "shutil", SimpleNamespace(which=lambda name: None, rmtree=shutil.rmtree))
    tools = _install_tools(generator, monkeypatch)

    with pytest.raises(SystemExit, match="Graphviz 'dot' binary not found"):
        generator.main()
    assert tools.calls == []
