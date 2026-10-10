# Importing an existing OpenEMR

This guide shows you how to move the data from an existing OpenEMR installation (its database and
site files) into a brand-new deployment of this stack, using the project's guarded import tool.
It's for administrators migrating a working OpenEMR to AWS. The tool is deliberately
conservative: it checks everything it can, refuses anything it can't safely handle, and asks you
to confirm every risky step.

> [!NOTE]
> The import tool hasn't been run against a real production migration as part of its
> development. Rehearse with non-production copies first. The design and the alternatives that
> were rejected are recorded in [ADR 0001](../adr/0001-guarded-openemr-import.md).

## Before you begin

You need:

- **A native OpenEMR backup** of the source, made with OpenEMR's built-in backup
  (`interface/main/backup.php`, under **Admin** > **System** > **Backup** in most versions), as a
  single `.tar` file.
- **The same OpenEMR version on both sides.** The source must be exactly the version this stack
  deploys (currently **8.4.1**). Upgrade the source first with OpenEMR's normal upgrade process
  if it's older.
- **A brand-new, non-production target stack** deployed in import mode (step 3), less than 24
  hours old and never updated.
- **Python 3** to run the tool locally, plus the AWS CLI with a narrowly scoped profile (see
  [Permissions you need](#permissions-you-need)).
- **A secure workstation:** an encrypted disk with restrictive file permissions. Treat the backup
  file as protected health information (PHI) at every step.

**Time:** inspection and planning take minutes. The import itself depends on data size; the tool
waits up to 960 checks for the import task by default.

**Cost:** import mode adds a small, short-lived S3 staging bucket, a KMS key, and a one-off
Fargate task (1 vCPU, 2 GB memory, 50 GiB temporary disk). Most cost is the normal stack's.

> [!WARNING]
> An import **replaces** the target database and site files and takes OpenEMR offline while it
> runs. Only ever import into a fresh target you're willing to throw away.

## What the tool supports

Automatic import works only when **all** of these are true:

- the source is one native OpenEMR backup archive produced by `interface/main/backup.php`;
- the source is stable, and its `version.php`, the SQL `version` row, the database schema version,
  and the target's OpenEMR container version all match;
- there's exactly one site, named `default`;
- the database dump is a recognizable MySQL or MariaDB logical dump;
- both current document-encryption key files (`sevena` and `sevenb`) are present, with valid
  versioned encrypted-key framing;
- the target is newly initialized and non-production, with no patients, encounters, or documents;
  and
- the target has recent, completed AWS Backup recovery points for both the Aurora database and the
  sites EFS file system.

The tool can *inspect* directory and manifest bundles but won't *import* them. It refuses
upgrades, downgrades, multisite installs, missing encryption keys, custom executable content in
imported data, prerelease versions, PostgreSQL, and imports into targets that are already in use.

These limits are intentional. Moving between OpenEMR versions must happen through OpenEMR's
supported upgrade path, outside this tool, so the import never relies on undocumented upgrade
behavior.

## What gets imported

The database (as a logical dump) and only these site folders and files:

- `sites/default/documents`
- `sites/default/images`
- `sites/default/LBF`
- the standard click, fax, and referral template files

The target keeps its own `sqlconf.php`, application configuration, server-control files, and RDS
certificate authority (CA) files. The source's application files and database credentials are
never copied. If any script-like file turns up inside the imported data, the import stops.

This list follows the structure of the native backup in the pinned upstream
[`backup.php`](https://github.com/openemr/openemr/blob/6125a2fd8089c8bcc3848071c1293c60e27a7585/interface/main/backup.php).

## Step 1: Set up the tool

From the repository root, create an isolated Python environment:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Step 2: Inspect and plan (offline)

These two commands run entirely on your computer. They make no AWS calls, start no other
programs, use no network, and never extract the archive's files to disk.

1. Inspect the backup:

   ```bash
   .venv/bin/python -m tools.openemr_import inspect \
     /secure/path/openemr-backup.tar \
     --output /secure/path/inspection.json
   ```

   The report has counts, versions, checksums, and policy findings only. It never contains SQL,
   file names from the archive, document content, credentials, or patient data.

   `--source-version` lets you state the source version when it can't be detected, but it can't
   authorize an automatic import of a native backup that's missing `version.php`.

2. Create the plan, a repeatable review document:

   ```bash
   .venv/bin/python -m tools.openemr_import plan \
     /secure/path/inspection.json \
     --output /secure/path/import-plan.json
   ```

   An exit status of `0` means the plan allows execution. An exit status of `2` means the plan was
   written but **blocks** execution. Either way, read its `blockers`, `warnings`,
   `preconditions`, and `rollback` sections, and note its `configuration_fingerprint`; you'll need
   it in step 5.

   The plan's target version comes straight from the version this stack deploys
   (`StackConstants.OPENEMR_VERSION` in
   [`openemr_ecs/constants.py`](../../openemr_ecs/constants.py)), so there's no separate version to
   keep in sync.

## Step 3: Deploy a fresh import target

Deploy a **new** stack with import mode turned on:

```bash
node_modules/.bin/cdk deploy -c openemr_import_target=true
```

Without this setting, the stack contains none of the import resources, and the tool refuses to
run against it even if you give every confirmation. Import mode adds:

- a dormant ARM64 Fargate task definition that never runs on its own;
- a private, versioned, KMS-encrypted S3 staging bucket where uploaded source archives expire after
  one day, status and recovery records expire after 30 days, and unfinished uploads are cancelled
  after one day;
- a 50 GiB temporary work volume for the task;
- task permissions that only allow reading `migrations/*/source.tar`, writing
  `migrations/*/status.json`, using the staging KMS key, and mounting and writing the sites EFS
  through one IAM-authorized access point;
- an import-only EFS access point that writes as root with OpenEMR's application group
  (`gid 101`), normalizes imported permissions to `0770` (folders) and `0660` (files), and runs a
  write-and-rename test before touching the database;
- a security group that accepts no incoming connections and allows only MySQL, sites-EFS (NFS),
  and outbound HTTPS traffic; and
- stack outputs that tie the tool to one account, Region, stack, database and EFS security group,
  file system, and access point: `OpenEMRImportTaskDefinitionArn`, `OpenEMRImportTargetMode`
  (`fresh-target-only`), `OpenEMRImportStagingBucketName`, `OpenEMRImportStagingKmsKeyArn`,
  `OpenEMRImportSecurityGroupId`, `OpenEMRImportDatabaseSecurityGroupId`,
  `OpenEMRImportEfsSecurityGroupId`, `OpenEMRImportEfsAccessPointId`, `PrivateSubnetIds`,
  `DatabaseClusterArn`, and `OpenEMRVersion`.

> [!IMPORTANT]
> The tool only accepts a target stack that's **less than 24 hours old and has never been
> updated**. This stops anyone from switching import mode on for an existing, in-use stack. If the
> window passes or you need to change the stack, destroy the unused target and deploy a new one.

## Step 4: Make sure recovery points exist

The tool requires completed AWS Backup recovery points for the target's Aurora cluster and sites
file system, no older than 36 hours by default (`--maximum-recovery-age-hours`). Scheduled
backups run at 05:00 UTC, so a new stack may not have any yet. Create them now and wait for the
jobs to finish:

```bash
STACK_NAME=OpenemrEcsStack AWS_DEFAULT_REGION=us-east-1 ./scripts/create-backup.sh
STACK_NAME=OpenemrEcsStack AWS_DEFAULT_REGION=us-east-1 ./scripts/list-backups.sh
```

See [Backup and restore](backup-and-restore.md#create-a-backup-now).

> [!IMPORTANT]
> Before approving an import, rehearse your organization's procedure for restoring Aurora and EFS
> from these exact recovery points and checking the application afterward, using
> [Backup and restore](backup-and-restore.md). Having a recovery point isn't proof that you can
> restore from it.

## Step 5: Run the import

Running the import causes downtime and destructively replaces the database. Get the approval
your organization requires first.

The confirmation token has this exact form, using the fingerprint from your plan:

```text
IMPORT:<account-id>:<region>:<stack-name>:<configuration_fingerprint>
```

Run:

```bash
.venv/bin/python -m tools.openemr_import execute \
  --plan /secure/path/import-plan.json \
  --source /secure/path/openemr-backup.tar \
  --account-id 123456789012 \
  --region us-east-1 \
  --stack-name OpenemrEcsStack \
  --profile approved-profile \
  --allow-aws-execution \
  --confirm-fresh-target \
  --confirm-non-production-target \
  --confirm-downtime \
  --confirm-recovery-points \
  --confirm-destructive-import \
  --confirmation-token 'IMPORT:123456789012:us-east-1:OpenemrEcsStack:<fingerprint>'
```

Other options: `--state PATH` to choose where the local receipt is written,
`--maximum-recovery-age-hours` (default `36`), and `--maximum-wait-attempts` (default `960`).

**What success looks like:** the command waits for the import task, then restores the service's
original task count, checks that the HTTPS login page responds, and turns autoscaling back on.
Log in to OpenEMR and check your data.

### What happens during the import

Before changing anything in AWS, the tool:

1. copies the source (without following links) into a private, read-only snapshot and inspects
   that snapshot again;
2. checks the plan byte for byte against the current policy;
3. checks the account, Region, stack, version, and import-target mode;
4. checks for current recovery points; and
5. takes a stack-wide lock (a conditional S3 object), uploads the source with KMS encryption, fully
   suspends autoscaling, and stops the OpenEMR service.

The import task then:

1. checks checksums and archive safety again;
2. rejects SQL client commands and stored executable definitions, and runs the MariaDB client in
   sandbox and binary modes;
3. proves the target is truly fresh: every table outside the reviewed OpenEMR seed and
   configuration set must be empty, and row counts and content fingerprints must exactly match the
   checked-in baseline for the deployed version
   ([`fresh-seed-manifest.json`](../../tools/openemr-import-worker/fresh-seed-manifest.json),
   currently for 8.4.1). Runtime timestamps aren't fingerprinted, nor is `globals.gl_value`,
   because first boot writes a generated `unique_installation_id` and other non-repeatable
   settings. The five bootstrap identity tables that depend on the generated administrator
   account are checked by row count only. Fingerprint queries sort by binary column order using
   the task's pinned MariaDB client, so results don't depend on the server's collation. The target
   must also have no stored routines, events, or triggers;
4. before the first change, writes a TLS-protected logical dump of the fresh target database into
   the migration's private rollback folder on EFS (the dump skips stored-code discovery for
   compatibility with Aurora MySQL);
5. imports the database, swaps the site folder in one atomic step, and checks the result.

The task also keeps the original EFS `default` folder in a migration-specific rollback folder.
That's a convenience copy, not a replacement for AWS Backup.

### Local receipts

The tool writes a local receipt to `.openemr-import/import-<id>.json` in the repository. The folder
is owner-only, refuses symlinks, and is ignored by Git; receipts have mode `0600`. Receipts contain
resource IDs, the S3 lock's identity, and recovery-point references, but no credentials or health
data. You pass the receipt to every later command with `--state`.

## Check status

Status never changes anything:

```bash
.venv/bin/python -m tools.openemr_import status \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile
```

## If something goes wrong

The tool fails *closed*: when in doubt, it leaves OpenEMR stopped rather than risk restarting it
against half-imported data. Find your situation below.

### The import task failed

If the task or the post-import health check fails, the service stays stopped and autoscaling
stays suspended, so the minimum task count can't restart OpenEMR mid-change. If the failure
happened after changes began, the task first tries to restore both the baseline database dump and
the original EFS `default` folder, and the status records whether that worked.

Even if automatic rollback succeeded, keep the service stopped until you've checked the original
target state yourself. Then [abort](#abort-a-failed-attempt).

### Automatic rollback failed, or the task was killed mid-change

Retry the local baseline restore in a separate guarded task:

```bash
.venv/bin/python -m tools.openemr_import recover \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirm-restore-local-baseline \
  --confirmation-token 'RECOVER:import-<id>'
```

This is only allowed when the original task is confirmed to be the right one and stopped with a
nonzero exit code during a changing phase, or its automatic rollback failed. It restores the
pre-change database dump and the original EFS folder, removes the temporary import database user,
checks the fresh-target baseline again, and deliberately leaves the service stopped and autoscaling
suspended. Check the baseline yourself, then [abort](#abort-a-failed-attempt).

### The local baseline is unavailable or recovery failed

Restore from the AWS Backup recovery points recorded in your local receipt:

1. Restore Aurora and EFS using [Backup and restore](backup-and-restore.md).
2. Check the restored database and the `sites/default` folder.
3. Restore the ECS service's desired task count while autoscaling stays suspended.
4. Check the HTTPS login page and how the application behaves.
5. Resume autoscaling for the ECS service.
6. Keep the failed staging evidence until you understand what happened.

The tool doesn't run AWS Backup restores itself, because Aurora and EFS restores create
replacement resources that must be deliberately reconciled with CloudFormation.

### You're not sure whether the import task started

If the tool can't tell whether ECS launched the task, look it up by its `startedBy` value, which
is the migration ID, using the guarded reconciliation command before any recovery:

```bash
.venv/bin/python -m tools.openemr_import reconcile-launch \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirmation-token 'RECONCILE:import-<id>'
```

If no task is visible after the minimum waiting window, you must also pass
`--confirm-no-task-launched`, and the tool queries ECS a second time before restoring the service.
"No task" recovery is only allowed between 15 and 45 minutes after the uncertain launch
(`--minimum-unknown-age-minutes`, `--maximum-unknown-age-minutes`; the maximum can be raised to 55)
while ECS still keeps records of stopped tasks. Outside that window, the command fails closed and
leaves the service, staging data, and lock in place for manual investigation. A successful
"no launch" reconciliation removes only that migration's staging data and releases its lock (only
if the lock's ETag still matches).

The same command finishes an interrupted pre-launch cleanup: it proves no task exists, restores the
service only if stopping it had begun, retries deleting the migration's staging data and releasing
the lock, and writes a completion record. If any of those steps fails, the state is recorded as
`prelaunch-cleanup-required` rather than claiming cleanup succeeded.

### The lock outcome is unknown

If the request to take the S3 lock loses its response before returning an ETag, the tool records
`lock-outcome-unknown` and stops before staging, autoscaling, service, or task changes. It doesn't
assume the lock was absent or delete an object it can't identify. Check the current
`locks/active.json` object's metadata and your S3 request records before removing that lock by
hand.

### The import succeeded but the command was interrupted

If the task succeeded but the `execute` command didn't finish, restart and health-check the
service explicitly:

```bash
.venv/bin/python -m tools.openemr_import finalize \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirmation-token 'FINALIZE:import-<id>'
```

> [!CAUTION]
> Never finalize a failed or uncertain import.

### Abort a failed attempt

After you've checked a successful automatic rollback (or a failure before any changes), abort:

```bash
.venv/bin/python -m tools.openemr_import abort \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirm-target-baseline-verified \
  --confirmation-token 'ABORT:import-<id>'
```

Abort restores and health-checks the service, resumes autoscaling, deletes only the failed
migration's EFS and S3 files, releases the lock (if its ETag still matches), and writes a record
so running it again is harmless. To retry, generate a new inspection and plan; the new plan gets a
new migration ID.

## Step 6: Clean up after a successful import

Cleanup permanently deletes the migration's EFS rollback copy and baseline database dump, and
every version and delete marker of its S3 source and status objects. It releases the stack-wide
lock and replaces your local receipt with a minimal owner-only completion record (just the
migration ID and cleanup result), so running `status` or `cleanup` again returns the result
without touching AWS. It's only allowed after the task reported success, the original task count
is restored, autoscaling is active, and the HTTPS login page is healthy.

```bash
.venv/bin/python -m tools.openemr_import cleanup \
  --state .openemr-import/import-<id>.json \
  --profile approved-profile \
  --allow-aws-execution \
  --confirm-delete-rollback-copy \
  --confirmation-token 'CLEANUP:import-<id>'
```

Then remove the import-only resources and permissions by deploying with import mode off:

```bash
node_modules/.bin/cdk deploy -c openemr_import_target=false
```

(or remove the key from `cdk.json`). Import mode can't be turned on again for this stack, because
the tool only accepts a newly created, never-updated target.

Keep the original source backup in approved secure storage, according to your organization's
retention and PHI-handling policies.

## Permissions you need

Give the operator's AWS profile only what the workflow needs. Don't grant broad administrator
access for this.

- `sts:GetCallerIdentity`, and `cloudformation:DescribeStacks` for the named stack;
- `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroups`, `rds:DescribeDBClusters`,
  `elasticfilesystem:DescribeFileSystems`, `elasticfilesystem:DescribeAccessPoints`, and
  `kms:DescribeKey` for the stack's emitted resources;
- `ecs:DescribeTaskDefinition`, `ecs:DescribeServices`, `ecs:UpdateService`, `ecs:RunTask`,
  `ecs:ListTasks`, and `ecs:DescribeTasks` for the stack's cluster, service, and import task
  definition, plus `iam:PassRole` only for that task's execution and task roles;
- `application-autoscaling:DescribeScalableTargets` and
  `application-autoscaling:RegisterScalableTarget` for the OpenEMR ECS service;
- `backup:ListRecoveryPointsByResource` for the emitted Aurora and EFS ARNs;
- `s3:ListBucket`, `s3:GetEncryptionConfiguration`, `s3:ListBucketVersions`, and
  `s3:ListBucketMultipartUploads` on the staging bucket;
- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:PutObjectTagging`,
  `s3:DeleteObjectVersion`, and `s3:AbortMultipartUpload` only for `locks/active.json` and the
  selected `migrations/<migration-id>/` prefix; and
- `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey`, and `kms:DescribeKey` only for the staging
  KMS key.

## Security notes

- Run inspection on an encrypted local volume with restrictive permissions.
- Treat the source archive as PHI, even though the generated reports are redacted.
- The tool rejects path traversal, absolute paths, case collisions, duplicate entries, links,
  devices, FIFOs, oversized entries, archive bombs, malformed compression, and encrypted zip
  entries.
- Database credentials come from ECS's Secrets Manager integration and never appear in task
  commands or logs.
- The task uses the administrator credential only to recreate the target schema and create a
  temporary, TLS-only import user limited to the target schema. Source SQL runs as that limited
  user, which is deleted afterward.
- Database connections require the AWS RDS CA and verify the server certificate.
- The task gets no public IP address, runs only in the stack's private subnets, and uses its own
  security group that accepts no incoming connections.
- The import task's base image is pinned by digest and its Python dependencies are pinned by hash;
  production builds leave out test files. The RDS CA bundle is downloaded at build time over
  HTTPS only from AWS's trust store (`https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`)
  and is **not** checksum-pinned, because AWS republishes it in place whenever it adds a Region.
  Alpine packages are deliberately not pinned to exact versions: the base image digest already
  fixes the Alpine release branch, and Alpine mirrors only serve the current build of each
  package, so exact pins would break the build on every upstream security rebuild without making
  builds more reproducible.
- The tool never runs source application code.

## For maintainers: testing the import task

CI tests the import task against a real database (the `import-worker-mysql-integration` job, which
runs `scripts/ci-import-worker-mysql.sh`) and checks the fresh-seed manifest for drift (the
`import-worker-seed-manifest` job, which runs `scripts/update-seed-manifest.sh --check`). These
tests don't exercise S3 staging, ECS task launch, or AWS Backup. To run them locally, and to
regenerate the manifest after an intentional OpenEMR upgrade, see
[CI](../maintainers/ci.md). The quick versions:

```bash
OPENEMR_IMPORT_MYSQL_INTEGRATION=1 pytest -m integration tests/tools/test_openemr_import_worker_mysql.py
# or
./scripts/ci-import-worker-mysql.sh

# regenerate the fresh-seed manifest after an intentional baseline upgrade
./scripts/update-seed-manifest.sh
```

The integration script boots the TLS OpenEMR and MariaDB Compose stack, builds and runs the
production ARM64 import image (under QEMU emulation on non-ARM64 runners), builds a temporary
synthetic native backup from the initialized schema, and drives the database-replacement and
sites-swap steps (the happy path, automatic rollback, and recovery after a hard termination). It's
slow because it pulls images and waits for OpenEMR to start.

## Related

- [ADR 0001: Guarded OpenEMR import](../adr/0001-guarded-openemr-import.md)
- [Backup and restore](backup-and-restore.md)
- [Configuration](configuration.md): the `openemr_import_target` setting
- [Security and compliance](../reference/security-and-compliance.md)
