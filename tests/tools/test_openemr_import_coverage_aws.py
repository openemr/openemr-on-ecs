"""Fail-closed AWS adapter safeguards, exercised with mocked boto3 clients."""

from __future__ import annotations

import io
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
import requests  # type: ignore[import-untyped]
from botocore.exceptions import ClientError

from tools._shared import atomic_write_private_json
from tools.openemr_import.aws import (
    AwsImportError,
    ExecutionReceipt,
    ImportLock,
    RecoveryPoint,
    StackContext,
    acquire_import_lock,
    assert_application_healthy,
    assert_import_resource_bindings,
    assert_new_import_target,
    assert_service_autoscaling_active,
    assert_service_stable,
    cleanup_staging,
    cleanup_staging_scope,
    find_import_tasks,
    read_receipt,
    read_remote_status,
    recent_recovery_points,
    release_import_lock,
    resolve_stack_context,
    set_service_autoscaling_suspended,
    set_service_desired_count,
    start_cleanup_task,
    start_import_task,
    start_recovery_task,
    wait_for_task,
)
from tools.openemr_import.models import ImportPlan

ACCOUNT = "123456789012"
REGION = "us-east-1"
MIGRATION = "import-0123456789abcdef"
KMS_ARN = f"arn:aws:kms:{REGION}:{ACCOUNT}:key/01234567-89ab-cdef-0123-456789abcdef"
TASK_DEFINITION = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/import:1"
TASK_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/openemr/0123456789abcdef"


def _session(region: str | None = REGION, **clients: Any) -> Any:
    def client(service: str, *, region_name: str) -> Any:
        assert region_name == REGION
        return clients[service]

    return SimpleNamespace(region_name=region, client=client)


def _client_error(code: str, status: int, operation: str = "Operation") -> ClientError:
    return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, operation)


def _context(**overrides: Any) -> StackContext:
    values: dict[str, Any] = {
        "account_id": ACCOUNT,
        "region": REGION,
        "stack_name": "OpenEMR",
        "stack_creation_time": "2026-07-31T00:00:00+00:00",
        "stack_last_updated_time": None,
        "cluster_name": "openemr",
        "service_name": "web",
        "service_url": "https://openemr.example.test",
        "openemr_version": "8.2.0",
        "import_target_mode": "fresh-target-only",
        "task_definition_arn": TASK_DEFINITION,
        "staging_bucket": "staging-bucket",
        "staging_kms_key_arn": KMS_ARN,
        "task_security_group_id": "sg-012345",
        "private_subnet_ids": ("subnet-0123abcd", "subnet-4567efab"),
        "database_arn": f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:openemr",
        "efs_arn": f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT}:file-system/fs-012345",
        "database_security_group_id": "sg-db012345",
        "efs_security_group_id": "sg-ef5012345",
        "efs_access_point_id": "fsap-012345",
    }
    values.update(overrides)
    return StackContext(**values)


def _receipt(**overrides: Any) -> ExecutionReceipt:
    context = _context()
    values: dict[str, Any] = {
        "schema_version": 4,
        "migration_id": MIGRATION,
        "account_id": ACCOUNT,
        "region": REGION,
        "stack_name": context.stack_name,
        "stack_creation_time": context.stack_creation_time,
        "stack_last_updated_time": None,
        "cluster_name": context.cluster_name,
        "service_name": context.service_name,
        "service_url": context.service_url,
        "openemr_version": context.openemr_version,
        "original_desired_count": 2,
        "task_arn": TASK_ARN,
        "task_definition_arn": TASK_DEFINITION,
        "staging_bucket": context.staging_bucket,
        "staging_kms_key_arn": KMS_ARN,
        "task_security_group_id": context.task_security_group_id,
        "private_subnet_ids": context.private_subnet_ids,
        "database_arn": context.database_arn,
        "efs_arn": context.efs_arn,
        "source_key": f"migrations/{MIGRATION}/source.tar",
        "started_at": "2026-07-31T00:05:00+00:00",
        "recovery_point_arns": ("db", "efs"),
        "recovery_point_dates": ("d1", "d2"),
        "lock_etag": '"lock-etag"',
        "lock_version_id": None,
    }
    values.update(overrides)
    return ExecutionReceipt(**values)


# --- stack resolution -------------------------------------------------------


def _outputs() -> dict[str, str]:
    return {
        "ECSClusterName": "openemr",
        "ECSServiceName": "web",
        "ApplicationURL": "https://openemr.example.test",
        "OpenEMRVersion": "8.2.0",
        "OpenEMRImportTargetMode": "fresh-target-only",
        "OpenEMRImportTaskDefinitionArn": TASK_DEFINITION,
        "OpenEMRImportStagingBucketName": "staging-bucket",
        "OpenEMRImportStagingKmsKeyArn": KMS_ARN,
        "OpenEMRImportSecurityGroupId": "sg-012345",
        "OpenEMRImportDatabaseSecurityGroupId": "sg-db012345",
        "OpenEMRImportEfsSecurityGroupId": "sg-ef5012345",
        "OpenEMRImportEfsAccessPointId": "fsap-012345",
        "PrivateSubnetIds": "subnet-0123abcd,subnet-4567efab",
        "DatabaseClusterArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:openemr",
        "EFSSitesFileSystemId": "fs-012345",
    }


