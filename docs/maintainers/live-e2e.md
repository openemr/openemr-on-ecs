# Guarded live E2E deployment test

This guide is for maintainers who run the live end-to-end (E2E) test: a tool
that deploys a real, isolated OpenEMR stack into AWS, checks that it works,
records how long each phase took, and then deletes it. Because it creates real,
billable AWS resources, the tool is wrapped in many safety guards, all
described here.

> [!CAUTION]
> **Do not run `preflight`, `run`, or `cleanup` until the maintainer has
> explicitly approved a live AWS test, the exact AWS account and Region, and
> the expected cost.** Normal CI and `python -m tools.live_e2e report` do not
> create or change AWS resources.

The AWS-facing modes (`preflight`, `plan`, `run`, and `cleanup`) are
intentionally local-only and are never invoked by GitHub Actions. Ordinary CI
does exercise the utility's unit tests and local report generation. To test the
tooling itself without AWS, use the [Floci emulator](floci.md).

## Contents

- [Safety model](#safety-model)
- [Profiles](#profiles)
- [Approved workflow](#approved-workflow)
- [Interrupted-run cleanup](#interrupted-run-cleanup)
- [Timing and local evidence](#timing-and-local-evidence)

## Safety model

**Where it can run**

- `CI` must be unset, and AWS-facing commands require an interactive local
  terminal unless the explicit `--noninteractive` option is used. All other
  confirmations and ownership guards remain mandatory. Setting `CI` still
  blocks every AWS-facing mode.
- The `live_e2e_run_id` CDK context is rejected unless the guarded runner
  supplies the matching process marker.

**Which account, DNS zone, and network it touches**

- The account is supplied explicitly and compared byte-for-byte with AWS STS
  (the AWS identity service) before synthesis and again before deployment.
  Only a local HMAC-derived opaque account label reaches committed timing
  history.
- A dedicated public Route 53 hosted-zone subdomain is required. Preflight
  rejects any records other than the zone's NS and SOA delegation records and
  binds the exact hosted-zone ID into synthesis. Don't use a zone that serves
  another application.
- Access is restricted to one explicit, globally routable IPv4 `/32` (a
  single IP address).

**What preflight checks**

- Preflight is read-only in AWS. It checks identity, Region, two available
  standard Availability Zones, the hosted zone, CDK bootstrap version, relevant
  read permissions, representative deployment permissions through IAM policy
  simulation, current quota headroom, Docker, and local tools. It then
  synthesizes without further lookups and runs a template-only
  `cdk diff --no-change-set`.
- Preflight expires after four hours and is bound to a clean Git commit, the
  exact owner-only account ID, Region, Availability Zones, profile, inputs,
  hosted-zone ID, tool versions, and the CDK and `cdk-assets` executable
  contents. `run` rechecks the supported Python and Node.js toolchain and both
  executable fingerprints before deployment.

**How deployment is approved**

- Deployment uses the fingerprinted cloud assembly produced by preflight; it
  does not silently synthesize a different stack after approval.
- Deployment requires two exact confirmation phrases, a cost acknowledgement,
  and an environment variable containing the unique run ID.
- Every run gets a unique CloudFormation stack, resource suffix, and
  `LiveE2ERunId` tag.
- The maintained profiles explicitly disable CloudTrail, Global Accelerator,
  analytics, Bedrock, SES, alarms, and ECS Exec, and pin desired capacity to
  one task, unless a profile deliberately overrides a feature.

**How cleanup works**

- Cleanup verifies an ownership marker embedded in the stack template before it
  can delete anything.
- Cleanup runs in a `finally` block, including after validation and deployment
  failures. A separate cleanup command supports interrupted local processes.
- E2E Aurora clusters use `RemovalPolicy.DESTROY`, and E2E load balancers and
  Aurora disable deletion protection. Production stacks keep their existing
  snapshot and protection behavior.
- Explicit VPC, WAF, and application log groups use delete policies in E2E
  stacks; normal stack log-retention policies are unchanged.
- Aurora log groups created outside the stack are inventoried from the exact
  owned RDS resource before deletion, then explicitly removed and verified.

**What may remain after cleanup**

- KMS keys can remain visible in `PendingDeletion` after a successful stack
  deletion. The report labels those delayed deletions as expected residuals;
  any other residual makes the run fail.
- Content-addressed file and container-image assets remain in the account's
  shared CDK bootstrap bucket and repository. The runner verifies and reports
  them as expected shared residuals rather than deleting assets that another
  stack may use.

> [!WARNING]
> The utility can't protect against a process that is forcibly terminated
> before its `finally` block runs. Keep the run ID and use the guarded
> [cleanup command](#interrupted-run-cleanup) in that case.

**Keeping a failed run for diagnosis**

By default, every failed run is cleaned up. Diagnostic retention is available
only with both `--keep-on-failure` and
`--confirm-keep-on-failure "KEEP FAILED E2E"`. It leaves billable resources
running and must be followed by the cleanup-only command. The report marks such
a run `retained-on-failure`, never as cleaned up.

**Scripting and output options**

For local scripting, `preflight`, `run`, and `cleanup` accept
`--noninteractive`; all account, phrase, environment, cost, ownership, and CI
guards remain active. `--json` emits machine-readable output on stdout, while
phase progress and elapsed-time heartbeats always go to stderr. `--verbose`
adds additional safe local diagnostics.

## Profiles

A profile is a fixed, maintained set of stack settings for the test.

- `default` exercises the core ECS, Aurora, EFS, Valkey, load balancer, WAF,
  backup, and encryption architecture with one application task and optional
  cost-heavy features disabled.
- `api-enabled` additionally enables the OpenEMR APIs, patient portal, and
  Aurora Data API.

List maintained profiles (local only) with:

```bash
python -m tools.live_e2e profiles
```

## Approved workflow

> [!IMPORTANT]
> Every step in this section needs prior maintainer approval of the account,
> Region, and expected cost.

Use Python 3.14.x and Node.js 24.x. Activate the project virtual environment,
run `npm ci` to install the pinned local AWS CDK and `cdk-assets` CLIs, and make
sure the worktree is clean. Node.js 22 and global CDK installations are not
supported by this runner. Replace every placeholder below; don't copy
documentation IP addresses.

1. Run the read-only preflight and local synthesis:

   ```bash
   python -m tools.live_e2e preflight \
     --approved-account <12-digit-account-id> \
     --region <approved-region> \
     --route53-domain <dedicated-e2e-hosted-zone> \
     --allowed-ipv4-cidr <operator-public-ip>/32 \
     --profile default \
     --confirm-dedicated-zone "DEDICATED E2E ZONE" \
     --confirm-non-production-account "NON-PRODUCTION ACCOUNT"
   ```

   The command prints an owner-only `.live-e2e/preflight/<run-id>.json` path.
   It does not deploy the application stack. `python -m tools.live_e2e plan`
   is an equivalent explicit dry-run alias; both commands include the
   template-only diff.

2. Review the preflight result and get explicit approval for that run ID.

3. Export the one-run approval guard and run the deployment:

   ```bash
   export OPENEMR_LIVE_E2E_APPROVED_RUN_ID=<run-id>
   python -m tools.live_e2e run \
     --preflight .live-e2e/preflight/<run-id>.json \
     --approved-account <12-digit-account-id> \
     --confirm-create "CREATE LIVE E2E" \
     --confirm-destroy "DESTROY LIVE E2E" \
     --confirm-costs
   unset OPENEMR_LIVE_E2E_APPROVED_RUN_ID
   ```

The runner validates the CloudFormation stack, expected resource types, Aurora
availability, both EFS file systems, ECS desired count and stability, target
health, and an HTTPS response containing OpenEMR. It never retrieves
application or database secret values.

## Interrupted-run cleanup

Use this only with the original run ID, account, Region, and explicit cleanup
approval. The worktree must be clean except for the timing history and report
written by the failed or retained run:

```bash
export OPENEMR_LIVE_E2E_APPROVED_RUN_ID=<run-id>
python -m tools.live_e2e cleanup \
  --run-id <run-id> \
  --approved-account <12-digit-account-id> \
  --region <approved-region> \
  --confirm-destroy "DESTROY LIVE E2E"
unset OPENEMR_LIVE_E2E_APPROVED_RUN_ID
```

Cleanup refuses any stack whose `LiveE2ERunId` output or original template
doesn't match exactly.

## Timing and local evidence

Owner-only logs, synthesis output, raw results, and state live under
`.live-e2e/` and are ignored by Git. The committed history contains only
sanitized measurements and metadata: repository, branch, and commit; one-way
account and stack identifiers; Region; profile and configuration fingerprint;
bootstrap state; runtime and platform versions; statuses; failure phase; and
hashed residual identifiers.

Measured phases include:

- Preflight, tool validation, synthesis, and template-only diff
- Structured Docker asset build timing and CDK asset publication
- Local CDK deployment duration, from a monotonic clock
- CloudFormation deployment and deletion, and Aurora, ElastiCache, EFS, and
  ECS creation, from AWS event timestamps
- Local validation latency for ECS steady state, target health, startup logs,
  and smoke checks; first-observed HTTPS readiness from stack creation; and
  post-deploy HTTP wait and request timing
- Cleanup request, residual verification, cleanup, and total duration

Regenerate the deterministic report without contacting AWS:

```bash
python -m tools.live_e2e report
```

The source data is [`e2e-results/history.json`](../../e2e-results/history.json),
and the generated report is [Deployment timing](deployment-timing.md). Until an
approved run completes, both deliberately contain no timing claims.
