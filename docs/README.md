# Documentation home

This page helps you find the right guide for what you're trying to do. If you're brand new,
start with the [Get Started guide](get-started/README.md); everything else builds on it.

> [!TIP]
> Words like *stack*, *Fargate*, or *CIDR* are explained in plain language in the
> [glossary](get-started/glossary.md).

## Which guide do I need?

### "I want to try OpenEMR on AWS for the first time"

Follow the [Get Started guide](get-started/README.md). It takes you from an empty laptop to a
working OpenEMR login in about an hour, and shows you how to delete everything afterwards so you
stop paying. Read [Costs](reference/costs.md) first if budget matters.

### "I want to understand what this builds before I deploy it"

Read [Architecture](reference/architecture.md) for how the pieces connect, then
[Security and compliance](reference/security-and-compliance.md) for encryption, network
isolation, and what HIPAA does and doesn't cover.

### "I need to set up HTTPS or use my own domain"

Every deployment needs a TLS certificate. [HTTPS and DNS](guides/https-and-dns.md) covers both
options: letting the project create and renew a certificate for a Route 53 domain, or bringing
your own certificate from AWS Certificate Manager. It also covers optional email (SES) setup.

### "I want to change settings: size, scaling, who can connect, optional features"

[Configuration](guides/configuration.md) lists every `cdk.json` setting with its default.
[Optional features](guides/optional-features.md) explains add-ons such as the patient portal,
monitoring alarms, Global Accelerator, the RDS Data API, and Amazon Bedrock integration.

### "I need to restore a backup" (or check that backups work)

[Backup and restore](guides/backup-and-restore.md) explains what's backed up automatically, how
to take an on-demand backup, and how to restore the database or file systems, including with
the interactive Backup Manager TUI.

### "I'm migrating an existing OpenEMR installation"

[Importing an existing OpenEMR](guides/importing-openemr.md) walks through the guarded import
workflow: inspect your source offline, produce a plan, then import into a fresh, empty
deployment. The design rationale is in [ADR 0001](adr/0001-guarded-openemr-import.md).

### "I need to connect to the database directly"

[Database access](guides/database-access.md) shows how to reach Aurora MySQL securely through
ECS Exec port forwarding, without opening the database to the internet.

### "I want to integrate with other systems"

[REST and FHIR APIs](guides/apis.md) walks through enabling OpenEMR's APIs and getting an access
token. For data science and machine learning, see the
[Analytics environment](guides/analytics.md) (SageMaker Studio and EMR Serverless).

### "I need to rotate passwords and database credentials"

[Credential rotation](guides/credential-rotation.md) covers the zero-downtime, dual-slot
rotation task for database credentials.

### "I want to test performance or test locally before deploying"

- [Load testing](guides/load-testing.md): measure how a deployed stack handles traffic.
- [Local testing](guides/local-testing.md): run the container startup locally with Docker Compose.

### "Something is broken"