def _stack(outputs: dict[str, str] | None = None, **overrides: Any) -> dict[str, Any]:
    stack: dict[str, Any] = {
        "StackId": f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/OpenEMR/identifier",
        "StackStatus": "CREATE_COMPLETE",
        "CreationTime": datetime(2026, 7, 31, tzinfo=UTC),
        "Outputs": [{"OutputKey": key, "OutputValue": value} for key, value in (outputs or _outputs()).items()],
    }
    stack.update(overrides)
    return stack


def _resolve(stacks: list[dict[str, Any]], *, session_region: str | None = REGION) -> Any:
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": ACCOUNT}
    cloudformation = MagicMock()
    cloudformation.describe_stacks.return_value = {"Stacks": stacks}
    context = resolve_stack_context(
        session=_session(session_region, sts=sts, cloudformation=cloudformation),
        region=REGION,
        expected_account_id=ACCOUNT,
        stack_name="OpenEMR",
    )
    cloudformation.describe_stacks.assert_called_once_with(StackName="OpenEMR")
    return context


def test_stack_resolution_records_last_update_time_when_session_region_unset() -> None:
    updated = datetime(2026, 7, 31, 6, tzinfo=UTC)

    context = _resolve([_stack(LastUpdatedTime=updated)], session_region=None)

    assert context.stack_last_updated_time == updated.isoformat()


def test_stack_resolution_rejects_session_bound_to_other_region() -> None:
    with pytest.raises(AwsImportError, match="Session region 'eu-west-1' does not match"):
        _resolve([_stack()], session_region="eu-west-1")


def _replace_output(key: str, value: str) -> dict[str, str]:
    return _outputs() | {key: value}


@pytest.mark.parametrize(
    ("stacks", "message"),
    (
        ([], "Expected exactly one stack"),
        ([_stack(StackStatus="UPDATE_IN_PROGRESS")], "Stack is not stable: UPDATE_IN_PROGRESS"),
        ([_stack(StackStatus=None)], "Stack is not stable: None"),
        ([_stack(CreationTime="2026-07-31")], "lifecycle timestamps"),
        ([_stack(LastUpdatedTime="later")], "lifecycle timestamps"),
        ([_stack({k: v for k, v in _outputs().items() if k != "ECSClusterName"})], "missing outputs: ECSClusterName"),
        ([_stack(_replace_output("OpenEMRImportTargetMode", "in-place"))], "invalid OpenEMR import-target mode"),
        (
            [_stack(StackId=f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/Other/identifier")],
            "Resolved stack identity",
        ),
        ([_stack(StackId="not-an-arn")], "Resolved stack identity"),
        ([_stack(_replace_output("EFSSitesFileSystemId", "vol-1"))], "Invalid EFS file-system output"),
        ([_stack(_replace_output("OpenEMRImportEfsSecurityGroupId", "sg-XYZ"))], "import security-group"),
        ([_stack(_replace_output("OpenEMRImportEfsAccessPointId", "ap-1"))], "EFS access-point"),
    ),
    ids=(
        "no-stack",
        "unstable",
        "missing-status",
        "creation-type",
        "update-type",
        "missing-output",
        "target-mode",
        "stack-name",
        "stack-arn",
        "efs-id",
        "security-group",
        "access-point",
    ),
)
def test_stack_resolution_fails_closed(stacks: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(AwsImportError, match=message):
        _resolve(stacks)


@pytest.mark.parametrize(
    ("context", "message"),
    (
        (_context(import_target_mode="in-place"), "not marked as a fresh import target"),
        (_context(stack_creation_time="yesterday"), "creation time is invalid"),
        (_context(stack_creation_time="2026-07-31T00:00:00"), "lacks a timezone"),
        (_context(stack_creation_time="2026-07-31T02:00:00+00:00"), "less than 24 hours old"),
    ),
    ids=("mode", "invalid-time", "naive-time", "future-time"),
)
def test_fresh_target_checks_fail_closed(context: Any, message: str) -> None:
    with pytest.raises(AwsImportError, match=message):
        assert_new_import_target(context, now=datetime(2026, 7, 31, 1, tzinfo=UTC))


# --- resource bindings ------------------------------------------------------


def _binding_clients(context: Any) -> dict[str, MagicMock]:
    file_system_id = context.efs_arn.rsplit("/", 1)[-1]
    s3 = MagicMock()
    s3.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "aws:kms",
                        "KMSMasterKeyID": context.staging_kms_key_arn,
                    }
                }
            ]
        }
    }
    kms = MagicMock()
    kms.describe_key.return_value = {
        "KeyMetadata": {
            "Arn": context.staging_kms_key_arn,
            "Enabled": True,
            "KeyManager": "CUSTOMER",
            "KeyUsage": "ENCRYPT_DECRYPT",
        }
    }
    rds = MagicMock()
    rds.describe_db_clusters.return_value = {
        "DBClusters": [{"DBClusterArn": context.database_arn, "Status": "available"}]
    }
    efs = MagicMock()
    efs.describe_file_systems.return_value = {
        "FileSystems": [{"FileSystemArn": context.efs_arn, "Encrypted": True, "LifeCycleState": "available"}]
    }
    efs.describe_access_points.return_value = {
        "AccessPoints": [
            {
                "AccessPointId": context.efs_access_point_id,
                "FileSystemId": file_system_id,
                "LifeCycleState": "available",
                "PosixUser": {"Uid": "0", "Gid": "101"},
                "RootDirectory": {"Path": "/"},
            }
        ]
    }
    ec2 = MagicMock()
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {"SubnetId": subnet, "VpcId": "vpc-1", "MapPublicIpOnLaunch": False}
            for subnet in context.private_subnet_ids
        ]
    }
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [
            {
                "GroupId": context.task_security_group_id,
                "VpcId": "vpc-1",
                "IpPermissions": [],
                "IpPermissionsEgress": [
                    {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 2049,
                        "ToPort": 2049,
                        "UserIdGroupPairs": [{"GroupId": context.efs_security_group_id}],
                    },
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 3306,
                        "ToPort": 3306,
                        "UserIdGroupPairs": [{"GroupId": context.database_security_group_id}],
                    },
                ],
            },
            {"GroupId": context.database_security_group_id, "VpcId": "vpc-1"},
            {"GroupId": context.efs_security_group_id, "VpcId": "vpc-1"},
        ]
    }
    ecs = MagicMock()
    ecs.describe_task_definition.return_value = {
        "taskDefinition": {
            "taskDefinitionArn": context.task_definition_arn,
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "runtimePlatform": {"cpuArchitecture": "ARM64"},
            "containerDefinitions": [
                {
                    "name": "openemr-import",
                    "environment": [
                        {"name": "IMPORT_STAGING_BUCKET_OWNER", "value": context.account_id},
                        {"name": "IMPORT_STAGING_KMS_KEY_ARN", "value": context.staging_kms_key_arn},
                    ],
                }
            ],
            "volumes": [
                {
                    "efsVolumeConfiguration": {
                        "fileSystemId": file_system_id,
                        "transitEncryption": "ENABLED",
                        "authorizationConfig": {"iam": "ENABLED", "accessPointId": context.efs_access_point_id},
                    }
                }
            ],
        }
    }
    return {"s3": s3, "kms": kms, "rds": rds, "efs": efs, "ec2": ec2, "ecs": ecs}


