"""Unit coverage for live E2E support modules: entry point, emulator, progress, Docker proxy, and Floci helpers."""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError

from tools._shared import ToolError
from tools.live_e2e import cli, docker_proxy, floci_images, floci_seed
from tools.live_e2e.emulator import assert_safe_emulator_endpoint, is_local_emulator_endpoint
from tools.live_e2e.progress import ProgressReporter


def _docker_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
    pytest.fail("Docker must not be invoked")


def _client_error(code: str, message: str = "") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Operation")


def test_module_entry_point_delegates_to_cli_main() -> None:
    module = importlib.import_module("tools.live_e2e.__main__")
    module = importlib.reload(module)
    assert module.main is cli.main


def test_emulator_endpoint_without_host_is_not_local() -> None:
    assert is_local_emulator_endpoint("http://") is False
    assert is_local_emulator_endpoint("http://floci.localhost:4566") is True


def test_emulator_rejects_local_looking_real_aws_hostnames() -> None:
    with pytest.raises(ToolError, match="real AWS endpoints"):
        assert_safe_emulator_endpoint("http://sts.amazonaws.com.localhost:4566")


def test_progress_pulse_with_non_positive_interval_runs_without_heartbeat_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(stream=stream, verbose=True)
    monkeypatch.setattr(
        "tools.live_e2e.progress.threading.Thread",
        lambda *_a, **_k: pytest.fail("no heartbeat thread for non-positive intervals"),
    )
    entered = False
    with reporter.pulse("waiting", interval_seconds=0):
        entered = True
    assert entered
    assert stream.getvalue() == ""


def test_progress_pulse_emits_elapsed_heartbeats_while_blocked() -> None:
    emitted = threading.Event()

    class Stream(io.StringIO):
        def write(self, value: str) -> int:
            if "elapsed" in value:
                emitted.set()
            return super().write(value)

    stream = Stream()
    reporter = ProgressReporter(stream=stream, verbose=True)
    with reporter.pulse("CDK deploy still running", interval_seconds=0.01):
        assert emitted.wait(timeout=5.0)
    output = stream.getvalue()
    assert "CDK deploy still running (elapsed 0m00s)" in output
    assert "CDK deploy still running finished in 0m00s" in output


@pytest.mark.parametrize(
    ("docker", "timings"),
    (
        ("relative/docker", "/tmp/timings.jsonl"),
        ("", "/tmp/timings.jsonl"),
    ),
)
def test_docker_proxy_refuses_unsafe_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    docker: str,
    timings: str,
) -> None:
    monkeypatch.setenv("OPENEMR_E2E_REAL_DOCKER", docker)
    monkeypatch.setenv("OPENEMR_E2E_DOCKER_TIMINGS", timings)
    monkeypatch.setattr(docker_proxy, "subprocess", SimpleNamespace(run=_docker_must_not_run))

    assert docker_proxy.main() == 126
    assert "not safely configured" in capsys.readouterr().err


def test_docker_proxy_records_relative_timing_path_as_unsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\n", encoding="utf-8")
    docker.chmod(0o700)
    monkeypatch.setenv("OPENEMR_E2E_REAL_DOCKER", str(docker))
    monkeypatch.setenv("OPENEMR_E2E_DOCKER_TIMINGS", "relative.jsonl")
    monkeypatch.setattr(docker_proxy, "subprocess", SimpleNamespace(run=_docker_must_not_run))
    assert docker_proxy.main() == 126
    assert not (tmp_path / "relative.jsonl").exists()


