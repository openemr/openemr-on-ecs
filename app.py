#!/usr/bin/env python3
"""CDK application entry point for the OpenEMR on AWS Fargate deployment."""

import os
import sys

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks, HIPAASecurityChecks

from openemr_ecs.stack import OpenemrEcsStack


def main() -> None:
    """Build and synthesise the CDK application."""
    app = cdk.App()
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    cdk.Aspects.of(app).add(HIPAASecurityChecks(verbose=True))

    # Derive the deployment environment from the CLI defaults so one synth template
    # can target the account/region currently configured for the CDK user.
    OpenemrEcsStack(
        app,
        "OpenemrEcsStack",
        env=cdk.Environment(
            account=os.getenv("CDK_DEFAULT_ACCOUNT"),
            region=os.getenv("CDK_DEFAULT_REGION"),
        ),
    )

    # Emit CloudFormation templates and assets for all defined stacks.
    app.synth()


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
            "  cdk deploy -c key=value\n"
            "\nSee README.md for full configuration reference.\n",
            file=sys.stderr,
        )
        sys.exit(1)
    raise
