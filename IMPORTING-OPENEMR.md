# Importing an Existing OpenEMR Installation

The import utility is conservative by design. Inspection and planning are local,
offline, read-only operations. AWS access is available only through explicit
execution, launch-reconciliation, local-baseline recovery, finalization, abort,
status, and cleanup
commands.

No import has been run as part of implementing this utility.

The architecture and rejected alternatives are recorded in
[ADR 0001](docs/adr/0001-guarded-openemr-import.md).

## First-release support policy

Automatic execution supports only all of the following:

- one native OpenEMR backup archive produced by `interface/main/backup.php`;
- a stable source whose `version.php`, SQL `version` row, database schema
  version, and deployed OpenEMR container version all match;
- one site named `default`;
- a recognizable MySQL or MariaDB logical dump;
- both current document-encryption key files (`sevena` and `sevenb`), with
  valid versioned encrypted-key framing;
- a newly initialized, non-production target with no patients, encounters, or
  documents; and
- recent completed AWS Backup recovery points for both Aurora and the sites EFS
  file system.

The utility inspects directory and manifest bundles, but does not execute them.
It also refuses upgrades, downgrades, multisite imports, missing encryption
keys, custom executable content in imported data, prerelease versions,
PostgreSQL, and in-place imports into used targets.

These limits are intentional. Cross-version migrations must first use
OpenEMR's supported upgrade path outside this utility.

## What is imported

The worker imports the logical database and a narrow site-data allowlist:

- `sites/default/documents`
- `sites/default/images`
- `sites/default/LBF`
- the standard click, fax, and referral template files

The deployed target's `sqlconf.php`, application configuration, server-control
files, and RDS CA material are preserved. Source application files and source
database credentials are never copied. Script-like files inside imported data
cause execution to stop.

