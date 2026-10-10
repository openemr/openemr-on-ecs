"""Offline coverage for live E2E deployment validation, timing, and HTTPS probes.

Every AWS client and HTTP request is an in-memory fake; nothing here can reach AWS.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Iterator
from unittest.mock import MagicMock

import pytest
import requests  # type: ignore[import-untyped]
from botocore.exceptions import ClientError

import tools.live_e2e.aws as aws_module
from tools._shared import ToolError
from tools.live_e2e.aws import LiveE2EAws

RUN_ID = "e2e-validation"
CREATED = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
LB_ARN = "arn:aws:elasticloadbalancing:us-east-1:111122223333:loadbalancer/app/test"
APP_URL = "https://openemr.test.example/"


def _client_error(code: str, message: str = "") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


class _Session:
    def __init__(self, clients: dict[str, Any]) -> None:
        self.clients = clients

    def client(self, service: str, **_: Any) -> Any:
        return self.clients[service]


class _Progress:
    """Duck-typed progress reporter that records every message."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.pulses: list[tuple[str, float | None]] = []

    def phase(self, name: str, detail: str = "") -> None:
        self.messages.append(f"phase:{name}:{detail}")

    def info(self, message: str) -> None:
        self.messages.append(message)

    def detail(self, message: str) -> None:
        self.messages.append(message)

    def heartbeat(self, message: str, *, force: bool = False) -> None:
        self.messages.append(f"heartbeat:{message}")

    @contextmanager
    def pulse(self, message: str, *, interval_seconds: float | None = None) -> Iterator[None]:
        self.pulses.append((message, interval_seconds))
        yield


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _response(status: int = 200, text: str = "<title>OpenEMR</title>") -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status,
        text=text,
        elapsed=SimpleNamespace(total_seconds=lambda: 0.25),
    )


def _healthy_task() -> dict[str, Any]:
    return {
        "lastStatus": "RUNNING",
        "healthStatus": "HEALTHY",
        "containers": [{"lastStatus": "RUNNING", "healthStatus": "HEALTHY"}, {"lastStatus": "RUNNING"}],
    }


def _healthy_clients() -> dict[str, MagicMock]:
    ecs = MagicMock()
    ecs.describe_services.return_value = {
        "services": [
            {
                "runningCount": 2,
                "desiredCount": 2,
                "deployments": [{"rolloutState": "COMPLETED"}, {}],
                "taskDefinition": "arn:aws:ecs:us-east-1:111122223333:task-definition/openemr:3",
                "events": [
                    {"createdAt": datetime(2026, 7, 31, 11, 0), "message": "service is unhealthy"},
                    {"createdAt": "not-a-datetime", "message": "task failed to start"},
                    {"createdAt": datetime(2026, 7, 31, 12, 5), "message": "service reached steady state"},
                ],
            }
        ]
    }
    ecs.list_tasks.return_value = {"taskArns": ["task-1", "task-2"]}
    ecs.describe_tasks.return_value = {"tasks": [_healthy_task(), _healthy_task()]}
    ecs.describe_task_definition.return_value = {
        "taskDefinition": {
            "containerDefinitions": [
                {"name": "sidecar", "secrets": []},
                {
                    "name": "openemr",
                    "secrets": [
                        {"name": "OPENEMR_SETTING_portal_onsite_two_enable"},
                        {"name": "OPENEMR_SETTING_rest_api"},
                        {"name": "OPENEMR_SETTING_rest_fhir_api"},
                        {"valueFrom": "unnamed"},
                    ],
                },
            ]
        }
    }

    elbv2 = MagicMock()
    elbv2.describe_target_health.return_value = {
        "TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}, {"TargetHealth": {"State": "healthy"}}]
    }
    efs = MagicMock()
    efs.describe_file_systems.return_value = {"FileSystems": [{"LifeCycleState": "available"}]}
    rds = MagicMock()
    rds.describe_db_clusters.return_value = {"DBClusters": [{"Status": "available", "HttpEndpointEnabled": True}]}
    cache = MagicMock()
    cache.describe_serverless_caches.return_value = {"ServerlessCaches": [{"Status": "AVAILABLE"}]}
    waf = MagicMock()
    waf.get_web_acl_for_resource.return_value = {"WebACL": {"Name": "acl"}}
    cloudwatch = MagicMock()
    cloudwatch.describe_alarms.return_value = {"MetricAlarms": [{"AlarmName": "cpu"}], "CompositeAlarms": []}
    logs = MagicMock()
    logs.filter_log_events.return_value = {"events": [{"message": "OpenEMR startup complete"}]}
    return {
        "ecs": ecs,
        "elbv2": elbv2,
        "efs": efs,
        "rds": rds,
        "elasticache": cache,
        "wafv2": waf,
        "cloudwatch": cloudwatch,
        "logs": logs,
    }


