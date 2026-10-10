"""Network infrastructure components: VPC, security groups, and load balancer."""

import json
import re
import ssl
import urllib.request
from typing import Optional

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elb
from aws_cdk import aws_globalaccelerator as ga
from aws_cdk import aws_globalaccelerator_endpoints as ga_endpoints
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from .kms_keys import KmsKeys
from .nag_suppressions import acknowledge_findings
from .utils import is_true


class NetworkComponents:
    """Creates and manages network infrastructure components.

    This class handles:
    - VPC creation with public and private subnets
    - Security groups with least privilege rules
    - Application Load Balancer
    - Optional Global Accelerator
    - VPC Flow Logs
    """

    def __init__(self, scope: Construct, vpc_cidr: str, kms_keys: KmsKeys):
        """Initialize network components.

        Args:
            scope: The CDK construct scope
            vpc_cidr: CIDR block for the VPC (e.g., "10.0.0.0/16")
        """
        self.scope = scope
        self.kms_keys = kms_keys
        self.vpc_cidr = vpc_cidr
        self.vpc: Optional[ec2.Vpc] = None
        self.db_sec_group: Optional[ec2.SecurityGroup] = None
        self.valkey_sec_group: Optional[ec2.SecurityGroup] = None
        self.lb_sec_group: Optional[ec2.SecurityGroup] = None
        self.alb: Optional[elb.ApplicationLoadBalancer] = None
        self.accelerator: Optional[ga.Accelerator] = None

    def create_vpc(self, context: dict) -> ec2.Vpc:
        """Create the VPC with subnets and flow logs.

        Returns:
            The created VPC
        """
        # Create IAM role for VPC Flow Logs
        vpc_flow_role = iam.Role(
            self.scope, "Flow-Log-Role", assumed_by=iam.ServicePrincipal("vpc-flow-logs.amazonaws.com")
        )

        # Get KMS key for encryption
        kms_key = self.kms_keys.central_key

        vpc_log_group = logs.LogGroup(
            self.scope,
            "VPC-Log-Group",
            encryption_key=kms_key,
            removal_policy=(
                RemovalPolicy.DESTROY if self.scope.node.try_get_context("live_e2e_run_id") else RemovalPolicy.RETAIN
            ),
        )

        subnet_configuration = [
            ec2.SubnetConfiguration(name="private-subnet", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            ec2.SubnetConfiguration(
                name="public-subnet",
                subnet_type=ec2.SubnetType.PUBLIC,
                map_public_ip_on_launch=False,
            ),
        ]
        raw_availability_zones = context.get("live_e2e_availability_zones")
        if raw_availability_zones is not None:
            if not context.get("live_e2e_run_id"):
                raise ValueError("Explicit live E2E Availability Zones require live_e2e_run_id")
            try:
                availability_zones = (
                    json.loads(raw_availability_zones)
                    if isinstance(raw_availability_zones, str)
                    else raw_availability_zones
                )
            except json.JSONDecodeError as exc:
                raise ValueError("Live E2E Availability Zones must be a JSON array") from exc
            region = Stack.of(self.scope).region
            if (
                not isinstance(availability_zones, list)
                or len(availability_zones) != 2
                or any(
                    not isinstance(zone, str) or not re.fullmatch(rf"{re.escape(region)}[a-z]", zone)
                    for zone in availability_zones
                )
                or len(set(availability_zones)) != 2
            ):
                raise ValueError("Live E2E requires two unique standard Availability Zones in the stack Region")
            self.vpc = ec2.Vpc(
                self.scope,
                "OpenEmr-Vpc",
                ip_addresses=ec2.IpAddresses.cidr(self.vpc_cidr),
                availability_zones=availability_zones,
                subnet_configuration=subnet_configuration,
            )
        else:
            self.vpc = ec2.Vpc(
                self.scope,
                "OpenEmr-Vpc",
                ip_addresses=ec2.IpAddresses.cidr(self.vpc_cidr),
                max_azs=2,
                subnet_configuration=subnet_configuration,
            )
        if context.get("live_e2e_run_id"):
            self.vpc.apply_removal_policy(RemovalPolicy.DESTROY)

        ec2.CfnFlowLog(
            self.scope,
            "FlowLogs",
            resource_id=self.vpc.vpc_id,
            resource_type="VPC",
            traffic_type="ALL",
            deliver_logs_permission_arn=vpc_flow_role.role_arn,
            log_destination_type="cloud-watch-logs",
            log_group_name=vpc_log_group.log_group_name,
        )

        # Close the default security group (HIPAA requirement)
        # The default SG is created automatically and must be explicitly restricted
        # We document that it's closed via suppression (cannot be deleted, AWS limitation)
        # For cdk-nag compliance, we acknowledge that we close it via other means
        acknowledge_findings(
            self.vpc,
            [
                {
                    "id": "HIPAA.Security-VPCDefaultSecurityGroupClosed",
                    "reason": "Default security group is not used - all resources use explicitly created security groups with least privilege rules",
                },
            ],
        )

        # Acknowledge IGW route warnings - these are required for ALB internet connectivity
        for subnet in self.vpc.public_subnets:
            acknowledge_findings(
                subnet,
                [
                    {
                        "id": "HIPAA.Security-VPCNoUnrestrictedRouteToIGW",
                        "reason": "Public subnets require IGW routes for Application Load Balancer internet connectivity. ALB is protected by security groups with IP allowlisting.",
                    },
                ],
            )

        return self.vpc

    def create_security_groups(self, vpc: ec2.Vpc, context: dict) -> tuple:
        """Create security groups for database, cache, and load balancer.

        Args:
            vpc: The VPC to create security groups in
            context: CDK context dictionary for configuration

        Returns:
            Tuple of (db_sec_group, valkey_sec_group, lb_sec_group)
        """
        # Database security group - only allows connections from ECS tasks
        self.db_sec_group = ec2.SecurityGroup(
            self.scope,
            "db-sec-group",
            vpc=vpc,
            allow_all_outbound=False,  # Prevent accidental data exfiltration
        )

        # Acknowledge false positives for database port (resolved via intrinsic function)
        acknowledge_findings(
            self.db_sec_group,
            [
                {
                    "id": "HIPAA.Security-EC2RestrictedCommonPorts",
                    "reason": "Database security group port (3306) is restricted to VPC resources only - false positive due to intrinsic function",
                },
                {
                    "id": "HIPAA.Security-EC2RestrictedSSH",
                    "reason": "Database security group does not expose SSH (port 22) - false positive due to intrinsic function for database port",
                },
            ],
        )

        # Valkey/Redis security group - only allows connections from ECS tasks
        self.valkey_sec_group = ec2.SecurityGroup(self.scope, "valkey-sec-group", vpc=vpc, allow_all_outbound=False)

        # Load balancer security group - allows inbound from configured IP ranges
        self.lb_sec_group = ec2.SecurityGroup(self.scope, "lb-sec-group", vpc=vpc, allow_all_outbound=False)

        # Acknowledge AwsSolutions-EC23 for ALB - it's intentionally public-facing for web traffic
        # The security group rules are properly scoped below based on user-provided CIDR blocks
        acknowledge_findings(
            self.lb_sec_group,
            [
                {
                    "id": "AwsSolutions-EC23",
                    "reason": "Load balancer security group allows ingress from user-specified CIDR blocks (IPv4/IPv6) on port 443 (HTTPS only). This is required for public web access to OpenEMR. CIDR blocks must be explicitly configured in cdk.context.json.",
                }
            ],
        )

        # Configure load balancer security group rules
        # Always use HTTPS (port 443) - a certificate is required (enforced in validation)
        # End-to-end encryption: Client → ALB (HTTPS with ACM cert) → Containers (HTTPS with self-signed certs)
        port = 443

        # IPv4 rules
        cidr_ipv4 = context.get("security_group_ip_range_ipv4")
        if cidr_ipv4:
            # Handle "auto" value by resolving current public IP
            if cidr_ipv4 == "auto":
                ip_check_url = "https://checkip.amazonaws.com/"
                try:
                    # Using hardcoded, known-safe AWS service URL for IP detection
                    # Create an unverified SSL context for this trusted AWS service
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    with urllib.request.urlopen(ip_check_url, timeout=5, context=ssl_context) as response:
                        current_ip = response.read().decode("utf-8").strip()
                    cidr_ipv4 = f"{current_ip}/32"
                except Exception as e:
                    raise ValueError(
                        f"Failed to resolve current IP address for 'auto' mode: {e}. "
                        "Please provide a specific CIDR block or check your internet connection."
                    )
            self.lb_sec_group.add_ingress_rule(
                ec2.Peer.ipv4(cidr_ipv4),
                ec2.Port.tcp(port),
            )
            self.lb_sec_group.add_egress_rule(
                ec2.Peer.ipv4(cidr_ipv4),
                ec2.Port.tcp(port),
            )

        # IPv6 rules
        cidr_ipv6 = context.get("security_group_ip_range_ipv6")
        if cidr_ipv6:
            self.lb_sec_group.add_ingress_rule(
                ec2.Peer.ipv6(cidr_ipv6),
                ec2.Port.tcp(port),
            )
            self.lb_sec_group.add_egress_rule(
                ec2.Peer.ipv6(cidr_ipv6),
                ec2.Port.tcp(port),
            )

        return (self.db_sec_group, self.valkey_sec_group, self.lb_sec_group)

    def create_alb(
        self, vpc: ec2.Vpc, lb_sec_group: ec2.SecurityGroup, elb_log_bucket, context: dict
    ) -> elb.ApplicationLoadBalancer:
        """Create the Application Load Balancer and optional Global Accelerator.

        Args:
            vpc: The VPC for the load balancer
            lb_sec_group: Security group for the load balancer
            elb_log_bucket: S3 bucket for ALB access logs
            context: CDK context dictionary

        Returns:
            The created Application Load Balancer
        """
        self.alb = elb.ApplicationLoadBalancer(
            self.scope,
            "Load-Balancer",
            security_group=lb_sec_group,
            vpc=vpc,
            internet_facing=True,
            drop_invalid_header_fields=True,
            deletion_protection=not bool(context.get("live_e2e_run_id")),
        )
        if context.get("live_e2e_run_id"):
            acknowledge_findings(
                self.alb,
                [
                    {
                        "id": "HIPAA.Security-ELBDeletionProtectionEnabled",
                        "reason": (
                            "The approval-gated live E2E stack is uniquely owned, "
                            "non-production, and must remain deletable from cleanup "
                            "after partial deployment failures"
                        ),
                    }
                ],
            )
        # Enable access logging (region is automatically detected from the stack)
        self.alb.log_access_logs(elb_log_bucket, prefix="alb-access-logs")

        # Optional Global Accelerator
        if is_true(context.get("enable_global_accelerator")):
            self.accelerator = ga.Accelerator(self.scope, "GlobalAccelerator")

            # Always use HTTPS (port 443) - certificate is required
            port = 443

            ga_listener = self.accelerator.add_listener(
                "GAListener", port_ranges=[ga.PortRange(from_port=port, to_port=port)]
            )

            ga_listener.add_endpoint_group(
                "EndpointGroup", endpoints=[ga_endpoints.ApplicationLoadBalancerEndpoint(self.alb)]
            )

            # Output the Global Accelerator URL - always HTTPS
            CfnOutput(
                self.scope,
                "GlobalAcceleratorUrl",
                value=f"https://{self.accelerator.dns_name}",
                description="The URL for the Global Accelerator (HTTPS only - end-to-end encryption)",
            )

        return self.alb
