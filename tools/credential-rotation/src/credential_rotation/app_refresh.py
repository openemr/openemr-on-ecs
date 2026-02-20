"""Application refresh strategy helpers."""

from __future__ import annotations

import boto3


def force_new_ecs_deployment(region: str, cluster_name: str, service_name: str) -> None:
    """Force a rolling ECS deployment and wait until services are stable.

    OpenEMR containers need several minutes to start (certificate downloads,
    database connectivity checks, initial setup). Combined with drain time for
    old tasks, the full rolling deployment can take 15-25 minutes.
    """

    ecs = boto3.client("ecs", region_name=region)
    ecs.update_service(cluster=cluster_name, service=service_name, forceNewDeployment=True)
    waiter = ecs.get_waiter("services_stable")
    waiter.wait(
        cluster=cluster_name,
        services=[service_name],
        WaiterConfig={"Delay": 15, "MaxAttempts": 120},
    )
