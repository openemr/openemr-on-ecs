import json
from pathlib import Path

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from openemr_ecs.openemr_ecs_stack import OpenemrEcsStack


def create_stack() -> assertions.Template:
    project_root = Path(__file__).resolve().parents[2]
    context = {}
    cdk_json = project_root / "cdk.json"
    if cdk_json.exists():
        context = json.loads(cdk_json.read_text()).get("context", {})

    app = cdk.App(context=context)
    stack = OpenemrEcsStack(
        app,
        "OpenEmrEcsStackUnderTest",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    return assertions.Template.from_stack(stack)


def test_rds_cluster_created():
    template = create_stack()
    rds_clusters = template.find_resources("AWS::RDS::DBCluster")
    assert rds_clusters, "Expected an Aurora DB cluster to be defined"


def test_load_balancer_created():
    template = create_stack()
    load_balancers = template.find_resources("AWS::ElasticLoadBalancingV2::LoadBalancer")
    assert load_balancers, "Expected an Application Load Balancer to be defined"


def test_ecs_cluster_created():
    template = create_stack()
    ecs_clusters = template.find_resources("AWS::ECS::Cluster")
    assert ecs_clusters, "Expected an ECS cluster to be defined"


def test_access_log_bucket_encrypted():
    template = create_stack()
    buckets = template.find_resources("AWS::S3::Bucket")
    assert buckets, "Expected S3 buckets to be defined"

    encrypted_buckets = [
        props
        for props in buckets.values()
        if props.get("Properties", {}).get("BucketEncryption")
    ]
    assert encrypted_buckets, "Expected at least one encrypted bucket for ALB access logs"
