"""Tests for openemr_ecs/cleanup.py.

Validates the cleanup Lambda function resource creation, IAM permissions,
timeout, and the custom resource that triggers cleanup during stack deletion.
"""

import aws_cdk.assertions as assertions

from tests.conftest import *  # noqa: F401,F403


class TestCleanupLambda:
    def _find_cleanup_lambda(self, template):
        """Find the cleanup Lambda function in the template."""
        functions = template.find_resources("AWS::Lambda::Function")
        for lid, fn in functions.items():
            code = fn.get("Properties", {}).get("Code", {})
            inline = code.get("ZipFile", "")
            if "Cleanup handler" in inline or "cleanup" in lid.lower() or "StackCleanup" in lid:
                return lid, fn
        return None, None

    def test_cleanup_lambda_exists(self, template):
        lid, fn = self._find_cleanup_lambda(template)
        assert lid is not None, "Cleanup Lambda should exist in the template"

    def test_cleanup_lambda_timeout(self, template):
        """Cleanup Lambda needs extended timeout for backup deletion."""
        lid, fn = self._find_cleanup_lambda(template)
        assert fn is not None
        timeout = fn["Properties"].get("Timeout", 3)
        assert timeout >= 600, "Cleanup Lambda should have at least 10 minute timeout"

    def test_cleanup_lambda_handler(self, template):
        lid, fn = self._find_cleanup_lambda(template)
        assert fn is not None
        assert fn["Properties"]["Handler"] == "index.handler"

    def test_cleanup_lambda_inline_code_contains_key_operations(self, template):
        """The inline Lambda code must handle RDS, SES, backup, and SageMaker cleanup."""
        lid, fn = self._find_cleanup_lambda(template)
        assert fn is not None
        code = fn["Properties"]["Code"]["ZipFile"]
        assert "rds" in code.lower() or "ModifyDBCluster" in code or "rds_client" in code
        assert "ses" in code.lower() or "set_active_receipt_rule_set" in code
        assert "backup" in code.lower() or "delete_recovery_point" in code
        assert "sagemaker" in code.lower() or "describe_domain" in code

    def test_cleanup_lambda_sends_cfn_response(self, template):
        """Inline code must call send_response for CloudFormation."""
        lid, fn = self._find_cleanup_lambda(template)
        assert fn is not None
        code = fn["Properties"]["Code"]["ZipFile"]
        assert "send_response" in code

    def test_cleanup_lambda_handles_delete_request_type(self, template):
        lid, fn = self._find_cleanup_lambda(template)
        assert fn is not None
        code = fn["Properties"]["Code"]["ZipFile"]
        assert "request_type == 'Delete'" in code or "RequestType" in code


class TestCleanupIAMPermissions:
    def test_cleanup_lambda_has_rds_permissions(self, template):
        policies = template.find_resources("AWS::IAM::Policy")
        found = False
        for _lid, pol in policies.items():
            doc = pol.get("Properties", {}).get("PolicyDocument", {})
            for stmt in doc.get("Statement", []):
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                if "rds:ModifyDBCluster" in actions:
                    found = True
        assert found, "Cleanup Lambda needs rds:ModifyDBCluster"

    def test_cleanup_lambda_has_ses_permissions(self, template):
        policies = template.find_resources("AWS::IAM::Policy")
        found = False
        for _lid, pol in policies.items():
            doc = pol.get("Properties", {}).get("PolicyDocument", {})
            for stmt in doc.get("Statement", []):
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                if "ses:SetActiveReceiptRuleSet" in actions:
                    found = True
        assert found, "Cleanup Lambda needs ses:SetActiveReceiptRuleSet"

    def test_cleanup_lambda_has_backup_permissions(self, template):
        policies = template.find_resources("AWS::IAM::Policy")
        found = False
        for _lid, pol in policies.items():
            doc = pol.get("Properties", {}).get("PolicyDocument", {})
            for stmt in doc.get("Statement", []):
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                if "backup:DeleteRecoveryPoint" in actions:
                    found = True
        assert found, "Cleanup Lambda needs backup:DeleteRecoveryPoint"

    def test_cleanup_lambda_has_efs_permissions(self, template):
        policies = template.find_resources("AWS::IAM::Policy")
        found = False
        for _lid, pol in policies.items():
            doc = pol.get("Properties", {}).get("PolicyDocument", {})
            for stmt in doc.get("Statement", []):
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                if "elasticfilesystem:DeleteFileSystem" in actions:
                    found = True
        assert found, "Cleanup Lambda needs elasticfilesystem:DeleteFileSystem"


class TestCleanupCustomResource:
    def test_custom_resource_exists(self, template):
        custom_resources = template.find_resources("AWS::CloudFormation::CustomResource")
        assert len(custom_resources) >= 1, "Stack should have a cleanup custom resource"

    def test_custom_resource_has_stack_name(self, template):
        custom_resources = template.find_resources("AWS::CloudFormation::CustomResource")
        found = False
        for _lid, cr in custom_resources.items():
            props = cr.get("Properties", {})
            if "StackName" in props:
                found = True
        assert found, "Custom resource should pass StackName property"
