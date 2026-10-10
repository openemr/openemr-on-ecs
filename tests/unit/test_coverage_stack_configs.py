"""Synthesized-template tests for context-driven OpenemrEcsStack variants.

Each variant is synthesized once per module and shared by the tests that
inspect it, because full-stack synthesis dominates test runtime.
"""

import json

import aws_cdk as cdk
import aws_cdk.assertions as assertions
import pytest

from openemr_ecs.stack import OpenemrEcsStack

ENV = cdk.Environment(account="123456789012", region="us-west-2")
CERT_ARN = "arn:aws:acm:us-west-2:123456789012:certificate/11111111-2222-3333-4444-555555555555"
BASE_CONTEXT = {
    "route53_domain": None,
    "certificate_arn": None,
    "configure_ses": False,
    "enable_monitoring_alarms": False,
    "create_serverless_analytics_environment": False,
    "enable_global_accelerator": False,
    "enable_bedrock_integration": False,
    "enable_data_api": False,
}
TIMEOUTS = {
    "net_read_timeout": "31000",
    "net_write_timeout": "32000",
    "wait_timeout": "33000",
    "connect_timeout": "34",
    "max_execution_time": "3500000",
}
LIVE_RUN_ID = "cov-run-123"
HOSTED_ZONE_ID = "Z0123456789ABCDEFGHIJ"


def _build(context: dict, stack_id: str) -> OpenemrEcsStack:
    app = cdk.App(context={**BASE_CONTEXT, **context})
    return OpenemrEcsStack(app, stack_id, env=ENV)


def _synth(context: dict, stack_id: str) -> assertions.Template:
    return assertions.Template.from_stack(_build(context, stack_id))


def _acknowledged_rules(construct) -> set[str]:
    return {
        rule_id
        for entry in construct.node.metadata
        if entry.type == cdk.Validations.ACKNOWLEDGED_RULES_METADATA_KEY
        for rule_id in entry.data
    }


def _resources_with_id(template: assertions.Template, resource_type: str, fragment: str) -> dict:
    return {
        logical_id: resource
        for logical_id, resource in template.find_resources(resource_type).items()
        if fragment in logical_id
    }


def _openemr_container(template: assertions.Template) -> dict:
    for task in template.find_resources("AWS::ECS::TaskDefinition").values():
        for container in task["Properties"]["ContainerDefinitions"]:
            if container.get("Name") == "openemr" and container.get("WorkingDirectory"):
                return container
    raise AssertionError("OpenEMR service container not found")


def _ssm_parameters(template: assertions.Template) -> dict:
    return {
        resource["Properties"]["Name"]: resource["Properties"]["Value"]
        for resource in template.find_resources("AWS::SSM::Parameter").values()
        if isinstance(resource["Properties"].get("Name"), str)
    }


# ---------------------------------------------------------------------------
# Certificate ARN + Global Accelerator + APIs + portal + import target
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def featured_template() -> assertions.Template:
    return _synth(
        {
            "certificate_arn": CERT_ARN,
            "enable_global_accelerator": True,
            "activate_openemr_apis": True,
            "enable_patient_portal": True,
            "enable_stack_termination_protection": True,
            "openemr_import_target": True,
            "openemr_resource_suffix": "covsuffix",
            "enable_bedrock_integration": True,
            "aurora_ml_inference_timeout": "45000",
            **TIMEOUTS,
        },
        "CoverageFeaturedStack",
    )


def test_certificate_arn_takes_precedence_over_route53_domain(capsys):
    template = _synth({"route53_domain": "example.com", "certificate_arn": CERT_ARN}, "CoverageBothCertStack")

    assert "WARNING: Both route53_domain and certificate_arn provided" in capsys.readouterr().out
    template.resource_count_is("AWS::CertificateManager::Certificate", 0)
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::Listener",
        {"Certificates": [{"CertificateArn": CERT_ARN}], "Protocol": "HTTPS"},
    )