def _healthy_stack() -> dict[str, Any]:
    return {
        "StackId": "stack-id",
        "StackStatus": "CREATE_COMPLETE",
        "CreationTime": datetime(2026, 7, 31, 12, 0),
        "Outputs": [
            {"OutputKey": "ECSClusterName", "OutputValue": "cluster"},
            {"OutputKey": "ECSServiceName", "OutputValue": "service"},
            {"OutputKey": "EFSSitesFileSystemId", "OutputValue": "fs-sites"},
            {"OutputKey": "EFSSSLFileSystemId", "OutputValue": "fs-ssl"},
            {"OutputKey": "DatabaseClusterArn", "OutputValue": "arn:aws:rds:us-east-1:111122223333:cluster:db-1"},
            {"OutputKey": "ApplicationURL", "OutputValue": APP_URL},
            {"OutputKey": "LogGroupName", "OutputValue": "/aws/ecs/test"},
        ],
    }


def _healthy_resources() -> list[dict[str, Any]]:
    complete = "CREATE_COMPLETE"
    return [
        {"ResourceType": "AWS::ECS::Cluster", "ResourceStatus": complete},
        {"ResourceType": "AWS::ECS::Service", "ResourceStatus": complete},
        {"ResourceType": "AWS::EFS::FileSystem", "ResourceStatus": complete},
        {
            "ResourceType": "AWS::ElastiCache::ServerlessCache",
            "ResourceStatus": complete,
            "PhysicalResourceId": "cache",
        },
        {
            "ResourceType": "AWS::ElasticLoadBalancingV2::LoadBalancer",
            "ResourceStatus": complete,
            "PhysicalResourceId": LB_ARN,
        },
        {
            "ResourceType": "AWS::ElasticLoadBalancingV2::TargetGroup",
            "ResourceStatus": complete,
            "PhysicalResourceId": "tg",
        },
        {"ResourceType": "AWS::ElasticLoadBalancingV2::TargetGroup", "ResourceStatus": complete},
        {"ResourceType": "AWS::RDS::DBCluster", "ResourceStatus": complete},
        {"ResourceType": "AWS::WAFv2::WebACLAssociation", "ResourceStatus": complete},
        {"ResourceType": "AWS::CloudWatch::Alarm", "ResourceStatus": complete, "PhysicalResourceId": "cpu"},
    ]


class _Adapter(LiveE2EAws):
    def __init__(self, *, emulated: bool = False) -> None:
        self.fakes = _healthy_clients()
        super().__init__(
            region="us-east-1",
            session=_Session(self.fakes),
            progress=_Progress(),  # type: ignore[arg-type]
            endpoint_url="http://127.0.0.1:4566" if emulated else None,
            emulated=emulated,
        )
        self.stack = _healthy_stack()
        self.resources = _healthy_resources()

    def assert_owned_stack(self, stack_name_or_id: str, run_id: str) -> dict[str, Any]:
        assert (stack_name_or_id, run_id) == ("stack-id", RUN_ID)
        return self.stack

    def _stack_resources(self, stack_id: str) -> list[dict[str, Any]]:
        assert stack_id == "stack-id"
        return self.resources

    def set_output(self, key: str, value: str | None) -> None:
        outputs = [item for item in self.stack["Outputs"] if item["OutputKey"] != key]
        if value is not None:
            outputs.append({"OutputKey": key, "OutputValue": value})
        self.stack["Outputs"] = outputs


