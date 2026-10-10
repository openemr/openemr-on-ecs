#!/usr/bin/env python3
"""CDK application entry point for the OpenEMR on AWS Fargate deployment."""

import hashlib
import json
import os
import re
import sys

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks, HIPAASecurityChecks

from openemr_ecs.stack import OpenemrEcsStack

LIVE_E2E_RUNNER_ENVIRONMENT = "OPENEMR_LIVE_E2E_RUNNER_RUN_ID"


def stack_id_for_live_e2e(live_e2e_run_id: object | None) -> str:
    """Return the normal or isolated live-E2E CloudFormation stack name."""

    if live_e2e_run_id is None:
        return "OpenemrEcsStack"
    run_id = str(live_e2e_run_id)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{5,47}", run_id):
        raise ValueError("live_e2e_run_id must be 6-48 lowercase letters, digits, or hyphens")
    suffix = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    return f"OpenemrE2E-{suffix}"


def assert_live_e2e_runner_context(live_e2e_run_id: object | None) -> None:
    """Reject accidental live-E2E context outside the guarded local runner."""

    if live_e2e_run_id is None:
        return
    run_id = str(live_e2e_run_id)
    if os.environ.get(LIVE_E2E_RUNNER_ENVIRONMENT) != run_id:
        raise ValueError(
            f"live_e2e_run_id is reserved for tools.live_e2e; {LIVE_E2E_RUNNER_ENVIRONMENT} must match the run ID"
        )


def bind_live_e2e_availability_zone_context(
    app: cdk.App,
    *,
    live_e2e_run_id: object | None,
    raw_availability_zones: object | None,
    account: str | None,
    region: str | None,
) -> None:
    """Bind runner-validated zones to CDK's cached context provider key."""

    if live_e2e_run_id is None:
        if raw_availability_zones is not None:
            raise ValueError("live_e2e_availability_zones is reserved for live E2E runs")
        return
    if not account or not re.fullmatch(r"\d{12}", account) or not region:
        raise ValueError("Live E2E synthesis requires a concrete AWS account and Region")
    try:
        availability_zones = (
            json.loads(raw_availability_zones) if isinstance(raw_availability_zones, str) else raw_availability_zones
        )
    except json.JSONDecodeError as exc:
        raise ValueError("live_e2e_availability_zones must be a JSON array") from exc
    if (
        not isinstance(availability_zones, list)
        or len(availability_zones) != 2
        or any(
            not isinstance(zone, str) or not re.fullmatch(rf"{re.escape(region)}[a-z]", zone)
            for zone in availability_zones
        )
        or len(set(availability_zones)) != 2
    ):
        raise ValueError("Live E2E synthesis requires two unique standard Availability Zones")
    app.node.set_context(
        f"availability-zones:account={account}:region={region}",
        availability_zones,
    )


def main() -> None:
    """Build and synthesise the CDK application."""
    app = cdk.App()

    # Live E2E deployments use a unique stack name. The guarded local runner is
    # the only supported way to set this context; normal deployments retain the
    # historical stack name.
    live_e2e_run_id = app.node.try_get_context("live_e2e_run_id")
    assert_live_e2e_runner_context(live_e2e_run_id)
    bind_live_e2e_availability_zone_context(
        app,
        live_e2e_run_id=live_e2e_run_id,
        raw_availability_zones=app.node.try_get_context("live_e2e_availability_zones"),
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION"),
    )
    stack_id = stack_id_for_live_e2e(live_e2e_run_id)
    cdk.Validations.of(app).add_plugins(
        AwsSolutionsChecks(app, verbose=True),
        HIPAASecurityChecks(app, verbose=True),
    )

    # Derive the deployment environment from the CLI defaults so one synth template
    # can target the account/region currently configured for the CDK user.
    OpenemrEcsStack(
        app,
        stack_id,
        stack_name=stack_id,
        env=cdk.Environment(
            account=os.getenv("CDK_DEFAULT_ACCOUNT"),
            region=os.getenv("CDK_DEFAULT_REGION"),
        ),
    )

    # Emit CloudFormation templates and assets for all defined stacks.
    app.synth()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        # Detect configuration/validation errors and present them cleanly
        if "Context validation failed" in msg or "validation" in msg.lower():
            # Strip the wrapper prefix for a cleaner message
            clean = msg.removeprefix("Context validation failed: ")
            print(
                "\n"
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║                  CONFIGURATION ERROR                         ║\n"
                "╚══════════════════════════════════════════════════════════════╝\n"
                f"\n{clean}\n"
                "\nEdit 'cdk.json' (context section) or pass values via:\n"
                "  node_modules/.bin/cdk deploy -c key=value\n"
                "\nSee README.md for full configuration reference.\n",
                file=sys.stderr,
            )
            sys.exit(1)
        raise