def _set_sse_algorithm(clients: dict[str, MagicMock]) -> None:
    rule = clients["s3"].get_bucket_encryption.return_value["ServerSideEncryptionConfiguration"]["Rules"][0]
    rule["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] = "AES256"


def _public_subnet(clients: dict[str, MagicMock]) -> None:
    clients["ec2"].describe_subnets.return_value["Subnets"][0]["MapPublicIpOnLaunch"] = True


def _two_containers(clients: dict[str, MagicMock]) -> None:
    definition = clients["ecs"].describe_task_definition.return_value["taskDefinition"]
    definition["containerDefinitions"].append({"name": "sidecar"})


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (_set_sse_algorithm, "not bound to the expected KMS key"),
        (
            lambda c: c["rds"].describe_db_clusters.return_value["DBClusters"][0].update(Status="stopped"),
            "not the available target cluster",
        ),
        (
            lambda c: c["efs"].describe_file_systems.return_value["FileSystems"][0].update(Encrypted=False),
            "not the available encrypted target",
        ),
        (
            lambda c: c["efs"].describe_access_points.return_value["AccessPoints"][0].update(PosixUser={"Uid": "1"}),
            "access point is not bound",
        ),
        (_public_subnet, "least-privilege private networking"),
        (_two_containers, "task definition is not bound"),
    ),
    ids=("bucket-kms", "database", "efs", "access-point", "public-subnet", "task-definition"),
)
def test_import_resource_bindings_fail_closed(mutate: Callable[[dict[str, MagicMock]], None], message: str) -> None:
    context = _context()
    clients = _binding_clients(context)
    assert_import_resource_bindings(context, session=_session(**clients))
    clients["s3"].head_bucket.assert_called_once_with(Bucket="staging-bucket", ExpectedBucketOwner=ACCOUNT)
    clients["rds"].describe_db_clusters.assert_called_once_with(DBClusterIdentifier="openemr")
    mutate(clients)

    with pytest.raises(AwsImportError, match=message):
        assert_import_resource_bindings(context, session=_session(**clients))


# --- service state and autoscaling ------------------------------------------


@pytest.mark.parametrize(
    ("response", "message"),
    (
        ({"failures": [{"reason": "MISSING"}], "services": []}, "Unable to resolve"),
        (
            {
                "services": [
                    {
                        "status": "ACTIVE",
                        "deployments": [{"rolloutState": "IN_PROGRESS"}],
                        "runningCount": 1,
                        "desiredCount": 1,
                        "pendingCount": 0,
                    }
                ]
            },
            "not deployment-stable",
        ),
        (
            {
                "services": [
                    {
                        "status": "ACTIVE",
                        "deployments": [{"rolloutState": "COMPLETED"}],
                        "runningCount": 0,
                        "desiredCount": 0,
                        "pendingCount": 0,
                    }
                ]
            },
            "no running application tasks",
        ),
    ),
    ids=("failures", "rollout", "scaled-to-zero"),
)
def test_service_stability_fails_closed(response: dict[str, Any], message: str) -> None:
    ecs = MagicMock()
    ecs.describe_services.return_value = response

    with pytest.raises(AwsImportError, match=message):
        assert_service_stable(_context(), session=_session(ecs=ecs))
    ecs.describe_services.assert_called_once_with(cluster="openemr", services=["web"])


def _scaling(*targets: dict[str, Any]) -> MagicMock:
    client = MagicMock()
    client.describe_scalable_targets.return_value = {"ScalableTargets": list(targets)}
    return client


def _target(suspended: bool | dict[str, Any] = False, **overrides: Any) -> dict[str, Any]:
    state = (
        suspended
        if isinstance(suspended, dict)
        else {
            "DynamicScalingInSuspended": suspended,
            "DynamicScalingOutSuspended": suspended,
            "ScheduledScalingSuspended": suspended,
        }
    )
    return {"MinCapacity": 1, "MaxCapacity": 3, "SuspendedState": state} | overrides