class _Http:
    """Record requests.get calls and answer by URL suffix."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.answers: dict[str, Any] = {}

    def __call__(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        for suffix, answer in self.answers.items():
            if url.endswith(suffix):
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        return _response()


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> _Http:
    fake = _Http()
    monkeypatch.setattr(aws_module.requests, "get", fake)
    return fake


def _validate(adapter: _Adapter, profile: str = "default") -> tuple[Any, Any]:
    return adapter.validate_deployment(
        stack_name_or_id="stack-id",
        run_id=RUN_ID,
        profile=profile,
        https_timeout_seconds=60,
        poll_seconds=12,
    )


# --- real AWS validation ----------------------------------------------------


def test_api_enabled_validation_checks_portal_alarms_and_waiter_bounds(http: _Http) -> None:
    adapter = _Adapter()

    checks, phases = _validate(adapter, profile="api-enabled")

    names = [check.name for check in checks]
    assert names == [
        "cloudformation-stack",
        "expected-resources",
        "application-https",
        "ecs-service",
        "ecs-task-health",
        "load-balancer-targets",
        "efs-file-systems",
        "aurora-cluster",
        "elasticache-serverless",
        "waf-association",
        "monitoring-resources",
        "startup-logs",
        "api-enabled-profile",
        "application-smoke",
    ]
    by_name = {check.name: check for check in checks}
    assert by_name["expected-resources"].detail == "10 stack resources healthy"
    assert by_name["monitoring-resources"].detail == "1 CloudWatch alarms present"
    assert by_name["ecs-task-health"].detail == "2 running healthy tasks with no crash-loop events"
    phase_by_name = {phase.name: phase for phase in phases}
    assert phase_by_name["http-readiness"].duration_seconds == 0.25
    ready = phase_by_name["application-https-ready"]
    assert ready.started_at == "2026-07-31T12:00:00Z"
    assert ready.finished_at is not None and ready.finished_at.endswith("Z")
    assert phases[-1].name == "deployment-validation"

    assert [url for url, _ in http.calls] == [
        APP_URL,
        "https://openemr.test.example/portal/",
        "https://openemr.test.example/interface/login/login.php",
    ]
    waiter = adapter.fakes["ecs"].get_waiter.return_value
    waiter_kwargs = waiter.wait.call_args.kwargs
    assert waiter_kwargs["cluster"] == "cluster"
    assert waiter_kwargs["services"] == ["service"]
    assert waiter_kwargs["WaiterConfig"]["Delay"] == 12
    assert 1 <= waiter_kwargs["WaiterConfig"]["MaxAttempts"] <= 5
    adapter.fakes["ecs"].list_tasks.assert_called_once_with(
        cluster="cluster", serviceName="service", desiredStatus="RUNNING"
    )
    adapter.fakes["elbv2"].describe_target_health.assert_called_once_with(TargetGroupArn="tg")
    adapter.fakes["rds"].describe_db_clusters.assert_called_once_with(DBClusterIdentifier="db-1")
    adapter.fakes["wafv2"].get_web_acl_for_resource.assert_called_once_with(ResourceArn=LB_ARN)
    adapter.fakes["cloudwatch"].describe_alarms.assert_called_once_with(AlarmNames=["cpu"])
    adapter.fakes["ecs"].describe_task_definition.assert_called_once_with(
        taskDefinition="arn:aws:ecs:us-east-1:111122223333:task-definition/openemr:3"
    )
    assert adapter.progress.pulses == [("ECS service still converging", 30.0)]  # type: ignore[attr-defined]


def test_validation_without_alarms_reports_no_monitoring_resources(http: _Http) -> None:
    adapter = _Adapter()
    adapter.resources = [item for item in adapter.resources if item["ResourceType"] != "AWS::CloudWatch::Alarm"]

    checks, _ = _validate(adapter)

    monitoring = next(check for check in checks if check.name == "monitoring-resources")
    assert monitoring.detail == "profile has no CloudWatch alarm resources"
    adapter.fakes["cloudwatch"].describe_alarms.assert_not_called()
    assert "api-enabled-profile" not in {check.name for check in checks}


def _set(attr: str, value: Any) -> Callable[[_Adapter], None]:
    def mutate(adapter: _Adapter) -> None:
        setattr(adapter, attr, value)

    return mutate


def _stack_status(adapter: _Adapter) -> None:
    adapter.stack["StackStatus"] = "UPDATE_ROLLBACK_COMPLETE"


def _drop_resource(resource_type: str) -> Callable[[_Adapter], None]:
    def mutate(adapter: _Adapter) -> None:
        adapter.resources = [item for item in adapter.resources if item["ResourceType"] != resource_type]

    return mutate


def _failed_resource(adapter: _Adapter) -> None:
    adapter.resources.append(
        {"ResourceType": "AWS::Lambda::Function", "ResourceStatus": "CREATE_FAILED", "LogicalResourceId": "Fn"}
    )


def _output(key: str, value: str | None) -> Callable[[_Adapter], None]:
    def mutate(adapter: _Adapter) -> None:
        adapter.set_output(key, value)

    return mutate


def _service(**changes: Any) -> Callable[[_Adapter], None]:
    def mutate(adapter: _Adapter) -> None:
        adapter.fakes["ecs"].describe_services.return_value["services"][0].update(changes)

    return mutate


def _fake_return(service: str, method: str, value: Any) -> Callable[[_Adapter], None]:
    def mutate(adapter: _Adapter) -> None:
        getattr(adapter.fakes[service], method).return_value = value

    return mutate


def _unhealthy_container(adapter: _Adapter) -> None:
    task = _healthy_task()
    task["containers"] = [{"lastStatus": "RUNNING", "healthStatus": "UNHEALTHY"}]
    adapter.fakes["ecs"].describe_tasks.return_value = {"tasks": [task, task]}


def _second_cache(adapter: _Adapter) -> None:
    adapter.resources.append(
        {
            "ResourceType": "AWS::ElastiCache::ServerlessCache",
            "ResourceStatus": "CREATE_COMPLETE",
            "PhysicalResourceId": "x",
        }
    )


def _non_arn_load_balancer(adapter: _Adapter) -> None:
    for item in adapter.resources:
        if item["ResourceType"] == "AWS::ElasticLoadBalancingV2::LoadBalancer":
            item["PhysicalResourceId"] = "not-an-arn"


@pytest.mark.parametrize(
    ("mutate", "profile", "message"),
    (
        (_stack_status, "default", "not complete: UPDATE_ROLLBACK_COMPLETE"),
        (
            _drop_resource("AWS::RDS::DBCluster"),
            "default",
            "missing expected resource types: AWS::RDS::DBCluster",
        ),
        (_failed_resource, "default", "failed resources: Fn"),
        (_output("ApplicationURL", "http://insecure.example"), "default", "ApplicationURL is not HTTPS"),
        (_output("ECSServiceName", None), "default", "Stack output is missing: ECSServiceName"),
        (_service(runningCount=1), "default", "did not reach its desired running count"),
        (_service(deployments=[]), "default", "rollout is not complete"),
        (_service(deployments=[{"rolloutState": "IN_PROGRESS"}]), "default", "rollout is not complete"),
        (_fake_return("ecs", "list_tasks", {"taskArns": ["task-1"]}), "default", "fewer running tasks"),
        (_service(runningCount=0, desiredCount=0), "default", "fewer running tasks"),
        (
            _fake_return("ecs", "describe_tasks", {"tasks": [_healthy_task()]}),
            "default",
            "did not return all running task descriptions",
        ),
        (
            _fake_return("ecs", "describe_tasks", {"tasks": [{"lastStatus": "PENDING"}, _healthy_task()]}),
            "default",
            "not running and healthy",
        ),
        (_unhealthy_container, "default", "essential containers are not healthy"),
        (
            _service(events=[{"createdAt": datetime(2026, 7, 31, 12, 1), "message": "Task failed to start"}]),
            "default",
            "startup or health failure",
        ),
        (_drop_resource("AWS::ElasticLoadBalancingV2::TargetGroup"), "default", "No load balancer target group"),
        (
            _fake_return(
                "elbv2",
                "describe_target_health",
                {"TargetHealthDescriptions": [{"TargetHealth": {"State": "draining"}}]},
            ),
            "default",
            r"not all healthy: \['draining'\]",
        ),
        (
            _fake_return("elbv2", "describe_target_health", {"TargetHealthDescriptions": []}),
            "default",
            "not all healthy",
        ),
        (
            _fake_return("efs", "describe_file_systems", {"FileSystems": [{"LifeCycleState": "creating"}]}),
            "default",
            "EFS file systems are not available",
        ),
        (
            _fake_return("rds", "describe_db_clusters", {"DBClusters": [{"Status": "modifying"}]}),
            "default",
            "Aurora cluster is not available: modifying",
        ),
        (
            _fake_return("rds", "describe_db_clusters", {"DBClusters": [{"Status": "available"}]}),
            "api-enabled",
            "did not enable the Aurora Data API",
        ),
        (_second_cache, "default", "exactly one ElastiCache Serverless cache"),
        (
            _fake_return("elasticache", "describe_serverless_caches", {"ServerlessCaches": [{"Status": "creating"}]}),
            "default",
            "ElastiCache Serverless is not available: creating",
        ),
        (_non_arn_load_balancer, "default", "Expected one load balancer ARN"),
        (_fake_return("wafv2", "get_web_acl_for_resource", {}), "default", "no WAF association"),
        (
            _fake_return("cloudwatch", "describe_alarms", {"MetricAlarms": [], "CompositeAlarms": []}),
            "default",
            "CloudWatch alarms are missing",
        ),
        (
            _fake_return(
                "logs",
                "filter_log_events",
                {"events": [{"message": "PHP Fatal error: x"}, {"message": "Segmentation fault"}]},
            ),
            "default",
            "contains 2 known fatal patterns",
        ),
        (
            _fake_return("ecs", "describe_task_definition", {"taskDefinition": {"containerDefinitions": []}}),
            "api-enabled",
            "missing required OpenEMR settings",
        ),
    ),
)
def test_validation_fails_closed_on_unhealthy_deployments(
    http: _Http,
    mutate: Callable[[_Adapter], None],
    profile: str,
    message: str,
) -> None:
    adapter = _Adapter()
    mutate(adapter)

    with pytest.raises(ToolError, match=message):
        _validate(adapter, profile=profile)


@pytest.mark.parametrize(
    ("suffix", "answer", "profile", "message"),
    (
        (
            "/portal/",
            requests.ConnectionError("down"),
            "api-enabled",
            "Patient portal smoke request failed: ConnectionError",
        ),
        ("/portal/", _response(status=500), "api-enabled", "patient portal smoke test failed"),
        ("/portal/", _response(text="nginx"), "api-enabled", "patient portal smoke test failed"),
        ("/login.php", requests.Timeout("slow"), "default", "application smoke request failed: Timeout"),
        ("/login.php", _response(status=302), "default", "smoke test returned HTTP 302"),
        ("/login.php", _response(text="welcome"), "default", "smoke test returned HTTP 200"),
    ),
)
def test_validation_smoke_requests_fail_closed(
    http: _Http,
    suffix: str,
    answer: Any,
    profile: str,
    message: str,
) -> None:
    http.answers[suffix] = answer

    with pytest.raises(ToolError, match=message):
        _validate(_Adapter(), profile=profile)


# --- emulated validation ----------------------------------------------------


def test_emulated_validation_probes_apis_and_caps_timeouts(
    http: _Http,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _Adapter(emulated=True)
    captured: dict[str, float] = {}

    def wait(url: str, *, timeout_seconds: float, poll_seconds: float, progress: Any = None) -> tuple[float, float]:
        captured.update(timeout=timeout_seconds, poll=poll_seconds)
        assert url == APP_URL
        return 0.1, 0.1

    monkeypatch.setattr(adapter, "_wait_for_https", wait)

    checks, phases = _validate(adapter, profile="api-enabled")

    assert captured == {"timeout": 30.0, "poll": 2.0}
    assert [(check.name, check.detail) for check in checks] == [
        ("cloudformation-stack", "CREATE_COMPLETE"),
        ("expected-resources", "10 stack resources healthy"),
        ("application-https", "HTTPS returned an OpenEMR response"),
        ("ecs-service", "emulator API probe succeeded"),
        ("efs-file-systems", "emulator API probe succeeded"),
        ("aurora-cluster", "emulator API probe succeeded"),
    ]
    assert [phase.name for phase in phases] == ["floci-emulated-validation"]
    adapter.fakes["ecs"].describe_services.assert_called_once_with(cluster="cluster", services=["service"])
    adapter.fakes["efs"].describe_file_systems.assert_called_once_with(FileSystemId="fs-sites")
    adapter.fakes["rds"].describe_db_clusters.assert_called_once_with(DBClusterIdentifier="db-1")
    adapter.fakes["ecs"].get_waiter.assert_not_called()


def test_emulated_validation_tolerates_unreachable_https_and_unsupported_apis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _Adapter(emulated=True)
    adapter.fakes["efs"].describe_file_systems.side_effect = _client_error("UnknownOperationException")

    def unreachable(*_: Any, **__: Any) -> tuple[float, float]:
        raise ToolError("OpenEMR HTTPS readiness timed out: ConnectionError")

    monkeypatch.setattr(adapter, "_wait_for_https", unreachable)

    checks, _ = _validate(adapter)

    by_name = {check.name: check.detail for check in checks}
    assert by_name["application-https"] == "floci-emulated; ApplicationURL present but not locally reachable"
    assert by_name["efs-file-systems"] == "floci-emulated; operation unsupported by emulator"
    assert by_name["aurora-cluster"] == "emulator API probe succeeded"


def test_emulated_validation_rejects_real_probe_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _Adapter(emulated=True)
    monkeypatch.setattr(adapter, "_wait_for_https", lambda *_a, **_k: (0.1, 0.1))
    adapter.fakes["rds"].describe_db_clusters.side_effect = _client_error("AccessDenied", "denied")

    with pytest.raises(ToolError, match=r"Floci validation probe failed \(aurora-cluster\)"):
        _validate(adapter)

    adapter = _Adapter(emulated=True)
    monkeypatch.setattr(adapter, "_wait_for_https", lambda *_a, **_k: (0.1, 0.1))
    adapter.set_output("ECSClusterName", None)
    with pytest.raises(ToolError, match=r"\(ecs-service\): Stack output is missing: ECSClusterName"):
        _validate(adapter)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (_stack_status, "not complete: UPDATE_ROLLBACK_COMPLETE"),
        (_drop_resource("AWS::ECS::Service"), "missing expected resource types: AWS::ECS::Service"),
        (_failed_resource, "failed resources: Fn"),
        (_output("ApplicationURL", "http://insecure.example"), "ApplicationURL is not HTTPS"),
    ),
)
def test_emulated_validation_fails_closed_on_stack_problems(
    mutate: Callable[[_Adapter], None],
    message: str,
) -> None:
    adapter = _Adapter(emulated=True)
    mutate(adapter)

    with pytest.raises(ToolError, match=message):
        _validate(adapter)


# --- event phases, log scans, HTTPS polling --------------------------------


def _event(resource_type: str, status: str, minute: int) -> dict[str, Any]:
    return {
        "ResourceType": resource_type,
        "ResourceStatus": status,
        "Timestamp": datetime(2026, 7, 31, 12, minute),
    }


def test_event_phases_measure_deletion_and_skip_inverted_windows() -> None:
    cloudformation = MagicMock()
    cloudformation.describe_stack_events.return_value = {
        "StackEvents": [
            _event("AWS::CloudFormation::Stack", "DELETE_IN_PROGRESS", 1),
            _event("AWS::CloudFormation::Stack", "DELETE_COMPLETE", 31),
            _event("AWS::EFS::FileSystem", "CREATE_IN_PROGRESS", 20),
            _event("AWS::EFS::FileSystem", "CREATE_COMPLETE", 10),
            _event("AWS::ECS::Service", "CREATE_IN_PROGRESS", 5),
        ]
    }
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"cloudformation": cloudformation}))

    deletion = adapter.event_phases("stack", operation="DELETE")
    assert len(deletion) == 1
    assert deletion[0].name == "cloudformation-deletion"
    assert deletion[0].duration_seconds == 1800
    assert deletion[0].started_at == "2026-07-31T12:01:00Z"
    assert deletion[0].finished_at == "2026-07-31T12:31:00Z"

    assert adapter.event_phases("stack", operation="CREATE") == ()

    with pytest.raises(ValueError, match="CREATE or DELETE"):
        adapter.event_phases("stack", operation="UPDATE")


def test_fatal_log_scan_paginates_until_token_repeats() -> None:
    logs = MagicMock()
    logs.filter_log_events.side_effect = [
        {"events": [{"message": "kernel panic - not syncing"}], "nextToken": "t1"},
        {"events": [{"message": "ok"}, {"message": "Out of memory: killed"}], "nextToken": "t2"},
        {"events": [], "nextToken": "t2"},
    ]
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"logs": logs}))

    assert adapter._fatal_startup_log_count("/aws/ecs/test", start_time=CREATED) == 2
    calls = [call.kwargs for call in logs.filter_log_events.call_args_list]
    assert calls[0] == {"logGroupName": "/aws/ecs/test", "startTime": int(CREATED.timestamp() * 1000), "limit": 10_000}
    assert calls[1]["nextToken"] == "t1"
    assert calls[2]["nextToken"] == "t2"


def test_fatal_log_scan_is_bounded_to_five_pages() -> None:
    logs = MagicMock()
    pages = iter(range(100))
    logs.filter_log_events.side_effect = lambda **_: {"events": [], "nextToken": f"t{next(pages)}"}
    adapter = LiveE2EAws(region="us-east-1", session=_Session({"logs": logs}))

    assert adapter._fatal_startup_log_count("/g", start_time=CREATED) == 0
    assert logs.filter_log_events.call_count == 5


def test_wait_for_https_retries_until_timeout_with_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(aws_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(aws_module.time, "sleep", clock.sleep)
    answers = iter([_response(status=503), _response(text="nginx default page"), requests.ConnectionError("down")])
    timeouts: list[float] = []

    def get(url: str, **kwargs: Any) -> Any:
        timeouts.append(kwargs["timeout"])
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(aws_module.requests, "get", get)
    progress = _Progress()

    with pytest.raises(ToolError, match="readiness timed out: ConnectionError"):
        LiveE2EAws._wait_for_https(
            APP_URL,
            timeout_seconds=25,
            poll_seconds=10,
            progress=progress,  # type: ignore[arg-type]
        )

    assert clock.sleeps == [10, 10, 5]
    assert timeouts == [25, 15, 5]
    assert progress.messages == [
        "heartbeat:HTTPS not ready yet (HTTP 503); retrying (elapsed 0m00s)",
        "heartbeat:HTTPS not ready yet (HTTP 200); retrying (elapsed 0m10s)",
        "heartbeat:HTTPS not ready yet (ConnectionError); retrying (elapsed 0m20s)",
    ]


def test_wait_for_https_does_not_sleep_past_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(aws_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(aws_module.time, "sleep", clock.sleep)

    def slow_failure(url: str, **kwargs: Any) -> Any:
        clock.now += kwargs["timeout"]
        raise requests.Timeout("slow")

    monkeypatch.setattr(aws_module.requests, "get", slow_failure)

    with pytest.raises(ToolError, match="readiness timed out: Timeout"):
        LiveE2EAws._wait_for_https(APP_URL, timeout_seconds=5, poll_seconds=1)
    assert clock.sleeps == []
