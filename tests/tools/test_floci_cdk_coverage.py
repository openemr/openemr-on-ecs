"""Floci CDK lifecycle and smoke-app tests with the CDK CLI and emulator mocked out."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import aws_cdk as cdk
import aws_cdk.assertions as assertions
import pytest

from tools._shared import CommandResult, ToolError
from tools.floci_cdk import app as floci_app
from tools.floci_cdk import deploy
from tools.live_e2e.emulator import FLOCI_E2E_ENVIRONMENT

ENDPOINT = "http://127.0.0.1:4566"
CREDENTIALS = {"endpoint_url": ENDPOINT, "access_key_id": "test", "secret_access_key": "secret"}


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "openemr_ecs").mkdir(parents=True)
    (root / "tools" / "floci_cdk").mkdir(parents=True)
    (root / "cdk.json").write_text("{}\n", encoding="utf-8")
    cli = root / "node_modules" / ".bin" / "cdk"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(FLOCI_E2E_ENVIRONMENT, "1")
    monkeypatch.setenv("AWS_PROFILE", "real-account")
    return root


class _Recorder:
    """Stand-in for run_command that records invocations and replays results."""

    def __init__(self, returncodes: dict[str, int] | None = None) -> None:
        self.returncodes = returncodes or {}
        self.calls: list[dict] = []

    def __call__(self, argv, *, cwd, timeout_seconds, env):
        self.calls.append({"argv": argv, "cwd": cwd, "timeout": timeout_seconds, "env": env})
        verb = argv[1]
        code = self.returncodes.get(verb, 0)
        return CommandResult(
            argv=tuple(argv),
            returncode=code,
            stdout=f"{verb} out",
            stderr=f"{verb} failed" if code else "",
            duration_seconds=0.1,
        )

    @property
    def verbs(self) -> list[str]:
        return [call["argv"][1] for call in self.calls]


def test_floci_cdk_root_points_at_app_directory(fake_repo: Path) -> None:
    assert deploy.floci_cdk_root(fake_repo) == fake_repo.resolve() / "tools" / "floci_cdk"


def test_resolve_cdk_command_returns_pinned_cli(fake_repo: Path) -> None:
    assert deploy.resolve_cdk_command(fake_repo) == str((fake_repo / "node_modules" / ".bin" / "cdk").resolve())


def test_emulator_environ_applies_extra_overrides(fake_repo: Path) -> None:
    env = deploy.emulator_environ(**CREDENTIALS, extra={"CDK_DEBUG": "true", "AWS_REGION": "us-west-2"})

    assert env["CDK_DEBUG"] == "true"
    assert env["AWS_REGION"] == "us-west-2"
    assert env["AWS_DEFAULT_REGION"] == deploy.DEFAULT_REGION
    assert "AWS_PROFILE" not in env


@pytest.mark.parametrize(
    ("operation", "expected_args", "timeout"),
    [
        (
            deploy.bootstrap,
            ["bootstrap", "aws://210987654321/eu-west-1", "--toolkit-stack-name", "CDKToolkit"],
            15 * 60,
        ),
        (deploy.deploy, ["deploy", deploy.STACK_NAME, "--require-approval", "never", "--outputs-file"], 20 * 60),
        (deploy.destroy, ["destroy", deploy.STACK_NAME, "--force"], 15 * 60),
    ],
)
def test_cdk_operations_target_floci_with_pinned_cli(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch, operation, expected_args, timeout
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(deploy, "run_command", recorder)

    result = operation(**CREDENTIALS, account_id="210987654321", region="eu-west-1", root=fake_repo)

    assert result.ok
    (call,) = recorder.calls
    argv = list(call["argv"])
    assert argv[0] == str((fake_repo / "node_modules" / ".bin" / "cdk").resolve())
    assert argv[1 : 1 + len(expected_args)] == expected_args
    assert argv[-6:] == ["--app", f"{sys.executable} app.py", "-c", "account=210987654321", "-c", "region=eu-west-1"]
    assert call["cwd"] == fake_repo.resolve() / "tools" / "floci_cdk"
    assert call["timeout"] == timeout
    assert call["env"]["AWS_ENDPOINT_URL"] == ENDPOINT
    assert call["env"]["CDK_DEFAULT_ACCOUNT"] == "210987654321"
    assert call["env"]["AWS_REGION"] == "eu-west-1"
    assert "AWS_PROFILE" not in call["env"]


def test_deploy_writes_outputs_inside_app_cdk_out(fake_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(deploy, "run_command", recorder)

    deploy.deploy(**CREDENTIALS, root=fake_repo)

    argv = list(recorder.calls[0]["argv"])
    outputs = Path(argv[argv.index("--outputs-file") + 1])
    assert outputs == fake_repo.resolve() / "tools" / "floci_cdk" / "cdk.out" / "floci-smoke-outputs.json"


def test_run_lifecycle_bootstraps_deploys_and_destroys(fake_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(deploy, "run_command", recorder)

    boot, deployed, destroyed = deploy.run_lifecycle(**CREDENTIALS, root=fake_repo)

    assert recorder.verbs == ["bootstrap", "deploy", "destroy"]
    assert (boot.stdout, deployed.stdout, destroyed.stdout) == ("bootstrap out", "deploy out", "destroy out")


def test_run_lifecycle_stops_when_bootstrap_fails(fake_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder({"bootstrap": 1})
    monkeypatch.setattr(deploy, "run_command", recorder)

    with pytest.raises(ToolError, match="bootstrap failed: bootstrap failed"):
        deploy.run_lifecycle(**CREDENTIALS, root=fake_repo)

    assert recorder.verbs == ["bootstrap"]


def test_run_lifecycle_destroys_even_when_deploy_fails(fake_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder({"deploy": 1})
    monkeypatch.setattr(deploy, "run_command", recorder)

    with pytest.raises(ToolError, match="deploy failed: deploy failed"):
        deploy.run_lifecycle(**CREDENTIALS, root=fake_repo)

    assert recorder.verbs == ["bootstrap", "deploy", "destroy"]


def test_run_lifecycle_reports_destroy_failure(fake_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder({"destroy": 1})
    monkeypatch.setattr(deploy, "run_command", recorder)

    with pytest.raises(ToolError, match="destroy failed: destroy failed"):
        deploy.run_lifecycle(**CREDENTIALS, root=fake_repo)

    assert recorder.verbs == ["bootstrap", "deploy", "destroy"]


def test_run_lifecycle_destroys_when_deploy_raises(fake_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(deploy, "run_command", recorder)
    original_deploy = deploy.deploy

    def exploding_deploy(**kwargs):
        original_deploy(**kwargs)
        raise RuntimeError("cdk crashed")

    monkeypatch.setattr(deploy, "deploy", exploding_deploy)

    with pytest.raises(RuntimeError, match="cdk crashed"):
        deploy.run_lifecycle(**CREDENTIALS, root=fake_repo)

    assert recorder.verbs == ["bootstrap", "deploy", "destroy"]


@pytest.fixture(scope="module")
def smoke_template() -> assertions.Template:
    app = cdk.App()
    stack = floci_app.FlociSmokeStack(
        app, "OpenemrFlociSmoke", env=cdk.Environment(account="123456789012", region="us-east-1")
    )
    return assertions.Template.from_stack(stack)


def test_smoke_stack_creates_disposable_emulator_resources(smoke_template: assertions.Template) -> None:
    for resource_type in ("AWS::S3::Bucket", "AWS::SQS::Queue", "AWS::SSM::Parameter", "AWS::Logs::LogGroup"):
        smoke_template.resource_count_is(resource_type, 1)
    smoke_template.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Delete"})
    smoke_template.has_resource_properties("AWS::SQS::Queue", {"MessageRetentionPeriod": 86400})
    smoke_template.has_resource_properties(
        "AWS::SSM::Parameter", {"Name": "/openemr/floci/smoke", "Value": "floci-cdk-smoke"}
    )
    smoke_template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 1})
    smoke_template.resource_count_is("Custom::S3AutoDeleteObjects", 0)


def test_smoke_stack_grants_role_bucket_and_queue_access(smoke_template: assertions.Template) -> None:
    smoke_template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "Description": "Floci CDK smoke role",
            "AssumeRolePolicyDocument": assertions.Match.object_like(
                {
                    "Statement": [
                        assertions.Match.object_like({"Principal": {"Service": "lambda.amazonaws.com"}}),
                    ]
                }
            ),
        },
    )
    rendered = json.dumps(smoke_template.find_resources("AWS::IAM::Policy"))
    assert "s3:PutObject" in rendered
    assert "sqs:SendMessage" in rendered
    assert set(smoke_template.to_json()["Outputs"]) == {
        "BucketName",
        "QueueUrl",
        "ParameterName",
        "LogGroupName",
        "RoleArn",
    }


@pytest.mark.parametrize(
    ("context", "expected_env"),
    [
        ({}, ("123456789012", "us-east-1")),
        ({"account": "210987654321", "region": "eu-west-1"}, ("210987654321", "eu-west-1")),
    ],
)
def test_main_synthesizes_smoke_stack_for_context_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context, expected_env
) -> None:
    real_app = cdk.App
    monkeypatch.setattr(floci_app.cdk, "App", lambda: real_app(outdir=str(tmp_path), context=context))

    floci_app.main()

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    artifact = manifest["artifacts"]["OpenemrFlociSmoke"]
    assert artifact["environment"] == f"aws://{expected_env[0]}/{expected_env[1]}"
    template = json.loads((tmp_path / "OpenemrFlociSmoke.template.json").read_text(encoding="utf-8"))
    assert template["Description"] == "Floci CDK deploy/destroy smoke stack for openemr-on-ecs CI"