@pytest.mark.parametrize(
    ("targets", "message"),
    (
        ((), "exactly one ECS service autoscaling target"),
        ((_target(MinCapacity=0),), "capacity bounds are invalid"),
        ((_target(MinCapacity=True),), "capacity bounds are invalid"),
        ((_target(MaxCapacity=0),), "capacity bounds are invalid"),
        ((_target(SuspendedState=[]),), "suspension state is invalid"),
        ((_target({"DynamicScalingInSuspended": "yes"}),), "suspension state is invalid"),
    ),
    ids=("no-target", "min-zero", "min-bool", "max-below-min", "state-type", "state-value"),
)
def test_autoscaling_target_validation_fails_closed(targets: tuple[dict[str, Any], ...], message: str) -> None:
    with pytest.raises(AwsImportError, match=message):
        assert_service_autoscaling_active(
            _context(), session=_session(**{"application-autoscaling": _scaling(*targets)})
        )


def test_mixed_autoscaling_suspension_is_never_changed() -> None:
    client = _scaling(_target({"DynamicScalingInSuspended": True}))

    with pytest.raises(AwsImportError, match="mixed suspension state"):
        set_service_autoscaling_suspended(
            _context(), session=_session(**{"application-autoscaling": client}), suspended=True
        )
    client.register_scalable_target.assert_not_called()


def test_autoscaling_change_must_be_observed_after_registration() -> None:
    client = _scaling(_target(False))

    with pytest.raises(AwsImportError, match="suspension change was not applied"):
        set_service_autoscaling_suspended(
            _context(), session=_session(**{"application-autoscaling": client}), suspended=True
        )
    client.register_scalable_target.assert_called_once_with(
        ServiceNamespace="ecs",
        ResourceId="service/openemr/web",
        ScalableDimension="ecs:service:DesiredCount",
        MinCapacity=1,
        MaxCapacity=3,
        SuspendedState={
            "DynamicScalingInSuspended": True,
            "DynamicScalingOutSuspended": True,
            "ScheduledScalingSuspended": True,
        },
    )


def test_desired_count_update_waits_for_stability_and_rejects_negative() -> None:
    ecs = MagicMock()
    session = _session(ecs=ecs)

    with pytest.raises(ValueError, match="cannot be negative"):
        set_service_desired_count(_context(), session=session, desired_count=-1)
    ecs.update_service.assert_not_called()

    set_service_desired_count(_context(), session=session, desired_count=0)

    ecs.update_service.assert_called_once_with(cluster="openemr", service="web", desiredCount=0)
    ecs.get_waiter.assert_called_once_with("services_stable")
    ecs.get_waiter.return_value.wait.assert_called_once_with(
        cluster="openemr", services=["web"], WaiterConfig={"Delay": 15, "MaxAttempts": 80}
    )


# --- application health -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ("http://openemr.example.test", "https://user:pass@openemr.example.test", "https://"),
)
def test_health_check_refuses_unsafe_urls(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools.openemr_import.aws.requests.get", lambda *a, **k: pytest.fail("must not request"))

    with pytest.raises(AwsImportError, match="not a safe HTTPS target"):
        assert_application_healthy(_context(service_url=url), sleep=lambda _: None)


def test_health_check_retries_and_reports_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[Any] = [
        SimpleNamespace(status_code=503, text="maintenance"),
        requests.ConnectionError("refused"),
    ]

    def fake_get(url: str, **kwargs: Any) -> Any:
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    sleeps: list[float] = []
    monkeypatch.setattr("tools.openemr_import.aws.requests.get", fake_get)

    with pytest.raises(AwsImportError, match=r"did not become healthy after import \(ConnectionError: refused\)"):
        assert_application_healthy(_context(), attempts=2, sleep=sleeps.append)
    assert sleeps == [15]


def test_health_check_reports_http_status_without_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.openemr_import.aws.requests.get",
        lambda *a, **k: SimpleNamespace(status_code=200, text="Welcome to nginx"),
    )

    with pytest.raises(AwsImportError, match="HTTP 200 without OpenEMR login markers"):
        assert_application_healthy(_context(), attempts=1, sleep=lambda _: pytest.fail("no sleep after last"))


def test_recovery_points_reject_invalid_stack_creation_time() -> None:
    with pytest.raises(AwsImportError, match="Stack creation time is invalid"):
        recent_recovery_points(_context(stack_creation_time="bad"), session=_session(backup=MagicMock()))


# --- import lock ------------------------------------------------------------


def test_lock_conflict_reports_existing_holder() -> None:
    s3 = MagicMock()
    s3.put_object.side_effect = _client_error("PreconditionFailed", 412, "PutObject")

    with pytest.raises(AwsImportError, match="Another import already holds"):
        acquire_import_lock(_context(), session=_session(s3=s3), migration_id=MIGRATION)


def test_lock_unexpected_client_errors_propagate_unchanged() -> None:
    s3 = MagicMock()
    error = _client_error("AccessDenied", 403, "PutObject")
    s3.put_object.side_effect = error

    with pytest.raises(ClientError) as raised:
        acquire_import_lock(_context(), session=_session(s3=s3), migration_id=MIGRATION)
    assert raised.value is error


