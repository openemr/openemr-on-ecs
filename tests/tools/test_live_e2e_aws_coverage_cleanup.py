"""Offline coverage for live E2E stack deletion, orphan sweeps, and residual audits.

Every AWS client is an in-memory fake; nothing here can reach AWS.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import BotoCoreError, ClientError

import tools.live_e2e.aws as aws_module
from tools._shared import ToolError
from tools.live_e2e.aws import LiveE2EAws, unexpected_residuals
from tools.live_e2e.models import ResidualResource

RUN_ID = "e2e-cleanup-run"
STACK_NAME = "OpenemrE2E-" + hashlib.sha256(RUN_ID.encode("utf-8")).hexdigest()[:12]
ACCOUNT = "111122223333"
REGION = "us-east-1"


def _client_error(code: str, message: str = "") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


def _arn(service: str, resource: str) -> str:
    return f"arn:aws:{service}:{REGION}:{ACCOUNT}:{resource}"


class _Session:
    def __init__(self, clients: dict[str, Any]) -> None:
        self.clients = clients
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def client(self, service: str, **kwargs: Any) -> Any:
        self.calls.append((service, kwargs))
        return self.clients[service]


class _Progress:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def phase(self, name: str, detail: str = "") -> None:
        self.messages.append(f"phase:{name}")

    def info(self, message: str) -> None:
        self.messages.append(message)

    def detail(self, message: str) -> None:
        self.messages.append(message)

    def heartbeat(self, message: str, *, force: bool = False) -> None:
        self.messages.append(f"heartbeat:{message}")

    @contextmanager
    def pulse(self, message: str, *, interval_seconds: float | None = None) -> Iterator[None]:
        yield


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(aws_module.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(aws_module.time, "sleep", fake.sleep)
    return fake


def _paginator(*pages: dict[str, Any]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = list(pages)
    return paginator


def _adapter(*, emulated: bool = False, **clients: Any) -> LiveE2EAws:
    return LiveE2EAws(
        region=REGION,
        session=_Session({key.replace("_", "-"): value for key, value in clients.items()}),
        progress=_Progress(),  # type: ignore[arg-type]
        endpoint_url="http://127.0.0.1:4566" if emulated else None,
        emulated=emulated,
    )


def _messages(adapter: LiveE2EAws) -> list[str]:
    return adapter.progress.messages  # type: ignore[attr-defined]


# --- wait_for_stack_deleted -------------------------------------------------


def _with_statuses(monkeypatch: pytest.MonkeyPatch, adapter: LiveE2EAws, *statuses: Any) -> None:
    remaining = iter(statuses)
    monkeypatch.setattr(adapter, "_describe_stack_raw", lambda _: next(remaining))


def test_wait_for_deletion_returns_when_stack_disappears(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    adapter = _adapter()
    _with_statuses(monkeypatch, adapter, {"StackStatus": "DELETE_IN_PROGRESS"}, {}, None)

    adapter.wait_for_stack_deleted("stack", timeout_seconds=60, poll_seconds=5)

    assert clock.sleeps == [5, 5]
    assert _messages(adapter)[-1] == "Stack deletion confirmed (stack no longer exists)"
    assert "heartbeat:Stack deletion in progress: status=unknown (elapsed 0m05s)" in _messages(adapter)


def test_wait_for_deletion_accepts_delete_complete_without_sleeping(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    adapter = _adapter()
    _with_statuses(monkeypatch, adapter, {"StackStatus": "DELETE_COMPLETE"})

    adapter.wait_for_stack_deleted("stack", timeout_seconds=60, poll_seconds=5)

    assert clock.sleeps == []
    assert _messages(adapter)[-1] == "Stack deletion confirmed (DELETE_COMPLETE)"


def test_emulated_delete_failed_with_remaining_resources_fails_after_one_retry(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    cloudformation = MagicMock()
    adapter = _adapter(emulated=True, cloudformation=cloudformation)
    failed = {"StackId": "arn:stack", "StackStatus": "DELETE_FAILED", "StackStatusReason": "ENI still attached"}
    _with_statuses(monkeypatch, adapter, failed, failed)
    monkeypatch.setattr(adapter, "_emulated_delete_tombstone_is_empty", lambda _: False)

    with pytest.raises(ToolError, match="Floci stack deletion failed with remaining resources: ENI still attached"):
        adapter.wait_for_stack_deleted("stack", timeout_seconds=60, poll_seconds=1)
    cloudformation.delete_stack.assert_called_once_with(StackName="arn:stack")


def test_emulated_delete_failed_retries_once_then_accepts_empty_tombstone(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    cloudformation = MagicMock()
    cloudformation.delete_stack.side_effect = _client_error("ValidationError", "rejected by floci")
    adapter = _adapter(emulated=True, cloudformation=cloudformation)
    failed = {"StackId": "arn:stack", "StackStatus": "DELETE_FAILED"}
    _with_statuses(monkeypatch, adapter, failed, failed)
    monkeypatch.setattr(adapter, "_emulated_delete_tombstone_is_empty", lambda stack: stack is failed)

    adapter.wait_for_stack_deleted("stack", timeout_seconds=60, poll_seconds=0.01)

    cloudformation.delete_stack.assert_called_once_with(StackName="arn:stack")
    assert clock.sleeps == [0.2]
    messages = _messages(adapter)
    assert any(message.startswith("Floci stack delete retry was rejected:") for message in messages)
    assert messages[-1] == "Verified Floci DELETE_FAILED tombstone has no remaining resources"


def test_emulated_delete_retry_quietly_ignores_missing_stack(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    cloudformation = MagicMock()
    cloudformation.delete_stack.side_effect = _client_error("ValidationError", "Stack does not exist")
    adapter = _adapter(emulated=True, cloudformation=cloudformation)
    _with_statuses(monkeypatch, adapter, {"StackStatus": "DELETE_FAILED"}, None)

    adapter.wait_for_stack_deleted("stack-name", timeout_seconds=60, poll_seconds=1)

    cloudformation.delete_stack.assert_called_once_with(StackName="stack-name")
    assert not any("rejected" in message for message in _messages(adapter))


def test_delete_failed_without_s3_cause_reports_reason(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    adapter = _adapter()
    _with_statuses(monkeypatch, adapter, {"StackStatus": "DELETE_FAILED", "StackStatusReason": "role is gone"})
    monkeypatch.setattr(adapter, "_s3_buckets_failed_to_delete", lambda _: [])

    with pytest.raises(ToolError, match="Stack deletion failed: role is gone"):
        adapter.wait_for_stack_deleted("stack", timeout_seconds=60, poll_seconds=1)


def test_delete_failed_s3_retries_are_bounded(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    cloudformation = MagicMock()
    cloudformation.delete_stack.side_effect = _client_error("ValidationError", "Stack does not exist")
    adapter = _adapter(cloudformation=cloudformation)
    monkeypatch.setattr(adapter, "_describe_stack_raw", lambda _: {"StackId": "arn:s", "StackStatus": "DELETE_FAILED"})
    monkeypatch.setattr(adapter, "_s3_buckets_failed_to_delete", lambda _: ["logs-bucket"])
    emptied: list[str] = []
    monkeypatch.setattr(adapter, "_empty_versioned_s3_bucket", lambda bucket: emptied.append(bucket) or 0)

    with pytest.raises(ToolError, match="Stack deletion failed: reason unavailable"):
        adapter.wait_for_stack_deleted("stack", timeout_seconds=600, poll_seconds=1)

    assert emptied == ["logs-bucket"] * 3
    assert cloudformation.delete_stack.call_count == 3
    assert sum("(attempt 3/3)" in message for message in _messages(adapter)) == 1


def test_delete_failed_s3_retry_wraps_unexpected_delete_errors(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    cloudformation = MagicMock()
    cloudformation.delete_stack.side_effect = _client_error("AccessDenied", "denied")
    adapter = _adapter(cloudformation=cloudformation)
    _with_statuses(monkeypatch, adapter, {"StackStatus": "DELETE_FAILED"})
    monkeypatch.setattr(adapter, "_s3_buckets_failed_to_delete", lambda _: ["logs-bucket"])
    monkeypatch.setattr(adapter, "_empty_versioned_s3_bucket", lambda _: 2)

    with pytest.raises(ToolError, match="Stack delete retry failed"):
        adapter.wait_for_stack_deleted("stack", timeout_seconds=60, poll_seconds=1)
    assert "Emptied 2 object version(s) from s3://logs-bucket" in _messages(adapter)


def test_deletion_timeout_rechecks_for_late_success_or_fails(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_describe_stack_raw", lambda _: {"StackStatus": "DELETE_IN_PROGRESS"})
    monkeypatch.setattr(adapter, "describe_stack", lambda _: None)

    adapter.wait_for_stack_deleted("stack", timeout_seconds=10, poll_seconds=4)
    assert clock.sleeps == [4, 4, 4]
    assert _messages(adapter)[-1] == "Stack deletion confirmed after timeout race"

    monkeypatch.setattr(adapter, "describe_stack", lambda _: {"StackStatus": "DELETE_IN_PROGRESS"})
    with pytest.raises(ToolError, match="Stack still exists after 10 seconds"):
        adapter.wait_for_stack_deleted("stack", timeout_seconds=10, poll_seconds=4)


# --- S3 bucket discovery and emptying --------------------------------------


def test_s3_buckets_failed_to_delete_filters_events_and_deduplicates() -> None:
    def event(**overrides: Any) -> dict[str, Any]:
        base = {
            "ResourceStatus": "DELETE_FAILED",
            "ResourceType": "AWS::S3::Bucket",
            "PhysicalResourceId": "bucket-a",
            "ResourceStatusReason": "The bucket you tried to delete is not empty",
        }
        return {**base, **overrides}

    cloudformation = MagicMock()
    cloudformation.get_paginator.return_value = _paginator(
        {
            "StackEvents": [
                event(),
                event(ResourceStatus="DELETE_IN_PROGRESS", PhysicalResourceId="ignored-status"),
                event(ResourceType="AWS::S3::BucketPolicy", PhysicalResourceId="ignored-type"),
                event(PhysicalResourceId="  "),
                event(PhysicalResourceId="bucket-b", ResourceStatusReason="Error: BucketNotEmpty"),
                event(PhysicalResourceId="bucket-c", ResourceStatusReason="access denied"),
            ]
        },
        {"StackEvents": [event()]},
    )
    adapter = _adapter(cloudformation=cloudformation)

    assert adapter._s3_buckets_failed_to_delete("stack") == ["bucket-a", "bucket-b"]
    cloudformation.get_paginator.assert_called_once_with("describe_stack_events")
    cloudformation.get_paginator.return_value.paginate.assert_called_once_with(StackName="stack")


def test_s3_bucket_discovery_tolerates_missing_stack_and_wraps_other_errors() -> None:
    cloudformation = MagicMock()
    adapter = _adapter(cloudformation=cloudformation)

    cloudformation.get_paginator.return_value.paginate.side_effect = _client_error("ValidationError", "does not exist")
    assert adapter._s3_buckets_failed_to_delete("stack") == []

    cloudformation.get_paginator.return_value.paginate.side_effect = BotoCoreError()
    with pytest.raises(ToolError, match="Cannot inspect DELETE_FAILED stack events"):
        adapter._s3_buckets_failed_to_delete("stack")


class _S3:
    def __init__(self, uploads: list[dict[str, Any]], version_pages: list[dict[str, Any]]) -> None:
        self.uploads = uploads
        self.version_pages = version_pages
        self.aborted: list[dict[str, Any]] = []
        self.abort_errors: dict[str, ClientError] = {}
        self.delete_responses: list[dict[str, Any]] = []
        self.deleted: list[list[dict[str, str]]] = []
        self.versions_error: ClientError | None = None

    def get_paginator(self, name: str) -> Any:
        outer = self

        class Paginator:
            def paginate(self, **kwargs: Any) -> Any:
                assert kwargs == {"Bucket": "bucket"}
                if name == "list_multipart_uploads":
                    return [{"Uploads": outer.uploads}]
                if outer.versions_error is not None:
                    raise outer.versions_error
                return outer.version_pages

        return Paginator()

    def abort_multipart_upload(self, **kwargs: Any) -> None:
        if kwargs["Key"] in self.abort_errors:
            raise self.abort_errors[kwargs["Key"]]
        self.aborted.append(kwargs)

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["Delete"]["Quiet"] is True
        self.deleted.append(kwargs["Delete"]["Objects"])
        return self.delete_responses.pop(0) if self.delete_responses else {}


def test_empty_bucket_aborts_uploads_and_batches_by_thousand() -> None:
    versions = [{"Key": f"k{index}", "VersionId": f"v{index}"} for index in range(1001)]
    s3 = _S3(
        uploads=[
            {"Key": "big.bin", "UploadId": "u1"},
            {"Key": "no-upload-id"},
            {"Key": "gone.bin", "UploadId": "u2"},
        ],
        version_pages=[{"Versions": [*versions, {"Key": "no-version"}], "DeleteMarkers": []}, {}],
    )
    s3.abort_errors["gone.bin"] = _client_error("NoSuchUpload", "The upload does not exist")
    adapter = _adapter(s3=s3)

    assert adapter._empty_versioned_s3_bucket("bucket") == 1001
    assert s3.aborted == [{"Bucket": "bucket", "Key": "big.bin", "UploadId": "u1"}]
    assert [len(batch) for batch in s3.deleted] == [1000, 1]
    assert s3.deleted[1] == [{"Key": "k1000", "VersionId": "v1000"}]


def test_empty_bucket_fails_on_unexpected_abort_error() -> None:
    s3 = _S3(uploads=[{"Key": "x", "UploadId": "u"}], version_pages=[])
    s3.abort_errors["x"] = _client_error("AccessDenied", "denied")
    adapter = _adapter(s3=s3)

    with pytest.raises(ToolError, match="Abort multipart upload s3://bucket/x failed"):
        adapter._empty_versioned_s3_bucket("bucket")


def test_empty_bucket_reports_per_object_delete_errors() -> None:
    s3 = _S3(uploads=[], version_pages=[{"Versions": [{"Key": "a", "VersionId": "1"}]}])
    s3.delete_responses = [{"Errors": [{"Code": "AccessDenied", "Message": "nope"}]}]
    adapter = _adapter(s3=s3)

    with pytest.raises(ToolError, match="Failed emptying s3://bucket: AccessDenied nope"):
        adapter._empty_versioned_s3_bucket("bucket")

    s3.delete_responses = [{"Errors": [{}]}]
    with pytest.raises(ToolError, match="Failed emptying s3://bucket: Error$"):
        adapter._empty_versioned_s3_bucket("bucket")


def test_empty_bucket_treats_missing_bucket_as_done_and_wraps_other_errors() -> None:
    s3 = _S3(uploads=[], version_pages=[])
    adapter = _adapter(s3=s3)

    s3.versions_error = _client_error("NoSuchBucket")
    assert adapter._empty_versioned_s3_bucket("bucket") == 0

    s3.versions_error = _client_error("AccessDenied", "denied")
    with pytest.raises(ToolError, match="Failed emptying s3://bucket: An error occurred"):
        adapter._empty_versioned_s3_bucket("bucket")


# --- tag discovery and lifecycle classification ----------------------------


def _mapping(arn: str, stack: str | None = STACK_NAME) -> dict[str, Any]:
    tags: list[dict[str, Any]] = [{"Key": "LiveE2ERunId", "Value": RUN_ID}, {"Value": "no-key"}]
    if stack is not None:
        tags.append({"Key": "aws:cloudformation:stack-name", "Value": stack})
    return {"ResourceARN": arn, "Tags": tags}


def _tagging(*mappings: dict[str, Any]) -> MagicMock:
    tagging = MagicMock()
    tagging.get_paginator.return_value = _paginator({"ResourceTagMappingList": list(mappings)})
    return tagging


def test_tag_discovery_validates_run_id_skips_blank_arns_and_deduplicates() -> None:
    vpc = _arn("ec2", "vpc/vpc-1")
    tagging = _tagging(_mapping(vpc), _mapping(""), _mapping(vpc))
    adapter = _adapter(resourcegroupstaggingapi=tagging)

    assert adapter._tagged_resource_arns(RUN_ID) == [vpc]
    tagging.get_paginator.return_value.paginate.assert_called_once_with(
        TagFilters=[{"Key": "LiveE2ERunId", "Values": [RUN_ID]}]
    )
    with pytest.raises(ToolError, match="invalid live E2E run ID"):
        adapter._tagged_resource_arns("Bad_Run")


def test_tag_discovery_stack_tag_rules() -> None:
    vpc = _arn("ec2", "vpc/vpc-1")
    adapter = _adapter(resourcegroupstaggingapi=_tagging(_mapping(vpc, stack=None)))
    with pytest.raises(ToolError, match="outside the owned CloudFormation stack"):
        adapter._tagged_resource_arns(RUN_ID)
    assert adapter._tagged_resource_arns(RUN_ID, require_stack_tag=False) == [vpc]

    adapter = _adapter(resourcegroupstaggingapi=_tagging(_mapping(vpc, stack="OpenemrE2E-foreign")))
    with pytest.raises(ToolError, match="outside the owned CloudFormation stack"):
        adapter._tagged_resource_arns(RUN_ID, require_stack_tag=False)


def test_assert_run_id_available_rejects_reused_run_ids() -> None:
    adapter = _adapter(resourcegroupstaggingapi=_tagging(_mapping(_arn("ec2", "vpc/vpc-1"))))
    with pytest.raises(ToolError, match="already attached to 1 resource"):
        adapter.assert_run_id_available(RUN_ID)

    _adapter(resourcegroupstaggingapi=_tagging()).assert_run_id_available(RUN_ID)


def test_kms_residual_classification() -> None:
    kms = MagicMock()
    adapter = _adapter(kms=kms)
    key_arn = _arn("kms", "key/key-1")

    assert adapter._is_expected_tagged_residual(_arn("ec2", "vpc/vpc-1")) is False
    kms.describe_key.return_value = {"KeyMetadata": {"KeyState": "PendingDeletion"}}
    assert adapter._is_expected_tagged_residual(key_arn) is True
    kms.describe_key.assert_called_with(KeyId="key-1")
    kms.describe_key.return_value = {"KeyMetadata": {"KeyState": "Enabled"}}
    assert adapter._is_expected_tagged_residual(key_arn) is False

    kms.describe_key.side_effect = _client_error("NotFoundException")
    assert adapter._is_expected_tagged_residual(key_arn) is False
    kms.describe_key.side_effect = _client_error("AccessDeniedException", "denied")
    with pytest.raises(ToolError, match="Cannot classify tagged KMS residual key-1"):
        adapter._is_expected_tagged_residual(key_arn)


@pytest.mark.parametrize(
    ("arn", "setup", "expected"),
    (
        (_arn("ec2", "natgateway/nat-1"), ("ec2", "describe_nat_gateways", {"NatGateways": []}), "missing"),
        (
            _arn("ec2", "natgateway/nat-1"),
            ("ec2", "describe_nat_gateways", {"NatGateways": [{"State": "deleting"}]}),
            "deleting",
        ),
        (
            _arn("ec2", "natgateway/nat-1"),
            ("ec2", "describe_nat_gateways", {"NatGateways": [{"State": "Deleted"}]}),
            "missing",
        ),
        (
            _arn("ec2", "natgateway/nat-1"),
            ("ec2", "describe_nat_gateways", {"NatGateways": [{"State": "available"}]}),
            "active",
        ),
        (_arn("ec2", "vpc-flow-log/fl-1"), ("ec2", "describe_flow_logs", {"FlowLogs": [{}]}), "active"),
        (_arn("ec2", "vpc-flow-log/fl-1"), ("ec2", "describe_flow_logs", {"FlowLogs": []}), "missing"),
        (_arn("ecs", "service/only-cluster"), None, "active"),
        (_arn("ecs", "service/c/s"), ("ecs", "describe_services", {"services": []}), "missing"),
        (_arn("ecs", "service/c/s"), ("ecs", "describe_services", {"services": [{"status": "DRAINING"}]}), "deleting"),
        (_arn("ecs", "service/c/s"), ("ecs", "describe_services", {"services": [{"status": "INACTIVE"}]}), "missing"),
        (_arn("ecs", "service/c/s"), ("ecs", "describe_services", {"services": [{"status": "ACTIVE"}]}), "active"),
        (_arn("ecs", "cluster/c"), ("ecs", "describe_clusters", {"clusters": []}), "missing"),
        (_arn("ecs", "cluster/c"), ("ecs", "describe_clusters", {"clusters": [{"status": "INACTIVE"}]}), "missing"),
        (_arn("ecs", "cluster/c"), ("ecs", "describe_clusters", {"clusters": [{"status": "ACTIVE"}]}), "active"),
        (
            _arn("ecs", "task-definition/app:1"),
            ("ecs", "describe_task_definition", {"taskDefinition": {"status": "INACTIVE"}}),
            "active",
        ),
        (
            _arn("ecs", "task-definition/app:2"),
            ("ecs", "describe_task_definition", {"taskDefinition": {"status": "delete_in_progress"}}),
            "deleting",
        ),
        (_arn("logs", "log-group:/x"), None, "active"),
    ),
)
def test_tagged_resource_lifecycle(arn: str, setup: Any, expected: str) -> None:
    clients = {"ec2": MagicMock(), "ecs": MagicMock()}
    if setup is not None:
        service, method, value = setup
        getattr(clients[service], method).return_value = value
    adapter = _adapter(**clients)

    assert adapter._tagged_resource_lifecycle(arn) == expected


def test_tagged_resource_lifecycle_api_calls_and_errors() -> None:
    ecs = MagicMock()
    ecs.describe_services.return_value = {"services": [{"status": "ACTIVE"}]}
    adapter = _adapter(ecs=ecs)
    adapter._tagged_resource_lifecycle(_arn("ecs", "service/cluster-a/svc-b"))
    ecs.describe_services.assert_called_once_with(cluster="cluster-a", services=["svc-b"])

    ecs.describe_clusters.side_effect = _client_error("ClusterNotFoundException")
    assert adapter._tagged_resource_lifecycle(_arn("ecs", "cluster/c")) == "missing"
    ecs.describe_clusters.side_effect = BotoCoreError()
    with pytest.raises(ToolError, match="Cannot classify tagged residual"):
        adapter._tagged_resource_lifecycle(_arn("ecs", "cluster/c"))


def test_residual_resources_classify_and_hash_each_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _arn("kms", "key/k")
    nat = _arn("ec2", "natgateway/nat-1")
    vpc = _arn("ec2", "vpc/vpc-1")
    gone = _arn("ecs", "cluster/gone")
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_tagged_resource_arns", lambda run_id, require_stack_tag: [key, nat, vpc, gone])
    monkeypatch.setattr(adapter, "_is_expected_tagged_residual", lambda arn: arn == key)
    lifecycles = {nat: "deleting", vpc: "active", gone: "missing"}
    monkeypatch.setattr(adapter, "_tagged_resource_lifecycle", lambda arn: lifecycles[arn])

    residuals = adapter.residual_resources(RUN_ID)

    assert [(item.resource_type, item.disposition) for item in residuals] == [
        ("kms", "scheduled-deletion-expected"),
        ("ec2", "scheduled-deletion-expected"),
        ("ec2", "unexpected-residual"),
    ]
    assert residuals[2].identifier_hash == f"sha256:{hashlib.sha256(vpc.encode()).hexdigest()[:12]}"
    assert unexpected_residuals(residuals) == (residuals[2],)
    assert unexpected_residuals([ResidualResource("s3", "sha256:x", "shared-content-addressed-asset")]) == ()


# --- tagged orphan sweep ----------------------------------------------------


def test_sweep_without_orphans_returns_zero_passes(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    key = _arn("kms", "key/k")
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_tagged_resource_arns", lambda run_id, require_stack_tag: [key])
    monkeypatch.setattr(adapter, "_is_expected_tagged_residual", lambda arn: True)
    monkeypatch.setattr(adapter, "_tagged_resource_lifecycle", lambda arn: pytest.fail("expected residual"))

    assert adapter.cleanup_owned_tagged_resources(RUN_ID) == 0
    assert _messages(adapter) == ["phase:tagged-orphan-sweep", "No unexpected tagged orphans found"]
    assert clock.sleeps == []


def test_sweep_waits_for_deleting_resources_then_completes(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    nat = _arn("ec2", "natgateway/nat-1")
    adapter = _adapter()
    states = iter([[nat], [nat], []])
    monkeypatch.setattr(adapter, "_tagged_resource_arns", lambda run_id, require_stack_tag: next(states))
    monkeypatch.setattr(adapter, "_is_expected_tagged_residual", lambda arn: False)
    monkeypatch.setattr(adapter, "_tagged_resource_lifecycle", lambda arn: "deleting")
    monkeypatch.setattr(adapter, "_delete_tagged_resources", lambda arns: pytest.fail("nothing is actionable"))

    assert adapter.cleanup_owned_tagged_resources(RUN_ID, timeout_seconds=60, poll_seconds=0.01) == 2

    assert clock.sleeps == [0.2, 0.2]
    messages = _messages(adapter)
    assert "Sweep pass 1: waiting on 1 already-deleting resource(s) (ec2)" in messages
    assert messages[-1] == "Tagged orphan sweep complete after 2 pass(es)"


def test_sweep_summarizes_many_services_and_fails_when_active_resources_remain(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    services = ["ec2", "ecs", "efs", "elasticache", "iam", "lambda", "logs", "rds", "s3"]
    arns = [_arn(service, f"thing/{service}-1") for service in services]
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_tagged_resource_arns", lambda run_id, require_stack_tag: arns)
    monkeypatch.setattr(adapter, "_is_expected_tagged_residual", lambda arn: False)
    monkeypatch.setattr(adapter, "_tagged_resource_lifecycle", lambda arn: "active")
    deleted: list[list[str]] = []
    monkeypatch.setattr(adapter, "_delete_tagged_resources", lambda items: deleted.append(list(items)))

    with pytest.raises(ToolError, match=r"remain after post-delete sweep: ec2:ec2-1, ecs:ecs-1"):
        adapter.cleanup_owned_tagged_resources(RUN_ID, timeout_seconds=1, poll_seconds=0.5)

    assert deleted == [arns, arns]
    assert any(
        message.endswith("(ec2, ecs, efs, elasticache, iam, lambda, logs, rds…)") for message in _messages(adapter)
    )


def test_sweep_timeout_returns_when_only_deleting_resources_remain(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    services = ["ec2", "ecs", "efs", "elasticache", "iam", "lambda", "logs", "rds", "s3"]
    arns = [_arn(service, f"thing/{service}-1") for service in services]
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_tagged_resource_arns", lambda run_id, require_stack_tag: arns)
    monkeypatch.setattr(adapter, "_is_expected_tagged_residual", lambda arn: False)
    monkeypatch.setattr(adapter, "_tagged_resource_lifecycle", lambda arn: "deleting")

    assert adapter.cleanup_owned_tagged_resources(RUN_ID, timeout_seconds=0.3, poll_seconds=0.2) == 2
    assert any("waiting on 9 already-deleting" in message and "…" in message for message in _messages(adapter))


def _ec2_for_delete() -> MagicMock:
    ec2 = MagicMock()
    ec2.describe_internet_gateways.return_value = {
        "InternetGateways": [{"Attachments": [{"VpcId": "vpc-1"}, {"State": "detaching"}]}]
    }
    return ec2


def test_delete_tagged_resources_tears_down_in_dependency_order() -> None:
    ec2 = _ec2_for_delete()
    ecs = MagicMock()
    kms = MagicMock()
    kms.describe_key.side_effect = [
        {"KeyMetadata": {"KeyState": "Enabled"}},
        {"KeyMetadata": {"KeyState": "PendingReplicaDeletion"}},
    ]
    parent = MagicMock()
    parent.attach_mock(ec2, "ec2")
    parent.attach_mock(ecs, "ecs")
    parent.attach_mock(kms, "kms")
    adapter = _adapter(ec2=ec2, ecs=ecs, kms=kms)
    arns = [
        _arn("kms", "key/k1"),
        _arn("kms", "key/k2"),
        _arn("ec2", "vpc/vpc-1"),
        _arn("ec2", "internet-gateway/igw-1"),
        _arn("ec2", "security-group/sg-1"),
        _arn("ec2", "subnet/subnet-1"),
        _arn("ec2", "network-interface/eni-1"),
        _arn("ec2", "vpc-flow-log/fl-1"),
        _arn("ec2", "natgateway/nat-1"),
        _arn("ecs", "task-definition/app:3"),
        _arn("ecs", "cluster/c"),
        _arn("ecs", "service/c/s"),
    ]

    adapter._delete_tagged_resources(arns)

    calls = [name for name, _args, _kwargs in parent.mock_calls if not name.endswith("().wait")]
    calls = [name for name in calls if name not in {"ec2.get_waiter", "kms.describe_key"}]
    assert calls == [
        "ecs.update_service",
        "ecs.delete_service",
        "ecs.delete_cluster",
        "ecs.deregister_task_definition",
        "ecs.delete_task_definitions",
        "ec2.delete_nat_gateway",
        "ec2.delete_flow_logs",
        "ec2.delete_network_interface",
        "ec2.delete_subnet",
        "ec2.delete_security_group",
        "ec2.describe_internet_gateways",
        "ec2.detach_internet_gateway",
        "ec2.delete_internet_gateway",
        "ec2.delete_vpc",
        "kms.schedule_key_deletion",
    ]
    ecs.update_service.assert_called_once_with(cluster="c", service="s", desiredCount=0)
    ecs.delete_service.assert_called_once_with(cluster="c", service="s", force=True)
    ecs.delete_task_definitions.assert_called_once_with(taskDefinitions=["app:3"])
    ec2.get_waiter.assert_called_once_with("nat_gateway_deleted")
    ec2.get_waiter.return_value.wait.assert_called_once_with(
        NatGatewayIds=["nat-1"], WaiterConfig={"Delay": 5, "MaxAttempts": 60}
    )
    ec2.detach_internet_gateway.assert_called_once_with(InternetGatewayId="igw-1", VpcId="vpc-1")
    kms.schedule_key_deletion.assert_called_once_with(KeyId="k1", PendingWindowInDays=7)


def test_delete_tagged_resources_tolerates_transient_errors() -> None:
    ec2 = _ec2_for_delete()
    transient = _client_error("DependencyViolation", "has dependencies")
    for method in (
        "delete_nat_gateway",
        "delete_flow_logs",
        "delete_network_interface",
        "delete_subnet",
        "delete_security_group",
        "delete_internet_gateway",
        "delete_vpc",
    ):
        getattr(ec2, method).side_effect = transient
    ec2.get_waiter.return_value.wait.side_effect = _client_error("InvalidNatGatewayID.NotFound", "not found")
    ecs = MagicMock()
    for method in ("update_service", "delete_service", "delete_cluster", "deregister_task_definition"):
        getattr(ecs, method).side_effect = _client_error("ServiceNotFoundException")
    ecs.delete_task_definitions.side_effect = _client_error("ClientException", "already being deleted")
    kms = MagicMock()
    kms.describe_key.side_effect = _client_error("NotFoundException")
    adapter = _adapter(ec2=ec2, ecs=ecs, kms=kms)

    adapter._delete_tagged_resources(
        [
            _arn("ecs", "service/c/s"),
            _arn("ecs", "cluster/c"),
            _arn("ecs", "task-definition/app:3"),
            _arn("ec2", "natgateway/nat-1"),
            _arn("ec2", "vpc-flow-log/fl-1"),
            _arn("ec2", "network-interface/eni-1"),
            _arn("ec2", "subnet/subnet-1"),
            _arn("ec2", "security-group/sg-1"),
            _arn("ec2", "internet-gateway/igw-1"),
            _arn("ec2", "vpc/vpc-1"),
            _arn("kms", "key/k1"),
        ]
    )

    ec2.delete_vpc.assert_called_once_with(VpcId="vpc-1")
    ecs.delete_service.assert_called_once()
    kms.schedule_key_deletion.assert_not_called()


@pytest.mark.parametrize(
    ("arn", "service", "method", "message"),
    (
        (_arn("ec2", "natgateway/nat-1"), "ec2", "delete_nat_gateway", "Delete NAT gateway nat-1 failed"),
        (_arn("ec2", "vpc/vpc-1"), "ec2", "delete_vpc", "Delete VPC vpc-1 failed"),
        (_arn("ecs", "service/c/s"), "ecs", "update_service", "Scale ECS service s to zero failed"),
        (_arn("ecs", "cluster/c"), "ecs", "delete_cluster", "Delete ECS cluster c failed"),
        (_arn("kms", "key/k1"), "kms", "describe_key", "Schedule KMS key deletion k1 failed"),
    ),
)
def test_delete_tagged_resources_fails_on_unexpected_errors(arn: str, service: str, method: str, message: str) -> None:
    clients = {"ec2": _ec2_for_delete(), "ecs": MagicMock(), "kms": MagicMock()}
    getattr(clients[service], method).side_effect = _client_error("AccessDenied", "denied")
    adapter = _adapter(**clients)

    with pytest.raises(ToolError, match=message):
        adapter._delete_tagged_resources([arn])


def test_nat_gateway_waiter_timeout_is_a_cleanup_failure() -> None:
    ec2 = _ec2_for_delete()
    ec2.get_waiter.return_value.wait.side_effect = BotoCoreError()
    adapter = _adapter(ec2=ec2)

    with pytest.raises(ToolError, match=r"Wait for NAT gateway deletion \(nat-1, nat-2\) failed"):
        adapter._delete_tagged_resources([_arn("ec2", "natgateway/nat-1"), _arn("ec2", "natgateway/nat-2")])


def test_delete_tagged_ecs_service_requires_cluster_and_service() -> None:
    adapter = _adapter(ecs=MagicMock())
    with pytest.raises(ToolError, match="Cannot parse ECS service ARN"):
        adapter._delete_tagged_ecs_service(_arn("ecs", "service/only-service"))


# --- owned RDS identifiers and log cleanup ---------------------------------


def _owned(monkeypatch: pytest.MonkeyPatch, adapter: LiveE2EAws, status: str = "CREATE_COMPLETE") -> None:
    monkeypatch.setattr(adapter, "assert_owned_stack", lambda name, run_id: {"StackId": "arn:s", "StackStatus": status})


def test_owned_rds_identifiers_paginate_and_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    cloudformation = MagicMock()
    cloudformation.list_stack_resources.side_effect = [
        {
            "StackResourceSummaries": [
                {"ResourceType": "AWS::RDS::DBCluster", "PhysicalResourceId": "db-b"},
                {"ResourceType": "AWS::EC2::VPC", "PhysicalResourceId": "vpc-1"},
                {"ResourceType": "AWS::RDS::DBCluster"},
            ],
            "NextToken": "n1",
        },
        {"StackResourceSummaries": [{"ResourceType": "AWS::RDS::DBCluster", "PhysicalResourceId": "db-a"}]},
    ]
    adapter = _adapter(cloudformation=cloudformation)
    _owned(monkeypatch, adapter)

    assert adapter.owned_rds_cluster_identifiers(STACK_NAME, RUN_ID) == ("db-a", "db-b")
    assert [call.kwargs for call in cloudformation.list_stack_resources.call_args_list] == [
        {"StackName": "arn:s"},
        {"StackName": "arn:s", "NextToken": "n1"},
    ]


def test_owned_rds_identifiers_reject_invalid_or_missing_clusters(monkeypatch: pytest.MonkeyPatch) -> None:
    cloudformation = MagicMock()
    adapter = _adapter(cloudformation=cloudformation)
    _owned(monkeypatch, adapter)

    cloudformation.list_stack_resources.return_value = {
        "StackResourceSummaries": [{"ResourceType": "AWS::RDS::DBCluster", "PhysicalResourceId": "1-bad id"}]
    }
    with pytest.raises(ToolError, match="invalid RDS cluster identifier"):
        adapter.owned_rds_cluster_identifiers(STACK_NAME, RUN_ID)

    cloudformation.list_stack_resources.return_value = {"StackResourceSummaries": []}
    with pytest.raises(ToolError, match="has no RDS cluster to inventory"):
        adapter.owned_rds_cluster_identifiers(STACK_NAME, RUN_ID)

    _owned(monkeypatch, adapter, status="ROLLBACK_COMPLETE")
    assert adapter.owned_rds_cluster_identifiers(STACK_NAME, RUN_ID) == ()


def test_log_cleanup_deletes_lambda_and_rds_groups_for_the_exact_stack() -> None:
    names = [
        f"/aws/lambda/{STACK_NAME}-fn",
        "/aws/lambda/OpenemrE2E-other-fn",
        "/aws/rds/cluster/db-1/error",
        "/aws/rds/cluster/db-10/error",
    ]
    logs = MagicMock()

    def pages(*, logGroupNamePrefix: str) -> list[dict[str, Any]]:
        return [{"logGroups": [{"logGroupName": name} for name in names if name.startswith(logGroupNamePrefix)]}]

    logs.get_paginator.return_value.paginate.side_effect = pages
    logs.delete_log_group.side_effect = lambda logGroupName: names.remove(logGroupName)
    adapter = _adapter(logs=logs)

    with pytest.raises(ToolError, match="outside the live E2E scope"):
        adapter.cleanup_owned_log_groups("OpenemrEcsStack", RUN_ID)

    assert adapter.cleanup_owned_log_groups(STACK_NAME, RUN_ID, ["db-1"]) == 2
    assert names == ["/aws/lambda/OpenemrE2E-other-fn", "/aws/rds/cluster/db-10/error"]
    logs.get_paginator.assert_called_with("describe_log_groups")


def test_log_cleanup_rejects_invalid_rds_identifiers_and_lingering_groups() -> None:
    logs = MagicMock()
    logs.get_paginator.return_value = _paginator(
        {"logGroups": [{"logGroupName": f"/aws/lambda/{STACK_NAME}-fn"}, {"logGroupName": 7}, {}]}
    )
    adapter = _adapter(logs=logs)

    with pytest.raises(ToolError, match="invalid RDS identifier"):
        adapter.cleanup_owned_log_groups(STACK_NAME, RUN_ID, ["bad/id"])

    with pytest.raises(ToolError, match="log groups remain after explicit cleanup"):
        adapter.cleanup_owned_log_groups(STACK_NAME, RUN_ID)
    logs.delete_log_group.assert_called_once_with(logGroupName=f"/aws/lambda/{STACK_NAME}-fn")


# --- CDK bootstrap asset residuals -----------------------------------------


def _write_manifest(directory: Path, payload: Any, name: str = "stack.assets.json") -> Path:
    path = directory / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_asset_manifest_rejects_symlinks_bad_json_and_non_objects(tmp_path: Path) -> None:
    adapter = _adapter()
    target = _write_manifest(tmp_path, {}, name="real.json")
    (tmp_path / "linked.assets.json").symlink_to(target)
    with pytest.raises(ToolError, match="unsafe asset manifest"):
        adapter.bootstrap_asset_residuals(tmp_path)

    bad_json = tmp_path / "bad"
    bad_json.mkdir()
    _write_manifest(bad_json, "{nope")
    with pytest.raises(ToolError, match="Cannot inspect CDK asset manifest"):
        adapter.bootstrap_asset_residuals(bad_json)

    as_list = tmp_path / "list"
    as_list.mkdir()
    _write_manifest(as_list, ["files"])
    with pytest.raises(ToolError, match="manifest must be an object"):
        adapter.bootstrap_asset_residuals(as_list)

    empty = tmp_path / "empty"
    empty.mkdir()
    _write_manifest(empty, {})
    assert adapter.bootstrap_asset_residuals(empty) == ()


@pytest.mark.parametrize(
    ("kind", "assets", "message"),
    (
        ("file", [], "file assets must be an object"),
        ("file", {"id": "not-a-dict"}, "file asset entry is invalid"),
        ("file", {"id": {"destinations": []}}, "file asset destinations must be an object"),
        ("file", {"id": {"destinations": {"d": "x"}}}, "file asset destination is invalid"),
        ("file", {"id": {"destinations": {"d": {"bucketName": "b"}}}}, "file asset destination is incomplete"),
        ("image", [], "image assets must be an object"),
        ("image", {"id": None}, "image asset entry is invalid"),
        ("image", {"id": {"destinations": "x"}}, "image asset destinations must be an object"),
        ("image", {"id": {"destinations": {"d": 1}}}, "image asset destination is invalid"),
        ("image", {"id": {"destinations": {"d": {"repositoryName": "r"}}}}, "image asset destination is incomplete"),
    ),
)
def test_asset_definitions_are_validated(kind: str, assets: Any, message: str) -> None:
    adapter = _adapter()
    method = adapter._file_asset_residuals if kind == "file" else adapter._image_asset_residuals

    with pytest.raises(ToolError, match=message):
        method(assets, {})


def test_asset_residuals_skip_missing_assets_and_wrap_other_errors() -> None:
    s3 = MagicMock()
    ecr = MagicMock()
    adapter = _adapter(s3=s3, ecr=ecr)
    residuals: dict[tuple[str, str], ResidualResource] = {}
    files = {"f": {"destinations": {"d": {"bucketName": "b", "objectKey": "k"}}}}
    images = {"i": {"destinations": {"d": {"repositoryName": "r", "imageTag": "t"}}}}

    s3.head_object.side_effect = _client_error("404")
    ecr.describe_images.side_effect = _client_error("ImageNotFoundException")
    adapter._file_asset_residuals(files, residuals)
    adapter._image_asset_residuals(images, residuals)
    assert residuals == {}

    s3.head_object.side_effect = _client_error("403")
    with pytest.raises(ToolError, match="Cannot verify retained CDK file asset"):
        adapter._file_asset_residuals(files, residuals)
    ecr.describe_images.side_effect = _client_error("AccessDeniedException")
    with pytest.raises(ToolError, match="Cannot verify retained CDK image asset"):
        adapter._image_asset_residuals(images, residuals)


def test_retained_assets_are_recorded_once_per_asset_id(tmp_path: Path) -> None:
    s3 = MagicMock()
    ecr = MagicMock()
    adapter = _adapter(s3=s3, ecr=ecr)
    destination = {"bucketName": "b", "objectKey": "k", "region": REGION}
    _write_manifest(
        tmp_path,
        {
            "files": {"file-id": {"destinations": {"one": destination, "two": dict(destination)}}},
            "dockerImages": {"image-id": {"destinations": {"one": {"repositoryName": "r", "imageTag": "t"}}}},
        },
    )

    residuals = adapter.bootstrap_asset_residuals(tmp_path)

    expected_file = f"sha256:{hashlib.sha256(b'file-id').hexdigest()[:12]}"
    expected_image = f"sha256:{hashlib.sha256(b'image-id').hexdigest()[:12]}"
    assert residuals == (
        ResidualResource("cdk-bootstrap-ecr-asset", expected_image, "shared-content-addressed-asset"),
        ResidualResource("cdk-bootstrap-s3-asset", expected_file, "shared-content-addressed-asset"),
    )
    assert s3.head_object.call_count == 2
    s3.head_object.assert_called_with(Bucket="b", Key="k")
    ecr.describe_images.assert_called_once_with(repositoryName="r", imageIds=[{"imageTag": "t"}])


def test_asset_client_resolves_partition_token_before_assuming_role(monkeypatch: pytest.MonkeyPatch) -> None:
    sts = MagicMock()
    sts.assume_role.return_value = {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "s", "SessionToken": "t"}}
    session = MagicMock()
    session.client.return_value = sts
    session.get_partition_for_region.return_value = "aws-cn"
    adapter = LiveE2EAws(region="cn-north-1", session=session)
    monkeypatch.setattr(aws_module.boto3, "Session", lambda **_: MagicMock())

    adapter._asset_client("s3", {"assumeRoleArn": "arn:${AWS::Partition}:iam::1:role/r"})

    session.get_partition_for_region.assert_called_once_with("cn-north-1")
    assert sts.assume_role.call_args.kwargs["RoleArn"] == "arn:aws-cn:iam::1:role/r"
    assert "ExternalId" not in sts.assume_role.call_args.kwargs


def test_asset_client_defaults_region_and_uses_emulator_endpoint() -> None:
    session = _Session({"s3": "s3-client"})
    adapter = LiveE2EAws(region=REGION, session=session, endpoint_url="http://127.0.0.1:4566", emulated=True)

    assert adapter._asset_client("s3", {"region": None, "assumeRoleArn": ""}) == "s3-client"
    assert session.calls == [("s3", {"region_name": REGION, "endpoint_url": "http://127.0.0.1:4566"})]


def test_asset_client_rejects_unresolvable_role_arns() -> None:
    session = MagicMock()
    session.get_partition_for_region.return_value = None
    adapter = LiveE2EAws(region=REGION, session=session)

    with pytest.raises(ToolError, match="Cannot resolve the AWS partition for asset Region eu-west-1"):
        adapter._asset_client("s3", {"region": "eu-west-1", "assumeRoleArn": "arn:${AWS::Partition}:iam::1:role/r"})
    with pytest.raises(ToolError, match="still contains unresolved tokens"):
        adapter._asset_client("s3", {"assumeRoleArn": "arn:aws:iam::${AWS::AccountId}:role/r"})


def test_asset_client_assumes_role_with_external_id_against_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    sts = MagicMock()
    sts.assume_role.return_value = {
        "Credentials": {"AccessKeyId": "AKIAFAKE", "SecretAccessKey": "fake", "SessionToken": "fake-token"}
    }
    adapter = LiveE2EAws(
        region=REGION,
        session=_Session({"sts": sts}),
        endpoint_url="http://127.0.0.1:4566",
        emulated=True,
    )
    created: list[dict[str, Any]] = []
    assumed_session = MagicMock()
    monkeypatch.setattr(aws_module.boto3, "Session", lambda **kwargs: created.append(kwargs) or assumed_session)
    role = "arn:aws:iam::111122223333:role/cdk-file-publishing"

    client = adapter._asset_client(
        "ecr",
        {"region": "us-west-2", "assumeRoleArn": role, "assumeRoleExternalId": "ext-1"},
    )

    assert client is assumed_session.client.return_value
    sts.assume_role.assert_called_once_with(
        RoleArn=role,
        RoleSessionName=f"openemr-e2e-audit-{hashlib.sha256(role.encode()).hexdigest()[:8]}",
        DurationSeconds=900,
        ExternalId="ext-1",
    )
    assert created == [
        {
            "aws_access_key_id": "AKIAFAKE",
            "aws_secret_access_key": "fake",
            "aws_session_token": "fake-token",
            "region_name": "us-west-2",
        }
    ]
    assumed_session.client.assert_called_once_with("ecr", region_name="us-west-2", endpoint_url="http://127.0.0.1:4566")
