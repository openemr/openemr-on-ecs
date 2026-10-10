# CI and automation

This page describes every GitHub Actions workflow in
[`.github/workflows/`](../../.github/workflows/), what each job checks, and how
to reproduce the checks locally. It is for maintainers and contributors who
need to understand why a check failed or want to change the pipeline.

## Contents

- [Overview](#overview)
- [CI Validation (`ci.yml`)](#ci-validation-ciyml)
- [CDK Configuration Matrix Testing (`cdk-config-matrix.yml`)](#cdk-configuration-matrix-testing-cdk-config-matrixyml)
- [Monthly Version Check (`monthly-version-check.yml`)](#monthly-version-check-monthly-version-checkyml)
- [Manual Release (`manual-release.yml`)](#manual-release-manual-releaseyml)
- [Testing tools used by CI](#testing-tools-used-by-ci)
- [Useful commands](#useful-commands)

## Overview

| Workflow | Runs on | Purpose |
|---|---|---|
| [CI Validation](#ci-validation-ciyml) | Push and pull request to `main` or `develop`; manual | Tests, synthesis, code quality, security, shell, Go, Docker, emulator, and import-worker checks |
| [CDK Configuration Matrix Testing](#cdk-configuration-matrix-testing-cdk-config-matrixyml) | Push and pull request to `main` or `develop`; manual | Synthesizes many `cdk.json` combinations |
| [Monthly Version Check](#monthly-version-check-monthly-version-checkyml) | 09:00 UTC on the 1st of each month; manual | Audits pinned versions and manages a tracking issue |
| [Manual Release](#manual-release-manual-releaseyml) | Manual only, from `main` | Bumps `VERSION`, tags, and publishes a GitHub release |

Shared conventions:

- All jobs run on `ubuntu-26.04` with Python 3.14, Node.js 24, and pip
  26.2.1 where needed.
- Every third-party action is pinned to a full commit SHA with a version
  comment.
- Workflows that synthesize the CDK app use fake AWS credentials
  (`AWS_ACCESS_KEY_ID=fake`, and so on) and `cdk synth --no-lookups`, so no
  real AWS credentials are needed. The Floci job goes further and refuses to
  run if any AWS credentials are present.
- No workflow deploys to AWS or runs an AWS-facing live E2E command. See
  [Live E2E](live-e2e.md).

## CI Validation (`ci.yml`)

Runs on every push and pull request targeting `main` or `develop`, and can be
started manually with **Run workflow** in the Actions tab (useful for a quick
regression check before merging large infrastructure changes). Workflow
permissions are read-only (`contents: read`).

### Run Unit Tests (`unit-tests`)

Installs `requirements.txt` and `requirements-dev.txt` and runs one pytest
command over `tests/` and `tools/credential-rotation/tests/` (the paths come
from `testpaths` in `pyproject.toml`):

```bash
pytest --cov --cov-report=xml --cov-report=html --cov-report=term-missing \
  --maxfail=1 --disable-warnings -n auto -m "not integration and not floci" -q
```

Coverage must be 100% (`fail_under = 100` in `pyproject.toml`); see
[Tests and coverage](README.md#tests-and-coverage). The XML report is uploaded
to Codecov (upload failures don't fail the job) and the HTML report is
uploaded as the `coverage-report` artifact.

> [!NOTE]
> Credential rotation tests used to run in a separate job. They are now part
> of this single pytest run.

### Repository Knowledge MCP (`knowledge-mcp-tests`)

Runs `pytest tests/tools/test_knowledge_mcp.py`, which exercises the server's
STDIO protocol and safety boundaries, then runs `ruff format --check`,
`ruff check`, and `mypy` on `tools/_shared.py`, `tools/knowledge_mcp`, and the
test file. See [Knowledge MCP](knowledge-mcp.md).

### Floci Live E2E Emulation (`floci-e2e`)

Exercises the live E2E tooling against the [Floci](https://github.com/floci-io/floci)
AWS emulator, with a 40-minute timeout. The job:

1. Fails immediately if any AWS credential environment variable is set.
2. Configures Docker to allow Floci's plain-HTTP local ECR registry and
   restarts Docker.
3. Sets up ARM64 emulation (QEMU) and Buildx, installs pinned dependencies,
   and runs `npm ci`.
4. Pulls the digest-pinned Floci image and prepares the MySQL and Valkey images
   Floci will start.
5. Runs the Floci test files with `OPENEMR_FLOCI_E2E=1`:
   `test_floci_e2e.py` (adapter tests, then the mocked runner),
   `test_floci_cdk_deploy.py`, and `test_floci_live_e2e_full.py`.
6. Uploads `.live-e2e/` diagnostics for 7 days if it fails.

See [Floci](floci.md) for details and local commands.

### CloudFormation Template Validation (`cdk-synth`)

Runs after `unit-tests` succeeds. It:

1. Installs Python dependencies and the pinned Node.js tools (`npm ci`).
2. Audits Node.js dependencies with `python scripts/check_npm_audit.py`.
3. Runs the synthesized stack assertions in
   `tests/unit/test_openemr_ecs_stack.py`.
4. Runs `npx --no-install cdk synth --no-lookups` with a placeholder
   certificate ARN. cdk-nag's AWS Solutions and HIPAA Security checks run as
   part of synthesis.
5. Runs `python3 scripts/test-cdk-synthesis.py`, which synthesizes 16
   configurations and runs cfn-lint on each template.
6. Builds the OpenEMR import worker image for `linux/arm64` with Buildx.

### Code Quality Checks (`code-quality`)

```bash
ruff format --check .
ruff check .
mypy app.py openemr_ecs/ diagrams/ tools/_shared.py tools/version_audit/ \
  tools/openemr_import/ tools/openemr-import-worker/worker.py \
  tools/credential-rotation/src/ scripts/check_npm_audit.py \
  scripts/test-cdk-synthesis.py
```

Ruff replaces the earlier black, flake8, and isort checks. Its rules are
configured in `pyproject.toml`.

### Security Scan (`security-scan`)

- Python security linting (Ruff's flake8-bandit `S` rules, which replace the
  earlier standalone bandit scan) runs as part of `ruff check .` in the
  Code Quality Checks job, so it honors the reviewed ignore list in
  `pyproject.toml`. Don't add a separate `ruff check --select S` step: a
  command-line `--select` overrides that ignore list.
- `pip-audit --strict` checks `requirements.txt`, `requirements-dev.txt`,
  `tools/credential-rotation/requirements.txt`, and
  `tools/openemr-import-worker/requirements.txt` for known vulnerabilities.

### Shell Script Validation (`shellcheck`)

Downloads ShellCheck 0.11.0, verifies its SHA-256 checksum, and runs it on
every `*.sh` file under `scripts/`, excluding the accepted warnings
`SC1090,SC1091,SC2015,SC2034,SC2059,SC2317`.

### Go TUI jobs (`go-tui-build`, `go-tui-lint`, `go-tui-test`)

All three run in `scripts/backup-tui` with Go 1.27:

| Job | Checks |
|---|---|
| Build Go TUI | `go mod download`, `go mod verify`, `make build`, and confirms `bin/backup-tui` exists |
| Lint Go TUI | `go vet ./...`, `gofmt -s -l .` must report nothing, and `golangci-lint run` (v2.14.0) |
| Test Go TUI | `go test ./... -v` with atomic coverage, then prints a coverage summary |

### Docker Compose Tests (`docker-compose-tests`)

Validates and starts the local test rigs in `compose/` under ARM64 emulation:

1. `docker-compose.test.yml` (basic): checks the configuration, starts MySQL,
   Redis, and OpenEMR, waits for MySQL and Redis to be ready, and checks the
   OpenEMR logs. The check passes if the logs contain
   `Container initialization complete`. Otherwise, any `ERROR` in the logs
   fails the job, and a container that is still initializing is accepted.
2. `docker-compose.test-ssl.yml` (TLS): the same checks with TLS-enabled MySQL
   and Redis (Redis on port 6380 with TLS).

Each startup test has a 5-minute timeout, and containers and volumes are
removed afterward. See [Local testing](../guides/local-testing.md).

### Import Worker MySQL Integration (`import-worker-mysql-integration`)

Runs `./scripts/ci-import-worker-mysql.sh`, a live TLS MySQL harness for the
OpenEMR import worker, with a 60-minute timeout. Logs from
`.import-worker-mysql-ci/logs/` are uploaded on failure. See
[Importing OpenEMR](../guides/importing-openemr.md).

### Import Worker Seed Manifest Freshness (`import-worker-seed-manifest`)

Runs `./scripts/update-seed-manifest.sh --check` to confirm that
`tools/openemr-import-worker/fresh-seed-manifest.json` still matches the pinned
OpenEMR image. Logs from `.seed-manifest-ci/logs/` are uploaded on failure.

## CDK Configuration Matrix Testing (`cdk-config-matrix.yml`)

Runs on every push and pull request targeting `main` or `develop`, and
manually. It is a dedicated workflow for broad configuration coverage
(monitoring, analytics, APIs, and other feature combinations):

1. `python3 scripts/test-cdk-synthesis.py --verbose`: the 16-configuration
   matrix with cfn-lint.
2. `bash scripts/stress-test.sh`: the original bash synthesis stress test.

Both use fake credentials and never deploy. If either fails, the
`/tmp/cdk-synth-*.log` files are uploaded as the `cdk-synth-logs` artifact for
7 days.

## Monthly Version Check (`monthly-version-check.yml`)

Gives automated dependency awareness. It runs at 09:00 UTC on the first day of
each month, and can be started manually (the `create_issue` input, default
`true`, controls whether a manual run manages the issue).

**Audit job.** Runs the same tool maintainers use locally:

```bash
python -m tools.version_audit --json version-check-report.json \
  --markdown version-check-summary.md --timestamp "$REPORT_TIMESTAMP" \
  --fail-if-all-sources-fail
```

It checks pinned Python packages, the Aurora MySQL engine version, Lambda
runtimes, EMR Serverless release labels, the pinned `openemr/openemr`
container, GitHub Actions, and toolchains. The report is added to the run
summary and uploaded as an artifact for 30 days. The job warns on partial
source failures and fails if the audit exits with a non-zero status.

**Issue job.** For scheduled runs (or manual runs with `create_issue`), it
creates or updates a single open issue labeled `dependencies`, `maintenance`,
and `version-check` when updates or source failures are found. When a later
audit is clean, it comments on that issue and closes it.

See [Version audit](README.md#version-audit) for the local commands and exit
codes.

## Manual Release (`manual-release.yml`)

A controlled semantic-version release process, started from the Actions tab
when you're ready to publish a change set. It only runs when started from
`main`, and only one release can run at a time.

**Inputs:**

| Input | Meaning |
|---|---|
| `release_type` | `major`, `minor`, or `patch` (default `patch`) |
| `release_notes` | Markdown for the release body (required) |
| `dry_run` | Show the plan without committing, tagging, or publishing |

**Steps:**

1. Checks that the `VERSION` file exists and that the checkout matches
   `origin/main`.
2. Counts commits since the last tag; if there are none, the release is
   skipped.
3. Calculates the next version from `VERSION`.
4. Unless it's a dry run: writes the new `VERSION`, commits
   `chore: release v<version>`, tags `v<version>`, pushes the commit and tag
   atomically, and creates a GitHub release with your notes.
5. Writes a summary of the dry run, the skip, or the published release.

## Testing tools used by CI

| Tool | What it does |
|---|---|
| [`scripts/test-cdk-synthesis.py`](../../scripts/test-cdk-synthesis.py) | Synthesizes 16 configurations (features, safeguards, sizing, and combined options) and runs cfn-lint on each. Use `--verbose` for detailed errors and `--fail-fast` to stop at the first failure. |
| [`scripts/stress-test.sh`](../../scripts/stress-test.sh) | Bash synthesis test over representative configurations; never deploys or destroys anything |
| [`scripts/check_npm_audit.py`](../../scripts/check_npm_audit.py) | Fails on any npm audit finding |
| [`scripts/validate-deployment-prerequisites.sh`](../../scripts/validate-deployment-prerequisites.sh) | Not run in CI; a pre-flight check users run before deploying |

The configuration matrix covers, among others: a minimal configuration (core
features only), a standard configuration (Bedrock and Data API), a
full-featured configuration (Global Accelerator and analytics), monitoring
alarms, APIs and the patient portal, CloudTrail, ECS Exec, deletion and
termination protection, IPv6, small and large Fargate sizes, and an
everything-on "kitchen sink".

## Useful commands

Run these from the repository root after `npm ci`:

| Command | What it does |
|---|---|
| `node_modules/.bin/cdk ls` | Lists the stacks in the app |
| `node_modules/.bin/cdk synth` | Emits the synthesized CloudFormation template |
| `node_modules/.bin/cdk deploy` | Deploys the stack to your default AWS account and Region |
| `node_modules/.bin/cdk diff` | Compares the deployed stack with your current code |
| `node_modules/.bin/cdk docs` | Opens the CDK documentation |

To reproduce the CI checks locally, see [Local validation](README.md#local-validation).