def test_lock_without_version_id_is_recorded_as_none() -> None:
    s3 = MagicMock()
    s3.put_object.return_value = {"ETag": '"etag"', "VersionId": None}

    lock = acquire_import_lock(_context(), session=_session(s3=s3), migration_id=MIGRATION)

    assert lock == ImportLock("staging-bucket", "locks/active.json", MIGRATION, '"etag"', None)
    body = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert body["migration_id"] == MIGRATION


def _lock(**overrides: Any) -> ImportLock:
    values = {"bucket": "staging-bucket", "key": "locks/active.json", "migration_id": MIGRATION, "etag": '"e"'}
    values.update(overrides)
    return ImportLock(version_id=None, **values)


@pytest.mark.parametrize("lock", (_lock(bucket="other-bucket"), _lock(key="locks/other.json")))
def test_lock_release_refuses_foreign_lock(lock: ImportLock) -> None:
    s3 = MagicMock()

    with pytest.raises(AwsImportError, match="not bound to the selected stack"):
        release_import_lock(_context(), session=_session(s3=s3), lock=lock)
    s3.delete_object.assert_not_called()


def test_lock_release_is_idempotent_when_lock_already_gone() -> None:
    s3 = MagicMock()
    s3.delete_object.side_effect = _client_error("NoSuchKey", 404, "DeleteObject")

    release_import_lock(_context(), session=_session(s3=s3), lock=_lock())

    s3.head_object.assert_not_called()


def test_lock_release_race_where_lock_disappears_is_accepted() -> None:
    s3 = MagicMock()
    s3.delete_object.side_effect = _client_error("PreconditionFailed", 412, "DeleteObject")
    s3.head_object.side_effect = _client_error("404", 404, "HeadObject")

    release_import_lock(_context(), session=_session(s3=s3), lock=_lock())

    s3.head_object.assert_called_once_with(
        Bucket="staging-bucket", Key="locks/active.json", ExpectedBucketOwner=ACCOUNT
    )


def test_lock_release_race_with_unexpected_head_error_propagates() -> None:
    s3 = MagicMock()
    s3.delete_object.side_effect = _client_error("ConditionalRequestConflict", 409, "DeleteObject")
    s3.head_object.side_effect = _client_error("AccessDenied", 403, "HeadObject")

    with pytest.raises(ClientError, match="AccessDenied"):
        release_import_lock(_context(), session=_session(s3=s3), lock=_lock())


def test_lock_release_unexpected_delete_error_propagates() -> None:
    s3 = MagicMock()
    s3.delete_object.side_effect = _client_error("AccessDenied", 403, "DeleteObject")

    with pytest.raises(ClientError, match="AccessDenied"):
        release_import_lock(_context(), session=_session(s3=s3), lock=_lock())


# --- task launch and reconciliation -----------------------------------------


def _plan() -> ImportPlan:
    return ImportPlan(
        schema_version=1,
        migration_id=MIGRATION,
        created_at="2026-07-31T00:00:00Z",
        source_kind="native-openemr-backup",
        source_fingerprint="f" * 32,
        checksums={"source": "sha256:" + "a" * 64},
        source_openemr_version="8.2.0",
        source_database_version=541,
        target_openemr_version="8.2.0",
        target_mode="fresh-target-only",
        site_ids=("default",),
        phases=(),
        preconditions=(),
        rollback=(),
        execution_allowed=True,
        blockers=(),
        warnings=(),
        configuration_fingerprint="d" * 32,
    )


@pytest.mark.parametrize(
    "response",
    ({"failures": [{"reason": "RESOURCE:CPU"}], "tasks": []}, {"tasks": [{}]}, {"tasks": []}),
    ids=("failure", "no-arn", "no-task"),
)
def test_import_task_launch_rejections_fail_closed(response: dict[str, Any]) -> None:
    ecs = MagicMock()
    ecs.run_task.return_value = response
    points = (RecoveryPoint("db", "db-rp", "d1"), RecoveryPoint("efs", "efs-rp", "d2"))

    with pytest.raises(AwsImportError, match="ECS rejected the import task launch"):
        start_import_task(
            _context(),
            session=_session(ecs=ecs),
            plan=_plan(),
            migration_id=MIGRATION,
            source_key="key",
            original_desired_count=1,
            recovery_points=points,
            lock=_lock(),
        )
    assert ecs.run_task.call_args.kwargs["clientToken"] == MIGRATION


def _task(arn: str, *, started_by: str = MIGRATION, definition: str = TASK_DEFINITION) -> dict[str, Any]:
    return {"taskArn": arn, "taskDefinitionArn": definition, "startedBy": started_by}


def test_reconciliation_paginates_and_filters_foreign_tasks() -> None:
    ecs = MagicMock()
    pages = {
        ("RUNNING", None): {"taskArns": ["arn:one"], "nextToken": "page-2"},
        ("RUNNING", "page-2"): {"taskArns": ["arn:two", "", 7]},
        ("STOPPED", None): {"taskArns": ["arn:three"]},
    }
    ecs.list_tasks.side_effect = lambda **kwargs: pages[(kwargs["desiredStatus"], kwargs.get("nextToken"))]
    ecs.describe_tasks.return_value = {
        "tasks": [
            _task("arn:one"),
            _task("arn:two", started_by="someone-else"),
            _task("arn:three", definition="arn:other"),
        ]
    }

    matches = find_import_tasks(_context(), session=_session(ecs=ecs), migration_id=MIGRATION)

    assert [task["taskArn"] for task in matches] == ["arn:one"]
    ecs.describe_tasks.assert_called_once_with(cluster="openemr", tasks=["arn:one", "arn:three", "arn:two"])