The policy follows the native backup structure in the pinned upstream
[`backup.php`](https://github.com/openemr/openemr/blob/6125a2fd8089c8bcc3848071c1293c60e27a7585/interface/main/backup.php).
The same-version restriction avoids invoking undocumented upgrade behavior
during an infrastructure import.

## Offline workflow

Install development dependencies in an isolated environment:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

Inspect the source without extracting its file trees and without AWS calls,
subprocesses, or network access:

```bash
.venv/bin/python -m tools.openemr_import inspect \
  /secure/path/openemr-backup.tar \
  --output /secure/path/inspection.json
```

The inspection report contains aggregate counts, versions, checksums, and
policy findings. It never contains SQL, archive member names, document content,
credentials, or patient values.

Create a deterministic review artifact:

```bash
.venv/bin/python -m tools.openemr_import plan \
  /secure/path/inspection.json \
  --output /secure/path/import-plan.json
```

An exit status of `2` means the plan was generated but blocks execution. Review
`blockers`, `warnings`, `preconditions`, and `rollback` before proceeding.
The planner's target version comes directly from
`StackConstants.OPENEMR_VERSION`; it is not maintained as a second import-tool
version pin.

## Deployment prerequisites

Deploy a new, non-production target with the explicit import-target marker:

```bash
node_modules/.bin/cdk deploy -c openemr_import_target=true
```

When the context key is absent or false, CDK synthesizes no import staging
bucket, EFS access point, task definition, worker image asset, import grants,
or import outputs. The executor therefore refuses a normal stack even if every
CLI confirmation is supplied. The import-target deployment adds:

- a dormant ARM64 Fargate task definition (it never runs automatically);
- a private, versioned, KMS-encrypted S3 staging bucket where source archive
  versions expire after one day, bounded status/recovery evidence expires after
  30 days, and incomplete multipart uploads abort after one day;
- a 50 GiB ephemeral work volume;
- task-role access only to read `migrations/*/source.tar`, write
  `migrations/*/status.json`, use the staging KMS key, and mount/write the
  sites EFS through one IAM-authorized access point;
- an import-only EFS access point that maps writes to root with OpenEMR's
  application group (`gid 101`), normalizes imported content to `0770/0660`,
  and runs a write/rename probe before any database replacement;
- a no-ingress import security group with only MySQL, sites-EFS NFS, and
  outbound HTTPS paths; and
- CloudFormation outputs used to bind the CLI to one account, region, stack,
  exact database/EFS security-group targets, filesystem, and access point.

Execution also requires the import-target stack to be less than 24 hours old
and to have no CloudFormation update timestamp. This prevents enabling import
mode later on an existing stack; if the window expires or an update is needed,
destroy the unused target and create a new import-target stack.

The operator needs narrowly scoped permission to:

- `sts:GetCallerIdentity` and `cloudformation:DescribeStacks` for the named
  stack;
- `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroups`,
  `rds:DescribeDBClusters`, `elasticfilesystem:DescribeFileSystems`,
  `elasticfilesystem:DescribeAccessPoints`, and `kms:DescribeKey` for the
  emitted resources;
- `ecs:DescribeTaskDefinition`, `ecs:DescribeServices`, `ecs:UpdateService`,
  `ecs:RunTask`, `ecs:ListTasks`, and `ecs:DescribeTasks` for the emitted
  cluster, service, and import task definition, plus `iam:PassRole` only for
  that task's execution and task roles;
- `application-autoscaling:DescribeScalableTargets` and
  `application-autoscaling:RegisterScalableTarget` for the selected ECS
  service;
- `backup:ListRecoveryPointsByResource` for the emitted Aurora and EFS ARNs;
- `s3:ListBucket`, `s3:GetEncryptionConfiguration`,
  `s3:ListBucketVersions`, and `s3:ListBucketMultipartUploads` on the emitted
  staging bucket;
- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`,
  `s3:PutObjectTagging`, `s3:DeleteObjectVersion`, and
  `s3:AbortMultipartUpload` only for `locks/active.json` and the selected
  `migrations/<migration-id>/` prefix; and
- `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey`, and `kms:DescribeKey`
  only for the emitted staging KMS key.

Do not grant broad administrator access solely for this workflow.

## Explicit execution

Execution causes downtime and destructive database replacement. It first
copies the source through a no-follow descriptor into an owner-only immutable
snapshot, re-inspects that snapshot, verifies the plan byte-for-byte against current policy,
checks account/region/stack/version/import-target mode, verifies current
recovery points, acquires a stack-wide conditional lock, uploads the source
with KMS encryption, fully suspends desired-count scaling, and stops the
OpenEMR service. The worker then revalidates checksums and archive safety,
rejects SQL client commands and stored executable definitions while enabling
MariaDB sandbox and binary modes, proves every table outside the reviewed
OpenEMR 8.2 seed/configuration set is empty, and verifies exact row counts plus
content fingerprints against the checked-in 8.2 baseline. Runtime timestamps
are excluded from fingerprints, and `globals.gl_value` is excluded because
first-boot writes a generated `unique_installation_id` and other
non-deterministic setting values. The five credential-derived bootstrap
identity tables retain exact row-count checks because their content depends on
the generated administrator identity. Fingerprint queries use binary column
ordering through the worker's pinned MariaDB client so ordering is independent
of server collation. The fresh target must also contain no routines, events, or
triggers; the rollback dump skips stored-code discovery for Aurora MySQL
compatibility. The worker then imports the database,
atomically swaps the site directory, and validates the result. Before
the first mutation, it writes a TLS-protected logical dump of the verified-fresh
target database into the migration's owner-only EFS rollback directory.

The exact confirmation token is:

```text
IMPORT:<account-id>:<region>:<stack-name>:<configuration_fingerprint>
```

After the mandatory approval checkpoint, execution has this form:

```bash
.venv/bin/python -m tools.openemr_import execute \
  --plan /secure/path/import-plan.json \
  --source /secure/path/openemr-backup.tar \
  --account-id 123456789012 \
  --region us-east-1 \
  --stack-name OpenEMR \
  --profile approved-profile \
  --allow-aws-execution \
  --confirm-fresh-target \
  --confirm-non-production-target \
  --confirm-downtime \
  --confirm-recovery-points \
  --confirm-destructive-import \
  --confirmation-token 'IMPORT:123456789012:us-east-1:OpenEMR:<fingerprint>'
```

The command waits for the task. On success it restores the original ECS desired
count, checks the HTTPS login page, and resumes autoscaling. On a worker or
health failure, the service remains stopped and autoscaling remains suspended,
so the configured minimum capacity cannot restart tasks during database or EFS
mutation. If a worker failure occurs after mutation begins, the worker first
attempts to restore both the baseline database dump and the original EFS
`default` directory; status records whether that rollback succeeded. If
task-launch status is uncertain, the deterministic ECS `startedBy` value is the
migration ID; use the guarded reconciliation command before any recovery
action:

```bash
.venv/bin/python -m tools.openemr_import reconcile-launch \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirmation-token 'RECONCILE:import-<id>'
```

If no task is visible after the command's minimum uncertainty window, it still
requires `--confirm-no-task-launched` and a second ECS query before restoring
the service. No-task recovery is allowed only inside the default 15-to-45
minute bounded window (configurable up to 55 minutes), while ECS still retains
stopped-task evidence. Outside that window it fails closed and preserves the
service, staging data, and lock for manual investigation. A successful
no-launch reconciliation removes only that migration's staging scope and
conditionally releases its ETag-bound lock.

The same reconciliation path resumes an interrupted prelaunch compensation:
it first proves no task exists, restores the service only when quiescing had
started, retries migration-scoped staging deletion and conditional lock
release, and writes a completion tombstone. A failure in any compensation step
remains durable as `prelaunch-cleanup-required` rather than claiming cleanup.

Local execution receipts are mode `0600` in an owner-only, no-symlink
`.openemr-import/` directory, which is gitignored. They contain resource
identifiers, an immutable S3 lock identity, and recovery-point references, but
no credentials or health data.

If the S3 lock request loses its response before returning an ETag, execution
records `lock-outcome-unknown` and stops before staging, autoscaling, service,
or task mutations. It does not guess that the lock was absent or delete an
unidentified object; inspect the current `locks/active.json` metadata and S3
request evidence before removing that lock manually.

## Status, recovery, and finalization

Status is read-only:

```bash
.venv/bin/python -m tools.openemr_import status \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile
```

If the import task succeeded but the original execute process was interrupted,
restart and health-check the service explicitly:

```bash
.venv/bin/python -m tools.openemr_import finalize \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirmation-token 'FINALIZE:import-<id>'
```

Never finalize a failed or uncertain import. Even when the worker reports a
successful automatic rollback, keep the service stopped until the original
target state has been independently checked. If automatic rollback failed,
or if the ECS task was forcibly terminated during database/EFS mutation, first
retry the worker-created local baseline through a separate guarded task:

```bash
.venv/bin/python -m tools.openemr_import recover \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirm-restore-local-baseline \
  --confirmation-token 'RECOVER:import-<id>'
```

Recovery is authorized only when the original task is identity-verified and
stopped with a nonzero exit during a mutating phase, or its automatic rollback
failed. It restores the pre-mutation logical database dump and original EFS
directory, drops the temporary import user, revalidates the fresh-target
baseline, and deliberately leaves the service stopped and autoscaling
suspended. Verify that baseline independently, then use `abort`.

If the local baseline is unavailable or recovery fails, restore the
recovery-point ARNs recorded in the local receipt:

1. restore Aurora and EFS using the repository backup/restore procedure;
2. verify the restored database and `sites/default` tree;
3. restore the ECS desired count while autoscaling remains suspended;
4. verify the HTTPS login page and application behavior;
5. resume ECS service autoscaling; and
6. retain the failed staging evidence until the incident is understood.

AWS Backup restores are not performed automatically because Aurora and EFS
restore jobs create replacement resources that must be deliberately reconciled
with CloudFormation. Before approving an import, maintainers must rehearse the
organization's resource-replacement runbook using the exact Aurora and EFS
recovery-point workflow in [BACKUP-RESTORE-GUIDE.md](BACKUP-RESTORE-GUIDE.md).
Do not treat the presence of a recovery point as proof that replacement and
application validation have been rehearsed.

After independently verifying a successful automatic rollback (or a
pre-mutation failure), explicitly abort the failed attempt:

```bash
.venv/bin/python -m tools.openemr_import abort \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirm-target-baseline-verified \
  --confirmation-token 'ABORT:import-<id>'
```

Abort restores and health-checks the service, resumes autoscaling, deletes only
the failed migration's EFS/S3 artifacts, conditionally releases the
ETag-matched lock object, and leaves an idempotent tombstone. Generate a new inspection and plan
before retrying; the new plan receives a new migration scope.

The worker also keeps the original EFS `default` directory under a
migration-scoped rollback path. This is a convenience copy, not a replacement
for AWS Backup.

## Cleanup

Cleanup permanently deletes the migration's EFS rollback copy and baseline
database dump, every version
and delete marker for its S3 source and status objects, releases the
ETag-bound stack-wide lock, and atomically replaces the local receipt with a
minimal owner-only completion tombstone. The tombstone contains only the
migration ID and cleanup result, so repeated `status` and `cleanup` commands
return the completed result without repeating AWS mutations.
It is allowed only after the worker reports success, the original ECS task
count is restored, autoscaling is active, and the HTTPS login page is healthy:

```bash
.venv/bin/python -m tools.openemr_import cleanup \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirm-delete-rollback-copy \
  --confirmation-token 'CLEANUP:import-<id>'
```

After cleanup, update the stack with `-c openemr_import_target=false` (or remove
the key) to deprovision the import-only resources and permissions. Import mode
cannot be re-enabled later on this used stack: execution accepts only a newly
created, never-updated import target.

Keep the original source backup in approved secure storage according to the
organization's retention and PHI-handling policy.

## CI coverage for the import worker

GitHub Actions runs `scripts/ci-import-worker-mysql.sh` in the
`import-worker-mysql-integration` job. That script boots the TLS OpenEMR/MariaDB
compose stack, builds and executes the production ARM64 worker under QEMU when
the runner is not ARM64, builds an ephemeral synthetic native backup from the initialized
schema, and drives the worker's database-replacement and sites-swap helpers
(happy path, automatic rollback, and explicit hard-termination recovery). It
does not exercise S3 staging, ECS task
launch, or AWS Backup.

Locally (slow; pulls images and waits for OpenEMR bootstrap):

```bash
OPENEMR_IMPORT_MYSQL_INTEGRATION=1 pytest -m integration tests/tools/test_openemr_import_worker_mysql.py
# or
./scripts/ci-import-worker-mysql.sh
```

The `import-worker-seed-manifest` job runs
`scripts/update-seed-manifest.sh --check`, which recomputes the fresh-seed
manifest from the compose-pinned OpenEMR image and fails when the checked-in
manifest drifts from it. After an intentional OpenEMR baseline upgrade,
regenerate the manifest locally and commit the result:

```bash
./scripts/update-seed-manifest.sh
```

## Security notes

- Run inspection on an encrypted local volume with restrictive permissions.
- Treat the source archive as PHI even though generated reports are redacted.
- The implementation rejects traversal, absolute paths, case collisions,
  duplicate members, links, devices, FIFOs, oversized members, archive bombs,
  malformed compression, and encrypted zip members.
- Database credentials come from ECS Secrets Manager integration and never
  appear in task commands or logs.
- The worker uses the administrator credential only to recreate the target
  schema and provision a temporary TLS-only, target-schema-scoped import user;
  source SQL runs as that limited user, which is deleted afterward.
- Database connections require the AWS RDS CA and certificate verification.
- The task receives no public IP, runs only in the stack's private subnets, and
  uses a dedicated no-ingress security group.
- The worker base image digest, Python dependency artifacts, and RDS CA bundle
  checksum are pinned; production builds exclude test harness files. Alpine
  packages are deliberately not pinned to exact versions: the base image digest
  already fixes the Alpine release branch, and Alpine mirrors only serve the
  current build of each package, so exact pins break the build on every
  upstream security rebuild without adding reproducibility.
- The import utility never executes source application code.
