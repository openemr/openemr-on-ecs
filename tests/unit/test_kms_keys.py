"""Tests for openemr_ecs/kms_keys.py.

Validates KMS key creation, key rotation settings, resource policies for
CloudWatch Logs, SNS, Secrets Manager, S3, and CloudTrail.
"""

from tests.conftest import *  # noqa: F401,F403


class TestCentralKey:
    def test_central_kms_key_created(self, template):
        """At least one KMS key should exist (the central key)."""
        keys = template.find_resources("AWS::KMS::Key")
        assert len(keys) >= 1

    def test_key_rotation_enabled(self, template):
        template.has_resource_properties(
            "AWS::KMS::Key",
            {"EnableKeyRotation": True},
        )

    def test_central_key_allows_cloudwatch_logs(self, template):
        """The central key policy should grant access to logs.<region>.amazonaws.com."""
        keys = template.find_resources("AWS::KMS::Key")
        found_logs_policy = False
        for _lid, key in keys.items():
            policy = key.get("Properties", {}).get("KeyPolicy", {})
            statements = policy.get("Statement", [])
            for stmt in statements:
                principals = stmt.get("Principal", {})
                service = principals.get("Service", "")
                if isinstance(service, dict):
                    service = str(service)
                if "logs" in str(service).lower():
                    found_logs_policy = True
                    assert "kms:Encrypt" in stmt.get("Action", [])
                    assert "kms:Decrypt" in stmt.get("Action", [])
        assert found_logs_policy, "Central key must allow CloudWatch Logs service"

    def test_central_key_allows_sns(self, template):
        keys = template.find_resources("AWS::KMS::Key")
        found = False
        for _lid, key in keys.items():
            policy = key.get("Properties", {}).get("KeyPolicy", {})
            for stmt in policy.get("Statement", []):
                principal = stmt.get("Principal", {})
                svc = principal.get("Service", "")
                if "sns.amazonaws.com" in str(svc):
                    found = True
                    assert "kms:Decrypt" in stmt.get("Action", [])
        assert found, "Central key must allow SNS service"

    def test_central_key_allows_secrets_manager(self, template):
        keys = template.find_resources("AWS::KMS::Key")
        found = False
        for _lid, key in keys.items():
            policy = key.get("Properties", {}).get("KeyPolicy", {})
            for stmt in policy.get("Statement", []):
                principal = stmt.get("Principal", {})
                svc = principal.get("Service", "")
                if "secretsmanager.amazonaws.com" in str(svc):
                    found = True
        assert found, "Central key must allow Secrets Manager service"


class TestS3Key:
    def test_multiple_kms_keys(self, template):
        """Should have at least 2 KMS keys (central + S3-specific)."""
        keys = template.find_resources("AWS::KMS::Key")
        assert len(keys) >= 2

    def test_s3_key_allows_s3_service(self, template):
        keys = template.find_resources("AWS::KMS::Key")
        found = False
        for _lid, key in keys.items():
            policy = key.get("Properties", {}).get("KeyPolicy", {})
            for stmt in policy.get("Statement", []):
                principal = stmt.get("Principal", {})
                svc = principal.get("Service", "")
                if "s3.amazonaws.com" in str(svc):
                    found = True
        assert found, "S3-specific key must allow s3.amazonaws.com"

    def test_s3_key_allows_cloudtrail(self, template):
        keys = template.find_resources("AWS::KMS::Key")
        found = False
        for _lid, key in keys.items():
            policy = key.get("Properties", {}).get("KeyPolicy", {})
            for stmt in policy.get("Statement", []):
                principal = stmt.get("Principal", {})
                svc = principal.get("Service", "")
                if "cloudtrail.amazonaws.com" in str(svc):
                    found = True
        assert found, "S3 key must allow CloudTrail service"

    def test_all_keys_have_rotation(self, template):
        keys = template.find_resources("AWS::KMS::Key")
        for lid, key in keys.items():
            props = key.get("Properties", {})
            assert props.get("EnableKeyRotation") is True, f"Key {lid} must have rotation enabled"

    def test_all_keys_have_destroy_removal(self, template):
        keys = template.find_resources("AWS::KMS::Key")
        for lid, key in keys.items():
            assert key.get("DeletionPolicy") == "Delete", f"Key {lid} should have DESTROY removal policy"