class TestFeaturedStack:
    def test_provided_resource_suffix_is_used_in_construct_ids(self, featured_template):
        clusters = featured_template.find_resources("AWS::ECS::Cluster")
        assert len(clusters) == 1
        assert "covsuffix" in next(iter(clusters))

    def test_certificate_arn_is_imported_not_created(self, featured_template):
        featured_template.resource_count_is("AWS::CertificateManager::Certificate", 0)
        featured_template.resource_count_is("AWS::Route53::RecordSet", 0)
        featured_template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::Listener",
            {"Certificates": [{"CertificateArn": CERT_ARN}], "Protocol": "HTTPS"},
        )

    def test_application_url_uses_global_accelerator_dns(self, featured_template):
        accelerator_id = next(iter(featured_template.find_resources("AWS::GlobalAccelerator::Accelerator")))
        app_url = featured_template.to_json()["Outputs"]["ApplicationURL"]["Value"]
        assert app_url == {"Fn::Join": ["", ["https://", {"Fn::GetAtt": [accelerator_id, "DnsName"]}]]}

    def test_api_and_portal_parameters_are_created(self, featured_template):
        accelerator_id = next(iter(featured_template.find_resources("AWS::GlobalAccelerator::Accelerator")))
        parameters = _ssm_parameters(featured_template)
        assert parameters["activate_fhir_service"] == "1"
        assert parameters["activate_rest_api"] == "1"
        assert parameters["portal_onsite_two_enable"] == "1"
        assert parameters["ccda_alt_service_enable"] == "3"
        assert parameters["rest_portal_api"] == "1"
        dns = {"Fn::GetAtt": [accelerator_id, "DnsName"]}
        assert parameters["site_addr_oath"] == {"Fn::Join": ["", ["https://", dns]]}
        assert parameters["portal_onsite_two_address"] == {"Fn::Join": ["", ["https://", dns, "/portal/"]]}

    def test_openemr_container_receives_api_and_portal_settings(self, featured_template):
        secret_names = {secret["Name"] for secret in _openemr_container(featured_template)["Secrets"]}
        assert {
            "OPENEMR_SETTING_site_addr_oath",
            "OPENEMR_SETTING_rest_api",
            "OPENEMR_SETTING_rest_fhir_api",
            "OPENEMR_SETTING_portal_onsite_two_address",
            "OPENEMR_SETTING_portal_onsite_two_enable",
            "OPENEMR_SETTING_ccda_alt_service_enable",
            "OPENEMR_SETTING_rest_portal_api",
        } <= secret_names
        assert not any(name.startswith("OPENEMR_SETTING_SMTP") for name in secret_names)

    def test_termination_protection_custom_resource(self, featured_template):
        lambdas = _resources_with_id(featured_template, "AWS::Lambda::Function", "EnableTerminationProtectionLambda")
        assert len(lambdas) == 1
        function = next(iter(lambdas.values()))["Properties"]
        assert function["Runtime"] == "python3.14"
        assert function["Timeout"] == 30
        assert "update_termination_protection" in function["Code"]["ZipFile"]
        custom = _resources_with_id(featured_template, "AWS::CloudFormation::CustomResource", "TerminationProtection")
        assert len(custom) == 1
        assert next(iter(custom.values()))["Properties"]["StackName"] == "CoverageFeaturedStack"
        policies = json.dumps(featured_template.find_resources("AWS::IAM::Policy"))
        assert "cloudformation:UpdateTerminationProtection" in policies
        assert "stack/CoverageFeaturedStack/*" in policies
        featured_template.has_output("StackTerminationProtection", {"Value": "ENABLED"})

    def test_parameter_group_includes_timeouts_and_bedrock_settings(self, featured_template):
        groups = featured_template.find_resources("AWS::RDS::DBClusterParameterGroup")
        (group,) = groups.values()
        parameters = group["Properties"]["Parameters"]
        for key, value in TIMEOUTS.items():
            assert parameters[key] == value
        assert parameters["aurora_ml_inference_timeout"] == "45000"
        assert "Fn::GetAtt" in parameters["aws_default_bedrock_role"]
        rendered = json.dumps(featured_template.find_resources("AWS::IAM::Policy"))
        assert "bedrock:InvokeModel" in rendered

    def test_import_staging_bucket_enforces_explicit_kms_key(self, featured_template):
        policies = [
            statement
            for policy in featured_template.find_resources("AWS::S3::BucketPolicy").values()
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        ]
        by_sid = {statement.get("Sid"): statement for statement in policies if statement.get("Sid")}
        no_kms = by_sid["DenyImportObjectsWithoutExplicitKmsEncryption"]
        assert no_kms["Effect"] == "Deny"
        assert no_kms["Condition"] == {"StringNotEquals": {"s3:x-amz-server-side-encryption": "aws:kms"}}
        other_key = by_sid["DenyImportObjectsEncryptedWithAnotherKey"]
        assert other_key["Effect"] == "Deny"
        assert "s3:x-amz-server-side-encryption-aws-kms-key-id" in other_key["Condition"]["StringNotEquals"]
        featured_template.has_resource_properties(
            "AWS::S3::Bucket",
            {
                "LoggingConfiguration": assertions.Match.object_like({"LogFilePrefix": "import-staging-access/"}),
                "LifecycleConfiguration": {
                    "Rules": assertions.Match.array_with(
                        [assertions.Match.object_like({"Id": "CleanImportLockHistory", "Prefix": "locks/"})]
                    )
                },
            },
        )

    def test_import_access_point_uses_root_with_apache_group(self, featured_template):
        featured_template.has_resource_properties(
            "AWS::EFS::AccessPoint",
            {"PosixUser": {"Uid": "0", "Gid": "101"}, "RootDirectory": {"Path": "/"}},
        )

    def test_import_task_has_scoped_runtime_permissions(self, featured_template):
        (task,) = [
            resource["Properties"]
            for resource in featured_template.find_resources("AWS::ECS::TaskDefinition").values()
            if resource["Properties"]["ContainerDefinitions"][0]["Name"] == "openemr-import"
        ]
        container = task["ContainerDefinitions"][0]
        assert {secret["Name"] for secret in container["Secrets"]} == {
            "MYSQL_HOST",
            "MYSQL_PORT",
            "MYSQL_USERNAME",
            "MYSQL_PASSWORD",
        }
        assert container["MountPoints"] == [
            {"ContainerPath": "/mnt/openemr-sites", "ReadOnly": False, "SourceVolume": "SitesFolderVolume"}
        ]
        rendered = json.dumps(featured_template.find_resources("AWS::IAM::Policy"))
        assert "migrations/*/source.tar" in rendered
        assert "migrations/*/status.json" in rendered
        assert "elasticfilesystem:AccessPointArn" in rendered

    def test_import_and_operational_outputs(self, featured_template):
        outputs = featured_template.to_json()["Outputs"]
        assert {
            "OpenEMRImportTaskDefinitionArn",
            "OpenEMRImportStagingBucketName",
            "OpenEMRImportSecurityGroupId",
            "OpenEMRImportEfsAccessPointId",
            "PrivateSubnetIds",
            "DatabaseClusterArn",
            "OpenEMRVersion",
        } <= set(outputs)
        assert outputs["OpenEMRImportTargetMode"]["Value"] == "fresh-target-only"
        assert "LiveE2ERunId" not in outputs


