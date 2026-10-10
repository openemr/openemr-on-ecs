"""Defensive guards in OpenemrEcsStack and its component builders.

These guards protect against component builders or CDK constructs returning
unexpected values. Each test forces exactly one such condition and asserts the
stack refuses to synthesize with a clear error.
"""

from types import SimpleNamespace

import aws_cdk as cdk
import pytest
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_ses as ses

from openemr_ecs import analytics as analytics_module
from openemr_ecs.compute import ComputeComponents
from openemr_ecs.database import DatabaseComponents
from openemr_ecs.security import SecurityComponents
from openemr_ecs.stack import OpenemrEcsStack
from openemr_ecs.storage import StorageComponents

ENV = cdk.Environment(account="123456789012", region="us-west-2")
CERT_ARN = "arn:aws:acm:us-west-2:123456789012:certificate/11111111-2222-3333-4444-555555555555"
BASE_CONTEXT = {
    "route53_domain": None,
    "certificate_arn": CERT_ARN,
    "configure_ses": False,
    "enable_monitoring_alarms": False,
    "create_serverless_analytics_environment": False,
    "enable_global_accelerator": False,
    "enable_bedrock_integration": False,
    "enable_data_api": False,
    "openemr_resource_suffix": "guards",
}
SES_CONTEXT = {"route53_domain": "example.com", "certificate_arn": None, "configure_ses": True}
ANALYTICS_CONTEXT = {"create_serverless_analytics_environment": True}
IMPORT_CONTEXT = {"openemr_import_target": True}


def _build(**context) -> OpenemrEcsStack:
    app = cdk.App(context={**BASE_CONTEXT, **context})
    return OpenemrEcsStack(app, "GuardStack", env=ENV)


def _null_attribute_for(monkeypatch, cls, attribute: str, construct_id: str) -> None:
    """Make ``cls.attribute`` read as None only for the construct with ``construct_id``."""

    original = getattr(cls, attribute)

    def getter(self):
        if self.node.id == construct_id:
            return None
        return original.fget(self)

    monkeypatch.setattr(cls, attribute, property(getter))


class _NotTheL1Type:
    """Replacement type that no synthesized construct is an instance of."""


def test_baseline_guard_configuration_synthesizes():
    stack = _build(**IMPORT_CONTEXT)
    assert stack.import_task_definition is not None
    assert stack.certificate is not None


class TestStackComponentResults:
    def test_invalid_context_is_reported_as_context_validation_failure(self):
        with pytest.raises(ValueError, match="Context validation failed: .*between 1 and 100") as excinfo:
            _build(openemr_service_fargate_cpu_autoscaling_percentage=150)
        assert type(excinfo.value.__cause__).__name__ == "ValidationError"

    def test_dns_failure_is_wrapped_with_domain_guidance(self, monkeypatch):
        failure = RuntimeError("hosted zone not found")

        def fail(self, alb, accelerator, context):
            raise failure

        monkeypatch.setattr(SecurityComponents, "create_dns_and_certificates", fail)
        with pytest.raises(ValueError, match="Failed to create DNS and certificates for domain example.com") as excinfo:
            _build(route53_domain="example.com", certificate_arn=None)
        assert excinfo.value.__cause__ is failure

    def test_missing_certificate_is_rejected(self, monkeypatch):
        monkeypatch.setattr(SecurityComponents, "create_dns_and_certificates", lambda *args: None)
        with pytest.raises(ValueError, match="Certificate is required for HTTPS"):
            _build(route53_domain="example.com", certificate_arn=None)

    def test_non_dict_ses_result_is_rejected(self, monkeypatch):
        monkeypatch.setattr(SecurityComponents, "configure_ses", lambda *args: None)
        with pytest.raises(ValueError, match="SES configuration failed.*NoneType"):
            _build(**SES_CONTEXT)

    @pytest.mark.parametrize(
        ("component", "method", "message"),
        [
            (DatabaseComponents, "create_valkey_cluster", "Failed to create Valkey cluster"),
            (StorageComponents, "create_efs_volumes", "Failed to create EFS volumes"),
            (ComputeComponents, "create_ecs_cluster", "Failed to create ECS cluster"),
        ],
    )
    def test_required_component_failures_are_fatal(self, monkeypatch, component, method, message):
        monkeypatch.setattr(component, method, lambda *args, **kwargs: None)
        with pytest.raises(ValueError, match=message):
            _build()

    def test_missing_database_secret_blocks_maintenance_task(self, monkeypatch):
        original = ComputeComponents.create_openemr_service

        def create_then_drop_secret(self, *args, **kwargs):
            service = original(self, *args, **kwargs)
            self.scope.db_secret = None
            return service

        monkeypatch.setattr(ComputeComponents, "create_openemr_service", create_then_drop_secret)
        with pytest.raises(RuntimeError, match="Database secret is required for maintenance tasks"):
            _build()

    def test_missing_database_secret_blocks_import_task(self, monkeypatch):
        original = ComputeComponents.create_credential_rotation_task

        def create_then_drop_secret(self, *args, **kwargs):
            task = original(self, *args, **kwargs)
            self.scope.db_secret = None
            return task

        monkeypatch.setattr(ComputeComponents, "create_credential_rotation_task", create_then_drop_secret)
        with pytest.raises(RuntimeError, match="Database secret is required for the OpenEMR import task"):
            _build(**IMPORT_CONTEXT)


