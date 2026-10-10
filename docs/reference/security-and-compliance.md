# Security and compliance

This page explains what this project does to protect health data, what it
can't do for you, and the legal disclaimers that apply. Read it before you put
real patient data into a deployment. It is written for clinic administrators,
compliance staff, and the engineers who deploy and operate the stack.

## Contents

- [HIPAA: what this project does and doesn't do](#hipaa-what-this-project-does-and-doesnt-do)
- [Notes on HIPAA compliance in general](#notes-on-hipaa-compliance-in-general)
- [Security controls built into the stack](#security-controls-built-into-the-stack)
- [Recommended settings for production](#recommended-settings-for-production)
- [How security is checked](#how-security-is-checked)
  - [cdk-nag rule packs](#cdk-nag-rule-packs)
  - [Automated checks in CI](#automated-checks-in-ci)
  - [Container vulnerabilities](#container-vulnerabilities)
- [Disclaimers](#disclaimers)
  - [Third-party packages](#third-party-packages)
  - [General](#general)

## HIPAA: what this project does and doesn't do

The architecture uses HIPAA-eligible AWS services, encrypts data at rest and in
transit, keeps audit logs, and is checked against the cdk-nag HIPAA Security
rule pack. That helps with the technical side of HIPAA. It does **not** make
you HIPAA compliant on its own.

> [!IMPORTANT]
> No matter what you deploy to AWS, full HIPAA compliance also requires:
>
> - An executed Business Associate Addendum (BAA) with AWS
> - Organizational policies and procedures
> - Staff training and access controls
> - Regular security audits and risk assessments

## Notes on HIPAA compliance in general

If you are an AWS customer who is a HIPAA covered entity, you need to sign a
Business Associate Addendum (BAA) with AWS before running anything that would
be considered in scope for HIPAA on AWS.

You must sign a separate BAA for **each AWS account** where you run anything in
scope for HIPAA.

- AWS's documentation on HIPAA compliance, including how to sign a BAA, is at
  [HIPAA compliance on AWS](https://aws.amazon.com/compliance/hipaa-compliance/).
- You can find and accept the BAA in AWS Artifact in the AWS console. See
  [Getting started with AWS Artifact](https://aws.amazon.com/artifact/getting-started/).

While this project may help with certain aspects of HIPAA compliance, we make
no claim that it alone will result in compliance with HIPAA. See the
[disclaimers](#disclaimers) below.

## Security controls built into the stack

The full technical description is in
[Architecture: Security architecture](architecture.md#security-architecture).
In summary:

- **Encryption in transit everywhere.** HTTPS from the browser to the load
  balancer and again from the load balancer to the containers. TLS is required
  for every database connection and used for every cache connection. EFS mounts
  use encryption in transit. There is no HTTP (port 80) listener.
- **Encryption at rest.** Aurora, EFS, Secrets Manager, CloudWatch Logs, and S3
  are encrypted. Customer-managed KMS keys have automatic key rotation enabled.
- **Network isolation.** The database, cache, file systems, and application
  containers are in private subnets. Each tier has its own least-privilege
  security group. Only the load balancer is reachable from the internet, and
  only from the IP ranges you allow.
- **Web application firewall.** AWS WAF is always attached, with AWS managed
  rule sets (common threats, SQL injection, known bad inputs), a per-IP rate
  limit, and suspicious user-agent blocking.
- **Secrets management.** Passwords are generated at deploy time, stored in
  Secrets Manager, and injected into containers at runtime. A
  [credential rotation task](../guides/credential-rotation.md) can rotate the
  database credentials without downtime.
- **Audit logging.** CloudTrail (on by default) records management events with
  long-term retention; Aurora audit logging records connections and queries;
  VPC Flow Logs, WAF logs, and load balancer access logs are kept.
- **Backups.** AWS Backup takes daily, weekly, and monthly backups of the
  database and file systems with up to 7-year retention. See
  [Backup and restore](../guides/backup-and-restore.md).

## Recommended settings for production

These are `cdk.json` settings described in
[Configuration](../guides/configuration.md):

- Restrict `security_group_ip_range_ipv4` to your organization's addresses.
  The default `"auto"` allows only the public IP of the computer that runs the
  deployment; avoid `"0.0.0.0/0"`.
- Keep `enable_ecs_exec` off (`"false"`, the default) and turn it on only while
  you need shell or database access. See
  [Database access](../guides/database-access.md).
- Keep `enable_long_term_cloudtrail_monitoring` on (`"true"`, the default).
- Set `rds_deletion_protection` to `true` so the production database can't be
  deleted by accident.
- Consider `enable_monitoring_alarms` so someone is notified about service
  health problems.

## How security is checked

### cdk-nag rule packs

This project is instrumented with [cdk-nag](https://github.com/cdklabs/cdk-nag).
The `AwsSolutionsChecks` and `HIPAASecurityChecks` rule packs are enabled in
[`app.py`](../../app.py), so every `cdk synth` and `cdk deploy` checks the
infrastructure against them:

```python
from cdk_nag import AwsSolutionsChecks, HIPAASecurityChecks

cdk.Validations.of(app).add_plugins(
    AwsSolutionsChecks(app, verbose=True),
    HIPAASecurityChecks(app, verbose=True),
)
```

Findings that are expected and safe for this architecture are acknowledged in
code with the `acknowledge_findings()` helper in
[`openemr_ecs/nag_suppressions.py`](../../openemr_ecs/nag_suppressions.py).
This project uses cdk-nag v3, which records acknowledgments through CDK's
native validations metadata instead of the older `NagSuppressions` API. The
rationale for every acknowledgment is in
[cdk-nag suppressions](cdk-nag-suppressions.md).

Any new finding reported during synthesis must be fixed or acknowledged, with a
reason, before deployment.

While these checks may help with certain aspects of HIPAA compliance, we make
no claim that they alone will result in compliance with HIPAA. See the
[disclaimers](#disclaimers).

### Automated checks in CI

Every pull request and push to `main` or `develop` also runs:

- Python security linting with Ruff's flake8-bandit (`S`) rules
- `pip-audit` on all pinned Python requirements
- `npm audit` on the pinned Node.js tools (`scripts/check_npm_audit.py`, which
  fails on any reported vulnerability)
- cfn-lint on synthesized CloudFormation templates
- ShellCheck on shell scripts

See [CI and automation](../maintainers/ci.md) for the full list.

### Container vulnerabilities

We recommend scanning the container image used by this project regularly. Two
ways to do that:

1. Push the image to Amazon ECR and turn on image scanning.
2. Use [Trivy](https://github.com/aquasecurity/trivy).

The OpenEMR image is pinned to an immutable ARM64 digest in
[`openemr_ecs/constants.py`](../../openemr_ecs/constants.py), and the monthly
version audit flags newer releases.

## Disclaimers

### Third-party packages

This package depends on and may incorporate or retrieve a number of
third-party software packages (such as open source packages) at install-time
or build-time or run-time ("External Dependencies"). The External Dependencies
are subject to license terms that you must accept in order to use this
package. If you do not accept all of the applicable license terms, you should
not use this package. We recommend that you consult your company's open source
approval policy before proceeding.

Provided below is a list of External Dependencies and the applicable license
identification as indicated by the documentation associated with the External
Dependencies as of Amazon's most recent review.

THIS INFORMATION IS PROVIDED FOR CONVENIENCE ONLY. AMAZON DOES NOT PROMISE THAT
THE LIST OR THE APPLICABLE TERMS AND CONDITIONS ARE COMPLETE, ACCURATE, OR
UP-TO-DATE, AND AMAZON WILL HAVE NO LIABILITY FOR ANY INACCURACIES. YOU SHOULD
CONSULT THE DOWNLOAD SITES FOR THE EXTERNAL DEPENDENCIES FOR THE MOST COMPLETE
AND UP-TO-DATE LICENSING INFORMATION.

YOUR USE OF THE EXTERNAL DEPENDENCIES IS AT YOUR SOLE RISK. IN NO EVENT WILL
AMAZON BE LIABLE FOR ANY DAMAGES, INCLUDING WITHOUT LIMITATION ANY DIRECT,
INDIRECT, CONSEQUENTIAL, SPECIAL, INCIDENTAL, OR PUNITIVE DAMAGES (INCLUDING
FOR ANY LOSS OF GOODWILL, BUSINESS INTERRUPTION, LOST PROFITS OR DATA, OR
COMPUTER FAILURE OR MALFUNCTION) ARISING FROM OR RELATING TO THE EXTERNAL
DEPENDENCIES, HOWEVER CAUSED AND REGARDLESS OF THE THEORY OF LIABILITY, EVEN
IF AMAZON HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. THESE LIMITATIONS
AND DISCLAIMERS APPLY EXCEPT TO THE EXTENT PROHIBITED BY APPLICABLE LAW.

- openemr (Repository: https://github.com/openemr/openemr // License:
  https://github.com/openemr/openemr/blob/master/LICENSE) - GPL-3.0

### General

AWS does not represent or warrant that this AWS Content is production ready.
You are responsible for making your own independent assessment of the
information, guidance, code and other AWS Content provided by AWS, which may
include you performing your own independent testing, securing, and optimizing.
You should take independent measures to ensure that you comply with your own
specific quality control practices and standards, and to ensure that you
comply with the local rules, laws, regulations, licenses and terms that apply
to you and your content. If you are in a regulated industry, you should take
extra care to ensure that your use of this AWS Content, in combination with
your own content, complies with applicable regulations (for example, the
Health Insurance Portability and Accountability Act of 1996). AWS does not make
any representations, warranties or guarantees that this AWS Content will result
in a particular outcome or result.