def test_reconciliation_returns_empty_without_describing_when_no_tasks() -> None:
    ecs = MagicMock()
    ecs.list_tasks.return_value = {"taskArns": []}

    assert find_import_tasks(_context(), session=_session(ecs=ecs), migration_id=MIGRATION) == ()
    ecs.describe_tasks.assert_not_called()


def test_reconciliation_cluster_scan_is_bounded() -> None:
    ecs = MagicMock()
    counter = iter(range(1000))
    ecs.list_tasks.side_effect = lambda **_: {"taskArns": [f"arn:{next(counter)}"], "nextToken": "more"}

    with pytest.raises(AwsImportError, match="exceeded its bounded cluster scan"):
        find_import_tasks(_context(), session=_session(ecs=ecs), migration_id=MIGRATION)
    assert ecs.list_tasks.call_count == 11


@pytest.mark.parametrize(
    ("list_response", "describe_response", "message"),
    (
        ({"taskArns": ["arn:one"], "nextToken": 5}, None, "pagination is malformed"),
        ({"taskArns": ["arn:one"]}, {"failures": [{"arn": "arn:one"}]}, "Unable to reconcile"),
        ({"taskArns": ["arn:one"]}, {"tasks": ["not-a-dict"]}, "malformed task data"),
        ({"taskArns": ["arn:one", "arn:two"]}, {"tasks": [_task("arn:one"), _task("arn:two")]}, "More than one"),
    ),
    ids=("token-type", "describe-failure", "task-type", "duplicate-migration"),
)
def test_reconciliation_fails_closed(
    list_response: dict[str, Any],
    describe_response: dict[str, Any] | None,
    message: str,
) -> None:
    ecs = MagicMock()
    ecs.list_tasks.return_value = list_response
    ecs.describe_tasks.return_value = describe_response

    with pytest.raises(AwsImportError, match=message):
        find_import_tasks(_context(), session=_session(ecs=ecs), migration_id=MIGRATION)