class TestComputeGuards:
    def test_service_requires_certificate(self, monkeypatch):
        original = ComputeComponents.create_openemr_service

        def without_certificate(self, ecs_cluster, log_group, alb, certificate, *args, **kwargs):
            return original(self, ecs_cluster, log_group, alb, None, *args, **kwargs)

        monkeypatch.setattr(ComputeComponents, "create_openemr_service", without_certificate)
        with pytest.raises(ValueError, match="should have been caught during validation"):
            _build()

    def test_service_requires_cloudformation_service_resource(self, monkeypatch):
        monkeypatch.setattr(ecs, "CfnService", _NotTheL1Type)
        with pytest.raises(RuntimeError, match="OpenEMR service is missing its CloudFormation resource"):
            _build()

    def test_import_task_requires_customer_managed_key(self, monkeypatch):
        original = ComputeComponents.create_openemr_import_task

        def with_unencrypted_bucket(self, *args):
            *leading, _bucket, version = args
            return original(self, *leading, SimpleNamespace(encryption_key=None), version)

        monkeypatch.setattr(ComputeComponents, "create_openemr_import_task", with_unencrypted_bucket)
        with pytest.raises(RuntimeError, match="requires a customer-managed encryption key"):
            _build(**IMPORT_CONTEXT)

    def test_import_task_requires_l1_bucket(self, monkeypatch):
        monkeypatch.setattr(s3, "CfnBucket", _NotTheL1Type)
        with pytest.raises(RuntimeError, match="Import staging bucket requires an L1 bucket resource"):
            _build(**IMPORT_CONTEXT)

    @pytest.mark.parametrize(
        ("construct_id", "context", "message"),
        [
            ("OpenEMRFargateTaskDefinition", {}, "OpenEMR task definition requires an execution role"),
            ("CredentialRotationTaskDefinition", {}, "Credential rotation task requires an execution role"),
            ("OpenEMRImportTaskDefinition", IMPORT_CONTEXT, "OpenEMR import task requires an execution role"),
            ("CreateSSLMaterialsTaskDefinition", {}, "SSL maintenance task requires an execution role"),
            ("SyncEFStoS3Task", ANALYTICS_CONTEXT, "EFS export task requires an execution role"),
        ],
    )
    def test_task_definitions_require_execution_role(self, monkeypatch, construct_id, context, message):
        _null_attribute_for(monkeypatch, ecs.FargateTaskDefinition, "execution_role", construct_id)
        with pytest.raises(RuntimeError, match=message):
            _build(**context)


class TestLambdaRoleGuards:
    @pytest.mark.parametrize(
        ("construct_id", "context", "message"),
        [
            ("StackCleanupLambda", {}, "Cleanup Lambda requires an execution role"),
            ("SMTPSetup", SES_CONTEXT, "SMTP credential Lambda requires an execution role"),
            ("RDStoS3ExportLambda", ANALYTICS_CONTEXT, "RDS export Lambda requires an execution role"),
        ],
    )
    def test_lambdas_require_execution_role(self, monkeypatch, construct_id, context, message):
        _null_attribute_for(monkeypatch, _lambda.Function, "role", construct_id)
        with pytest.raises(RuntimeError, match=message):
            _build(**context)

    def test_efs_export_lambda_requires_execution_role(self, monkeypatch):
        original_suppress = analytics_module.suppress_lambda_role_common_findings

        def suppress_unless_missing(role, **kwargs):
            if role is not None:
                original_suppress(role, **kwargs)

        monkeypatch.setattr(analytics_module, "suppress_lambda_role_common_findings", suppress_unless_missing)
        _null_attribute_for(monkeypatch, _lambda.Function, "role", "EFStoS3ExportLambda")
        with pytest.raises(RuntimeError, match="EFS export Lambda requires an execution role"):
            _build(**ANALYTICS_CONTEXT)


def test_ses_rule_set_requires_cloudformation_resource(monkeypatch):
    monkeypatch.setattr(ses, "CfnReceiptRuleSet", _NotTheL1Type)
    with pytest.raises(RuntimeError, match="SES rule set is missing its CloudFormation resource"):
        _build(**SES_CONTEXT)
