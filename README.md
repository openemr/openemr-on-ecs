<div align="center">

<img src="logo/openemr_on_ecs_logo.png" alt="OpenEMR on ECS Logo" width="600">

<br/>

[![License: MIT](https://img.shields.io/github/license/openemr/openemr-on-ecs?style=flat&color=yellow)](LICENSE)
[![Version](https://img.shields.io/github/v/release/openemr/openemr-on-ecs?style=flat&label=version&color=blue)](https://github.com/openemr/openemr-on-ecs/releases)
[![OpenEMR](https://img.shields.io/badge/OpenEMR-v8.4.1-2ea44f?style=flat)](https://hub.docker.com/r/openemr/openemr/tags)

<table>
<tr><td><b>Tests</b></td><td>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/ci.yml?style=flat&logo=pytest&label=CDK%20unit%20tests" alt="CDK Unit Tests"></a>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/ci.yml?style=flat&logo=go&label=backup%20TUI%20tests" alt="Backup TUI Tests"></a>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/ci.yml?style=flat&logo=docker&label=Docker%20Compose" alt="Docker Compose Tests"></a>
</td></tr>
<tr><td><b>Infra</b></td><td>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/ci.yml?style=flat&logo=amazonaws&label=CDK%20synth%20%2B%20cfn-lint" alt="CDK Synth"></a>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/cdk-config-matrix.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/cdk-config-matrix.yml?style=flat&logo=amazonaws&label=config%20matrix" alt="CDK Config Matrix"></a>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/monthly-version-check.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/monthly-version-check.yml?style=flat&logo=dependabot&label=version%20audit" alt="Version Audit"></a>
</td></tr>
<tr><td><b>Quality</b></td><td>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/ci.yml?style=flat&logo=python&logoColor=white&label=ruff%20%7C%20mypy" alt="Python Quality"></a>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/ci.yml?style=flat&logo=go&label=go%20vet%20%7C%20golangci-lint" alt="Go Quality"></a>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/ci.yml?style=flat&logo=gnu-bash&label=shellcheck" alt="ShellCheck"></a>
</td></tr>
<tr><td><b>Security</b></td><td>
  <a href="https://github.com/openemr/openemr-on-ecs/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/openemr/openemr-on-ecs/ci.yml?style=flat&logo=ruff&label=ruff%20security%20rules" alt="Ruff Security Rules"></a>
  <img src="https://img.shields.io/badge/cdk--nag-HIPAA%20%2B%20AWS%20Solutions-8A2BE2?style=flat&logo=amazonaws" alt="CDK Nag">
  <img src="https://img.shields.io/badge/KMS-encryption%20at%20rest-8A2BE2?style=flat&logo=amazonaws" alt="KMS Encryption">
  <img src="https://img.shields.io/badge/HTTPS%2FTLS-encryption%20in%20transit-8A2BE2?style=flat&logo=letsencrypt&logoColor=white" alt="HTTPS/TLS">
  <a href="docs/guides/credential-rotation.md"><img src="https://img.shields.io/badge/credential%20rotation-available-2ea44f?style=flat&logo=keycdn&logoColor=white" alt="Credential Rotation"></a>
</td></tr>
<tr><td><b>Stack</b></td><td>
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Go-1.27-00ADD8?style=flat&logo=go&logoColor=white" alt="Go">
  <img src="https://img.shields.io/badge/AWS_CDK-v2-FF9900?style=flat&logo=amazonaws&logoColor=white" alt="AWS CDK">
  <img src="https://img.shields.io/badge/Fargate-serverless-FF9900?style=flat&logo=awsfargate&logoColor=white" alt="Fargate">
  <img src="https://img.shields.io/badge/Aurora-MySQL-4479A1?style=flat&logo=mysql&logoColor=white" alt="Aurora MySQL">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white" alt="Docker">
</td></tr>
</table>

*OpenEMR on Amazon ECS with Fargate: automatic scaling, encryption, backups, and monitoring, deployed with one command.*

</div>

> [!IMPORTANT]
> **HIPAA notice.** This project uses HIPAA-eligible AWS services and is checked against
> HIPAA security rules, but deploying it does not make you HIPAA compliant. Compliance also
> requires a signed Business Associate Addendum (BAA) with AWS for each account, your own
> policies and procedures, staff training and access controls, and regular risk assessments.
> See [Security and compliance](docs/reference/security-and-compliance.md).

## What is this?

[OpenEMR](https://www.open-emr.org/) is free, open-source software for electronic health
records and medical practice management. This repository sets up a complete, production-style
OpenEMR installation in your own AWS account, using infrastructure-as-code written with the
AWS Cloud Development Kit (CDK). You edit a short settings file, run one command, and roughly
40 minutes later you have OpenEMR running behind HTTPS.

**Who it's for:**

- Clinics, IT teams, and consultants who want OpenEMR on AWS without managing servers.
- People evaluating OpenEMR who want a realistic, secure test environment they can delete afterwards.
- Developers who want to extend or contribute to an OpenEMR-on-AWS reference architecture.

You don't need to be an AWS expert. The [Get Started guide](docs/get-started/README.md)
explains every step, and the [glossary](docs/get-started/glossary.md) defines the jargon.

## What does it cost?

You pay AWS directly for what the deployment uses. The project's baseline estimate is
**about $320 per month** (roughly $10–11 per day) for the default settings in us-west-2 with
light usage. Most of that is the two always-on application containers, the database's minimum
capacity, and two NAT gateways. Costs go up as traffic grows and the system scales out, and they
vary by AWS Region. Charges continue until you delete the deployment. See
[Costs](docs/reference/costs.md) for the full breakdown and ways to reduce it. If you only need
a small installation, OpenEMR's
[AWS Marketplace offerings](https://aws.amazon.com/marketplace/seller-profile?id=bec33905-edcb-4c30-b3ae-e2960a9a5ef4)
may be cheaper.

## How it works

![OpenEMR on AWS Fargate architecture](diagrams/architecture.png)

- Your users reach OpenEMR over **HTTPS** through an **Application Load Balancer**, which is protected by a **web application firewall** (AWS WAF) and only accepts the IP addresses you allow.
- OpenEMR runs in **containers on AWS Fargate**, so there are no servers to patch. At least two copies run at all times, and more start automatically when traffic grows.
- Patient data lives in an **Aurora MySQL Serverless v2** database that grows and shrinks its capacity on demand.
- Shared files (documents, images, site configuration) live on **Amazon EFS**, a network drive every container can use. A **Valkey** cache (ElastiCache Serverless) stores user sessions.
- Everything is **encrypted** with keys in AWS KMS, passwords are generated and stored in **Secrets Manager**, and **AWS Backup** takes automatic daily, weekly, and monthly backups.
- Everything runs in a private network (VPC) spread across two data centers (Availability Zones) for resilience.

For the full tour, see [Architecture](docs/reference/architecture.md).

## Choose your path

| I want to... | Start here |
|---|---|
| **Deploy it.** Get OpenEMR running in my AWS account. | [Get Started guide](docs/get-started/README.md) |
| **Understand it.** Learn how the pieces fit together before I commit. | [Architecture](docs/reference/architecture.md) |
| **Contribute or maintain it.** Change the code, run tests, cut releases. | [CONTRIBUTING.md](CONTRIBUTING.md) and the [Maintainer guide](docs/maintainers/README.md) |

Not sure? The [documentation home](docs/README.md) routes you by goal.

## Documentation map

| Section | Pages |
|---|---|
| **Get started** | [Step-by-step deployment](docs/get-started/README.md) · [Glossary](docs/get-started/glossary.md) |
| **Guides** | [Configuration](docs/guides/configuration.md) · [HTTPS and DNS](docs/guides/https-and-dns.md) · [Backup and restore](docs/guides/backup-and-restore.md) · [Importing an existing OpenEMR](docs/guides/importing-openemr.md) · [Database access](docs/guides/database-access.md) · [REST and FHIR APIs](docs/guides/apis.md) · [Analytics environment](docs/guides/analytics.md) · [Optional features](docs/guides/optional-features.md) · [Credential rotation](docs/guides/credential-rotation.md) · [Load testing](docs/guides/load-testing.md) · [Local testing](docs/guides/local-testing.md) |
| **Reference** | [Architecture](docs/reference/architecture.md) · [Costs](docs/reference/costs.md) · [Security and compliance](docs/reference/security-and-compliance.md) · [cdk-nag suppressions](docs/reference/cdk-nag-suppressions.md) · [Troubleshooting](docs/reference/troubleshooting.md) |
| **Maintainers** | [Overview](docs/maintainers/README.md) · [CI workflows](docs/maintainers/ci.md) · [Live end-to-end tests](docs/maintainers/live-e2e.md) · [Floci emulator](docs/maintainers/floci.md) · [Knowledge MCP server](docs/maintainers/knowledge-mcp.md) · [Deployment timing](docs/maintainers/deployment-timing.md) |
| **Design decisions** | [ADR 0001: Guarded OpenEMR import](docs/adr/0001-guarded-openemr-import.md) |

Day-to-day backups can also be browsed and restored from an interactive terminal app, the
Backup Manager TUI. See [Backup and restore](docs/guides/backup-and-restore.md).

## Getting help

- **Something broken?** Check [Troubleshooting](docs/reference/troubleshooting.md) first.
- **Found a bug or have a feature idea?** Open an issue on [GitHub](https://github.com/openemr/openemr-on-ecs/issues).
- **Questions about OpenEMR itself?** Ask the [OpenEMR community forum](https://community.open-emr.org/).
- **Security issue?** Don't open a public issue. Follow the process in [CONTRIBUTING.md](CONTRIBUTING.md#security-issue-notifications).

## License and disclaimers

This repository is released under the [MIT No Attribution License](LICENSE). OpenEMR itself is licensed under
GPL-3.0 and is downloaded as a container image at deploy time. This project depends on other
third-party open-source packages, each under its own license; review them against your
organization's open-source policy before use.

This is sample infrastructure code provided as-is. You are responsible for testing, securing,
and operating it, and for making sure your use complies with the laws and regulations that apply
to you (for example, HIPAA). See [Security and compliance](docs/reference/security-and-compliance.md).
