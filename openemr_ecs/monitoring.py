"""Monitoring infrastructure: CloudWatch alarms and SNS notifications."""

from typing import Optional

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subs
from constructs import Construct


class MonitoringComponents:
    """Creates and manages monitoring infrastructure.

    This class handles:
    - CloudWatch alarms for ECS service health
    - CloudWatch alarms for ALB target health
    - SNS topics for alerting
    - Email subscriptions for notifications
    """

    def __init__(self, scope: Construct):
        """Initialize monitoring components.

        Args:
            scope: The CDK construct scope
        """
        self.scope = scope
        self.alarms_topic: Optional[sns.Topic] = None
        self.deployment_topic: Optional[sns.Topic] = None

    def create_alarms_topic(self, email_address: Optional[str] = None) -> sns.Topic:
        """Create SNS topic for CloudWatch alarms.

        Args:
            email_address: Optional email address to subscribe to alarms

        Returns:
            The created SNS topic
        """
        # Get KMS key for SNS encryption
        kms_key = self.scope.kms_keys.central_key

        self.alarms_topic = sns.Topic(
            self.scope,
            "MonitoringAlarmsTopic",
            display_name="OpenEMR Monitoring Alarms",
            topic_name=f"openemr-monitoring-alarms-{Stack.of(self.scope).stack_name.lower()}",
            master_key=kms_key,
        )

        # Add SSL-only policy to SNS topic
        self.alarms_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowPublishThroughSSLOnly",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["SNS:Publish"],
                resources=[self.alarms_topic.topic_arn],
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            )
        )

        if email_address:
            self.alarms_topic.add_subscription(sns_subs.EmailSubscription(email_address))

        return self.alarms_topic

    def create_deployment_topic(self, email_address: Optional[str] = None) -> sns.Topic:
        """Create SNS topic for deployment events.

        Args:
            email_address: Optional email address to subscribe to deployment notifications

        Returns:
            The created SNS topic
        """
        # Get KMS key for SNS encryption
        kms_key = self.scope.kms_keys.central_key

        self.deployment_topic = sns.Topic(
            self.scope,
            "DeploymentEventsTopic",
            display_name="OpenEMR Deployment Events",
            topic_name=f"openemr-deployment-events-{Stack.of(self.scope).stack_name.lower()}",
            master_key=kms_key,
        )

        # Add SSL-only policy to SNS topic
        self.deployment_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowPublishThroughSSLOnly",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["SNS:Publish"],
                resources=[self.deployment_topic.topic_arn],
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            )
        )

        if email_address:
            self.deployment_topic.add_subscription(sns_subs.EmailSubscription(email_address))

        return self.deployment_topic

    def create_ecs_service_alarms(
        self, service: ecs.FargateService, alarms_topic: Optional[sns.Topic] = None
    ) -> list[cloudwatch.Alarm]:
        """Create CloudWatch alarms for ECS service health.

        Args:
            service: The ECS Fargate service to monitor
            alarms_topic: Optional SNS topic for alarm notifications

        Returns:
            List of created CloudWatch alarms
        """
        alarms = []

        # Alarm for service CPU utilization
        cpu_alarm = cloudwatch.Alarm(
            self.scope,
            "ECSServiceHighCPUAlarm",
            metric=service.metric_cpu_utilization(),
            threshold=85.0,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Alert when ECS service CPU utilization exceeds 85%",
            alarm_name=f"openemr-ecs-high-cpu-{Stack.of(self.scope).stack_name.lower()}",
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )
        alarms.append(cpu_alarm)

        # Alarm for service memory utilization
        memory_alarm = cloudwatch.Alarm(
            self.scope,
            "ECSServiceHighMemoryAlarm",
            metric=service.metric_memory_utilization(),
            threshold=85.0,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Alert when ECS service memory utilization exceeds 85%",
            alarm_name=f"openemr-ecs-high-memory-{Stack.of(self.scope).stack_name.lower()}",
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )
        alarms.append(memory_alarm)

        # Alarm for running task count (service may be down)
        # Construct metric manually as FargateService doesn't have metric_running_task_count()
        running_task_metric = cloudwatch.Metric(
            namespace="AWS/ECS",
            metric_name="RunningTaskCount",
            dimensions_map={
                "ClusterName": service.cluster.cluster_name,
                "ServiceName": service.service_name,
            },
            statistic="Average",
            period=Duration.minutes(1),
        )
        running_tasks_alarm = cloudwatch.Alarm(
            self.scope,
            "ECSServiceLowRunningTasksAlarm",
            metric=running_task_metric,
            threshold=1.0,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            alarm_description="Alert when ECS service has fewer than 1 running task",
            alarm_name=f"openemr-ecs-low-tasks-{Stack.of(self.scope).stack_name.lower()}",
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )
        alarms.append(running_tasks_alarm)

        # Add SNS actions to alarms if topic provided
        if alarms_topic:
            alarm_action = cw_actions.SnsAction(alarms_topic)
            for alarm in alarms:
                alarm.add_alarm_action(alarm_action)
                alarm.add_ok_action(alarm_action)  # Notify when alarm recovers

        return alarms

    def create_alb_health_alarms(
        self,
        target_group: elb.ApplicationTargetGroup,
        load_balancer: elb.ApplicationLoadBalancer,
        alarms_topic: Optional[sns.Topic] = None,
    ) -> list[cloudwatch.Alarm]:
        """Create CloudWatch alarms for ALB target health.

        Args:
            target_group: The ALB target group to monitor
            load_balancer: The application load balancer
            alarms_topic: Optional SNS topic for alarm notifications

        Returns:
            List of created CloudWatch alarms
        """
        alarms = []

        # Alarm for unhealthy target count
        unhealthy_targets_alarm = cloudwatch.Alarm(
            self.scope,
            "ALBUnhealthyTargetsAlarm",
            metric=target_group.metrics.unhealthy_host_count(),
            threshold=1.0,
            evaluation_periods=2,
            datapoints_to_alarm=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Alert when ALB has unhealthy targets",
            alarm_name=f"openemr-alb-unhealthy-targets-{Stack.of(self.scope).stack_name.lower()}",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        alarms.append(unhealthy_targets_alarm)

        # Alarm for high HTTP 5xx error rate
        # Use with() to create a new metric with custom period
        http_5xx_metric = load_balancer.metrics.http_code_target(code=elb.HttpCodeTarget.TARGET_5XX_COUNT).with_(
            period=Duration.minutes(5)
        )
        http_5xx_alarm = cloudwatch.Alarm(
            self.scope,
            "ALBHigh5xxErrorsAlarm",
            metric=http_5xx_metric,
            threshold=10.0,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Alert when ALB returns more than 10 HTTP 5xx errors in 2 periods",
            alarm_name=f"openemr-alb-high-5xx-{Stack.of(self.scope).stack_name.lower()}",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        alarms.append(http_5xx_alarm)

        # Alarm for high response time
        response_time_alarm = cloudwatch.Alarm(
            self.scope,
            "ALBHighResponseTimeAlarm",
            metric=target_group.metrics.target_response_time(),
            threshold=5.0,  # 5 seconds
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Alert when ALB target response time exceeds 5 seconds",
            alarm_name=f"openemr-alb-high-response-time-{Stack.of(self.scope).stack_name.lower()}",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        alarms.append(response_time_alarm)

        # Add SNS actions to alarms if topic provided
        if alarms_topic:
            alarm_action = cw_actions.SnsAction(alarms_topic)
            for alarm in alarms:
                alarm.add_alarm_action(alarm_action)
                alarm.add_ok_action(alarm_action)  # Notify when alarm recovers

        return alarms

    def create_deployment_failure_alarm(
        self, service: ecs.FargateService, alarms_topic: Optional[sns.Topic] = None
    ) -> cloudwatch.Alarm:
        """Create alarm for ECS deployment failures.

        Args:
            service: The ECS Fargate service to monitor
            alarms_topic: Optional SNS topic for alarm notifications

        Returns:
            The created CloudWatch alarm
        """
        # Alarm for deployment failures (tasks stopping unexpectedly)
        # Construct metric manually as FargateService doesn't have metric_stopped_task_count()
        stopped_task_metric = cloudwatch.Metric(
            namespace="AWS/ECS",
            metric_name="StoppedTaskCount",
            dimensions_map={
                "ClusterName": service.cluster.cluster_name,
                "ServiceName": service.service_name,
            },
            statistic="Sum",
            period=Duration.minutes(5),
        )
        deployment_alarm = cloudwatch.Alarm(
            self.scope,
            "ECSDeploymentFailureAlarm",
            metric=stopped_task_metric,
            threshold=2.0,  # More than 2 stopped tasks in 5 minutes
            evaluation_periods=1,
            datapoints_to_alarm=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Alert when ECS service has multiple stopped tasks (possible deployment failure)",
            alarm_name=f"openemr-ecs-deployment-failure-{Stack.of(self.scope).stack_name.lower()}",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        if alarms_topic:
            alarm_action = cw_actions.SnsAction(alarms_topic)
            deployment_alarm.add_alarm_action(alarm_action)
            deployment_alarm.add_ok_action(alarm_action)

        return deployment_alarm