@pytest.mark.parametrize(
    ("launcher", "operation", "purpose", "extra"),
    (
        (start_cleanup_task, "cleanup", "OpenEMRImportCleanup", ["--delete-site-backup"]),
        (start_recovery_task, "recover", "OpenEMRImportRecovery", []),
    ),
    ids=("cleanup", "recovery"),
)
def test_scoped_worker_launch_is_private_and_attempt_scoped(
    launcher: Callable[..., str],
    operation: str,
    purpose: str,
    extra: list[str],
) -> None:
    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:worker"}]}

    assert launcher(_context(), session=_session(ecs=ecs), migration_id=MIGRATION, attempt=3) == "arn:worker"

    arguments = ecs.run_task.call_args.kwargs
    identity = f"{MIGRATION}-{'cleanup' if operation == 'cleanup' else 'recovery'}-3"
    assert arguments["startedBy"] == identity
    assert arguments["clientToken"] == identity
    assert arguments["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "DISABLED"
    assert arguments["overrides"]["containerOverrides"][0]["command"] == [
        "--operation",
        operation,
        "--migration-id",
        MIGRATION,
        *extra,
    ]
    assert {"key": "Purpose", "value": purpose} in arguments["tags"]


@pytest.mark.parametrize("launcher", (start_cleanup_task, start_recovery_task), ids=("cleanup", "recovery"))
def test_scoped_worker_launch_rejects_bad_attempts_and_responses(launcher: Callable[..., str]) -> None:
    ecs = MagicMock()
    session = _session(ecs=ecs)

    with pytest.raises(ValueError, match="attempt must be positive"):
        launcher(_context(), session=session, migration_id=MIGRATION, attempt=0)
    ecs.run_task.assert_not_called()

    ecs.run_task.return_value = {"failures": [{"reason": "x"}], "tasks": [{"taskArn": "arn"}]}
    with pytest.raises(AwsImportError, match="ECS rejected"):
        launcher(_context(), session=session, migration_id=MIGRATION, attempt=1)

    ecs.run_task.return_value = {"tasks": [{"taskArn": 12345}]}
    with pytest.raises(AwsImportError, match="invalid .* task ARN"):
        launcher(_context(), session=session, migration_id=MIGRATION, attempt=1)


def test_wait_for_task_returns_sole_container_exit_code() -> None:
    ecs = MagicMock()
    ecs.describe_tasks.return_value = {"tasks": [{"containers": [{"exitCode": 3}]}]}

    assert wait_for_task(_context(), session=_session(ecs=ecs), task_arn="arn:t", maximum_attempts=5) == 3

    ecs.get_waiter.assert_called_once_with("tasks_stopped")
    ecs.get_waiter.return_value.wait.assert_called_once_with(
        cluster="openemr", tasks=["arn:t"], WaiterConfig={"Delay": 15, "MaxAttempts": 5}
    )


@pytest.mark.parametrize(
    ("response", "message"),
    (
        ({"tasks": []}, "Unable to resolve completed ECS task"),
        ({"tasks": [{"containers": [{"exitCode": 0}, {"exitCode": 0}]}]}, "did not publish an exit code"),
        ({"tasks": [{"containers": [{}]}]}, "did not publish an exit code"),
    ),
    ids=("missing-task", "two-containers", "no-exit-code"),
)
def test_wait_for_task_fails_closed(response: dict[str, Any], message: str) -> None:
    ecs = MagicMock()
    ecs.describe_tasks.return_value = response

    with pytest.raises(AwsImportError, match=message):
        wait_for_task(_context(), session=_session(ecs=ecs), task_arn="arn:t")


# --- remote status ----------------------------------------------------------


def _status_clients(worker_body: bytes | Exception, *, tasks: list[dict[str, Any]], services: list[Any]) -> Any:
    s3 = MagicMock()
    if isinstance(worker_body, Exception):
        s3.get_object.side_effect = worker_body
    else:
        s3.get_object.return_value = {"Body": io.BytesIO(worker_body)}
    ecs = MagicMock()
    ecs.describe_tasks.return_value = {"tasks": tasks}
    ecs.describe_services.return_value = {"services": services}
    scaling = _scaling(_target(True))
    return s3, ecs, _session(s3=s3, ecs=ecs, **{"application-autoscaling": scaling})


def test_remote_status_combines_worker_task_service_and_autoscaling() -> None:
    worker = {"schema_version": 1, "migration_id": MIGRATION, "status": "succeeded"}
    task = {
        "taskArn": TASK_ARN,
        "taskDefinitionArn": TASK_DEFINITION,
        "startedBy": MIGRATION,
        "lastStatus": "STOPPED",
        "stopCode": "EssentialContainerExited",
        "containers": [{"name": "openemr-import", "exitCode": 0}],
    }
    s3, _, session = _status_clients(
        json.dumps(worker).encode(),
        tasks=[task],
        services=[{"desiredCount": 0, "runningCount": 0, "pendingCount": 0}],
    )

    status = read_remote_status(_receipt(), session=session)

    s3.get_object.assert_called_once_with(
        Bucket="staging-bucket", Key=f"migrations/{MIGRATION}/status.json", ExpectedBucketOwner=ACCOUNT
    )
    assert status["worker"] == worker
    assert status["task"] == {
        "last_status": "STOPPED",
        "stop_code": "EssentialContainerExited",
        "container_exit_code": 0,
        "identity_verified": True,
    }
    assert status["service"] == {"desired_count": 0, "running_count": 0, "pending_count": 0}
    assert status["autoscaling"] == {"minimum_capacity": 1, "maximum_capacity": 3, "active": False, "suspended": True}


def test_remote_status_without_published_worker_or_task() -> None:
    _, _, session = _status_clients(_client_error("NoSuchKey", 404, "GetObject"), tasks=[], services=[])

    status = read_remote_status(_receipt(), session=session)

    assert status["worker"] == {"status": "not-yet-published", "phase": "pending"}
    assert status["task"] == {}
    assert status["service"] == {}


def test_remote_status_marks_foreign_task_as_unverified() -> None:
    worker = json.dumps({"schema_version": 1, "migration_id": MIGRATION, "status": "running"}).encode()
    foreign = {"taskArn": TASK_ARN, "taskDefinitionArn": TASK_DEFINITION, "startedBy": "other", "containers": []}
    _, _, session = _status_clients(worker, tasks=[foreign], services=[])

    assert read_remote_status(_receipt(), session=session)["task"]["identity_verified"] is False


@pytest.mark.parametrize(
    ("body", "message"),
    (
        (b"x" * (64 * 1024 + 1), "exceeds the size limit"),
        (
            json.dumps({"schema_version": 1, "migration_id": "import-ffffffffffffffff", "status": "running"}),
            "malformed",
        ),
        (json.dumps({"schema_version": 1, "migration_id": MIGRATION, "status": "exploded"}), "malformed"),
        (json.dumps([1]), "malformed"),
    ),
    ids=("oversized", "foreign-migration", "unknown-status", "not-object"),
)
def test_remote_status_rejects_untrusted_worker_objects(body: bytes | str, message: str) -> None:
    payload = body.encode() if isinstance(body, str) else body
    _, ecs, session = _status_clients(payload, tasks=[], services=[])

    with pytest.raises(AwsImportError, match=message):
        read_remote_status(_receipt(), session=session)
    ecs.describe_tasks.assert_not_called()


def test_remote_status_propagates_unexpected_s3_errors() -> None:
    _, _, session = _status_clients(_client_error("AccessDenied", 403, "GetObject"), tasks=[], services=[])

    with pytest.raises(ClientError, match="AccessDenied"):
        read_remote_status(_receipt(), session=session)


# --- staging cleanup --------------------------------------------------------


class _CleanupS3:
    def __init__(
        self,
        *,
        uploads: list[dict[str, Any]],
        versions: list[dict[str, Any]],
        verification: dict[str, Any] | None = None,
        multipart_verification: dict[str, Any] | None = None,
    ) -> None:
        self.upload_pages = list(uploads)
        self.version_pages = list(versions)
        self.verification = verification or {}
        self.multipart_verification = multipart_verification or {}
        self.upload_calls: list[dict[str, Any]] = []
        self.version_calls: list[dict[str, Any]] = []
        self.aborted: list[str] = []
        self.deleted: list[dict[str, str]] = []

    def list_multipart_uploads(self, **kwargs: Any) -> dict[str, Any]:
        self.upload_calls.append(kwargs)
        if kwargs.get("MaxUploads") == 1:
            return self.multipart_verification
        return self.upload_pages.pop(0)

    def abort_multipart_upload(self, **kwargs: Any) -> None:
        self.aborted.append(kwargs["UploadId"])

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        self.version_calls.append(kwargs)
        if kwargs.get("MaxKeys") == 1:
            return self.verification
        return self.version_pages.pop(0)

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
        self.deleted.extend(kwargs["Delete"]["Objects"])
        return {}


def _cleanup(s3: _CleanupS3) -> int:
    return cleanup_staging_scope(
        region=REGION,
        staging_bucket="staging-bucket",
        migration_id=MIGRATION,
        expected_bucket_owner=ACCOUNT,
        session=_session(s3=s3),
    )


def test_cleanup_paginates_multipart_uploads_and_aborts_only_scoped_ones() -> None:
    prefix = f"migrations/{MIGRATION}/"
    s3 = _CleanupS3(
        uploads=[
            {
                "Uploads": [
                    {"Key": prefix + "a", "UploadId": "u1"},
                    {"Key": "migrations/other/a", "UploadId": "foreign"},
                ],
                "IsTruncated": True,
                "NextKeyMarker": prefix + "a",
                "NextUploadIdMarker": "u1",
            },
            {"Uploads": [{"Key": prefix + "b", "UploadId": "u2"}], "IsTruncated": False},
        ],
        versions=[{"Versions": [], "IsTruncated": False}],
    )

    assert _cleanup(s3) == 0
    assert s3.aborted == ["u1", "u2"]
    assert s3.upload_calls[1]["KeyMarker"] == prefix + "a"
    assert s3.upload_calls[1]["UploadIdMarker"] == "u1"


@pytest.mark.parametrize(
    ("s3", "message"),
    (
        (
            _CleanupS3(uploads=[{"Uploads": [], "IsTruncated": True}], versions=[]),
            "Malformed S3 multipart-upload pagination",
        ),
        (
            _CleanupS3(uploads=[{"IsTruncated": False}], versions=[{"IsTruncated": True}]),
            "Malformed S3 pagination response",
        ),
        (
            _CleanupS3(
                uploads=[{"IsTruncated": False}],
                versions=[{"IsTruncated": False}],
                verification={"DeleteMarkers": [{"Key": "late"}]},
            ),
            "not empty after cleanup",
        ),
        (
            _CleanupS3(
                uploads=[{"IsTruncated": False}],
                versions=[{"IsTruncated": False}],
                multipart_verification={"Uploads": [{"Key": "late"}]},
            ),
            "retains multipart uploads",
        ),
    ),
    ids=("multipart-pagination", "version-pagination", "versions-remain", "uploads-remain"),
)
def test_cleanup_fails_closed_on_malformed_or_incomplete_results(s3: _CleanupS3, message: str) -> None:
    with pytest.raises(AwsImportError, match=message):
        _cleanup(s3)


def test_cleanup_staging_is_scoped_by_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "tools.openemr_import.aws.cleanup_staging_scope",
        lambda **kwargs: calls.append(kwargs) or 4,
    )
    session = object()

    assert cleanup_staging(_receipt(), session=session) == 4
    assert calls == [
        {
            "region": REGION,
            "staging_bucket": "staging-bucket",
            "migration_id": MIGRATION,
            "expected_bucket_owner": ACCOUNT,
            "session": session,
        }
    ]