def test_docker_proxy_records_interrupted_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\n", encoding="utf-8")
    docker.chmod(0o700)
    timing = tmp_path / "nested" / "timing.jsonl"
    monkeypatch.setenv("OPENEMR_E2E_REAL_DOCKER", str(docker))
    monkeypatch.setenv("OPENEMR_E2E_DOCKER_TIMINGS", str(timing))
    monkeypatch.setattr(sys, "argv", ["docker-proxy", "buildx", "build", "."])
    calls: list[list[str]] = []

    def interrupted(argv: list[str], **kwargs: Any) -> Any:
        calls.append(argv)
        assert kwargs == {"check": False}
        raise KeyboardInterrupt

    monkeypatch.setattr(docker_proxy, "subprocess", SimpleNamespace(run=interrupted))

    assert docker_proxy.main() == 130
    assert calls == [[str(docker), "buildx", "build", "."]]
    record = json.loads(timing.read_text(encoding="utf-8"))
    assert record["category"] == "build"
    assert record["returncode"] == 130
    assert record["schema_version"] == 1
    assert timing.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("arguments", "category"),
    (
        (["build", "."], "build"),
        (["buildx", "build", "."], "build"),
        (["buildx", "ls"], "other"),
        (["push", "repo:tag"], "publish"),
        ([], "other"),
    ),
)
def test_docker_proxy_categories(arguments: list[str], category: str) -> None:
    assert docker_proxy._category(arguments) == category


def test_docker_proxy_closes_descriptor_when_fdopen_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[int] = []

    class OsProxy:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        @staticmethod
        def fdopen(*_a: Any, **_k: Any) -> Any:
            raise OSError("fdopen failed")

        @staticmethod
        def close(descriptor: int) -> None:
            closed.append(descriptor)
            os.close(descriptor)

    monkeypatch.setattr(docker_proxy, "os", OsProxy())
    with pytest.raises(OSError, match="fdopen failed"):
        docker_proxy._append_record(tmp_path / "timing.jsonl", {"category": "other"})
    assert len(closed) == 1
    assert (tmp_path / "timing.jsonl").read_text(encoding="utf-8") == ""


def test_floci_engine_version_rejects_blank_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        floci_images.StackConstants,
        "AURORA_MYSQL_ENGINE_VERSION",
        SimpleNamespace(aurora_mysql_full_version="  "),
    )
    with pytest.raises(ToolError, match="aurora_mysql_full_version"):
        floci_images.aurora_mysql_engine_version()


class _FakeImage:
    def __init__(self, log: list[tuple[str, ...]], reference: str) -> None:
        self.log = log
        self.reference = reference

    def tag(self, *, repository: str, tag: str) -> None:
        self.log.append(("tag", self.reference, repository, tag))


class _FakeImages:
    def __init__(self, log: list[tuple[str, ...]]) -> None:
        self.log = log

    def pull(self, image: str) -> None:
        self.log.append(("pull", image))

    def get(self, image: str) -> _FakeImage:
        self.log.append(("get", image))
        return _FakeImage(self.log, image)


def _install_fake_docker(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    log: list[tuple[str, ...]] = []
    client = SimpleNamespace(images=_FakeImages(log))
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))
    return log


def test_floci_runtime_images_pull_and_retag_aurora_image(monkeypatch: pytest.MonkeyPatch) -> None:
    log = _install_fake_docker(monkeypatch)

    prepared = floci_images.ensure_floci_runtime_images(
        engine_version="8.0.mysql_aurora.3.12.0",
        mysql_source_image="mysql:8.0@sha256:abc",
        extra_images=("valkey/valkey:8", ""),
    )

    assert prepared == ("mysql:8.0.mysql_aurora.3.12.0", "valkey/valkey:8")
    assert log == [
        ("pull", "mysql:8.0@sha256:abc"),
        ("get", "mysql:8.0@sha256:abc"),
        ("tag", "mysql:8.0@sha256:abc", "mysql", "8.0.mysql_aurora.3.12.0"),
        ("pull", "valkey/valkey:8"),
    ]


def test_floci_runtime_images_skip_retag_when_source_matches_target(monkeypatch: pytest.MonkeyPatch) -> None:
    log = _install_fake_docker(monkeypatch)

    prepared = floci_images.ensure_floci_runtime_images(
        engine_version="8.0.39",
        mysql_source_image="mysql:8.0.39",
        extra_images=(),
    )

    assert prepared == ("mysql:8.0.39",)
    assert log == [("pull", "mysql:8.0.39")]