# ---------------------------------------------------------------------------
# Guarded Floci live-E2E synthesis
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_emulated_stack() -> OpenemrEcsStack:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OPENEMR_FLOCI_E2E", "1")
        mp.delenv("OPENEMR_AWS_ENDPOINT_URL", raising=False)
        mp.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        return _build(
            {
                "route53_domain": "example.com",
                "route53_hosted_zone_id": HOSTED_ZONE_ID,
                "live_e2e_run_id": LIVE_RUN_ID,
                "live_e2e_availability_zones": '["us-west-2a", "us-west-2c"]',
                "live_e2e_emulated": "true",
            },
            "CoverageLiveEmulatedStack",
        )


@pytest.fixture(scope="module")
def live_emulated_template(live_emulated_stack) -> assertions.Template:
    return assertions.Template.from_stack(live_emulated_stack)


class TestLiveEmulatedStack:
    def test_resources_are_tagged_with_run_ownership(self, live_emulated_template):
        live_emulated_template.has_resource_properties(
            "AWS::EC2::VPC",
            {
                "Tags": assertions.Match.array_with(
                    [
                        {"Key": "LiveE2ERunId", "Value": LIVE_RUN_ID},
                        {"Key": "Purpose", "Value": "OpenEMRLiveE2E"},
                    ]
                )
            },
        )
        live_emulated_template.has_output("LiveE2ERunId", {"Value": LIVE_RUN_ID})
        outputs = live_emulated_template.to_json()["Outputs"]
        assert {"DatabaseClusterArn", "OpenEMRVersion"} <= set(outputs)
        assert "OpenEMRImportTargetMode" not in outputs

    def test_vpc_uses_runner_validated_availability_zones(self, live_emulated_template):
        zones = {
            subnet["Properties"]["AvailabilityZone"]
            for subnet in live_emulated_template.find_resources("AWS::EC2::Subnet").values()
        }
        assert zones == {"us-west-2a", "us-west-2c"}
        live_emulated_template.has_resource("AWS::EC2::VPC", {"DeletionPolicy": "Delete"})

    def test_backup_is_omitted_under_floci(self, live_emulated_stack, live_emulated_template):
        live_emulated_template.resource_count_is("AWS::Backup::BackupPlan", 0)
        live_emulated_template.resource_count_is("AWS::Backup::BackupVault", 0)
        assert "BackupVaultName" not in live_emulated_template.to_json()["Outputs"]
        assert live_emulated_stack.backup_vault is None
        for file_system in (
            live_emulated_stack.file_system_for_sites_folder,
            live_emulated_stack.file_system_for_ssl_folder,
        ):
            assert "HIPAA.Security-EFSInBackupPlan" in _acknowledged_rules(file_system)

    def test_one_time_ssl_trigger_is_omitted_but_schedule_kept(self, live_emulated_stack, live_emulated_template):
        assert live_emulated_stack.one_time_create_ssl_materials_lambda is None
        assert not _resources_with_id(live_emulated_template, "AWS::Lambda::Function", "OneTimeSSLSetup")
        assert _resources_with_id(live_emulated_template, "AWS::Lambda::Function", "MaintainSSLMaterialsLambda")
        assert _resources_with_id(live_emulated_template, "AWS::Events::Rule", "RegularScheduleforSSLMaintenance")

    def test_dns_uses_preflighted_hosted_zone_id(self, live_emulated_template):
        live_emulated_template.has_resource_properties(
            "AWS::Route53::RecordSet",
            {"HostedZoneId": HOSTED_ZONE_ID, "Name": "openemr.example.com.", "Type": "A"},
        )
        live_emulated_template.has_resource_properties(
            "AWS::CertificateManager::Certificate",
            {"DomainName": "*.example.com"},
        )

    def test_stack_is_fully_deletable(self, live_emulated_stack, live_emulated_template):
        live_emulated_template.has_resource_properties(
            "AWS::RDS::DBCluster", assertions.Match.object_like({"DeletionProtection": False})
        )
        live_emulated_template.has_resource("AWS::RDS::DBCluster", {"DeletionPolicy": "Delete"})
        live_emulated_template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            {
                "LoadBalancerAttributes": assertions.Match.array_with(
                    [{"Key": "deletion_protection.enabled", "Value": "false"}]
                )
            },
        )
        assert "HIPAA.Security-ELBDeletionProtectionEnabled" in _acknowledged_rules(live_emulated_stack.alb)

    def test_ssm_parameters_are_scoped_to_run(self, live_emulated_template):
        names = set(_ssm_parameters(live_emulated_template))
        assert "swarm_mode" not in names
        assert any(name.startswith("swarm_mode_") for name in names)
        assert any(name.startswith("mysql_port_") for name in names)
