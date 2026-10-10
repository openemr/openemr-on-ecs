"""Read probes, validation, timing, and owned-stack cleanup for live E2E."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

import boto3
import requests  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError

from tools._shared import ToolError, hash_account_id

from .models import CheckResult, PhaseTiming, ResidualResource
from .progress import NULL_PROGRESS, ProgressReporter

_EXPECTED_RESOURCE_TYPES = {
    "AWS::ECS::Cluster",
    "AWS::ECS::Service",
    "AWS::EFS::FileSystem",
    "AWS::ElastiCache::ServerlessCache",
    "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "AWS::RDS::DBCluster",
    "AWS::WAFv2::WebACLAssociation",
}
_FATAL_LOG_PATTERN = re.compile(
    r"(?i)\b(?:segmentation fault|out of memory|uncaught (?:exception|error)|"
    r"(?:fatal php|php fatal) error|kernel panic|panic:)\b"
)
_QUOTAS = (
    ("vpc-count", "vpc", "L-F678F1CE", 1.0),
    ("elastic-ip-count", "ec2", "L-0263D0A3", 2.0),
    ("fargate-vcpu", "fargate", "L-3032A538", 4.0),
    ("rds-clusters", "rds", "L-952B80B8", 1.0),
)
_WRITE_ACTIONS = (
    "acm:RequestCertificate",
    "application-autoscaling:RegisterScalableTarget",
    "backup:CreateBackupVault",
    "cloudformation:CreateChangeSet",
    "cloudformation:CreateStack",
    "cloudformation:DeleteStack",
    "cloudformation:ExecuteChangeSet",
    "cloudtrail:CreateTrail",
    "ec2:CreateVpc",
    "ecs:CreateCluster",
    "ecs:CreateService",
    "efs:CreateFileSystem",
    "elasticache:CreateServerlessCache",
    "elasticloadbalancing:CreateLoadBalancer",
    "iam:CreateRole",
    "iam:PassRole",
    "kms:CreateKey",
    "lambda:CreateFunction",
    "logs:CreateLogGroup",
    "rds:CreateDBCluster",
    "route53:ChangeResourceRecordSets",
    "s3:CreateBucket",
    "secretsmanager:CreateSecret",
    "sns:CreateTopic",
    "wafv2:CreateWebACL",
)
_LOCAL_CLEANUP_ACTIONS = (
    "cloudformation:DeleteStack",
    "cloudformation:DescribeStackEvents",
    "cloudformation:DescribeStacks",
    "cloudformation:GetTemplate",
    "cloudformation:ListStackResources",
    "ec2:DeleteFlowLogs",
    "ec2:DeleteInternetGateway",
    "ec2:DeleteNatGateway",
    "ec2:DeleteNetworkInterface",
    "ec2:DeleteSecurityGroup",
    "ec2:DeleteSubnet",
    "ec2:DeleteVpc",
    "ec2:DescribeFlowLogs",
    "ec2:DescribeInternetGateways",
    "ec2:DescribeNatGateways",
    "ec2:DetachInternetGateway",
    "ecr:DescribeImages",
    "ecs:DeleteCluster",
    "ecs:DeleteService",
    "ecs:DeleteTaskDefinitions",
    "ecs:DeregisterTaskDefinition",
    "ecs:DescribeClusters",
    "ecs:DescribeServices",
    "ecs:DescribeTaskDefinition",
    "ecs:UpdateService",
    "kms:DescribeKey",
    "kms:ScheduleKeyDeletion",
    "logs:DeleteLogGroup",
    "logs:DescribeLogGroups",
    "tag:GetResources",
    "s3:AbortMultipartUpload",
    "s3:DeleteObject",
    "s3:DeleteObjectVersion",
    "s3:GetObject",
    "s3:ListBucketMultipartUploads",
    "s3:ListBucketVersions",
    "sts:AssumeRole",
)
_BOOTSTRAP_ROLE_PURPOSES = (
    "deploy",
    "file-publishing",
    "image-publishing",
    "lookup",
)


_MISSING_RESOURCE_CODES = {
    "AccessPointNotFound",
    "ClusterNotFoundException",
    "InvalidGroup.NotFound",
    "InvalidNetworkInterfaceID.NotFound",
    "InvalidSubnetID.NotFound",
    "InvalidVpcID.NotFound",
    "NatGatewayNotFound",
    "NotFoundException",
    "ResourceNotFoundException",
    "ServiceNotFoundException",
    "InvalidInternetGatewayID.NotFound",
}
# Transient dependency conflicts are expected while NAT/ENIs drain between sweep passes.
_RETRYABLE_CLEANUP_CODES = {
    "DependencyViolation",
    "ResourceInUse",
    "ResourceInUseException",
    "InvalidParameterException",
    "InvalidParameterValue",
}


def _client_error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _client_error_message(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Message", ""))


def _is_missing_resource_error(exc: BaseException) -> bool:
    """Return True for already-deleted / not-found AWS API failures during cleanup."""

    if not isinstance(exc, ClientError):
        return False
    code = _client_error_code(exc)
    if code in _MISSING_RESOURCE_CODES:
        return True
    lowered = _client_error_message(exc).lower()
    return (
        "does not exist" in lowered
        or "not found" in lowered
        or "could not be found" in lowered
        or "cannot be found" in lowered
        or "no longer available" in lowered
        or "invalid nat gateway" in lowered
    )


def _is_retryable_cleanup_error(exc: BaseException) -> bool:
    if not isinstance(exc, ClientError):
        return False
    code = _client_error_code(exc)
    if code in _RETRYABLE_CLEANUP_CODES:
        return True
    lowered = _client_error_message(exc).lower()
    return (
        "dependency violation" in lowered
        or "has dependencies" in lowered
        or "in use" in lowered
        # ECS task defs can still be tagged while CloudFormation is deleting them.
        or "in the process of being deleted" in lowered
        or "already being deleted" in lowered
    )


def _ignore_transient_cleanup_or_raise(exc: BaseException, *, action: str) -> None:
    """Ignore missing/retryable cleanup errors; re-raise unexpected failures with context."""

    if _is_missing_resource_error(exc) or _is_retryable_cleanup_error(exc):
        return
    raise ToolError(f"{action} failed: {exc}") from exc


def _is_unsupported_emulator_operation(exc: BaseException) -> bool:
    """Return True when an emulator rejects an API that real AWS must support."""

    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
        message = str(exc.response.get("Error", {}).get("Message", ""))
        if code in {"UnknownOperationException", "InternalError", "NotImplemented", "501"}:
            return True
        lowered = message.lower()
        if "unknown operation" in lowered or "not implemented" in lowered:
            return True
    text = str(exc).lower()
    return "unknown operation" in text or "not implemented" in text


class LiveE2EAws:
    """Bounded AWS adapter tied to one profile, account, and region."""

    def __init__(
        self,
        *,
        region: str,
        profile_name: str | None = None,
        session: Any | None = None,
        progress: ProgressReporter | None = None,
        endpoint_url: str | None = None,
        emulated: bool = False,
    ) -> None:
        self.region = region
        self.progress = progress or NULL_PROGRESS
        if endpoint_url and not emulated:
            raise ToolError("An emulator endpoint requires explicit emulated mode")
        self.endpoint_url = endpoint_url
        self.emulated = emulated
        if session is not None:
            self.session = session
        elif self.endpoint_url:
            self.session = boto3.Session(
                region_name=region,
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
                aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
            )
        else:
            self.session = boto3.Session(profile_name=profile_name, region_name=region)
        self._clients: dict[str, Any] = {}

    def client(self, service: str, *, global_service: bool = False) -> Any:
        """Return a cached boto3 client."""

        key = f"{service}:{global_service}"
        if key not in self._clients:
            kwargs: dict[str, Any] = {} if global_service else {"region_name": self.region}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self._clients[key] = self.session.client(service, **kwargs)
        return self._clients[key]

    def identity(self) -> dict[str, str]:
        """Resolve caller identity without exposing it in committed results."""

        response = self.client("sts").get_caller_identity()
        account_id = str(response.get("Account", ""))
        arn = str(response.get("Arn", ""))
        if len(account_id) != 12 or not account_id.isdigit() or not arn.startswith("arn:"):
            raise ToolError("STS returned an invalid caller identity")
        return {
            "account_id": account_id,
            "account_hash": hash_account_id(account_id),
            "caller_arn": arn,
        }

    def preflight(
        self,
        *,
        approved_account: str,
        route53_domain: str,
        bootstrap_stack_name: str,
    ) -> tuple[tuple[CheckResult, ...], dict[str, Any]]:
        """Run read-only account, region, DNS, bootstrap, permission, and quota probes."""

        checks: list[CheckResult] = []
        identity = self.identity()
        if identity["account_id"] != approved_account:
            raise ToolError("Active AWS account does not match --approved-account")
        checks.append(CheckResult("aws-identity", "pass", f"account={identity['account_hash']}; region={self.region}"))

        zone = self._hosted_zone(route53_domain)
        checks.append(
            CheckResult(
                "dedicated-public-hosted-zone",
                "pass",
                f"public hosted zone found for {route53_domain}; zone ID omitted",
            )
        )
        if zone.get("Config", {}).get("PrivateZone"):
            raise ToolError("The live E2E Route 53 hosted zone must be public")
        self._assert_dedicated_zone_records(zone)
        checks.append(
            CheckResult(
                "dedicated-zone-records",
                "pass",
                "hosted zone contains only NS and SOA delegation records",
            )
        )

        bootstrap = self.client("cloudformation").describe_stacks(StackName=bootstrap_stack_name)["Stacks"][0]
        bootstrap_status = str(bootstrap.get("StackStatus", ""))
        if bootstrap_status not in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"}:
            raise ToolError(f"CDK bootstrap stack is not ready: {bootstrap_status}")
        bootstrap_outputs = {
            str(item.get("OutputKey")): str(item.get("OutputValue")) for item in bootstrap.get("Outputs", [])
        }
        try:
            bootstrap_version = int(bootstrap_outputs["BootstrapVersion"])
        except (KeyError, ValueError) as exc:
            raise ToolError("CDK bootstrap stack does not expose a valid BootstrapVersion") from exc
        if bootstrap_version < 21:
            raise ToolError(f"CDK bootstrap template version {bootstrap_version} is too old; require at least 21")
        checks.append(
            CheckResult(
                "cdk-bootstrap",
                "pass",
                f"{bootstrap_stack_name} is {bootstrap_status} at template version {bootstrap_version}",
            )
        )

        checks.extend(self._permission_probes())
        try:
            zone_response = self.client("ec2").describe_availability_zones(AllAvailabilityZones=False)
        except (BotoCoreError, ClientError) as exc:
            raise ToolError(f"Cannot resolve available deployment zones: {exc}") from exc
        availability_zones = sorted(
            {
                str(zone.get("ZoneName"))
                for zone in zone_response.get("AvailabilityZones", [])
                if zone.get("State") == "available"
                and zone.get("ZoneType", "availability-zone") == "availability-zone"
                and re.fullmatch(rf"{re.escape(self.region)}[a-z]", str(zone.get("ZoneName", "")))
            }
        )
        if len(availability_zones) < 2:
            raise ToolError("Live E2E requires at least two available standard Availability Zones")
        selected_availability_zones = availability_zones[:2]
        checks.append(
            CheckResult(
                "availability-zones",
                "pass",
                f"{len(availability_zones)} standard Availability Zones available",
            )
        )
        checks.extend(
            self._write_permission_probes(
                caller_arn=identity["caller_arn"],
                account_id=identity["account_id"],
                bootstrap=bootstrap,
            )
        )
        checks.extend(self._quota_probes())
        hosted_zone_id = str(zone.get("Id", "")).removeprefix("/hostedzone/").upper()
        if self.emulated and hosted_zone_id and not hosted_zone_id.startswith("Z"):
            hosted_zone_id = f"Z{hosted_zone_id}"
        if not re.fullmatch(r"Z[A-Z0-9]+", hosted_zone_id):
            raise ToolError("Route 53 returned an invalid hosted-zone ID")
        return tuple(checks), {
            "bootstrap_version": bootstrap_version,
            "hosted_zone_id": hosted_zone_id,
            "availability_zones": selected_availability_zones,
        }

    def describe_stack(self, stack_name_or_id: str) -> dict[str, Any] | None:
        """Return one stack, treating a validated missing-stack response as absent."""

        try:
            stacks = self.client("cloudformation").describe_stacks(StackName=stack_name_or_id).get("Stacks", [])
        except ClientError as exc:
            message = str(exc.response.get("Error", {}).get("Message", ""))
            if "does not exist" in message:
                return None
            raise
        if not stacks:
            return None
        stack = cast(dict[str, Any], stacks[0])
        status = str(stack.get("StackStatus", ""))
        # Floci can retain terminal tombstones that AWS omits. DELETE_FAILED is
        # absent only after every stack resource is independently confirmed gone.
        if self.emulated and status == "DELETE_COMPLETE":
            return None
        if self.emulated and status == "DELETE_FAILED" and self._emulated_delete_tombstone_is_empty(stack):
            return None
        return stack

    def assert_owned_stack(self, stack_name_or_id: str, run_id: str) -> dict[str, Any]:
        """Fail closed unless the stack carries the exact E2E ownership output."""

        stack = self.describe_stack(stack_name_or_id)
        if stack is None:
            raise ToolError("Live E2E stack does not exist")
        outputs = {str(item.get("OutputKey")): str(item.get("OutputValue")) for item in stack.get("Outputs", [])}
        if outputs.get("LiveE2ERunId") != run_id:
            template = (
                self.client("cloudformation")
                .get_template(
                    StackName=str(stack["StackId"]),
                    TemplateStage="Original",
                )
                .get("TemplateBody", {})
            )
            if isinstance(template, str):
                try:
                    template = json.loads(template)
                except json.JSONDecodeError as exc:
                    raise ToolError("Refusing cleanup: stack ownership template is not valid JSON") from exc
            if not isinstance(template, dict):
                raise ToolError("Refusing cleanup: stack ownership template has an invalid shape")
            template_outputs = template.get("Outputs", {})
            marker_output = template_outputs.get("LiveE2ERunId", {}) if isinstance(template_outputs, dict) else {}
            marker = marker_output.get("Value") if isinstance(marker_output, dict) else None
            if marker != run_id:
                raise ToolError("Refusing cleanup: stack does not carry the expected live E2E ownership marker")
        return stack

    def validate_deployment(
        self,
        *,
        stack_name_or_id: str,
        run_id: str,
        profile: str,
        https_timeout_seconds: float,
        poll_seconds: float,
    ) -> tuple[tuple[CheckResult, ...], tuple[PhaseTiming, ...]]:
        """Validate infrastructure, task health, startup logs, WAF, and HTTPS."""

        if self.emulated:
            return self._validate_deployment_emulated(
                stack_name_or_id=stack_name_or_id,
                run_id=run_id,
                profile=profile,
                https_timeout_seconds=min(https_timeout_seconds, 30.0),
                poll_seconds=min(poll_seconds, 2.0),
            )

        validation_started = time.monotonic()
        readiness_deadline = validation_started + https_timeout_seconds
        phases: list[PhaseTiming] = []
        self.progress.phase("validation", "checking stack outputs and application readiness")
        stack = self.assert_owned_stack(stack_name_or_id, run_id)
        stack_status = str(stack.get("StackStatus", ""))
        if stack_status not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
            raise ToolError(f"Deployment stack is not complete: {stack_status}")
        checks = [CheckResult("cloudformation-stack", "pass", stack_status)]
        outputs = {str(item.get("OutputKey")): str(item.get("OutputValue")) for item in stack.get("Outputs", [])}

        resources = self._stack_resources(str(stack["StackId"]))
        present_types = {str(item.get("ResourceType")) for item in resources}
        missing = sorted(_EXPECTED_RESOURCE_TYPES - present_types)
        if missing:
            raise ToolError(f"Deployment is missing expected resource types: {', '.join(missing)}")
        failed = [
            str(item.get("LogicalResourceId"))
            for item in resources
            if str(item.get("ResourceStatus", "")).endswith(("FAILED", "ROLLBACK"))
        ]
        if failed:
            raise ToolError(f"Stack contains failed resources: {', '.join(failed)}")
        checks.append(CheckResult("expected-resources", "pass", f"{len(resources)} stack resources healthy"))
        self.progress.info(f"Stack resources healthy ({len(resources)} resources)")

        application_url = _required_output(outputs, "ApplicationURL")
        if not application_url.startswith("https://"):
            raise ToolError("ApplicationURL is not HTTPS")
        self.progress.info("Waiting for HTTPS application readiness")
        readiness_duration, request_duration = self._wait_for_https(
            application_url,
            timeout_seconds=max(0.1, readiness_deadline - time.monotonic()),
            poll_seconds=poll_seconds,
            progress=self.progress,
        )
        ready_at = datetime.now(timezone.utc)
        application_ready_duration = round(
            max(0.0, (ready_at - _aware(stack["CreationTime"])).total_seconds()),
            3,
        )
        checks.append(CheckResult("application-https", "pass", "HTTPS returned an OpenEMR response"))
        phases.extend(
            (
                PhaseTiming(
                    "post-deploy-https-wait",
                    readiness_duration,
                    "local-monotonic-clock-with-https-probes",
                ),
                PhaseTiming(
                    "http-readiness",
                    request_duration,
                    "http-client-monotonic-clock",
                ),
                PhaseTiming(
                    "application-https-ready",
                    application_ready_duration,
                    "cloudformation-stack-creation-and-local-https-probe",
                    _timestamp(_aware(stack["CreationTime"])),
                    _timestamp(ready_at),
                ),
            )
        )

        cluster = _required_output(outputs, "ECSClusterName")
        service = _required_output(outputs, "ECSServiceName")
        ecs_started = time.monotonic()
        waiter_delay = max(5, int(poll_seconds))
        waiter_attempts = max(
            1,
            math.ceil(max(0.1, readiness_deadline - time.monotonic()) / waiter_delay),
        )
        self.progress.info(f"Waiting for ECS service stability ({service})")
        with self.progress.pulse("ECS service still converging", interval_seconds=max(30.0, float(waiter_delay))):
            self.client("ecs").get_waiter("services_stable").wait(
                cluster=cluster,
                services=[service],
                WaiterConfig={"Delay": waiter_delay, "MaxAttempts": waiter_attempts},
            )
        self.progress.info("ECS service is stable")
        service_data = self.client("ecs").describe_services(cluster=cluster, services=[service])["services"][0]
        if int(service_data.get("runningCount", 0)) < int(service_data.get("desiredCount", 0)):
            raise ToolError("ECS service did not reach its desired running count")
        deployments = service_data.get("deployments", [])
        if not deployments or any(
            deployment.get("rolloutState") not in {None, "COMPLETED"} for deployment in deployments
        ):
            raise ToolError("ECS service deployment rollout is not complete")
        task_arns = (
            self.client("ecs")
            .list_tasks(
                cluster=cluster,
                serviceName=service,
                desiredStatus="RUNNING",
            )
            .get("taskArns", [])
        )
        desired_count = int(service_data.get("desiredCount", 0))
        if len(task_arns) < desired_count or desired_count < 1:
            raise ToolError("ECS service has fewer running tasks than its desired count")
        tasks = self.client("ecs").describe_tasks(cluster=cluster, tasks=task_arns).get("tasks", [])
        if len(tasks) < desired_count:
            raise ToolError("ECS did not return all running task descriptions")
        for task in tasks:
            if task.get("lastStatus") != "RUNNING" or task.get("healthStatus") != "HEALTHY":
                raise ToolError("One or more ECS tasks are not running and healthy")
            for container in task.get("containers", []):
                if container.get("lastStatus") != "RUNNING" or container.get("healthStatus") not in {None, "HEALTHY"}:
                    raise ToolError("One or more essential containers are not healthy")
        creation_time = _aware(stack["CreationTime"])
        crash_events = [
            str(event.get("message"))
            for event in service_data.get("events", [])
            if isinstance(event.get("createdAt"), datetime)
            and _aware(event["createdAt"]) >= creation_time
            and any(
                marker in str(event.get("message", "")).lower()
                for marker in (
                    "unable to consistently start tasks",
                    "is unhealthy",
                    "task failed to start",
                )
            )
        ]
        if crash_events:
            raise ToolError("ECS service events show a startup or health failure")
        checks.append(CheckResult("ecs-service", "pass", "service stable at desired count"))
        checks.append(
            CheckResult(
                "ecs-task-health",
                "pass",
                f"{len(tasks)} running healthy tasks with no crash-loop events",
            )
        )
        phases.append(_local_phase("ecs-steady-state-validation", ecs_started))

        target_started = time.monotonic()
        target_groups = [
            str(item["PhysicalResourceId"])
            for item in resources
            if item.get("ResourceType") == "AWS::ElasticLoadBalancingV2::TargetGroup" and item.get("PhysicalResourceId")
        ]
        if not target_groups:
            raise ToolError("No load balancer target group was found")
        target_health = self.client("elbv2").describe_target_health(TargetGroupArn=target_groups[0])
        states = [
            str(description.get("TargetHealth", {}).get("State"))
            for description in target_health.get("TargetHealthDescriptions", [])
        ]
        if not states or any(state != "healthy" for state in states):
            raise ToolError(f"Load balancer targets are not all healthy: {states}")
        checks.append(CheckResult("load-balancer-targets", "pass", f"{len(states)} healthy targets"))
        phases.append(_local_phase("alb-target-health-validation", target_started))

        efs_started = time.monotonic()
        efs_ids = (
            _required_output(outputs, "EFSSitesFileSystemId"),
            _required_output(outputs, "EFSSSLFileSystemId"),
        )
        efs = self.client("efs").describe_file_systems(FileSystemId=efs_ids[0])["FileSystems"][0]
        ssl_efs = self.client("efs").describe_file_systems(FileSystemId=efs_ids[1])["FileSystems"][0]
        if {efs.get("LifeCycleState"), ssl_efs.get("LifeCycleState")} != {"available"}:
            raise ToolError("One or more EFS file systems are not available")
        checks.append(CheckResult("efs-file-systems", "pass", "sites and SSL file systems available"))
        phases.append(_local_phase("efs-availability-validation", efs_started))

        database_started = time.monotonic()
        database_arn = _required_output(outputs, "DatabaseClusterArn")
        database_id = database_arn.rsplit(":", 1)[-1]
        database = self.client("rds").describe_db_clusters(DBClusterIdentifier=database_id)["DBClusters"][0]
        if database.get("Status") != "available":
            raise ToolError(f"Aurora cluster is not available: {database.get('Status')}")
        if profile == "api-enabled" and database.get("HttpEndpointEnabled") is not True:
            raise ToolError("API-enabled profile did not enable the Aurora Data API")
        checks.append(CheckResult("aurora-cluster", "pass", "cluster available"))
        phases.append(_local_phase("aurora-availability-validation", database_started))

        cache_started = time.monotonic()
        cache_ids = [
            str(item["PhysicalResourceId"])
            for item in resources
            if item.get("ResourceType") == "AWS::ElastiCache::ServerlessCache" and item.get("PhysicalResourceId")
        ]
        if len(cache_ids) != 1:
            raise ToolError("Expected exactly one ElastiCache Serverless cache")
        cache = self.client("elasticache").describe_serverless_caches(ServerlessCacheName=cache_ids[0])[
            "ServerlessCaches"
        ][0]
        if str(cache.get("Status", "")).lower() != "available":
            raise ToolError(f"ElastiCache Serverless is not available: {cache.get('Status')}")
        checks.append(CheckResult("elasticache-serverless", "pass", "cache available"))
        phases.append(_local_phase("elasticache-availability-validation", cache_started))

        waf_started = time.monotonic()
        load_balancer_arns = [
            str(item["PhysicalResourceId"])
            for item in resources
            if item.get("ResourceType") == "AWS::ElasticLoadBalancingV2::LoadBalancer"
            and item.get("PhysicalResourceId")
        ]
        if len(load_balancer_arns) != 1 or not load_balancer_arns[0].startswith("arn:"):
            raise ToolError("Expected one load balancer ARN in stack resources")
        waf = self.client("wafv2").get_web_acl_for_resource(ResourceArn=load_balancer_arns[0])
        if not isinstance(waf.get("WebACL"), dict):
            raise ToolError("Application load balancer has no WAF association")
        checks.append(CheckResult("waf-association", "pass", "web ACL associated"))
        phases.append(_local_phase("waf-association", waf_started))

        alarm_names = [
            str(item["PhysicalResourceId"])
            for item in resources
            if item.get("ResourceType") == "AWS::CloudWatch::Alarm" and item.get("PhysicalResourceId")
        ]
        if alarm_names:
            alarm_response = self.client("cloudwatch").describe_alarms(AlarmNames=alarm_names[:100])
            alarms = [
                *alarm_response.get("MetricAlarms", []),
                *alarm_response.get("CompositeAlarms", []),
            ]
            if len(alarms) != len(alarm_names[:100]):
                raise ToolError("One or more expected CloudWatch alarms are missing")
            checks.append(
                CheckResult(
                    "monitoring-resources",
                    "pass",
                    f"{len(alarm_names)} CloudWatch alarms present",
                )
            )
        else:
            checks.append(
                CheckResult(
                    "monitoring-resources",
                    "pass",
                    "profile has no CloudWatch alarm resources",
                )
            )

        log_started = time.monotonic()
        log_group = _required_output(outputs, "LogGroupName")
        fatal_count = self._fatal_startup_log_count(
            log_group,
            start_time=creation_time,
        )
        if fatal_count:
            raise ToolError(f"OpenEMR startup log contains {fatal_count} known fatal patterns")
        checks.append(
            CheckResult(
                "startup-logs",
                "pass",
                "no known fatal startup patterns in bounded log scan",
            )
        )
        phases.append(_local_phase("startup-log-validation", log_started))

        if profile == "api-enabled":
            task_definition = (
                self.client("ecs")
                .describe_task_definition(
                    taskDefinition=str(service_data.get("taskDefinition", "")),
                )
                .get("taskDefinition", {})
            )
            definitions = task_definition.get("containerDefinitions", [])
            openemr_container = next(
                (item for item in definitions if item.get("name") == "openemr"),
                None,
            )
            secret_names = {
                str(item.get("name")) for item in (openemr_container or {}).get("secrets", []) if item.get("name")
            }
            expected_settings = {
                "OPENEMR_SETTING_portal_onsite_two_enable",
                "OPENEMR_SETTING_rest_api",
                "OPENEMR_SETTING_rest_fhir_api",
            }
            if not expected_settings.issubset(secret_names):
                raise ToolError("API-enabled task definition is missing required OpenEMR settings")
            try:
                portal = requests.get(
                    application_url.rstrip("/") + "/portal/",
                    timeout=30,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                raise ToolError(f"Patient portal smoke request failed: {type(exc).__name__}") from exc
            if portal.status_code >= 400 or "openemr" not in portal.text[:200_000].lower():
                raise ToolError("API-enabled patient portal smoke test failed")
            checks.append(
                CheckResult(
                    "api-enabled-profile",
                    "pass",
                    "Data API, REST/FHIR settings, and patient portal verified",
                )
            )

        smoke_started = time.monotonic()
        smoke_url = application_url.rstrip("/") + "/interface/login/login.php"
        try:
            smoke = requests.get(smoke_url, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            raise ToolError(f"OpenEMR application smoke request failed: {type(exc).__name__}") from exc
        if smoke.status_code != 200 or "openemr" not in smoke.text[:200_000].lower():
            raise ToolError(f"OpenEMR application smoke test returned HTTP {smoke.status_code}")
        checks.append(CheckResult("application-smoke", "pass", "login page responded"))
        phases.append(_local_phase("application-smoke-test", smoke_started))
        phases.append(_local_phase("deployment-validation", validation_started))
        return tuple(checks), tuple(phases)

    def event_phases(self, stack_id: str, *, operation: str) -> tuple[PhaseTiming, ...]:
        """Measure CloudFormation phases from API event timestamps."""

        if operation not in {"CREATE", "DELETE"}:
            raise ValueError("operation must be CREATE or DELETE")
        events = self._stack_events(stack_id)
        definitions: tuple[tuple[str, set[str]], ...]
        if operation == "CREATE":
            definitions = (
                ("cloudformation-deployment", {"AWS::CloudFormation::Stack"}),
                (
                    "aurora-provisioning",
                    {"AWS::RDS::DBCluster", "AWS::RDS::DBInstance"},
                ),
                (
                    "elasticache-provisioning",
                    {"AWS::ElastiCache::ServerlessCache"},
                ),
                ("efs-provisioning", {"AWS::EFS::FileSystem"}),
                ("ecs-service-creation", {"AWS::ECS::Service"}),
            )
        else:
            definitions = (("cloudformation-deletion", {"AWS::CloudFormation::Stack"}),)
        phases: list[PhaseTiming] = []
        for name, resource_types in definitions:
            starts = [
                event["Timestamp"]
                for event in events
                if event.get("ResourceType") in resource_types
                and event.get("ResourceStatus") == f"{operation}_IN_PROGRESS"
            ]
            finishes = [
                event["Timestamp"]
                for event in events
                if event.get("ResourceType") in resource_types
                and event.get("ResourceStatus") == f"{operation}_COMPLETE"
            ]
            if starts and finishes:
                started = min(_aware(item) for item in starts)
                finished = max(_aware(item) for item in finishes)
                if finished >= started:
                    phases.append(
                        PhaseTiming(
                            name,
                            round((finished - started).total_seconds(), 3),
                            "cloudformation-events-api",
                            _timestamp(started),
                            _timestamp(finished),
                        )
                    )
        return tuple(phases)

    def _validate_deployment_emulated(
        self,
        *,
        stack_name_or_id: str,
        run_id: str,
        profile: str,
        https_timeout_seconds: float,
        poll_seconds: float,
    ) -> tuple[tuple[CheckResult, ...], tuple[PhaseTiming, ...]]:
        """Validate a Floci-deployed stack without requiring public HTTPS reachability."""

        del profile  # profile-specific portal checks need a reachable OpenEMR URL
        validation_started = time.monotonic()
        stack = self.assert_owned_stack(stack_name_or_id, run_id)
        stack_status = str(stack.get("StackStatus", ""))
        if stack_status not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
            raise ToolError(f"Deployment stack is not complete: {stack_status}")
        checks: list[CheckResult] = [CheckResult("cloudformation-stack", "pass", stack_status)]
        outputs = {str(item.get("OutputKey")): str(item.get("OutputValue")) for item in stack.get("Outputs", [])}
        resources = self._stack_resources(str(stack["StackId"]))
        present_types = {str(item.get("ResourceType")) for item in resources}
        missing = sorted(_EXPECTED_RESOURCE_TYPES - present_types)
        if missing:
            raise ToolError(f"Deployment is missing expected resource types: {', '.join(missing)}")
        failed = [
            str(item.get("LogicalResourceId"))
            for item in resources
            if str(item.get("ResourceStatus", "")).endswith(("FAILED", "ROLLBACK"))
        ]
        if failed:
            raise ToolError(f"Stack contains failed resources: {', '.join(failed)}")
        checks.append(CheckResult("expected-resources", "pass", f"{len(resources)} stack resources healthy"))

        application_url = _required_output(outputs, "ApplicationURL")
        if not application_url.startswith("https://"):
            raise ToolError("ApplicationURL is not HTTPS")
        try:
            self._wait_for_https(
                application_url,
                timeout_seconds=max(0.1, https_timeout_seconds),
                poll_seconds=max(0.1, poll_seconds),
            )
            checks.append(CheckResult("application-https", "pass", "HTTPS returned an OpenEMR response"))
        except ToolError:
            checks.append(
                CheckResult(
                    "application-https",
                    "pass",
                    "floci-emulated; ApplicationURL present but not locally reachable",
                )
            )

        for name, probe in (
            (
                "ecs-service",
                lambda: self.client("ecs").describe_services(
                    cluster=_required_output(outputs, "ECSClusterName"),
                    services=[_required_output(outputs, "ECSServiceName")],
                ),
            ),
            (
                "efs-file-systems",
                lambda: self.client("efs").describe_file_systems(
                    FileSystemId=_required_output(outputs, "EFSSitesFileSystemId")
                ),
            ),
            (
                "aurora-cluster",
                lambda: self.client("rds").describe_db_clusters(
                    DBClusterIdentifier=_required_output(outputs, "DatabaseClusterArn").rsplit(":", 1)[-1]
                ),
            ),
        ):
            try:
                probe()
                checks.append(CheckResult(name, "pass", "emulator API probe succeeded"))
            except (BotoCoreError, ClientError, ToolError) as exc:
                if _is_unsupported_emulator_operation(exc):
                    checks.append(
                        CheckResult(
                            name,
                            "pass",
                            "floci-emulated; operation unsupported by emulator",
                        )
                    )
                else:
                    raise ToolError(f"Floci validation probe failed ({name}): {exc}") from exc

        phases = (
            PhaseTiming(
                "floci-emulated-validation",
                round(time.monotonic() - validation_started, 3),
                "local-monotonic-clock",
            ),
        )
        return tuple(checks), phases

    def delete_owned_stack(self, stack_name_or_id: str, run_id: str) -> str | None:
        """Delete only a stack with the exact ownership marker."""

        stack = self.describe_stack(stack_name_or_id)
        if stack is None:
            return None
        owned = self.assert_owned_stack(stack_name_or_id, run_id)
        stack_id = str(owned["StackId"])
        self.client("cloudformation").delete_stack(StackName=stack_id)
        return stack_id

    def wait_for_stack_deleted(
        self,
        stack_name_or_id: str,
        *,
        timeout_seconds: float,
        poll_seconds: float,
    ) -> None:
        """Wait until CloudFormation confirms deletion, failing on terminal errors."""

        deadline = time.monotonic() + timeout_seconds
        s3_empty_retries = 0
        max_s3_empty_retries = 3
        retried_emulated_delete_failed = False
        started = time.monotonic()
        self.progress.info("Waiting for CloudFormation stack deletion")
        while time.monotonic() < deadline:
            # Keep the full stack status visible for bounded delete-retry logic.
            raw = self._describe_stack_raw(stack_name_or_id)
            if raw is None:
                self.progress.info("Stack deletion confirmed (stack no longer exists)")
                return
            status = str(raw.get("StackStatus", ""))
            elapsed = time.monotonic() - started
            minutes, seconds = divmod(int(elapsed), 60)
            self.progress.heartbeat(
                f"Stack deletion in progress: status={status or 'unknown'} (elapsed {minutes}m{seconds:02d}s)"
            )
            if status == "DELETE_COMPLETE":
                self.progress.info("Stack deletion confirmed (DELETE_COMPLETE)")
                return
            if status == "DELETE_FAILED":
                stack_ref = str(raw.get("StackId") or stack_name_or_id)
                if self.emulated and not retried_emulated_delete_failed:
                    retried_emulated_delete_failed = True
                    try:
                        self.client("cloudformation").delete_stack(StackName=stack_ref)
                    except (BotoCoreError, ClientError) as exc:
                        if not _is_missing_resource_error(exc):
                            self.progress.info(f"Floci stack delete retry was rejected: {exc}")
                    time.sleep(max(poll_seconds, 0.2))
                    continue
                if self.emulated:
                    if self._emulated_delete_tombstone_is_empty(raw):
                        self.progress.info("Verified Floci DELETE_FAILED tombstone has no remaining resources")
                        return
                    reason = str(raw.get("StackStatusReason", "reason unavailable"))
                    raise ToolError(f"Floci stack deletion failed with remaining resources: {reason}")
                # Real AWS: ALB access-log buckets are versioned and can receive
                # late objects after AutoDeleteObjects runs. Empty them and retry.
                if s3_empty_retries < max_s3_empty_retries:
                    buckets = self._s3_buckets_failed_to_delete(stack_ref)
                    if buckets:
                        s3_empty_retries += 1
                        self.progress.info(
                            f"DELETE_FAILED due to non-empty S3 bucket(s); emptying "
                            f"{len(buckets)} bucket(s) and retrying delete "
                            f"(attempt {s3_empty_retries}/{max_s3_empty_retries})"
                        )
                        for bucket in buckets:
                            removed = self._empty_versioned_s3_bucket(bucket)
                            self.progress.info(f"Emptied {removed} object version(s) from s3://{bucket}")
                        try:
                            self.client("cloudformation").delete_stack(StackName=stack_ref)
                        except (BotoCoreError, ClientError) as exc:
                            if not _is_missing_resource_error(exc):
                                raise ToolError(f"Stack delete retry failed: {exc}") from exc
                        time.sleep(max(poll_seconds, 0.2))
                        continue
                reason = str(raw.get("StackStatusReason", "reason unavailable"))
                raise ToolError(f"Stack deletion failed: {reason}")
            time.sleep(poll_seconds)
        if self.describe_stack(stack_name_or_id) is None:
            self.progress.info("Stack deletion confirmed after timeout race")
            return
        raise ToolError(f"Stack still exists after {timeout_seconds:g} seconds")

    def _emulated_delete_tombstone_is_empty(self, stack: dict[str, Any]) -> bool:
        """Confirm a Floci DELETE_FAILED tombstone has no undeleted resources."""

        stack_ref = str(stack.get("StackId") or stack.get("StackName") or "")
        if not stack_ref:
            return False
        resources = self._stack_resources(stack_ref)
        return all(str(resource.get("ResourceStatus", "")) == "DELETE_COMPLETE" for resource in resources)

    def _s3_buckets_failed_to_delete(self, stack_name_or_id: str) -> list[str]:
        """Return PhysicalResourceIds for S3 buckets that failed delete as non-empty."""

        client = self.client("cloudformation")
        buckets: list[str] = []
        try:
            paginator = client.get_paginator("describe_stack_events")
            for page in paginator.paginate(StackName=stack_name_or_id):
                for event in page.get("StackEvents", []):
                    if event.get("ResourceStatus") != "DELETE_FAILED":
                        continue
                    if event.get("ResourceType") != "AWS::S3::Bucket":
                        continue
                    physical_id = str(event.get("PhysicalResourceId") or "").strip()
                    if not physical_id:
                        continue
                    reason = str(event.get("ResourceStatusReason", "")).lower()
                    if (
                        "not empty" in reason
                        or "delete all versions" in reason
                        or "bucketnotempty" in reason.replace(" ", "")
                    ):
                        buckets.append(physical_id)
        except (BotoCoreError, ClientError) as exc:
            if _is_missing_resource_error(exc):
                return []
            raise ToolError(f"Cannot inspect DELETE_FAILED stack events: {exc}") from exc
        return sorted(set(buckets))

    def _empty_versioned_s3_bucket(self, bucket_name: str) -> int:
        """Delete every object version and delete marker from a bucket."""

        s3 = self.client("s3")
        deleted = 0
        try:
            # Abort lingering multipart uploads that can also block bucket delete.
            uploads = s3.get_paginator("list_multipart_uploads")
            for page in uploads.paginate(Bucket=bucket_name):
                for upload in page.get("Uploads", []):
                    key = upload.get("Key")
                    upload_id = upload.get("UploadId")
                    if not key or not upload_id:
                        continue
                    try:
                        s3.abort_multipart_upload(Bucket=bucket_name, Key=key, UploadId=upload_id)
                    except ClientError as exc:
                        _ignore_transient_cleanup_or_raise(
                            exc,
                            action=f"Abort multipart upload s3://{bucket_name}/{key}",
                        )

            versions = s3.get_paginator("list_object_versions")
            for page in versions.paginate(Bucket=bucket_name):
                objects: list[dict[str, str]] = []
                for collection in ("Versions", "DeleteMarkers"):
                    for item in page.get(collection, []):
                        key = item.get("Key")
                        version_id = item.get("VersionId")
                        if key and version_id:
                            objects.append({"Key": str(key), "VersionId": str(version_id)})
                for offset in range(0, len(objects), 1000):
                    batch = objects[offset : offset + 1000]
                    response = s3.delete_objects(
                        Bucket=bucket_name,
                        Delete={"Objects": batch, "Quiet": True},
                    )
                    errors = response.get("Errors") or []
                    if errors:
                        sample = errors[0]
                        raise ToolError(
                            f"Failed emptying s3://{bucket_name}: "
                            f"{sample.get('Code', 'Error')} {sample.get('Message', '')}".strip()
                        )
                    deleted += len(batch)
        except ClientError as exc:
            code = _client_error_code(exc)
            if code in {"NoSuchBucket", "404"} or _is_missing_resource_error(exc):
                return deleted
            raise ToolError(f"Failed emptying s3://{bucket_name}: {exc}") from exc
        return deleted

    def _describe_stack_raw(self, stack_name_or_id: str) -> dict[str, Any] | None:
        """Return stack metadata including DELETE_* tombstones."""

        try:
            stacks = self.client("cloudformation").describe_stacks(StackName=stack_name_or_id).get("Stacks", [])
        except ClientError as exc:
            message = str(exc.response.get("Error", {}).get("Message", ""))
            if "does not exist" in message:
                return None
            raise
        if not stacks:
            return None
        return cast(dict[str, Any], stacks[0])

    def residual_resources(self, run_id: str) -> tuple[ResidualResource, ...]:
        """Inventory taggable resources left after deletion and classify delayed deletions."""

        residuals: list[ResidualResource] = []
        for arn in self._tagged_resource_arns(run_id, require_stack_tag=False):
            service = _arn_service(arn)
            if self._is_expected_tagged_residual(arn):
                disposition = "scheduled-deletion-expected"
            else:
                life = self._tagged_resource_lifecycle(arn)
                if life == "missing":
                    # Tag index lag after the service API already reports the resource gone.
                    continue
                disposition = "scheduled-deletion-expected" if life == "deleting" else "unexpected-residual"
            residuals.append(
                ResidualResource(
                    resource_type=service,
                    identifier_hash=f"sha256:{hashlib.sha256(arn.encode('utf-8')).hexdigest()[:12]}",
                    disposition=disposition,
                )
            )
        return tuple(residuals)

    def bootstrap_asset_residuals(self, assembly_dir: Path) -> tuple[ResidualResource, ...]:
        """Verify content-addressed CDK assets that remain in shared bootstrap storage."""

        residuals: dict[tuple[str, str], ResidualResource] = {}
        for manifest_path in sorted(assembly_dir.glob("*.assets.json")):
            if manifest_path.is_symlink() or manifest_path.stat().st_size > 10_000_000:
                raise ToolError("Cloud assembly contains an unsafe asset manifest")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ToolError(f"Cannot inspect CDK asset manifest: {exc}") from exc
            if not isinstance(manifest, dict):
                raise ToolError("CDK asset manifest must be an object")
            self._file_asset_residuals(manifest.get("files", {}), residuals)
            self._image_asset_residuals(manifest.get("dockerImages", {}), residuals)
        return tuple(residuals[key] for key in sorted(residuals))

    def owned_rds_cluster_identifiers(
        self,
        stack_name_or_id: str,
        run_id: str,
    ) -> tuple[str, ...]:
        """Capture RDS cluster IDs from the exact owned stack before deletion."""

        owned = self.assert_owned_stack(stack_name_or_id, run_id)
        stack_id = str(owned["StackId"])
        cloudformation = self.client("cloudformation")
        identifiers: set[str] = set()
        token: str | None = None
        while True:
            arguments: dict[str, Any] = {"StackName": stack_id}
            if token:
                arguments["NextToken"] = token
            response = cloudformation.list_stack_resources(**arguments)
            for resource in response.get("StackResourceSummaries", []):
                if resource.get("ResourceType") != "AWS::RDS::DBCluster":
                    continue
                identifier = str(resource.get("PhysicalResourceId", ""))
                if not identifier:
                    continue
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,62}", identifier):
                    raise ToolError("Owned stack returned an invalid RDS cluster identifier")
                identifiers.add(identifier)
            token = response.get("NextToken")
            if not token:
                break
        if str(owned.get("StackStatus", "")) in {"CREATE_COMPLETE", "UPDATE_COMPLETE"} and not identifiers:
            raise ToolError("Owned E2E stack has no RDS cluster to inventory")
        return tuple(sorted(identifiers))

    def cleanup_owned_log_groups(
        self,
        stack_name: str,
        run_id: str,
        rds_cluster_identifiers: Sequence[str] = (),
    ) -> int:
        """Delete Lambda and RDS log groups derived from the exact owned stack."""

        run_hash = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
        expected_stack_name = f"OpenemrE2E-{run_hash}"
        if stack_name != expected_stack_name:
            raise ToolError("Refusing log cleanup for a stack name outside the live E2E scope")
        prefixes = [f"/aws/lambda/{expected_stack_name}-"]
        for identifier in rds_cluster_identifiers:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,62}", identifier):
                raise ToolError("Refusing log cleanup for an invalid RDS identifier")
            prefixes.append(f"/aws/rds/cluster/{identifier}/")
        paginator = self.client("logs").get_paginator("describe_log_groups")
        names: list[str] = []
        for prefix in prefixes:
            for page in paginator.paginate(logGroupNamePrefix=prefix):
                for item in page.get("logGroups", []):
                    name = item.get("logGroupName")
                    if isinstance(name, str) and name.startswith(prefix):
                        names.append(name)
        for name in sorted(set(names)):
            self.client("logs").delete_log_group(logGroupName=name)
        remaining: list[str] = []
        for prefix in prefixes:
            for page in paginator.paginate(logGroupNamePrefix=prefix):
                remaining.extend(
                    str(item["logGroupName"])
                    for item in page.get("logGroups", [])
                    if str(item.get("logGroupName", "")).startswith(prefix)
                )
        if remaining:
            raise ToolError("Owned CloudWatch log groups remain after explicit cleanup")
        return len(set(names))

    def cleanup_owned_tagged_resources(
        self,
        run_id: str,
        *,
        timeout_seconds: float = 900.0,
        poll_seconds: float = 5.0,
    ) -> int:
        """Delete LiveE2ERunId-tagged orphans that CloudFormation left behind.

        Real AWS stack deletion can return DELETE_COMPLETE while ECS services,
        task definitions, NAT gateways, or subnets remain tagged. Sweep those
        leftovers in dependency order before residual verification.
        """

        deadline = time.monotonic() + timeout_seconds
        attempts = 0
        self.progress.phase("tagged-orphan-sweep", "removing LiveE2E-tagged leftovers")
        while time.monotonic() < deadline:
            arns = self._tagged_resource_arns(run_id, require_stack_tag=False)
            lifecycles = {
                arn: self._tagged_resource_lifecycle(arn) for arn in arns if not self._is_expected_tagged_residual(arn)
            }
            actionable = [arn for arn, life in lifecycles.items() if life == "active"]
            waiting = [arn for arn, life in lifecycles.items() if life == "deleting"]
            if not actionable and not waiting:
                if attempts:
                    self.progress.info(f"Tagged orphan sweep complete after {attempts} pass(es)")
                else:
                    self.progress.info("No unexpected tagged orphans found")
                return attempts
            attempts += 1
            if actionable:
                services = sorted({_arn_service(arn) for arn in actionable})
                self.progress.info(
                    f"Sweep pass {attempts}: deleting {len(actionable)} tagged resource(s) "
                    f"({', '.join(services[:8])}{'…' if len(services) > 8 else ''})"
                )
                self._delete_tagged_resources(actionable)
            if waiting:
                services = sorted({_arn_service(arn) for arn in waiting})
                self.progress.info(
                    f"Sweep pass {attempts}: waiting on {len(waiting)} already-deleting "
                    f"resource(s) ({', '.join(services[:8])}{'…' if len(services) > 8 else ''})"
                )
            time.sleep(max(poll_seconds, 0.2))
        remaining = [
            arn
            for arn in self._tagged_resource_arns(run_id, require_stack_tag=False)
            if not self._is_expected_tagged_residual(arn) and self._tagged_resource_lifecycle(arn) == "active"
        ]
        if remaining:
            raise ToolError(
                "Owned tagged resources remain after post-delete sweep: "
                + ", ".join(f"{_arn_service(arn)}:{arn.rsplit('/', 1)[-1]}" for arn in remaining[:12])
            )
        return attempts

    def assert_run_id_available(self, run_id: str) -> None:
        """Reject a run ID already attached to any stack-owned resource."""

        existing = self._tagged_resource_arns(run_id)
        if existing:
            raise ToolError(f"Live E2E run ID is already attached to {len(existing)} resource(s); choose a new run ID")

    def _tagged_resource_arns(self, run_id: str, *, require_stack_tag: bool = True) -> list[str]:
        """Collect resources carrying this run's tag.

        CloudFormation drops its ``aws:cloudformation:*`` tags once the owning stack is
        deleted, so post-deletion callers pass ``require_stack_tag=False``: an absent
        stack tag is expected there, while a tag naming a *different* stack still means
        the run ID is not ours to act on and remains fatal.
        """

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{5,47}", run_id):
            raise ToolError("Cannot inspect resources for an invalid live E2E run ID")
        expected_stack_name = "OpenemrE2E-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
        client = self.client("resourcegroupstaggingapi")
        paginator = client.get_paginator("get_resources")
        arns: list[str] = []
        for page in paginator.paginate(TagFilters=[{"Key": "LiveE2ERunId", "Values": [run_id]}]):
            for mapping in page.get("ResourceTagMappingList", []):
                arn = str(mapping.get("ResourceARN", ""))
                tags = {
                    str(tag.get("Key")): str(tag.get("Value"))
                    for tag in mapping.get("Tags", [])
                    if tag.get("Key") is not None
                }
                if not arn:
                    continue
                stack_tag = tags.get("aws:cloudformation:stack-name")
                if stack_tag != expected_stack_name and (require_stack_tag or stack_tag is not None):
                    raise ToolError(
                        "Refusing live E2E resource discovery: the run tag is "
                        "attached to a resource outside the owned CloudFormation stack"
                    )
                arns.append(arn)
        return sorted({arn for arn in arns if arn})

    def _is_expected_tagged_residual(self, arn: str) -> bool:
        if _arn_service(arn) != "kms":
            return False
        key_id = arn.rsplit("/", 1)[-1]
        try:
            key = self.client("kms").describe_key(KeyId=key_id)["KeyMetadata"]
        except (BotoCoreError, ClientError) as exc:
            if _is_missing_resource_error(exc):
                return False
            raise ToolError(f"Cannot classify tagged KMS residual {key_id}: {exc}") from exc
        return bool(key.get("KeyState") == "PendingDeletion")

    def _tagged_resource_lifecycle(self, arn: str) -> str:
        """Classify a tagged orphan as active, deleting, or missing (tagging lag)."""

        service = _arn_service(arn)
        try:
            if service == "ec2" and ":natgateway/" in arn:
                nat_id = arn.rsplit("/", 1)[-1]
                gateways = self.client("ec2").describe_nat_gateways(NatGatewayIds=[nat_id]).get("NatGateways", [])
                if not gateways:
                    return "missing"
                state = str(gateways[0].get("State", "")).lower()
                if state in {"deleted", "deleting"}:
                    return "deleting" if state == "deleting" else "missing"
                return "active"
            if service == "ec2" and ":vpc-flow-log/" in arn:
                flow_log_id = arn.rsplit("/", 1)[-1]
                logs = self.client("ec2").describe_flow_logs(FlowLogIds=[flow_log_id]).get("FlowLogs", [])
                return "active" if logs else "missing"
            if service == "ecs" and ":service/" in arn:
                parts = arn.split(":service/", 1)[-1].split("/", 1)
                if len(parts) != 2:
                    return "active"
                cluster, service_name = parts
                services = (
                    self.client("ecs").describe_services(cluster=cluster, services=[service_name]).get("services", [])
                )
                if not services:
                    return "missing"
                status = str(services[0].get("status", "")).upper()
                if status in {"INACTIVE", "DRAINING"}:
                    return "deleting" if status == "DRAINING" else "missing"
                return "active"
            if service == "ecs" and ":cluster/" in arn:
                cluster = arn.rsplit("/", 1)[-1]
                clusters = self.client("ecs").describe_clusters(clusters=[cluster]).get("clusters", [])
                if not clusters:
                    return "missing"
                status = str(clusters[0].get("status", "")).upper()
                if status in {"INACTIVE", "MISSING"}:
                    return "missing"
                return "active"
            if service == "ecs" and ":task-definition/" in arn:
                task_def = arn.split(":task-definition/", 1)[-1]
                detail = self.client("ecs").describe_task_definition(taskDefinition=task_def)
                status = str(detail.get("taskDefinition", {}).get("status", "")).upper()
                if status == "DELETE_IN_PROGRESS":
                    return "deleting"
                # ACTIVE/INACTIVE both still need delete_task_definitions until the
                # revision disappears from the service API (tag index can lag).
                return "active"
        except (BotoCoreError, ClientError) as exc:
            if _is_missing_resource_error(exc):
                return "missing"
            raise ToolError(f"Cannot classify tagged residual {arn}: {exc}") from exc
        return "active"

    def _delete_tagged_resources(self, arns: Sequence[str]) -> None:
        services = {arn: _arn_service(arn) for arn in arns}
        # Dependency order matters: drain ECS before network tear-down.
        for arn in (item for item, service in services.items() if service == "ecs" and ":service/" in item):
            self._delete_tagged_ecs_service(arn)
        for arn in (item for item, service in services.items() if service == "ecs" and ":cluster/" in item):
            self._delete_tagged_ecs_cluster(arn)
        for arn in (item for item, service in services.items() if service == "ecs" and ":task-definition/" in item):
            self._delete_tagged_ecs_task_definition(arn)
        nat_ids = [
            arn.rsplit("/", 1)[-1] for arn, service in services.items() if service == "ec2" and ":natgateway/" in arn
        ]
        for nat_id in nat_ids:
            try:
                self.client("ec2").delete_nat_gateway(NatGatewayId=nat_id)
            except ClientError as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Delete NAT gateway {nat_id}")
        if nat_ids:
            try:
                self.client("ec2").get_waiter("nat_gateway_deleted").wait(
                    NatGatewayIds=nat_ids,
                    WaiterConfig={"Delay": 5, "MaxAttempts": 60},
                )
            except (BotoCoreError, ClientError) as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Wait for NAT gateway deletion ({', '.join(nat_ids)})")
        for arn in (item for item, service in services.items() if service == "ec2" and ":vpc-flow-log/" in item):
            flow_log_id = arn.rsplit("/", 1)[-1]
            try:
                self.client("ec2").delete_flow_logs(FlowLogIds=[flow_log_id])
            except ClientError as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Delete VPC flow log {flow_log_id}")
        for arn in (item for item, service in services.items() if service == "ec2" and ":network-interface/" in item):
            eni = arn.rsplit("/", 1)[-1]
            try:
                self.client("ec2").delete_network_interface(NetworkInterfaceId=eni)
            except ClientError as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Delete network interface {eni}")
        for arn in (item for item, service in services.items() if service == "ec2" and ":subnet/" in item):
            subnet_id = arn.rsplit("/", 1)[-1]
            try:
                self.client("ec2").delete_subnet(SubnetId=subnet_id)
            except ClientError as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Delete subnet {subnet_id}")
        for arn in (item for item, service in services.items() if service == "ec2" and ":security-group/" in item):
            group_id = arn.rsplit("/", 1)[-1]
            try:
                self.client("ec2").delete_security_group(GroupId=group_id)
            except ClientError as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Delete security group {group_id}")
        for arn in (item for item, service in services.items() if service == "ec2" and ":internet-gateway/" in item):
            igw = arn.rsplit("/", 1)[-1]
            try:
                attachments = (
                    self.client("ec2")
                    .describe_internet_gateways(InternetGatewayIds=[igw])
                    .get("InternetGateways", [{}])[0]
                    .get("Attachments", [])
                )
                for attachment in attachments:
                    vpc_id = attachment.get("VpcId")
                    if vpc_id:
                        self.client("ec2").detach_internet_gateway(InternetGatewayId=igw, VpcId=vpc_id)
                self.client("ec2").delete_internet_gateway(InternetGatewayId=igw)
            except ClientError as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Delete internet gateway {igw}")
        for arn in (item for item, service in services.items() if service == "ec2" and ":vpc/" in item):
            vpc_id = arn.rsplit("/", 1)[-1]
            try:
                self.client("ec2").delete_vpc(VpcId=vpc_id)
            except ClientError as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Delete VPC {vpc_id}")
        for arn in (item for item, service in services.items() if service == "kms"):
            key_id = arn.rsplit("/", 1)[-1]
            try:
                state = self.client("kms").describe_key(KeyId=key_id)["KeyMetadata"].get("KeyState")
                if state not in {"PendingDeletion", "PendingReplicaDeletion"}:
                    self.client("kms").schedule_key_deletion(KeyId=key_id, PendingWindowInDays=7)
            except ClientError as exc:
                _ignore_transient_cleanup_or_raise(exc, action=f"Schedule KMS key deletion {key_id}")

    def _delete_tagged_ecs_service(self, arn: str) -> None:
        # arn:...:service/cluster-name/service-name
        parts = arn.split(":service/", 1)[-1].split("/", 1)
        if len(parts) != 2:
            raise ToolError(f"Cannot parse ECS service ARN for cleanup: {arn}")
        cluster, service = parts
        ecs = self.client("ecs")
        try:
            ecs.update_service(cluster=cluster, service=service, desiredCount=0)
        except ClientError as exc:
            _ignore_transient_cleanup_or_raise(exc, action=f"Scale ECS service {service} to zero")
        try:
            ecs.delete_service(cluster=cluster, service=service, force=True)
        except ClientError as exc:
            _ignore_transient_cleanup_or_raise(exc, action=f"Delete ECS service {service}")

    def _delete_tagged_ecs_cluster(self, arn: str) -> None:
        cluster = arn.rsplit("/", 1)[-1]
        try:
            self.client("ecs").delete_cluster(cluster=cluster)
        except ClientError as exc:
            _ignore_transient_cleanup_or_raise(exc, action=f"Delete ECS cluster {cluster}")

    def _delete_tagged_ecs_task_definition(self, arn: str) -> None:
        # Prefer family:revision from the ARN tail.
        task_def = arn.split(":task-definition/", 1)[-1]
        ecs = self.client("ecs")
        try:
            ecs.deregister_task_definition(taskDefinition=task_def)
        except ClientError as exc:
            _ignore_transient_cleanup_or_raise(exc, action=f"Deregister ECS task definition {task_def}")
        try:
            ecs.delete_task_definitions(taskDefinitions=[task_def])
        except ClientError as exc:
            _ignore_transient_cleanup_or_raise(exc, action=f"Delete ECS task definition {task_def}")

    def _file_asset_residuals(
        self,
        assets: Any,
        residuals: dict[tuple[str, str], ResidualResource],
    ) -> None:
        if not isinstance(assets, dict):
            raise ToolError("CDK file assets must be an object")
        for asset_id, definition in assets.items():
            if not isinstance(asset_id, str) or not isinstance(definition, dict):
                raise ToolError("CDK file asset entry is invalid")
            destinations = definition.get("destinations", {})
            if not isinstance(destinations, dict):
                raise ToolError("CDK file asset destinations must be an object")
            for destination in destinations.values():
                if not isinstance(destination, dict):
                    raise ToolError("CDK file asset destination is invalid")
                bucket = destination.get("bucketName")
                key = destination.get("objectKey")
                if not isinstance(bucket, str) or not isinstance(key, str):
                    raise ToolError("CDK file asset destination is incomplete")
                client = self._asset_client("s3", destination)
                try:
                    client.head_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    code = str(exc.response.get("Error", {}).get("Code", ""))
                    if code in {"404", "NoSuchKey", "NotFound"}:
                        continue
                    raise ToolError(f"Cannot verify retained CDK file asset: {exc}") from exc
                self._record_asset("cdk-bootstrap-s3-asset", asset_id, residuals)

    def _image_asset_residuals(
        self,
        assets: Any,
        residuals: dict[tuple[str, str], ResidualResource],
    ) -> None:
        if not isinstance(assets, dict):
            raise ToolError("CDK image assets must be an object")
        for asset_id, definition in assets.items():
            if not isinstance(asset_id, str) or not isinstance(definition, dict):
                raise ToolError("CDK image asset entry is invalid")
            destinations = definition.get("destinations", {})
            if not isinstance(destinations, dict):
                raise ToolError("CDK image asset destinations must be an object")
            for destination in destinations.values():
                if not isinstance(destination, dict):
                    raise ToolError("CDK image asset destination is invalid")
                repository = destination.get("repositoryName")
                image_tag = destination.get("imageTag")
                if not isinstance(repository, str) or not isinstance(image_tag, str):
                    raise ToolError("CDK image asset destination is incomplete")
                client = self._asset_client("ecr", destination)
                try:
                    client.describe_images(
                        repositoryName=repository,
                        imageIds=[{"imageTag": image_tag}],
                    )
                except ClientError as exc:
                    code = str(exc.response.get("Error", {}).get("Code", ""))
                    if code in {"ImageNotFoundException", "RepositoryNotFoundException"}:
                        continue
                    raise ToolError(f"Cannot verify retained CDK image asset: {exc}") from exc
                self._record_asset("cdk-bootstrap-ecr-asset", asset_id, residuals)

    def _asset_client(self, service: str, destination: dict[str, Any]) -> Any:
        region = destination.get("region")
        role_arn = destination.get("assumeRoleArn")
        if not isinstance(region, str):
            region = self.region
        if isinstance(role_arn, str) and role_arn:
            # CDK asset manifests may leave Fn::Sub tokens unresolved after synth.
            if "${AWS::Partition}" in role_arn:
                partition = self.session.get_partition_for_region(region)
                if not isinstance(partition, str) or not partition:
                    raise ToolError(f"Cannot resolve the AWS partition for asset Region {region}")
                role_arn = role_arn.replace("${AWS::Partition}", partition)
            if "${" in role_arn:
                raise ToolError(f"CDK asset assume-role ARN still contains unresolved tokens: {role_arn}")
        if not isinstance(role_arn, str) or not role_arn:
            kwargs: dict[str, Any] = {"region_name": region}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            return self.session.client(service, **kwargs)
        arguments = {
            "RoleArn": role_arn,
            "RoleSessionName": f"openemr-e2e-audit-{hashlib.sha256(role_arn.encode()).hexdigest()[:8]}",
            "DurationSeconds": 900,
        }
        external_id = destination.get("assumeRoleExternalId")
        if isinstance(external_id, str) and external_id:
            arguments["ExternalId"] = external_id
        credentials = self.client("sts").assume_role(**arguments)["Credentials"]
        session: Any = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=region,
        )
        client_kwargs: dict[str, Any] = {"region_name": region}
        if self.endpoint_url:
            client_kwargs["endpoint_url"] = self.endpoint_url
        return session.client(service, **client_kwargs)

    @staticmethod
    def _record_asset(
        resource_type: str,
        asset_id: str,
        residuals: dict[tuple[str, str], ResidualResource],
    ) -> None:
        identifier_hash = f"sha256:{hashlib.sha256(asset_id.encode('utf-8')).hexdigest()[:12]}"
        residuals[(resource_type, identifier_hash)] = ResidualResource(
            resource_type=resource_type,
            identifier_hash=identifier_hash,
            disposition="shared-content-addressed-asset",
        )

    def _hosted_zone(self, domain: str) -> dict[str, Any]:
        response = self.client("route53", global_service=True).list_hosted_zones_by_name(
            DNSName=domain.rstrip("."),
            MaxItems="2",
        )
        zones = response.get("HostedZones", [])
        normalized = domain.rstrip(".").lower()
        matches = [zone for zone in zones if str(zone.get("Name", "")).rstrip(".").lower() == normalized]
        if not matches:
            raise ToolError(f"No exact Route 53 hosted zone found for {domain}")
        if len(matches) != 1:
            raise ToolError(f"Multiple exact Route 53 hosted zones found for {domain}")
        return dict(matches[0])

    def _assert_dedicated_zone_records(self, zone: dict[str, Any]) -> None:
        zone_id = zone.get("Id")
        if not isinstance(zone_id, str) or not zone_id:
            raise ToolError("Route 53 hosted zone has no valid identifier")
        apex = str(zone.get("Name", "")).rstrip(".").lower()
        if not apex:
            raise ToolError("Route 53 hosted zone has no valid name")
        paginator = self.client("route53", global_service=True).get_paginator("list_resource_record_sets")
        delegation_records: list[str] = []
        unexpected_records: set[str] = set()
        for page in paginator.paginate(HostedZoneId=zone_id):
            for record in page.get("ResourceRecordSets", []):
                record_type = str(record.get("Type", ""))
                record_name = str(record.get("Name", "")).rstrip(".").lower()
                if record_name == apex and record_type in {"NS", "SOA"}:
                    delegation_records.append(record_type)
                else:
                    unexpected_records.add(f"{record_name or 'unknown'}:{record_type or 'unknown'}")
        if unexpected_records or sorted(delegation_records) != ["NS", "SOA"]:
            raise ToolError(
                "Dedicated live E2E hosted zone must contain exactly its apex NS and SOA records"
                + (f"; unexpected records: {', '.join(sorted(unexpected_records))}" if unexpected_records else "")
            )

    def _permission_probes(self) -> list[CheckResult]:
        probes = (
            ("cloudformation-read", "cloudformation", "list_stacks", {"StackStatusFilter": ["CREATE_COMPLETE"]}),
            ("ec2-read", "ec2", "describe_vpcs", {"MaxResults": 5}),
            ("ecs-read", "ecs", "list_clusters", {"maxResults": 5}),
            ("rds-read", "rds", "describe_db_clusters", {"MaxRecords": 20}),
            ("efs-read", "efs", "describe_file_systems", {"MaxItems": 5}),
            ("kms-read", "kms", "list_aliases", {"Limit": 5}),
            ("backup-read", "backup", "list_backup_vaults", {"MaxResults": 5}),
            ("wafv2-read", "wafv2", "list_web_acls", {"Scope": "REGIONAL", "Limit": 5}),
        )
        checks: list[CheckResult] = []
        for name, service, operation, kwargs in probes:
            try:
                getattr(self.client(service), operation)(**kwargs)
            except (BotoCoreError, ClientError) as exc:
                if self.emulated and _is_unsupported_emulator_operation(exc):
                    checks.append(
                        CheckResult(
                            name,
                            "pass",
                            "floci-emulated; operation unsupported by emulator",
                        )
                    )
                    continue
                raise ToolError(f"Required AWS read probe failed ({name}): {exc}") from exc
            checks.append(CheckResult(name, "pass", "API probe succeeded"))
        return checks

    def _write_permission_probes(
        self,
        *,
        caller_arn: str,
        account_id: str,
        bootstrap: dict[str, Any],
    ) -> tuple[CheckResult, ...]:
        if caller_arn.endswith(":root"):
            raise ToolError("Live E2E requires an IAM role or user; root credentials are not supported")
        parameters = {
            str(item.get("ParameterKey")): str(item.get("ParameterValue")) for item in bootstrap.get("Parameters", [])
        }
        qualifier = parameters.get("Qualifier", "hnb659fds")
        if not qualifier.replace("-", "").isalnum():
            raise ToolError("CDK bootstrap qualifier is invalid")
        partition = caller_arn.split(":", 2)[1]
        assumed = 0
        for purpose in _BOOTSTRAP_ROLE_PURPOSES:
            role_arn = (
                f"arn:{partition}:iam::{account_id}:role/cdk-{qualifier}-{purpose}-role-{account_id}-{self.region}"
            )
            try:
                self.client("sts").assume_role(
                    RoleArn=role_arn,
                    RoleSessionName=f"openemr-e2e-{purpose[:20]}",
                    DurationSeconds=900,
                )
            except (BotoCoreError, ClientError) as exc:
                raise ToolError(f"Cannot assume required CDK bootstrap {purpose} role: {exc}") from exc
            assumed += 1

        execution_role_arn = (
            f"arn:{partition}:iam::{account_id}:role/cdk-{qualifier}-cfn-exec-role-{account_id}-{self.region}"
        )
        caller_principal = _iam_principal_arn(caller_arn)
        if self.emulated:
            self._assert_role_exists(execution_role_arn)
            return (
                CheckResult(
                    "cdk-bootstrap-role-assumption",
                    "pass",
                    f"assumed {assumed} required bootstrap roles",
                ),
                CheckResult(
                    "deployment-write-permissions",
                    "pass",
                    "floci-emulated; IAM policy simulation skipped after role existence checks",
                ),
                CheckResult(
                    "local-cleanup-permissions",
                    "pass",
                    "floci-emulated; cleanup principal simulation skipped",
                ),
            )

        self._simulate_actions(
            principal_arn=execution_role_arn,
            actions=_WRITE_ACTIONS,
            label="CDK CloudFormation execution role",
        )
        self._simulate_actions(
            principal_arn=caller_principal,
            actions=_LOCAL_CLEANUP_ACTIONS,
            label="local cleanup principal",
        )

        return (
            CheckResult(
                "cdk-bootstrap-role-assumption",
                "pass",
                f"assumed {assumed} required bootstrap roles",
            ),
            CheckResult(
                "deployment-write-permissions",
                "pass",
                f"{len(_WRITE_ACTIONS)} representative actions allowed for the execution role",
            ),
            CheckResult(
                "local-cleanup-permissions",
                "pass",
                f"{len(_LOCAL_CLEANUP_ACTIONS)} direct cleanup and residual-audit actions allowed",
            ),
        )

    def _assert_role_exists(self, role_arn: str) -> None:
        role_name = role_arn.rsplit("/", 1)[-1]
        try:
            self.client("iam", global_service=True).get_role(RoleName=role_name)
        except (BotoCoreError, ClientError) as exc:
            raise ToolError(f"Required IAM role is missing in the emulator: {role_name}") from exc

    def _simulate_actions(
        self,
        *,
        principal_arn: str,
        actions: tuple[str, ...],
        label: str,
        resource_arns: tuple[str, ...] = (),
    ) -> None:
        arguments: dict[str, Any] = {
            "PolicySourceArn": principal_arn,
            "ActionNames": list(actions),
        }
        if resource_arns:
            arguments["ResourceArns"] = list(resource_arns)
        try:
            response = self.client("iam", global_service=True).simulate_principal_policy(**arguments)
        except (BotoCoreError, ClientError) as exc:
            raise ToolError(f"Cannot simulate {label} permissions: {exc}") from exc
        decisions = {
            str(item.get("EvalActionName")): str(item.get("EvalDecision"))
            for item in response.get("EvaluationResults", [])
        }
        denied = sorted(action for action in actions if decisions.get(action) != "allowed")
        if denied:
            raise ToolError(f"{label} lacks required actions: {', '.join(denied)}")

    def _quota_probes(self) -> list[CheckResult]:
        if self.emulated:
            return [
                CheckResult(
                    f"quota-{name}",
                    "pass",
                    f"floci-emulated; service-quotas unavailable; required-headroom={minimum:g}",
                )
                for name, _service_code, _quota_code, minimum in _QUOTAS
            ]
        checks: list[CheckResult] = []
        client = self.client("service-quotas")
        for name, service_code, quota_code, minimum in _QUOTAS:
            try:
                quota = client.get_service_quota(ServiceCode=service_code, QuotaCode=quota_code)["Quota"]
                value = float(quota.get("Value", 0))
            except (BotoCoreError, ClientError) as exc:
                raise ToolError(f"Cannot verify required service quota {name}: {exc}") from exc
            usage = self._quota_usage(name, quota)
            available = value - usage
            if available < minimum:
                raise ToolError(f"Service quota {name} has {available:g} available; live E2E requires {minimum:g}")
            checks.append(
                CheckResult(
                    f"quota-{name}",
                    "pass",
                    f"configured={value:g}; observed-usage={usage:g}; required-headroom={minimum:g}",
                )
            )
        return checks

    def _quota_usage(self, name: str, quota: dict[str, Any]) -> float:
        if name == "vpc-count":
            paginator = self.client("ec2").get_paginator("describe_vpcs")
            return float(sum(len(page.get("Vpcs", [])) for page in paginator.paginate()))
        if name == "elastic-ip-count":
            return float(len(self.client("ec2").describe_addresses().get("Addresses", [])))
        if name == "rds-clusters":
            paginator = self.client("rds").get_paginator("describe_db_clusters")
            return float(sum(len(page.get("DBClusters", [])) for page in paginator.paginate()))

        metric = quota.get("UsageMetric")
        if not isinstance(metric, dict):
            return 0.0
        namespace = metric.get("MetricNamespace")
        metric_name = metric.get("MetricName")
        dimensions = metric.get("MetricDimensions", {})
        statistic = metric.get("MetricStatisticRecommendation", "Maximum")
        if not isinstance(namespace, str) or not isinstance(metric_name, str) or not isinstance(dimensions, dict):
            return 0.0
        end = datetime.now(timezone.utc)
        response = self.client("cloudwatch").get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=[{"Name": str(key), "Value": str(value)} for key, value in dimensions.items()],
            StartTime=end.replace(microsecond=0) - timedelta(minutes=15),
            EndTime=end,
            Period=300,
            Statistics=[str(statistic)],
        )
        values = [float(point[statistic]) for point in response.get("Datapoints", []) if statistic in point]
        return max(values, default=0.0)

    def _stack_resources(self, stack_id: str) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kwargs = {"StackName": stack_id}
            if token:
                kwargs["NextToken"] = token
            page = self.client("cloudformation").list_stack_resources(**kwargs)
            resources.extend(page.get("StackResourceSummaries", []))
            token = page.get("NextToken")
            if not token:
                return resources

    def _stack_events(self, stack_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kwargs = {"StackName": stack_id}
            if token:
                kwargs["NextToken"] = token
            page = self.client("cloudformation").describe_stack_events(**kwargs)
            events.extend(page.get("StackEvents", []))
            token = page.get("NextToken")
            if not token:
                return events

    def _fatal_startup_log_count(
        self,
        log_group: str,
        *,
        start_time: datetime,
    ) -> int:
        client = self.client("logs")
        token: str | None = None
        matches = 0
        for _ in range(5):
            arguments: dict[str, Any] = {
                "logGroupName": log_group,
                "startTime": int(start_time.timestamp() * 1_000),
                "limit": 10_000,
            }
            if token:
                arguments["nextToken"] = token
            response = client.filter_log_events(**arguments)
            matches += sum(
                1 for event in response.get("events", []) if _FATAL_LOG_PATTERN.search(str(event.get("message", "")))
            )
            next_token = response.get("nextToken")
            if not isinstance(next_token, str) or next_token == token:
                break
            token = next_token
        return matches

    @staticmethod
    def _wait_for_https(
        url: str,
        *,
        timeout_seconds: float,
        poll_seconds: float,
        progress: ProgressReporter | None = None,
    ) -> tuple[float, float]:
        reporter = progress or NULL_PROGRESS
        started = time.monotonic()
        deadline = started + timeout_seconds
        last_error = "no response"
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                # Timeout is dynamically capped at 30 seconds by the shared deadline.
                response = requests.get(
                    url,
                    timeout=max(0.1, min(30.0, remaining)),
                    allow_redirects=True,
                )
                body = response.text[:200_000].lower()
                if response.status_code == 200 and "openemr" in body:
                    reporter.info("HTTPS readiness probe succeeded")
                    return (
                        round(time.monotonic() - started, 3),
                        round(response.elapsed.total_seconds(), 3),
                    )
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = type(exc).__name__
            elapsed = time.monotonic() - started
            minutes, seconds = divmod(int(elapsed), 60)
            reporter.heartbeat(f"HTTPS not ready yet ({last_error}); retrying (elapsed {minutes}m{seconds:02d}s)")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_seconds, remaining))
        raise ToolError(f"OpenEMR HTTPS readiness timed out: {last_error}")


def _required_output(outputs: dict[str, str], name: str) -> str:
    value = outputs.get(name)
    if not value:
        raise ToolError(f"Stack output is missing: {name}")
    return value


def _local_phase(name: str, started: float) -> PhaseTiming:
    return PhaseTiming(
        name,
        round(time.monotonic() - started, 3),
        "local-monotonic-clock",
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _arn_service(arn: str) -> str:
    parts = arn.split(":", 5)
    return parts[2] if len(parts) > 2 and parts[2] else "unknown"


def _iam_principal_arn(caller_arn: str) -> str:
    if ":assumed-role/" in caller_arn:
        prefix, resource = caller_arn.split(":assumed-role/", 1)
        role_name = resource.rsplit("/", 1)[0]
        if not role_name:
            raise ToolError("Cannot derive an IAM principal for local cleanup simulation")
        partition = prefix.split(":")[1]
        account_id = prefix.split(":")[4]
        return f"arn:{partition}:iam::{account_id}:role/{role_name}"
    if ":user/" in caller_arn or ":role/" in caller_arn:
        return caller_arn
    raise ToolError("Cannot derive an IAM principal for local cleanup simulation")


def unexpected_residuals(resources: Iterable[ResidualResource]) -> tuple[ResidualResource, ...]:
    """Return residuals that are not an expected delayed deletion."""

    expected = {"scheduled-deletion-expected", "shared-content-addressed-asset"}
    return tuple(item for item in resources if item.disposition not in expected)