def test_floci_runtime_images_reject_untagged_target(monkeypatch: pytest.MonkeyPatch) -> None:
    log = _install_fake_docker(monkeypatch)

    with pytest.raises(ToolError, match="missing a tag"):
        floci_images.ensure_floci_runtime_images(
            engine_version="   ",
            mysql_source_image="mysql:8.0",
            extra_images=(),
        )
    assert ("pull", "mysql:8.0") in log


class _Recorder:
    """Generic fake boto3 client that records calls and replays scripted responses."""

    def __init__(self, service: str, responses: dict[str, list[Any]] | None = None) -> None:
        self.service = service
        self.responses = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def call(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            scripted = self.responses.get(name)
            if not scripted:
                return {}
            value = scripted.pop(0) if len(scripted) > 1 else scripted[0]
            if isinstance(value, BaseException):
                raise value
            return value

        return call

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class _Session:
    def __init__(self, clients: dict[str, _Recorder]) -> None:
        self.clients = clients
        self.requests: list[tuple[str, str, str]] = []

    def client(self, service: str, *, endpoint_url: str, region_name: str) -> _Recorder:
        self.requests.append((service, endpoint_url, region_name))
        return self.clients.setdefault(service, _Recorder(service))


def test_seed_world_creates_operator_zone_bootstrap_and_roles() -> None:
    iam = _Recorder(
        "iam",
        {
            "list_access_keys": [{"AccessKeyMetadata": [{"AccessKeyId": "AKOLD"}, {"AccessKeyId": ""}, {}]}],
            "create_access_key": [{"AccessKey": {"AccessKeyId": "AKNEW", "SecretAccessKey": "s3cr3t"}}],
            "create_role": [_client_error("EntityAlreadyExists"), {}],
            "put_role_policy": [_client_error("NotImplemented"), {}],
        },
    )
    route53 = _Recorder(
        "route53",
        {
            "list_hosted_zones_by_name": [{"HostedZones": [{"Name": "other.test.", "Id": "/hostedzone/ZOTHER"}]}],
            "create_hosted_zone": [{"HostedZone": {"Id": "/hostedzone/abc123"}}],
        },
    )
    cfn = _Recorder(
        "cloudformation",
        {"describe_stacks": [_client_error("ValidationError", "Stack with id CDKToolkit does not exist")]},
    )
    session = _Session({"iam": iam, "route53": route53, "cloudformation": cfn})

    world = floci_seed.seed_live_e2e_world(
        session, endpoint_url="http://localhost:4566", route53_domain="E2E.Floci.Test."
    )

    assert world == {
        "account_id": floci_seed.DEFAULT_ACCOUNT_ID,
        "region": floci_seed.DEFAULT_REGION,
        "route53_domain": "E2E.Floci.Test.",
        "hosted_zone_id": "ZABC123",
        "bootstrap_stack_name": "CDKToolkit",
        "qualifier": "hnb659fds",
        "role_count": "5",
        "aws_access_key_id": "AKNEW",
        "aws_secret_access_key": "s3cr3t",
        "operator_user": floci_seed.DEFAULT_OPERATOR_USER,
    }
    assert all(endpoint == "http://localhost:4566" for _, endpoint, _ in session.requests)
    assert ("delete_access_key", {"UserName": "openemr-floci-e2e", "AccessKeyId": "AKOLD"}) in iam.calls
    assert iam.names().count("delete_access_key") == 1
    role_names = [kwargs["RoleName"] for name, kwargs in iam.calls if name == "create_role"]
    assert "cdk-hnb659fds-cfn-exec-role-123456789012-us-east-1" in role_names
    assert "cdk-hnb659fds-deploy-role-123456789012-us-east-1" in role_names
    create_zone = next(kwargs for name, kwargs in route53.calls if name == "create_hosted_zone")
    assert create_zone["Name"] == "e2e.floci.test"
    create_stack = next(kwargs for name, kwargs in cfn.calls if name == "create_stack")
    assert create_stack["StackName"] == "CDKToolkit"
    assert json.loads(create_stack["TemplateBody"])["Outputs"]["BootstrapVersion"]["Value"] == "21"


def test_seed_world_reuses_existing_zone_and_skips_bootstrap_fixture() -> None:
    iam = _Recorder(
        "iam",
        {
            "create_user": [_client_error("EntityAlreadyExistsException")],
            "put_user_policy": [_client_error("Weird", "Operation not implemented here")],
            "list_access_keys": [{}],
            "create_access_key": [{"AccessKey": {"AccessKeyId": "AK2", "SecretAccessKey": "s2"}}],
        },
    )
    route53 = _Recorder(
        "route53",
        {"list_hosted_zones_by_name": [{"HostedZones": [{"Name": "e2e.floci.test.", "Id": "/hostedzone/Z9"}]}]},
    )
    cfn = _Recorder("cloudformation")
    session = _Session({"iam": iam, "route53": route53, "cloudformation": cfn})

    world = floci_seed.seed_live_e2e_world(
        session,
        endpoint_url="http://localhost:4566",
        include_bootstrap_fixture=False,
    )

    assert world["hosted_zone_id"] == "Z9"
    assert world["role_count"] == "0"
    assert "create_hosted_zone" not in route53.names()
    assert cfn.calls == []
    assert "create_role" not in iam.names()


def test_seed_world_existing_bootstrap_stack_is_not_recreated() -> None:
    iam = _Recorder("iam", {"create_access_key": [{"AccessKey": {"AccessKeyId": "A", "SecretAccessKey": "B"}}]})
    route53 = _Recorder("route53", {"create_hosted_zone": [{"HostedZone": {"Id": "Z1"}}]})
    cfn = _Recorder("cloudformation", {"describe_stacks": [{"Stacks": [{}]}]})
    session = _Session({"iam": iam, "route53": route53, "cloudformation": cfn})

    floci_seed.seed_live_e2e_world(session, endpoint_url="http://localhost:4566")

    assert cfn.names() == ["describe_stacks"]


@pytest.mark.parametrize(
    ("client", "operation", "error"),
    (
        ("iam", "create_user", _client_error("AccessDenied")),
        ("iam", "put_user_policy", _client_error("Throttling", "slow down")),
        ("cloudformation", "describe_stacks", _client_error("Throttling", "rate exceeded")),
        ("iam", "create_role", _client_error("LimitExceeded")),
        ("iam", "put_role_policy", _client_error("MalformedPolicyDocument", "bad policy")),
    ),
)
def test_seed_world_propagates_unexpected_client_errors(client: str, operation: str, error: ClientError) -> None:
    clients = {
        "iam": _Recorder("iam", {"create_access_key": [{"AccessKey": {"AccessKeyId": "A", "SecretAccessKey": "B"}}]}),
        "route53": _Recorder("route53", {"create_hosted_zone": [{"HostedZone": {"Id": "Z1"}}]}),
        "cloudformation": _Recorder("cloudformation", {"describe_stacks": [{"Stacks": [{}]}]}),
    }
    clients[client].responses[operation] = [error]

    with pytest.raises(ClientError) as raised:
        floci_seed.seed_live_e2e_world(_Session(clients), endpoint_url="http://localhost:4566")
    assert raised.value is error


def test_seed_owned_stack_waits_for_in_progress_status(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(floci_seed, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleeps.append))
    cfn = _Recorder(
        "cloudformation",
        {
            "create_stack": [_client_error("AlreadyExistsException")],
            "describe_stacks": [
                {"Stacks": [{"StackId": "stack-1", "StackStatus": "CREATE_IN_PROGRESS"}]},
                {"Stacks": [{"StackId": "stack-1", "StackStatus": "CREATE_COMPLETE"}]},
            ],
        },
    )

    stack_id = floci_seed.seed_owned_stack(
        _Session({"cloudformation": cfn}),
        endpoint_url="http://localhost:4566",
        stack_name="OpenemrE2E-abc",
        run_id="e2e-floci-run",
    )

    assert stack_id == "stack-1"
    assert sleeps == [0.2]
    template = json.loads(cfn.calls[0][1]["TemplateBody"])
    assert template["Outputs"]["LiveE2ERunId"]["Value"] == "e2e-floci-run"
    assert cfn.calls[0][1]["Capabilities"] == ["CAPABILITY_NAMED_IAM"]


def test_seed_owned_stack_propagates_unexpected_create_error() -> None:
    error = _client_error("InsufficientCapabilities")
    cfn = _Recorder("cloudformation", {"create_stack": [error]})
    with pytest.raises(ClientError) as raised:
        floci_seed.seed_owned_stack(
            _Session({"cloudformation": cfn}),
            endpoint_url="http://localhost:4566",
            stack_name="OpenemrE2E-abc",
            run_id="e2e-floci-run",
        )
    assert raised.value is error


def _smoke_clients(**errors: list[Any]) -> dict[str, _Recorder]:
    clients = {
        "s3": _Recorder("s3"),
        "kms": _Recorder("kms", {"create_key": [{"KeyMetadata": {"KeyId": "key-1"}}]}),
        "logs": _Recorder("logs"),
        "ecs": _Recorder("ecs"),
    }
    for key, value in errors.items():
        service, operation = key.split("__")
        clients[service].responses[operation] = value
    return clients


def test_seed_service_smoke_resources_in_us_east_1_tolerates_existing_resources() -> None:
    clients = _smoke_clients(
        s3__create_bucket=[_client_error("BucketAlreadyOwnedByYou")],
        logs__create_log_group=[_client_error("ResourceAlreadyExistsException")],
        ecs__create_cluster=[_client_error("InvalidParameterException")],
    )

    details = floci_seed.seed_service_smoke_resources(_Session(clients), endpoint_url="http://localhost:4566")

    assert details == {
        "bucket_name": "openemr-floci-smoke",
        "kms_key_id": "key-1",
        "log_group": "/openemr/floci/smoke",
        "ecs_cluster": "openemr-floci-smoke",
    }
    assert clients["s3"].calls[0] == ("create_bucket", {"Bucket": "openemr-floci-smoke"})
    assert clients["s3"].calls[1] == (
        "put_object",
        {"Bucket": "openemr-floci-smoke", "Key": "smoke.txt", "Body": b"floci"},
    )


def test_seed_service_smoke_resources_outside_us_east_1_sets_location_constraint() -> None:
    clients = _smoke_clients()
    floci_seed.seed_service_smoke_resources(
        _Session(clients),
        endpoint_url="http://localhost:4566",
        region="us-west-2",
        bucket_name="custom-bucket",
    )
    assert clients["s3"].calls[0] == (
        "create_bucket",
        {"Bucket": "custom-bucket", "CreateBucketConfiguration": {"LocationConstraint": "us-west-2"}},
    )


@pytest.mark.parametrize(
    "key",
    ("s3__create_bucket", "logs__create_log_group", "ecs__create_cluster"),
)
def test_seed_service_smoke_resources_propagates_unexpected_errors(key: str) -> None:
    error = _client_error("AccessDenied")
    clients = _smoke_clients(**{key: [error]})
    with pytest.raises(ClientError) as raised:
        floci_seed.seed_service_smoke_resources(_Session(clients), endpoint_url="http://localhost:4566")
    assert raised.value is error


def test_operator_session_uses_seeded_credentials_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_session(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "session"

    monkeypatch.setattr(floci_seed, "boto3", SimpleNamespace(Session=fake_session))
    assert (
        floci_seed.operator_session(
            endpoint_url="http://localhost:4566",
            region="us-east-1",
            access_key_id="AK",
            secret_access_key="SK",
        )
        == "session"
    )
    assert captured == {"region_name": "us-east-1", "aws_access_key_id": "AK", "aws_secret_access_key": "SK"}


def test_hosted_zone_id_normalization() -> None:
    assert floci_seed._normalize_hosted_zone_id("/hostedzone/z123") == "Z123"
    assert floci_seed._normalize_hosted_zone_id("abc") == "ZABC"
    assert floci_seed._normalize_hosted_zone_id("") == ""
