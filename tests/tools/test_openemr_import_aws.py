"""Tests for guarded AWS import orchestration without contacting AWS."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from tools._shared import ToolError
from tools.openemr_import.aws import (
    AwsImportError,
    ExecutionReceipt,
    ImportLock,
    ImportLockOutcomeUnknown,
    RecoveryPoint,
    StackContext,
    acquire_import_lock,
    assert_application_healthy,
    assert_import_resource_bindings,
    assert_new_import_target,
    assert_service_autoscaling_active,
    assert_service_stable,
    cleanup_staging_scope,
    find_import_tasks,
    read_receipt,
    recent_recovery_points,
    release_import_lock,
    resolve_stack_context,
    set_service_autoscaling_suspended,
    start_import_task,
    upload_source,
    write_receipt,
)
from tools.openemr_import.models import ImportPlan


class _Session:
    def __init__(self, clients: dict[str, Any], region: str = "us-east-1") -> None:
        self.clients = clients
        self.region_name = region

    def client(self, service: str, *, region_name: str) -> Any:
        assert region_name == self.region_name
        return self.clients[service]


class _Responses:
    def __init__(self, **responses: dict[str, Any]) -> None:
        self.responses = responses

    def __getattr__(self, name: str) -> Any:
        return lambda **_: self.responses[name]


class _Sts:
    def __init__(self, account: str = "123456789012") -> None:
        self.account = account

    def get_caller_identity(self) -> dict[str, str]:
        return {"Account": self.account}


class _CloudFormation:
    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = outputs

    def describe_stacks(self, *, StackName: str) -> dict[str, Any]:
        return {
            "Stacks": [
                {
                    "StackId": (f"arn:aws:cloudformation:us-east-1:123456789012:stack/{StackName}/identifier"),
                    "StackStatus": "CREATE_COMPLETE",
                    "CreationTime": datetime(2026, 7, 31, tzinfo=UTC),
                    "Outputs": [{"OutputKey": key, "OutputValue": value} for key, value in self.outputs.items()],
                }
            ]
        }


class _Ecs:
    def __init__(self) -> None:
        self.run_arguments: dict[str, Any] | None = None

    def describe_services(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "failures": [],
            "services": [
                {
                    "status": "ACTIVE",
                    "runningCount": 2,
                    "desiredCount": 2,
                    "pendingCount": 0,
                    "deployments": [{"rolloutState": "COMPLETED"}],
                }
            ],
        }

    def run_task(self, **kwargs: Any) -> dict[str, Any]:
        self.run_arguments = kwargs
        return {
            "failures": [],
            "tasks": [{"taskArn": ("arn:aws:ecs:us-east-1:123456789012:task/openemr/0123456789abcdef")}],
        }


class _ApplicationAutoscaling:
    def __init__(self) -> None:
        self.suspended = False
        self.register_calls: list[dict[str, Any]] = []

    def describe_scalable_targets(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {
            "ServiceNamespace": "ecs",
            "ResourceIds": ["service/openemr/web"],
            "ScalableDimension": "ecs:service:DesiredCount",
        }
        return {
            "ScalableTargets": [
                {
                    "MinCapacity": 2,
                    "MaxCapacity": 4,
                    "RoleARN": "arn:aws:iam::123456789012:role/scaling",
                    "SuspendedState": {
                        "DynamicScalingInSuspended": self.suspended,
                        "DynamicScalingOutSuspended": self.suspended,
                        "ScheduledScalingSuspended": self.suspended,
                    },
                }
            ]
        }

    def register_scalable_target(self, **kwargs: Any) -> None:
        self.register_calls.append(kwargs)
        states = kwargs["SuspendedState"]
        assert len(set(states.values())) == 1
        self.suspended = next(iter(states.values()))


class _Backup:
    def __init__(self, creation_date: datetime) -> None:
        self.creation_date = creation_date

    def list_recovery_points_by_resource(
        self,
        *,
        ResourceArn: str,
        MaxResults: int,
    ) -> dict[str, Any]:
        assert MaxResults == 100
        return {
            "RecoveryPoints": [
                {
                    "Status": "COMPLETED",
                    "CreationDate": self.creation_date,
                    "RecoveryPointArn": f"{ResourceArn}:recovery/one",
                }
            ]
        }


class _S3:
    def __init__(self, *, existing: bool = False) -> None:
        self.existing = existing
        self.upload: tuple[Any, ...] | None = None

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "Versions": [{"Key": "existing", "VersionId": "one"}] if self.existing else [],
        }

    def upload_file(self, *args: Any, **kwargs: Any) -> None:
        self.upload = (*args, kwargs)


def _outputs() -> dict[str, str]:
    return {
        "ECSClusterName": "openemr",
        "ECSServiceName": "web",
        "ApplicationURL": "https://openemr.example.test",
        "OpenEMRVersion": "8.2.0",
        "OpenEMRImportTargetMode": "fresh-target-only",
        "OpenEMRImportTaskDefinitionArn": ("arn:aws:ecs:us-east-1:123456789012:task-definition/import:1"),
        "OpenEMRImportStagingBucketName": "staging-bucket",
        "OpenEMRImportStagingKmsKeyArn": (
            "arn:aws:kms:us-east-1:123456789012:key/01234567-89ab-cdef-0123-456789abcdef"
        ),
        "OpenEMRImportSecurityGroupId": "sg-012345",
        "OpenEMRImportDatabaseSecurityGroupId": "sg-db012345",
        "OpenEMRImportEfsSecurityGroupId": "sg-ef5012345",
        "OpenEMRImportEfsAccessPointId": "fsap-012345",
        "PrivateSubnetIds": "subnet-0123abcd,subnet-4567efab",
        "DatabaseClusterArn": ("arn:aws:rds:us-east-1:123456789012:cluster:openemr"),
        "EFSSitesFileSystemId": "fs-012345",
    }


def _context() -> StackContext:
    return StackContext(
        account_id="123456789012",
        region="us-east-1",
        stack_name="OpenEMR",
        stack_creation_time="2026-07-31T00:00:00+00:00",
        stack_last_updated_time=None,
        cluster_name="openemr",
        service_name="web",
        service_url="https://openemr.example.test",
        openemr_version="8.2.0",
        import_target_mode="fresh-target-only",
        task_definition_arn=("arn:aws:ecs:us-east-1:123456789012:task-definition/import:1"),
        staging_bucket="staging-bucket",
        staging_kms_key_arn=("arn:aws:kms:us-east-1:123456789012:key/01234567-89ab-cdef-0123-456789abcdef"),
        task_security_group_id="sg-012345",
        private_subnet_ids=("subnet-0123abcd", "subnet-4567efab"),
        database_arn="arn:aws:rds:us-east-1:123456789012:cluster:openemr",
        efs_arn=("arn:aws:elasticfilesystem:us-east-1:123456789012:file-system/fs-012345"),
        database_security_group_id="sg-db012345",
        efs_security_group_id="sg-ef5012345",
        efs_access_point_id="fsap-012345",
    )


def _plan() -> ImportPlan:
    return ImportPlan(
        schema_version=1,
        migration_id="import-0123456789abcdef",
        created_at="2026-07-31T00:00:00Z",
        source_kind="native-openemr-backup",
        source_fingerprint="f" * 32,
        checksums={
            "source": f"sha256:{'a' * 64}",
            "sql": f"sha256:{'b' * 64}",
            "sites": f"sha256:{'c' * 64}",
        },
        source_openemr_version="8.2.0",
        source_database_version=541,
        target_openemr_version="8.2.0",
        target_mode="fresh-target-only",
        site_ids=("default",),
        phases=("verify",),
        preconditions=("fresh",),
        rollback=("restore",),
        execution_allowed=True,
        blockers=(),
        warnings=(),
        configuration_fingerprint="d" * 32,
    )


def test_fresh_import_target_must_be_new_and_never_updated() -> None:
    context = _context()
    assert_new_import_target(
        context,
        now=datetime(2026, 7, 31, 1, tzinfo=UTC),
    )

    with pytest.raises(AwsImportError, match="must not have any stack updates"):
        assert_new_import_target(
            replace(
                context,
                stack_last_updated_time="2026-07-31T00:30:00+00:00",
            ),
            now=datetime(2026, 7, 31, 1, tzinfo=UTC),
        )
    with pytest.raises(AwsImportError, match="less than 24 hours"):
        assert_new_import_target(
            context,
            now=datetime(2026, 8, 2, tzinfo=UTC),
        )


def test_import_outputs_are_bound_to_private_encrypted_resources() -> None:
    context = _context()
    session = _Session(
        {
            "s3": _Responses(
                head_bucket={},
                get_bucket_encryption={
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
                },
            ),
            "kms": _Responses(
                describe_key={
                    "KeyMetadata": {
                        "Arn": context.staging_kms_key_arn,
                        "Enabled": True,
                        "KeyManager": "CUSTOMER",
                        "KeyUsage": "ENCRYPT_DECRYPT",
                    }
                }
            ),
            "rds": _Responses(
                describe_db_clusters={
                    "DBClusters": [
                        {
                            "DBClusterArn": context.database_arn,
                            "Status": "available",
                        }
                    ]
                }
            ),
            "efs": _Responses(
                describe_file_systems={
                    "FileSystems": [
                        {
                            "FileSystemArn": context.efs_arn,
                            "Encrypted": True,
                            "LifeCycleState": "available",
                        }
                    ]
                },
                describe_access_points={
                    "AccessPoints": [
                        {
                            "AccessPointId": context.efs_access_point_id,
                            "FileSystemId": context.efs_arn.rsplit("/", 1)[-1],
                            "LifeCycleState": "available",
                            "PosixUser": {"Uid": "0", "Gid": "101"},
                            "RootDirectory": {"Path": "/"},
                        }
                    ]
                },
            ),
            "ec2": _Responses(
                describe_subnets={
                    "Subnets": [
                        {
                            "SubnetId": subnet_id,
                            "VpcId": "vpc-012345",
                            "MapPublicIpOnLaunch": False,
                        }
                        for subnet_id in context.private_subnet_ids
                    ]
                },
                describe_security_groups={
                    "SecurityGroups": [
                        {
                            "GroupId": context.task_security_group_id,
                            "VpcId": "vpc-012345",
                            "IpPermissions": [],
                            "IpPermissionsEgress": [
                                {
                                    "IpProtocol": "tcp",
                                    "FromPort": 443,
                                    "ToPort": 443,
                                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                                },
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
                        {
                            "GroupId": context.database_security_group_id,
                            "VpcId": "vpc-012345",
                        },
                        {
                            "GroupId": context.efs_security_group_id,
                            "VpcId": "vpc-012345",
                        },
                    ]
                },
            ),
            "ecs": _Responses(
                describe_task_definition={
                    "taskDefinition": {
                        "taskDefinitionArn": context.task_definition_arn,
                        "networkMode": "awsvpc",
                        "requiresCompatibilities": ["FARGATE"],
                        "runtimePlatform": {"cpuArchitecture": "ARM64"},
                        "containerDefinitions": [
                            {
                                "name": "openemr-import",
                                "environment": [
                                    {
                                        "name": "IMPORT_STAGING_BUCKET_OWNER",
                                        "value": context.account_id,
                                    },
                                    {
                                        "name": "IMPORT_STAGING_KMS_KEY_ARN",
                                        "value": context.staging_kms_key_arn,
                                    },
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "efsVolumeConfiguration": {
                                    "fileSystemId": context.efs_arn.rsplit("/", 1)[-1],
                                    "transitEncryption": "ENABLED",
                                    "authorizationConfig": {
                                        "iam": "ENABLED",
                                        "accessPointId": context.efs_access_point_id,
                                    },
                                }
                            }
                        ],
                    }
                }
            ),
        }
    )

    assert_import_resource_bindings(context, session=session)

    session.clients["kms"] = _Responses(
        describe_key={
            "KeyMetadata": {
                "Arn": context.staging_kms_key_arn,
                "Enabled": False,
                "KeyManager": "CUSTOMER",
                "KeyUsage": "ENCRYPT_DECRYPT",
            }
        }
    )
    with pytest.raises(AwsImportError, match="not enabled"):
        assert_import_resource_bindings(context, session=session)


def test_stack_resolution_binds_account_region_and_required_outputs() -> None:
    context = resolve_stack_context(
        session=_Session(
            {
                "sts": _Sts(),
                "cloudformation": _CloudFormation(_outputs()),
            }
        ),
        region="us-east-1",
        expected_account_id="123456789012",
        stack_name="OpenEMR",
    )

    assert context == _context()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    (
        ("PrivateSubnetIds", "subnet-one", "private-subnet"),
        (
            "OpenEMRImportStagingKmsKeyArn",
            "arn:aws:kms:us-east-1:999999999999:key/01234567-89ab-cdef-0123-456789abcdef",
            "KMS key",
        ),
    ),
)
def test_stack_resolution_rejects_unbound_network_and_kms_outputs(
    key: str,
    value: str,
    message: str,
) -> None:
    outputs = _outputs()
    outputs[key] = value

    with pytest.raises(AwsImportError, match=message):
        resolve_stack_context(
            session=_Session(
                {
                    "sts": _Sts(),
                    "cloudformation": _CloudFormation(outputs),
                }
            ),
            region="us-east-1",
            expected_account_id="123456789012",
            stack_name="OpenEMR",
        )


def test_stack_resolution_rejects_wrong_account_before_stack_lookup() -> None:
    with pytest.raises(AwsImportError, match="does not match"):
        resolve_stack_context(
            session=_Session(
                {
                    "sts": _Sts("999999999999"),
                    "cloudformation": _CloudFormation(_outputs()),
                }
            ),
            region="us-east-1",
            expected_account_id="123456789012",
            stack_name="OpenEMR",
        )


def test_service_and_backup_preflights_require_stable_current_state() -> None:
    now = datetime(2026, 7, 31, 2, tzinfo=UTC)
    session = _Session(
        {
            "ecs": _Ecs(),
            "backup": _Backup(now - timedelta(hours=1)),
        }
    )

    assert assert_service_stable(_context(), session=session) == 2
    points = recent_recovery_points(
        _context(),
        session=session,
        now=now,
    )

    assert len(points) == 2
    assert all(point.creation_date.startswith("2026-07-31") for point in points)


def test_service_autoscaling_is_suspended_and_resumed_idempotently() -> None:
    autoscaling = _ApplicationAutoscaling()
    session = _Session({"application-autoscaling": autoscaling})

    assert_service_autoscaling_active(_context(), session=session)
    set_service_autoscaling_suspended(
        _context(),
        session=session,
        suspended=True,
    )
    set_service_autoscaling_suspended(
        _context(),
        session=session,
        suspended=True,
    )

    assert len(autoscaling.register_calls) == 1
    assert autoscaling.register_calls[0]["ResourceId"] == "service/openemr/web"
    with pytest.raises(AwsImportError, match="already suspended"):
        assert_service_autoscaling_active(_context(), session=session)

    set_service_autoscaling_suspended(
        _context(),
        session=session,
        suspended=False,
    )
    assert len(autoscaling.register_calls) == 2
    assert_service_autoscaling_active(_context(), session=session)


def test_application_health_uses_only_stack_https_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    class _Response:
        status_code = 200
        text = "<title>OpenEMR</title>"

    def fake_get(url: str, **kwargs: Any) -> _Response:
        requested.append(url)
        assert kwargs["timeout"] == 20
        assert kwargs["allow_redirects"] is False
        return _Response()

    monkeypatch.setattr("tools.openemr_import.aws.requests.get", fake_get)

    assert_application_healthy(_context(), sleep=lambda _: None)

    assert requested == ["https://openemr.example.test/interface/login/login.php?site=default"]


def test_stale_recovery_points_block_execution() -> None:
    now = datetime(2026, 7, 31, tzinfo=UTC)
    with pytest.raises(AwsImportError, match="No recent completed"):
        recent_recovery_points(
            _context(),
            session=_Session({"backup": _Backup(now - timedelta(days=3))}),
            now=now,
        )


def test_recovery_point_preflight_checks_every_page() -> None:
    now = datetime(2026, 7, 31, 2, tzinfo=UTC)

    class PaginatedBackup:
        def list_recovery_points_by_resource(self, **kwargs: Any) -> dict[str, Any]:
            if "NextToken" not in kwargs:
                return {
                    "RecoveryPoints": [],
                    "NextToken": "second-page",
                }
            assert kwargs["NextToken"] == "second-page"
            resource_arn = kwargs["ResourceArn"]
            return {
                "RecoveryPoints": [
                    {
                        "Status": "COMPLETED",
                        "CreationDate": now - timedelta(hours=1),
                        "RecoveryPointArn": f"{resource_arn}:recovery/current",
                    }
                ]
            }

    points = recent_recovery_points(
        _context(),
        session=_Session({"backup": PaginatedBackup()}),
        now=now,
    )

    assert len(points) == 2
    assert all(point.recovery_point_arn.endswith("/current") for point in points)


def test_upload_refuses_to_overwrite_prior_migration(tmp_path: Path) -> None:
    source = tmp_path / "source.tar"
    source.write_bytes(b"source")
    with pytest.raises(AwsImportError, match="already exists"):
        upload_source(
            _context(),
            session=_Session({"s3": _S3(existing=True)}),
            migration_id="import-0123456789abcdef",
            source=source,
        )


def test_upload_binds_bucket_owner_and_customer_managed_kms_key(tmp_path: Path) -> None:
    source = tmp_path / "source.tar"
    source.write_bytes(b"source")
    s3 = _S3()

    key = upload_source(
        _context(),
        session=_Session({"s3": s3}),
        migration_id="import-0123456789abcdef",
        source=source,
    )

    assert key == "migrations/import-0123456789abcdef/source.tar"
    assert s3.upload is not None
    assert s3.upload[-1]["ExtraArgs"] == {
        "ExpectedBucketOwner": "123456789012",
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": _context().staging_kms_key_arn,
        "Tagging": ("MigrationId=import-0123456789abcdef&DataClass=ImportSource"),
    }


def test_cleanup_deletes_every_version_only_under_migration_prefix() -> None:
    class VersionedS3:
        def __init__(self) -> None:
            self.pages = 0
            self.multipart_calls = 0
            self.aborted: list[tuple[str, str]] = []
            self.deleted: list[dict[str, str]] = []

        def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
            self.pages += 1
            assert kwargs["Prefix"] == "migrations/import-0123456789abcdef/"
            if self.pages == 1:
                assert "KeyMarker" not in kwargs
                return {
                    "Versions": [
                        {
                            "Key": "migrations/import-0123456789abcdef/source.tar",
                            "VersionId": "source-v1",
                        },
                        {"Key": "migrations/other/source.tar", "VersionId": "other"},
                    ],
                    "DeleteMarkers": [
                        {
                            "Key": "migrations/import-0123456789abcdef/status.json",
                            "VersionId": "status-delete",
                        }
                    ],
                    "IsTruncated": True,
                    "NextKeyMarker": "next-key",
                    "NextVersionIdMarker": "next-version",
                }
            if self.pages == 2:
                assert kwargs["KeyMarker"] == "next-key"
                assert kwargs["VersionIdMarker"] == "next-version"
                return {
                    "Versions": [
                        {
                            "Key": "migrations/import-0123456789abcdef/status.json",
                            "VersionId": "status-v1",
                        }
                    ],
                    "IsTruncated": False,
                }
            assert kwargs["MaxKeys"] == 1
            return {"Versions": [], "DeleteMarkers": [], "IsTruncated": False}

        def list_multipart_uploads(self, **_: Any) -> dict[str, Any]:
            self.multipart_calls += 1
            if self.multipart_calls == 1:
                return {
                    "Uploads": [
                        {
                            "Key": "migrations/import-0123456789abcdef/source.tar",
                            "UploadId": "incomplete-upload",
                        }
                    ],
                    "IsTruncated": False,
                }
            return {"Uploads": [], "IsTruncated": False}

        def abort_multipart_upload(self, **kwargs: Any) -> None:
            self.aborted.append((kwargs["Key"], kwargs["UploadId"]))

        def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
            self.deleted.extend(kwargs["Delete"]["Objects"])
            return {}

    s3 = VersionedS3()

    deleted = cleanup_staging_scope(
        region="us-east-1",
        staging_bucket="staging-bucket",
        migration_id="import-0123456789abcdef",
        expected_bucket_owner="123456789012",
        session=_Session({"s3": s3}),
    )

    assert deleted == 3
    assert s3.aborted == [
        (
            "migrations/import-0123456789abcdef/source.tar",
            "incomplete-upload",
        )
    ]
    assert s3.deleted == [
        {
            "Key": "migrations/import-0123456789abcdef/source.tar",
            "VersionId": "source-v1",
        },
        {
            "Key": "migrations/import-0123456789abcdef/status.json",
            "VersionId": "status-delete",
        },
        {
            "Key": "migrations/import-0123456789abcdef/status.json",
            "VersionId": "status-v1",
        },
    ]


def test_cleanup_of_an_already_empty_scope_is_idempotent() -> None:
    class EmptyS3:
        delete_called = False

        def list_multipart_uploads(self, **_: Any) -> dict[str, Any]:
            return {"Uploads": [], "IsTruncated": False}

        def list_object_versions(self, **_: Any) -> dict[str, Any]:
            return {"Versions": [], "DeleteMarkers": [], "IsTruncated": False}

        def delete_objects(self, **_: Any) -> dict[str, Any]:
            self.delete_called = True
            return {}

    s3 = EmptyS3()
    arguments = {
        "region": "us-east-1",
        "staging_bucket": "staging-bucket",
        "migration_id": "import-0123456789abcdef",
        "expected_bucket_owner": "123456789012",
        "session": _Session({"s3": s3}),
    }

    assert cleanup_staging_scope(**arguments) == 0
    assert cleanup_staging_scope(**arguments) == 0
    assert s3.delete_called is False


def test_stack_wide_import_lock_uses_conditional_owner_checked_object() -> None:
    class LockS3:
        put: dict[str, Any] | None = None
        deleted: dict[str, Any] | None = None

        def put_object(self, **kwargs: Any) -> dict[str, str]:
            self.put = kwargs
            return {"ETag": '"lock-etag"', "VersionId": "lock-version"}

        def delete_object(self, **kwargs: Any) -> None:
            self.deleted = kwargs

    s3 = LockS3()
    session = _Session({"s3": s3})
    context = _context()

    lock = acquire_import_lock(
        context,
        session=session,
        migration_id="import-0123456789abcdef",
    )
    release_import_lock(
        context,
        session=session,
        lock=lock,
    )

    assert s3.put is not None
    assert s3.put["Key"] == "locks/active.json"
    assert s3.put["IfNoneMatch"] == "*"
    assert s3.put["ExpectedBucketOwner"] == context.account_id
    assert s3.put["ServerSideEncryption"] == "aws:kms"
    assert s3.put["SSEKMSKeyId"] == context.staging_kms_key_arn
    assert s3.deleted == {
        "Bucket": context.staging_bucket,
        "Key": "locks/active.json",
        "ExpectedBucketOwner": context.account_id,
        "IfMatch": '"lock-etag"',
    }


def test_import_lock_release_fails_closed_on_conditional_race() -> None:
    class RacingS3:
        def delete_object(self, **kwargs: Any) -> None:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "DeleteObject",
            )

        def head_object(self, **kwargs: Any) -> dict[str, Any]:
            return {"ETag": '"replacement-etag"'}

    with pytest.raises(AwsImportError, match="ownership changed during release"):
        release_import_lock(
            _context(),
            session=_Session({"s3": RacingS3()}),
            lock=ImportLock(
                bucket="staging-bucket",
                key="locks/active.json",
                migration_id="import-0123456789abcdef",
                etag='"old-etag"',
                version_id=None,
            ),
        )


def test_import_lock_missing_response_identity_is_outcome_unknown() -> None:
    class IdentityLostS3:
        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            return {}

    with pytest.raises(ImportLockOutcomeUnknown):
        acquire_import_lock(
            _context(),
            session=_Session({"s3": IdentityLostS3()}),
            migration_id="import-0123456789abcdef",
        )


def test_import_lock_server_error_is_outcome_unknown() -> None:
    class AmbiguousS3:
        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            raise ClientError(
                {
                    "Error": {"Code": "InternalError"},
                    "ResponseMetadata": {"HTTPStatusCode": 500},
                },
                "PutObject",
            )

    with pytest.raises(ImportLockOutcomeUnknown):
        acquire_import_lock(
            _context(),
            session=_Session({"s3": AmbiguousS3()}),
            migration_id="import-0123456789abcdef",
        )


def test_cleanup_fails_closed_on_partial_s3_delete() -> None:
    class PartialDeleteS3:
        def list_multipart_uploads(self, **_: Any) -> dict[str, Any]:
            return {"Uploads": [], "IsTruncated": False}

        def list_object_versions(self, **_: Any) -> dict[str, Any]:
            return {
                "Versions": [
                    {
                        "Key": "migrations/import-0123456789abcdef/source.tar",
                        "VersionId": "v1",
                    }
                ],
                "IsTruncated": False,
            }

        def delete_objects(self, **_: Any) -> dict[str, Any]:
            return {"Errors": [{"Code": "AccessDenied"}]}

    with pytest.raises(AwsImportError, match="partial"):
        cleanup_staging_scope(
            region="us-east-1",
            staging_bucket="staging-bucket",
            migration_id="import-0123456789abcdef",
            expected_bucket_owner="123456789012",
            session=_Session({"s3": PartialDeleteS3()}),
        )


def test_task_launch_is_private_scoped_and_contains_no_credentials() -> None:
    ecs = _Ecs()
    points = (
        RecoveryPoint("db", "db-recovery", "2026-07-31T00:00:00+00:00"),
        RecoveryPoint("efs", "efs-recovery", "2026-07-31T00:00:00+00:00"),
    )

    receipt = start_import_task(
        _context(),
        session=_Session({"ecs": ecs}),
        plan=_plan(),
        migration_id="import-0123456789abcdef",
        source_key="migrations/import-0123456789abcdef/source.tar",
        original_desired_count=2,
        recovery_points=points,
        lock=ImportLock(
            bucket="staging-bucket",
            key="locks/active.json",
            migration_id="import-0123456789abcdef",
            etag='"lock-etag"',
            version_id="lock-version",
        ),
    )

    assert receipt.original_desired_count == 2
    assert receipt.recovery_point_arns == ("db-recovery", "efs-recovery")
    assert ecs.run_arguments is not None
    network = ecs.run_arguments["networkConfiguration"]["awsvpcConfiguration"]
    assert network["assignPublicIp"] == "DISABLED"
    assert network["subnets"] == ["subnet-0123abcd", "subnet-4567efab"]
    command = ecs.run_arguments["overrides"]["containerOverrides"][0]["command"]
    assert "password" not in " ".join(command).lower()
    assert "secret" not in " ".join(command).lower()


def test_uncertain_launch_reconciliation_binds_started_task_identity() -> None:
    context = _context()
    task_arn = "arn:aws:ecs:us-east-1:123456789012:task/openemr/one"

    class ReconciliationEcs:
        def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
            assert "startedBy" not in kwargs
            return {"taskArns": [task_arn] if kwargs["desiredStatus"] == "STOPPED" else []}

        def describe_tasks(self, **_: Any) -> dict[str, Any]:
            return {
                "failures": [],
                "tasks": [
                    {
                        "taskArn": task_arn,
                        "taskDefinitionArn": context.task_definition_arn,
                        "startedBy": "import-0123456789abcdef",
                    }
                ],
            }

    tasks = find_import_tasks(
        context,
        session=_Session({"ecs": ReconciliationEcs()}),
        migration_id="import-0123456789abcdef",
    )

    assert tasks[0]["taskArn"] == task_arn


def test_execution_receipt_round_trip_is_owner_only(tmp_path: Path) -> None:
    receipt = ExecutionReceipt(
        schema_version=4,
        migration_id="import-0123456789abcdef",
        account_id="123456789012",
        region="us-east-1",
        stack_name="OpenEMR",
        stack_creation_time="2026-07-31T00:00:00+00:00",
        stack_last_updated_time=None,
        cluster_name="openemr",
        service_name="web",
        service_url="https://openemr.example.test",
        openemr_version="8.2.0",
        original_desired_count=2,
        task_arn="task",
        task_definition_arn="task-definition",
        staging_bucket="bucket",
        staging_kms_key_arn="arn:aws:kms:us-east-1:123456789012:key/example",
        task_security_group_id="sg-0123456789abcdef0",
        private_subnet_ids=("subnet-0123abcd", "subnet-4567efab"),
        database_arn="arn:aws:rds:us-east-1:123456789012:cluster:openemr",
        efs_arn="arn:aws:elasticfilesystem:us-east-1:123456789012:file-system/fs-0123456789abcdef0",
        source_key="migrations/import-0123456789abcdef/source.tar",
        started_at="2026-07-31T00:00:00+00:00",
        recovery_point_arns=("db", "efs"),
        recovery_point_dates=("date-one", "date-two"),
        lock_etag='"lock-etag"',
        lock_version_id="lock-version",
    )
    path = tmp_path / "state" / "receipt.json"

    write_receipt(path, receipt)

    assert read_receipt(path) == receipt
    in_progress = receipt.__dict__ | {
        "cleanup_status": "in-progress",
        "cleanup_attempt": 2,
        "abort_status": "cleanup-in-progress",
        "abort_cleanup_attempt": 3,
        "recovery_status": "failed",
        "recovery_attempt": 1,
    }
    path.write_text(json.dumps(in_progress), encoding="utf-8")
    assert read_receipt(path) == receipt
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_execution_receipt_rejects_symlinked_state_directory(tmp_path: Path) -> None:
    receipt = ExecutionReceipt(
        schema_version=4,
        migration_id="import-0123456789abcdef",
        account_id="123456789012",
        region="us-east-1",
        stack_name="OpenEMR",
        stack_creation_time="2026-07-31T00:00:00+00:00",
        stack_last_updated_time=None,
        cluster_name="openemr",
        service_name="web",
        service_url="https://openemr.example.test",
        openemr_version="8.2.0",
        original_desired_count=2,
        task_arn="task",
        task_definition_arn="task-definition",
        staging_bucket="bucket",
        staging_kms_key_arn="arn:aws:kms:us-east-1:123456789012:key/example",
        task_security_group_id="sg-0123456789abcdef0",
        private_subnet_ids=("subnet-0123abcd", "subnet-4567efab"),
        database_arn="arn:aws:rds:us-east-1:123456789012:cluster:openemr",
        efs_arn="arn:aws:elasticfilesystem:us-east-1:123456789012:file-system/fs-0123456789abcdef0",
        source_key="migrations/import-0123456789abcdef/source.tar",
        started_at="2026-07-31T00:00:00+00:00",
        recovery_point_arns=("db", "efs"),
        recovery_point_dates=("date-one", "date-two"),
        lock_etag='"lock-etag"',
        lock_version_id="lock-version",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    state = tmp_path / "state"
    state.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ToolError, match="symlinked OpenEMR import state"):
        write_receipt(state / "receipt.json", receipt)

    assert not (outside / "receipt.json").exists()
