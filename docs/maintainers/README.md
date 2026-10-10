# Maintainer guide

This is the starting point for people who maintain this repository: the
supported toolchains, how to validate changes locally, how to review
dependency upgrades, and where the operational guides live. Routine CI and the
local validation commands on this page do not deploy infrastructure.

## Contents

- [Maintainer guides](#maintainer-guides)
- [Supported toolchains](#supported-toolchains)
- [Set up a local environment](#set-up-a-local-environment)
- [Command safety](#command-safety)
- [Local validation](#local-validation)
  - [Tests and coverage](#tests-and-coverage)
  - [Formatting, linting, typing, and security checks](#formatting-linting-typing-and-security-checks)
  - [Pre-commit hooks](#pre-commit-hooks)
  - [Go code](#go-code)
  - [CDK synthesis](#cdk-synthesis)
- [Version audit](#version-audit)
- [Upgrade review checklist](#upgrade-review-checklist)
- [Read-only repository knowledge MCP](#read-only-repository-knowledge-mcp)
- [Release and CI](#release-and-ci)
- [Live deployment E2E and timing](#live-deployment-e2e-and-timing)

## Maintainer guides

| Guide | Covers |
|---|---|
| [CI and automation](ci.md) | Every GitHub Actions workflow and job, and useful CDK commands |
| [Live E2E](live-e2e.md) | Approval-gated deployment test against real AWS, and timing methodology |
| [Floci](floci.md) | Local AWS emulator used to test the live E2E tooling without AWS |
| [Knowledge MCP](knowledge-mcp.md) | Read-only repository knowledge server for MCP-capable assistants |
| [Deployment timing](deployment-timing.md) | Generated report of measured live E2E deployment timings |
| [Test suite](../../tests/README.md) | How the unit tests are organized and how to write them |

## Supported toolchains

- Python 3.14 for the application and all Python checks, including `cfn-lint`
- Node.js 24
- Go 1.27 for `scripts/backup-tui`
- AWS CDK v2 CLI, installed from the repository's pinned `package.json` by
  `npm ci` (don't use a global CDK)
- Docker with Buildx for local container validation

## Set up a local environment

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install "pip==26.2.1"
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
npm ci
```

The private npm package pins the AWS CDK, `cdk-assets`, and cdk-dia CLIs used
for synthesis and diagram generation. Python production and development
requirements are exact pins; update them through the audited
[upgrade workflow](#upgrade-review-checklist) rather than relying on floating
ranges in CI.

## Command safety

- Tests, formatting checks, static analysis, the offline version inventory,
  `scripts/stress-test.sh`, `scripts/test-cdk-synthesis.py`, and diagram
  synthesis are local-only. Diagram generation writes only its documented
  output files.
- The online version audit makes public, read-only network requests.
- Deployment prerequisite checks (`scripts/validate-deployment-prerequisites.sh`)
  read AWS configuration and resource state.
- Normal CDK deployment and the operational scripts can change AWS resources.
  Use them only after reviewing their documented confirmation steps.

## Local validation

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) is the
authoritative list of checks. These commands reproduce them locally.

### Tests and coverage

A single `pytest` run covers both `tests/` and
`tools/credential-rotation/tests/`. [`pyproject.toml`](../../pyproject.toml)
sets `testpaths` to both directories and adds
`tools/credential-rotation/src` to `pythonpath`, so no `PYTHONPATH` setup is
needed.

Coverage is enforced at **100% for all Python code** through the
`[tool.coverage.run]` and `[tool.coverage.report]` sections of
`pyproject.toml` (`fail_under = 100`). Coverage includes the repository root
plus `diagrams`, `lambda`, `scripts`, and `tools/openemr-import-worker`. Test
files and generated or local-state directories are excluded, plus a short,
reviewed list of files that only run against real infrastructure:

- `tools/openemr-import-worker/ci_live_mysql_import.py`
- `tools/openemr-import-worker/ci_prepare_mysql_fixtures.py`
- `scripts/api_endpoint_test.py`
- `scripts/test_data_api.py`

The two import-worker harnesses are exercised by the Docker MySQL CI jobs, and
the two API scripts target a live deployment. Lines under
`if __name__ == "__main__":` and `if TYPE_CHECKING:` are excluded.

Run the suite the same way CI does:

```bash
.venv/bin/pytest --cov -n auto -m "not integration and not floci"
```

CI adds `--cov-report=xml --cov-report=html --cov-report=term-missing
--maxfail=1 --disable-warnings -q` and uploads the reports to Codecov and as a
build artifact.

> [!TIP]
> When you run only some tests (for example
> `.venv/bin/pytest tests/unit/test_validation.py`), leave off `--cov`. The
> 100% gate applies to the whole codebase, so a partial run with `--cov` always
> reports a coverage failure.

Tests marked `floci` need Docker and the Floci emulator; see
[Floci](floci.md). Tests marked `integration` are excluded from normal runs.

### Formatting, linting, typing, and security checks

Python formatting and linting use [Ruff](https://docs.astral.sh/ruff/) only.
The security rules are Ruff's flake8-bandit (`S`) rules, configured in the
`[tool.ruff.lint]` section of `pyproject.toml` together with pycodestyle (`E`,
`W`), Pyflakes (`F`), and import sorting (`I`). Low-severity bandit checks are
ignored to match the previous `bandit -ll` threshold, and test directories
don't run the `S` rules.

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy app.py openemr_ecs/ diagrams/ tools/_shared.py \
  tools/version_audit/ tools/openemr_import/ \
  tools/openemr-import-worker/worker.py tools/credential-rotation/src/ \
  scripts/check_npm_audit.py scripts/test-cdk-synthesis.py
.venv/bin/pip-audit --strict --progress-spinner off \
  -r requirements.txt -r requirements-dev.txt \
  -r tools/credential-rotation/requirements.txt
.venv/bin/pip-audit --strict --progress-spinner off \
  -r tools/openemr-import-worker/requirements.txt
.venv/bin/python scripts/check_npm_audit.py
```

`ruff check .` includes the security rules. Don't run them on their own with
`ruff check --select S`: a command-line `--select` overrides the reviewed
ignore list in `pyproject.toml` and reports findings CI accepts. Use
`ruff format .` and `ruff check --fix .` to apply fixes.

`scripts/check_npm_audit.py` fails on any reported npm vulnerability. Update
lockfiles with the native package manager, and never change a lockfile version
without updating the installed package.

### Pre-commit hooks

[`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) runs:

- Standard hygiene hooks from `pre-commit-hooks`: trailing whitespace, end of
  file, YAML/JSON/TOML syntax, large files, and merge-conflict markers
- `ruff-check` and `ruff-format` from `astral-sh/ruff-pre-commit`
- `shellcheck` on `.sh` files, with the same exclusions as CI
  (`SC1090,SC1091,SC2015,SC2034,SC2059,SC2317`)

Install the hooks once, then run them against staged changes:

```bash
.venv/bin/pre-commit install
.venv/bin/pre-commit run
```

Run the hooks after staging your changes, then restage any formatter fixes
before committing.

### Go code

```bash
cd scripts/backup-tui
go mod verify
go test ./...
go vet ./...
test -z "$(gofmt -s -l .)"
```

CI also builds the binary with `make build` and runs
`golangci-lint run` (v2.14.0).

### CDK synthesis

Synthesize without AWS lookups or real credentials:

```bash
AWS_ACCESS_KEY_ID=fake \
AWS_SECRET_ACCESS_KEY=fake \
AWS_DEFAULT_REGION=us-west-2 \
CDK_DEFAULT_REGION=us-west-2 \
  node_modules/.bin/cdk synth --no-lookups \
  -c certificate_arn=arn:aws:acm:us-west-2:123456789012:certificate/00000000-0000-0000-0000-000000000000
```

`scripts/stress-test.sh` and `scripts/test-cdk-synthesis.py` synthesize more
configuration combinations without deploying. The Python matrix
(16 configurations) also runs `cfn-lint` against every synthesized template.

## Version audit

Inventory the declared dependency, platform, container, GitHub Action, and
toolchain versions without network access:

```bash
.venv/bin/python -m tools.version_audit --offline
```

Resolve current stable versions from authoritative public sources and write
review files:

```bash
.venv/bin/python -m tools.version_audit \
  --json /tmp/openemr-version-audit.json \
  --markdown /tmp/openemr-version-audit.md \
  --fail-if-all-sources-fail
```

List or select categories with `--list-categories` and repeated `--category`
arguments. Add `--fail-on-updates` in automation.

| Exit code | Meaning |
|---|---|
| `0` | Audit completed |
| `1` | Actionable findings, when `--fail-on-updates` was given |
| `2` | Command-line usage error |
| `3` | Audit error |
| `4` | All selected online sources failed, when `--fail-if-all-sources-fail` was given |

The audit never edits declarations. Review compatibility, release notes,
architecture support, and lockfile changes before upgrading. Record an
intentional temporary exception in `tools/version_audit/deferrals.json` with a
specific reason and review date; don't hide an update by weakening discovery.
The audit also verifies that labeled GitHub dependencies resolve to their
committed SHA pins.

The scheduled [Monthly Version Check workflow](ci.md#monthly-version-check-monthly-version-checkyml)
runs the same tool and opens or updates a GitHub issue when action is needed.

## Upgrade review checklist

For each proposed dependency, platform, container, or GitHub Action upgrade:

1. Confirm the latest stable release from the audit's cited source.
2. Check runtime and architecture compatibility, especially Python
   constraints, CDK CLI/library behavior, and Go directives.
3. Pin GitHub Actions to immutable commit SHAs with a version comment.
4. Update lockfiles with the native package manager.
5. When the OpenEMR container baseline changes, regenerate
   `tools/openemr-import-worker/fresh-seed-manifest.json` with
   `scripts/update-seed-manifest.sh` and review the row-count and fingerprint
   differences.
6. Run the focused tests, then the broader local validation relevant to the
   change.
7. Review synthesized CloudFormation changes and any new cdk-nag findings
   (see [cdk-nag suppressions](../reference/cdk-nag-suppressions.md)).
8. Document a justified deferral instead of applying an unsafe upgrade.

## Read-only repository knowledge MCP

The optional local knowledge server gives MCP-capable assistants bounded,
redacted context about this repository. Start its STDIO entry point from the
repository root after installing the project dependencies:

```bash
.venv/bin/python -m tools.knowledge_mcp
```

The server is constrained to the repository root and is local-only. It has no
write, shell, subprocess, AWS, or network operations; rejects path traversal,
symlinks, secret-like paths, unsupported files, and oversized files; redacts
sensitive output; and returns operational commands as documentation without
running them.

Run its focused checks before changing it:

```bash
.venv/bin/pytest tests/tools/test_knowledge_mcp.py -q
.venv/bin/ruff format --check tools/_shared.py tools/knowledge_mcp tests/tools/test_knowledge_mcp.py
.venv/bin/ruff check tools/_shared.py tools/knowledge_mcp tests/tools/test_knowledge_mcp.py
.venv/bin/mypy tools/_shared.py tools/knowledge_mcp
```

See [Knowledge MCP](knowledge-mcp.md) for client configuration, the full tool
and resource list, output limits, examples, and troubleshooting.

## Release and CI

Keep commits reviewable, and don't commit generated caches, local state, or
credentials. Before a release, check the fork and push target, run the normal
validation, and use the
[Manual Release workflow](ci.md#manual-release-manual-releaseyml). Watch the
regular GitHub Actions checks until they pass, or until a confirmed external
service, permission, or quota problem blocks progress.

## Live deployment E2E and timing

The guarded live E2E runner needs Python 3.14, Node.js 24, Docker with Buildx,
and the repository-pinned CDK and `cdk-assets` binaries installed by `npm ci`.
It rejects global CDK installations and unsupported tool versions.

Tests, formatting, static analysis, and `python -m tools.live_e2e report` are
local-only. `preflight` performs local synthesis and read-only AWS checks;
`run` and `cleanup` change AWS resources and require the full set of account,
ownership, cost, and confirmation guards documented in
[Live E2E](live-e2e.md).

> [!WARNING]
> Don't run `preflight`, `run`, or `cleanup` without explicit maintainer
> approval of the AWS account, the Region, and the expected cost. No regular
> GitHub Actions workflow may invoke an AWS-facing live E2E command.

The sanitized timing data is
[`e2e-results/history.json`](../../e2e-results/history.json), and the
deterministic report generated from it is
[Deployment timing](deployment-timing.md). Commit both files together, and only
after an approved run. Owner-only state under `.live-e2e/` can contain AWS
identifiers and must stay untracked.