# --- receipt validation -----------------------------------------------------


def _write_raw(path: Path, value: Any) -> Path:
    atomic_write_private_json(path, value, label="test state")
    return path


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"cleanup_status": "complete"}, "Invalid execution receipt"),
        ({"cleanup_attempt": True}, "Invalid execution receipt"),
        ({"abort_status": "done"}, "Invalid execution receipt"),
        ({"abort_cleanup_attempt": -1}, "Invalid execution receipt"),
        ({"recovery_status": "maybe"}, "Invalid execution receipt"),
        ({"recovery_attempt": "1"}, "Invalid execution receipt"),
        ({"unexpected": 1}, "Invalid execution receipt"),
        ({"schema_version": 3}, "Unsupported execution receipt schema"),
        ({"migration_id": "import-XYZ"}, "Invalid migration identifier"),
        ({"lock_etag": ""}, "Invalid import lock identity"),
    ),
    ids=(
        "cleanup-status",
        "cleanup-attempt",
        "abort-status",
        "abort-attempt",
        "recovery-status",
        "recovery-attempt",
        "unknown-field",
        "schema",
        "migration-id",
        "lock-etag",
    ),
)
def test_receipt_validation_fails_closed(tmp_path: Path, changes: dict[str, Any], message: str) -> None:
    path = _write_raw(tmp_path / "state" / "receipt.json", asdict(_receipt()) | changes)

    with pytest.raises(AwsImportError, match=message):
        read_receipt(path)


def test_receipt_must_be_an_existing_json_object(tmp_path: Path) -> None:
    path = _write_raw(tmp_path / "state" / "receipt.json", [asdict(_receipt())])
    with pytest.raises(AwsImportError, match="Invalid execution receipt"):
        read_receipt(path)

    with pytest.raises(AwsImportError, match="Invalid execution receipt"):
        read_receipt(tmp_path / "state" / "missing.json")


def test_receipt_with_lock_version_round_trips(tmp_path: Path) -> None:
    receipt = replace(_receipt(), lock_version_id="v1")
    path = _write_raw(tmp_path / "state" / "receipt.json", asdict(receipt))

    assert read_receipt(path) == receipt


def test_fresh_target_age_is_measured_from_creation() -> None:
    created = datetime(2026, 7, 31, tzinfo=UTC)

    assert_new_import_target(_context(), now=created + timedelta(hours=23))
    with pytest.raises(AwsImportError, match="less than 2 hours old"):
        assert_new_import_target(_context(), maximum_age_hours=2, now=created + timedelta(hours=3))