Go to [Troubleshooting](reference/troubleshooting.md). It covers failed deployments, the site not
loading, database and TLS errors, and stack deletion problems. The Get Started guide also has a
[quick troubleshooting table](get-started/README.md#quick-troubleshooting) for first-time setup.

### "I maintain this repository"

Start with the [Maintainer guide](maintainers/README.md), then:

- [CI workflows](maintainers/ci.md): what each GitHub Actions workflow checks.
- [Live end-to-end tests](maintainers/live-e2e.md): the approval-gated real-AWS lifecycle test.
- [Floci emulator](maintainers/floci.md): the credential-free local AWS emulation test.
- [Knowledge MCP server](maintainers/knowledge-mcp.md): a read-only project assistant for AI tools.
- [Deployment timing](maintainers/deployment-timing.md): measured deploy and teardown durations.
- [cdk-nag suppressions](reference/cdk-nag-suppressions.md): why each security-check finding is acknowledged.

Contribution rules (issues, pull requests, security reports) are in
[CONTRIBUTING.md](../CONTRIBUTING.md).

## All pages

| Section | Page | What it covers |
|---|---|---|
| Get started | [Get Started guide](get-started/README.md) | Install tools, configure, deploy, log in, clean up |
| | [Glossary](get-started/glossary.md) | Plain-language definitions of AWS and project terms |
| Guides | [Configuration](guides/configuration.md) | Every `cdk.json` setting |
| | [HTTPS and DNS](guides/https-and-dns.md) | Certificates, Route 53, SES email |
| | [Backup and restore](guides/backup-and-restore.md) | AWS Backup, restore scripts, Backup Manager TUI |
| | [Importing an existing OpenEMR](guides/importing-openemr.md) | Guarded migration into a fresh deployment |
| | [Database access](guides/database-access.md) | ECS Exec and port forwarding to Aurora |
| | [REST and FHIR APIs](guides/apis.md) | Enabling the APIs and getting a token |
| | [Analytics environment](guides/analytics.md) | SageMaker Studio, EMR Serverless, data exports |
| | [Optional features](guides/optional-features.md) | Portal, alarms, Global Accelerator, Data API, Bedrock |
| | [Credential rotation](guides/credential-rotation.md) | Rotating database credentials |
| | [Load testing](guides/load-testing.md) | Load test script and past results |
| | [Local testing](guides/local-testing.md) | Docker Compose test rigs |
| Reference | [Architecture](reference/architecture.md) | Components, network, data flow, design decisions |
| | [Costs](reference/costs.md) | Monthly estimate and how to reduce it |
| | [Security and compliance](reference/security-and-compliance.md) | Encryption, HIPAA, BAA, disclaimers |
| | [cdk-nag suppressions](reference/cdk-nag-suppressions.md) | Acknowledged security-check findings |
| | [Troubleshooting](reference/troubleshooting.md) | Common problems and fixes |
| Maintainers | [Maintainer guide](maintainers/README.md) | Toolchains, validation, version audit |
| | [CI workflows](maintainers/ci.md) | GitHub Actions |
| | [Live end-to-end tests](maintainers/live-e2e.md) | Real-AWS lifecycle runner |
| | [Floci emulator](maintainers/floci.md) | Local AWS emulator tests |
| | [Knowledge MCP server](maintainers/knowledge-mcp.md) | Read-only repository MCP server |
| | [Deployment timing](maintainers/deployment-timing.md) | Measured durations |
| Decisions | [ADR 0001](adr/0001-guarded-openemr-import.md) | Why imports are guarded and fresh-target-only |

## Repository layout

A quick map of the repository so you know where things live. You only need to touch `cdk.json`
to deploy; everything else is for customizing or contributing.

```text
.
├── README.md                  Project landing page
├── app.py                     CDK entry point: creates the "OpenemrEcsStack" stack and turns on cdk-nag checks
├── cdk.json                   Deployment settings ("context"); the file you edit before deploying
├── requirements.txt           Python packages needed to deploy
├── requirements-dev.txt       Extra Python packages for tests and linting
├── package.json               Pins the CDK command-line tool (installed with `npm ci`)
├── pyproject.toml             Python tool settings (ruff, mypy, pytest)
├── VERSION                    Release version of this project
├── openemr_ecs/               The infrastructure code, one module per area
│   ├── stack.py               Puts all the pieces together and defines the stack outputs
│   ├── network.py             VPC, subnets, load balancer, security groups
│   ├── compute.py             ECS cluster, Fargate service, one-off maintenance tasks
│   ├── database.py            Aurora MySQL Serverless v2
│   ├── storage.py             EFS, S3 log buckets, CloudTrail, AWS Backup
│   ├── security.py            WAF, certificates, Route 53 records, SES email
│   ├── validation.py          Checks your cdk.json settings before anything is built
│   ├── constants.py           Pinned versions (OpenEMR, Aurora engine, Lambda runtime)
│   └── ...                    Monitoring, KMS keys, analytics, cleanup, cdk-nag helpers
├── lambda/                    Small AWS Lambda functions used by the stack
├── scripts/                   Helper scripts: pre-deploy checks, backups, restores, load tests
│   └── backup-tui/            Backup Manager terminal app (Go)
├── tools/
│   ├── credential-rotation/   Container that rotates database credentials
│   ├── openemr_import/        Offline inspect and plan steps for importing an existing OpenEMR
│   ├── openemr-import-worker/ Container that performs a guarded import inside AWS
│   ├── knowledge_mcp/         Read-only MCP server describing this repository
│   ├── live_e2e/              Approval-gated real-AWS end-to-end test runner
│   ├── floci_cdk/             Helpers for the local Floci AWS-emulator tests
│   └── version_audit/         Checks pinned dependencies for newer versions
├── tests/                     Unit tests (tests/unit) and tool tests (tests/tools)
├── compose/                   Docker Compose files for local testing
├── diagrams/                  Architecture diagrams generated from the CDK code
├── docs/                      This documentation, plus images/ (screenshots) and adr/ (design decisions)
├── e2e-results/               Sanitized timing history from live end-to-end runs
├── logo/                      Project logo
├── CONTRIBUTING.md            How to report issues and contribute
├── CODE_OF_CONDUCT.md         Community code of conduct
└── LICENSE                    MIT No Attribution license
```

Several folders have their own README with more detail:
[scripts/](../scripts/README.md) (every helper script), [lambda/](../lambda/README.md),
[compose/](../compose/README.md), [diagrams/](../diagrams/README.md),
[openemr_ecs/](../openemr_ecs/README.md), and
[tools/credential-rotation/](../tools/credential-rotation/README.md).
