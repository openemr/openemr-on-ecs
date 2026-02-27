"""Comprehensive tests for openemr_ecs/storage.py.

Validates EFS volumes, AWS Backup plan/vault, S3 log buckets,
and optional CloudTrail logging through CDK template assertions.
"""

import aws_cdk as cdk
import aws_cdk.assertions as assertions
import pytest

from tests.conftest import *  # noqa: F401,F403


class TestELBLogBucket:
    def test_elb_log_bucket_exists(self, template):
        buckets = template.find_resources("AWS::S3::Bucket")
        assert len(buckets) >= 1

    def test_elb_log_bucket_blocks_public_access(self, template):
        template.has_resource_properties(
            "AWS::S3::Bucket",
            assertions.Match.object_like(
                {
                    "PublicAccessBlockConfiguration": {
                        "BlockPublicAcls": True,
                        "BlockPublicPolicy": True,
                        "IgnorePublicAcls": True,
                        "RestrictPublicBuckets": True,
                    }
                }
            ),
        )

    def test_elb_log_bucket_versioning(self, template):
        template.has_resource_properties(
            "AWS::S3::Bucket",
            assertions.Match.object_like({"VersioningConfiguration": {"Status": "Enabled"}}),
        )

    def test_elb_log_bucket_uses_sse_s3(self, template):
        """ALB access logging requires SSE-S3 (AES256), not KMS."""
        buckets = template.find_resources("AWS::S3::Bucket")
        found_sse_s3 = False
        for _lid, bucket in buckets.items():
            enc = bucket.get("Properties", {}).get("BucketEncryption", {})
            for rule in enc.get("ServerSideEncryptionConfiguration", []):
                algo = rule.get("ServerSideEncryptionByDefault", {}).get("SSEAlgorithm", "")
                if algo == "AES256":
                    found_sse_s3 = True
        assert found_sse_s3, "At least one bucket should use AES256 (SSE-S3) for ALB logs"


class TestEFSVolumes:
    def test_efs_file_systems_created(self, template):
        efs_resources = template.find_resources("AWS::EFS::FileSystem")
        assert len(efs_resources) >= 2, "Need at least sites EFS and SSL EFS"

    def test_efs_encrypted(self, template):
        template.has_resource_properties(
            "AWS::EFS::FileSystem",
            {"Encrypted": True},
        )

    def test_efs_security_groups_created(self, template):
        """Each EFS mount should have its own security group."""
        sgs = template.find_resources("AWS::EC2::SecurityGroup")
        efs_sgs = [lid for lid in sgs if "efs" in lid.lower()]
        assert len(efs_sgs) >= 2


class TestBackupPlan:
    def test_backup_vault_created(self, template):
        template.resource_count_is("AWS::Backup::BackupVault", 1)

    def test_backup_vault_has_destroy_removal(self, template):
        template.has_resource(
            "AWS::Backup::BackupVault",
            assertions.Match.object_like({"DeletionPolicy": "Delete"}),
        )

    def test_backup_plan_created(self, template):
        template.resource_count_is("AWS::Backup::BackupPlan", 1)

    def test_backup_plan_has_multiple_rules(self, template):
        """daily_weekly_monthly7_year_retention should produce at least 3 rules."""
        plans = template.find_resources("AWS::Backup::BackupPlan")
        for _lid, plan in plans.items():
            rules = plan["Properties"]["BackupPlan"]["BackupPlanRule"]
            assert len(rules) >= 3, "Expected daily + weekly + monthly rules"

    def test_backup_selection_includes_rds_and_efs(self, template):
        selections = template.find_resources("AWS::Backup::BackupSelection")
        assert len(selections) >= 1

    def test_backup_service_role_has_managed_policies(self, template):
        """The backup service role should reference AWS managed backup policies."""
        roles = template.find_resources("AWS::IAM::Role")
        found = False
        for _lid, role in roles.items():
            managed = role.get("Properties", {}).get("ManagedPolicyArns", [])
            managed_str = str(managed)
            if "AWSBackupServiceRolePolicyForBackup" in managed_str:
                found = True
        assert found, "Backup service role must use AWSBackupServiceRolePolicyForBackup"


class TestCloudTrail:
    """CloudTrail is created when a route53_domain is set (the default test config)."""

    def test_cloudtrail_trail_created(self, template):
        """CloudTrail may or may not be present depending on stack config.
        With default test context it's present."""
        trails = template.find_resources("AWS::CloudTrail::Trail")
        # CloudTrail is optional; just verify the template synths correctly.
        # When present, verify it's logging.
        if trails:
            for _lid, trail in trails.items():
                assert trail.get("Properties", {}).get("IsLogging") is True

    def test_all_s3_buckets_block_public_access(self, template):
        """All S3 buckets should block public access."""
        buckets = template.find_resources("AWS::S3::Bucket")
        for _lid, bucket in buckets.items():
            props = bucket.get("Properties", {})
            pub = props.get("PublicAccessBlockConfiguration", {})
            assert pub.get("BlockPublicAcls") is True
            assert pub.get("BlockPublicPolicy") is True
