# ADR 0001: Guarded fresh-target OpenEMR import

- Status: Accepted
- Date: 2026-07-31
- Decision owners: OpenEMR on ECS maintainers
- Upstream reference: OpenEMR `v8_2_0`, commit
  `6125a2fd8089c8bcc3848071c1293c60e27a7585`

## Context

Users need a repeatable way to move an existing OpenEMR installation into this
ECS architecture. The source can contain protected health information,
credentials, executable customizations, document-encryption keys, and version-
specific database state. A partial or incompatible import can cause data loss
or expose an inconsistent application.

The upstream
[`interface/main/backup.php`](https://github.com/openemr/openemr/blob/6125a2fd8089c8bcc3848071c1293c60e27a7585/interface/main/backup.php)
produces a final tar archive containing a MySQL dump and
`openemr.tar.gz`. The nested application archive contains the canonical
`version.php` and selected `sites/<site-id>` tree. The project stores OpenEMR
site state on EFS and the application database in Aurora MySQL.

The first implementation should serve the common offline migration case
without introducing continuous replication, a new managed migration service,
or a second long-lived application environment.

## Decision

Use eight separated phases:

1. offline source inspection;
2. deterministic compatibility planning;
3. explicitly approved AWS execution;
4. uncertain-launch reconciliation;
5. explicit local-baseline recovery after hard task termination;
6. read-only status and guarded finalization;
7. verified failed-run abort; and
8. explicit successful-run cleanup.

Automatic execution accepts only one native OpenEMR backup for a same-version,
single-`default`-site import into a stack explicitly deployed with
`openemr_import_target=true`. Every table outside the reviewed OpenEMR 8.2
seed/configuration set must be empty. Seed tables must match exact row counts
and deterministic content fingerprints from the checked-in 8.2 baseline;
runtime timestamp columns are normalized, `globals.gl_value` is excluded
because first-boot writes a generated installation UUID and other
non-deterministic setting values, and credential-derived bootstrap identity
tables use exact counts because their values vary with the generated
administrator. Inspection supports explicit SQL/site directories and manifest
bundles, but those adapters are not executable in the first release.
The planner reads the target from the deployment's authoritative
`StackConstants.OPENEMR_VERSION` pin.

The context flag is a synthesis boundary, not only an execution guard. With
the key absent or false, the template contains no import bucket, EFS access
point, task definition, image asset, IAM grant, policy, or output. The complete
import surface is constructed only when the key is true.

Execution uses:

- a short-lived, private, KMS-encrypted S3 staging bucket;
- a conditional stack-wide S3 import lock whose ETag is retained for a
  conditional release (the version ID is retained as diagnostic evidence);
- a dormant ARM64 Fargate task definition;
- an OpenEMR target image pinned by both official release tag and ARM64 digest;
- a digest-pinned worker base image (which fixes the Alpine release branch the
  worker's packages resolve from), hash-locked Python artifacts, and a
  checksummed RDS CA bundle;
- private subnets, no public IP, and a dedicated no-ingress security group with
  only MySQL, NFS, and HTTPS egress;
- an IAM-authorized import-only sites EFS access point, with write and
  atomic-rename checks before database replacement;
- Secrets Manager injection for Aurora administrator credentials;
- RDS TLS certificate verification;
- execution of source SQL through an ephemeral TLS-only account whose
  privileges are limited to the target OpenEMR schema;
- MariaDB sandbox, binary, and disabled local-infile modes plus full-stream
  rejection scans for filesystem/shell client commands and stored executable
  definitions;
- fully suspended ECS desired-count autoscaling and a stopped OpenEMR service
  for the full mutation window;
- database emptiness plus exact SQL, `version.php`, target semantic-version,
  and database-schema-version checks immediately before replacement;
- an EFS-local rollback copy plus verified Aurora and EFS AWS Backup recovery
  points; and
- an application health check before declaring success or deleting rollback
  artifacts.

The worker stages validated source data in ephemeral storage before changing
Aurora or EFS. It replaces the database first and then swaps the EFS
`sites/default` directory on the same file system. This is not globally atomic.
Any failure leaves the application service stopped.

The target's `sqlconf.php`, application configuration, server-control files,
and RDS CA file are preserved. Only a narrow data allowlist is overlaid:

- `documents`
- `images`
- `LBF`
- standard click, fax, and referral template files

Source application code and source connection configuration are not copied.
Script-like files in imported data block execution.

## Alternatives considered

### Native OpenEMR backup

Chosen as the only executable source. It is produced by upstream OpenEMR,
contains the logical database and selected application/site tree, and can be
validated offline.

### SQL dump plus site-data archive

Supported for inspection because it is useful for diagnostics and future
bundles. It is not executable yet: provenance and pairing are weaker than a
single native backup unless a separately governed manifest is required.

### Manifest-based migration bundle

Supported for inspection with checksums. Deferred for execution until the
manifest schema has signing/provenance policy and real interoperability tests.

### Direct extraction from a source server

Rejected. It would require source credentials, remote filesystem assumptions,
network access to a clinical environment, and more complex failure handling.

### Online migration or continuous replication

Rejected. DMS or dual-write behavior adds substantial cost and operational
complexity, while OpenEMR's EFS data and encryption keys still require a
separate consistency mechanism.

### Private one-off Fargate task

Chosen. It reuses the deployed VPC, EFS, Secrets Manager, and logging model,
while using a dedicated least-privilege security group and isolating import
code from the normal application task.

### ECS Exec

Rejected as the transfer mechanism. ECS Exec is interactive, does not provide
a safe bulk file-copy protocol, is difficult to resume deterministically, and
would mutate a normal application task.

### Local Aurora tunnel and local import process

Rejected. It exposes database connectivity to the operator workstation, still
needs a separate EFS transfer, and makes credentials and network setup harder
to constrain.

### Encrypted S3 staging

Chosen. It provides checksummed transfer, lifecycle expiration, scoped IAM, and
private task access. Staging is temporary and never the durable backup.

### AWS DMS

Rejected for the first release. It does not migrate EFS site data, is excessive
for a planned downtime migration, and introduces replication infrastructure
that small maintainership would need to operate.

### Import into a populated target

Rejected. No safe conflict-resolution policy exists for patient identifiers,
encounters, documents, users, ACLs, or encryption keys. The worker permits
rows only in the reviewed OpenEMR 8.2 seed/configuration table set and refuses
rows in every other table. It also rejects seed row-count or deterministic
content drift rather than treating arbitrary configuration changes as fresh.

### Version upgrades during import

Rejected. The utility does not invent an SQL upgrade sequence. Older sources
must first follow OpenEMR's supported upgrade behavior; newer sources are
incompatible with the target.

### Multisite

Inspection reports all site IDs. Execution is deferred until per-site routing,
configuration, encryption keys, and validation have dedicated tests.

### Custom application code

Inventory is reported and execution is blocked. Custom modules, themes, and
application patches must be reviewed and ported to the target image through a
separate software-delivery process.

## Threat model and controls

The source archive is untrusted even when supplied by an authorized operator.
Controls cover:

- traversal, absolute paths, drive paths, and NULs;
- symbolic links, hard links, devices, and FIFOs;
- duplicate and case-colliding members;
- member count, member size, total expanded size, nested artifact size, and
  compression ratio;
- malformed tar, zip, gzip, SQL, and version data;
- source-path swaps or checksum changes between inspection and execution,
  prevented by a no-follow owner-only execution snapshot;
- arbitrary source executable content;
- accidental target-account, region, stack, or version selection;
- concurrent or repeated import attempts against one stack;
- minimum-capacity or scheduled scaling restarting the application during
  mutation;
- production or populated targets;
- missing recovery points;
- credentials in commands or logs; and
- partially imported applications.

Reports contain aggregates and checksums only. They omit SQL, document content,
archive member names, credentials, endpoints, and patient values.

## Data-loss and recovery boundaries

Aurora replacement is destructive and cannot be made atomic with the EFS
directory swap. Autoscaling remains suspended and the service remains stopped
during both operations. Recovery
uses the exact Aurora and EFS AWS Backup recovery-point ARNs recorded in the
owner-only local receipt. The EFS rollback copy is additional convenience, not
a substitute for those recovery points.

If the task fails, times out, has an uncertain launch result, or application
health fails, the service remains stopped and autoscaling remains suspended.
Operators must use the bounded task-reconciliation flow before recovery.
Successful automatic rollback or pre-mutation failure can be explicitly
aborted only after independent target-baseline verification; abort restores
and health-checks the service, removes the failed migration scope, and permits
a newly planned attempt with a new migration ID. Successful-run cleanup remains
unavailable until the worker reports success, the restored service is healthy,
and autoscaling is active.

## Operational prerequisites

- source and target are stable OpenEMR releases with exactly matching versions;
- the target is new, initialized, non-production, and empty;
- account, region, stack, profile, downtime, cost, and destructive-operation
  acknowledgements have been reviewed;
- current completed Aurora and EFS recovery points exist;
- the updated CDK stack and import task definition are deployed; and
- the operator has only the documented CloudFormation, ECS, Application Auto
  Scaling, Backup, S3, STS, and `iam:PassRole` permissions.

## Consequences

Benefits:

- inspection and planning require no AWS credentials or network;
- execution is explicit, isolated, and recoverable;
- normal application tasks remain unchanged;
- the source/target policy is understandable by a small maintainer team; and
- synthetic fixtures cover archive and orchestration safeguards.

Costs and limitations:

- downtime is required;
- only same-version, single-site, fresh-target imports execute;
- two recovery systems must be understood;
- explicitly enabling import staging and the worker adds temporary CDK
  resources and IAM permissions; and
- global database/EFS atomicity is not provided.

## Future extension path

Extensions require a new ADR or amendment plus tests. Likely sequence:

1. signed or otherwise governed manifest-bundle execution;
2. synthetic import against live TLS MySQL in the optional local E2E / Floci
   profile (worker-centric MySQL+sites CI coverage already exists via
   `scripts/ci-import-worker-mysql.sh`);
3. validated older-version imports that deliberately invoke an upstream
   supported upgrade path;
4. multisite mapping and validation; and
5. multipart upload resume without weakening fail-closed behavior.
