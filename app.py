#!/usr/bin/env python3
"""CDK application entry point for the OpenEMR on AWS Fargate deployment."""

import os

import aws_cdk as cdk

from openemr_ecs.openemr_ecs_stack import OpenemrEcsStack
# from cdk_nag import AwsSolutionsChecks, HIPAASecurityChecks

app = cdk.App()
# cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
# cdk.Aspects.of(app).add(HIPAASecurityChecks(verbose=True))

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
