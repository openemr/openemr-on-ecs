# Architecture

This page explains how OpenEMR on ECS is put together: what AWS services it uses,
how they connect, and why they were chosen. It starts with a plain-language
overview for anyone deciding whether to deploy, then goes into the detail that
operators and contributors need. If a term is unfamiliar, check the
[glossary](../get-started/glossary.md).

## Contents

- [The big picture](#the-big-picture)
- [Architecture diagram](#architecture-diagram)
- [Components at a glance](#components-at-a-glance)
- [Component deep dive](#component-deep-dive)
  - [Compute layer](#compute-layer)
  - [Storage layer](#storage-layer)
  - [Caching layer](#caching-layer)
  - [Network layer](#network-layer)
  - [Security layer](#security-layer)
  - [Monitoring and logging](#monitoring-and-logging)
- [Network flows](#network-flows)
- [Security architecture](#security-architecture)
- [Data flow](#data-flow)
- [Credential rotation architecture](#credential-rotation-architecture)
- [Scaling](#scaling)
- [Disaster recovery and high availability](#disaster-recovery-and-high-availability)
- [Design decisions](#design-decisions)
- [Performance considerations](#performance-considerations)
- [Full resource diagram](#full-resource-diagram)
- [Where each piece is defined in code](#where-each-piece-is-defined-in-code)

## The big picture

OpenEMR is a web application: people open it in a browser, it shows patient
records, and it saves what they type into a database. To run it on AWS, this
project creates everything the application needs and wires it together for
you. You don't manage any servers; AWS runs the pieces and you pay for what is
running.

A useful way to picture it is a clinic building:

| Clinic analogy | What it is in this deployment | AWS service |
|---|---|---|
| Security guard at the front door | Blocks known attacks, floods of requests, and bad bots | AWS WAF |
| Receptionist | Accepts every visitor over HTTPS and sends them to a free worker | Application Load Balancer (ALB) |
| Staff who do the work | Copies of the OpenEMR application running in containers; more are added when it gets busy | Amazon ECS on AWS Fargate |
| Locked filing cabinet | The OpenEMR database (patients, appointments, billing) | Amazon Aurora Serverless v2 (MySQL) |
| Shared document room | Uploaded documents, site configuration, and certificates every worker can reach | Amazon EFS |
| Sticky notes on the desk | Fast, short-lived data such as login sessions | Amazon ElastiCache Serverless (Valkey) |
| Key safe | Passwords and encryption keys | AWS Secrets Manager and AWS KMS |
| Off-site archive | Daily, weekly, and monthly copies of the database and files, kept up to 7 years | AWS Backup |

When someone uses OpenEMR:

1. Their browser connects over HTTPS to the load balancer. Only IP addresses you
   allow in `cdk.json` can reach it.
2. AWS WAF inspects the request and blocks it if it looks malicious.
3. The load balancer forwards the request, still encrypted, to one of the
   OpenEMR containers.
4. The container reads and writes the database, the shared file system, and the
   cache, all over encrypted connections inside a private network.
5. The response goes back the same way.

Everything that stores data sits in private subnets with no direct route from
the internet, everything is encrypted, and two copies of the critical parts run
in separate AWS Availability Zones (separate data centers) so a single failure
doesn't take OpenEMR down.

## Architecture diagram

![OpenEMR on AWS Fargate architecture](../../diagrams/architecture.png)

This diagram is generated from the CDK code by
[`diagrams/generate.py`](../../diagrams/generate.py); see
[`diagrams/README.md`](../../diagrams/README.md) for how to regenerate it. A
[full resource diagram](#full-resource-diagram) is at the end of this page.

## Components at a glance

| Component | Purpose | Connects to | Scaling |
|---|---|---|---|
| Application Load Balancer | Distributes traffic and terminates the public TLS connection | WAF, Fargate tasks | Automatic |
| AWS WAF | Web application firewall | Internet, ALB | Fixed (one web ACL) |
| ECS on Fargate | Runs the OpenEMR containers | ALB, EFS, Aurora, ElastiCache | Automatic (2–100 tasks by default) |
| Amazon EFS (2 file systems) | Shared OpenEMR `sites` directory and shared TLS material | Fargate tasks | Automatic |
| Aurora Serverless v2 (MySQL) | Primary database | Fargate tasks | Automatic (0.5–256 ACUs per instance) |
| ElastiCache Serverless (Valkey) | Redis-compatible session and application cache | Fargate tasks | Automatic |
| Secrets Manager | Stores generated credentials | Fargate tasks, rotation task | Managed |
| KMS | Encryption keys | Encrypted services | Managed |
| AWS Backup | Scheduled backups of Aurora and both EFS file systems | Aurora, EFS | Managed |

## Component deep dive

### Compute layer

**ECS on Fargate tasks** run the OpenEMR application containers.

- **CPU architecture:** ARM64 (AWS Graviton), which costs less per hour than
  x86. The image is the official `openemr/openemr` image pinned to a reviewed
  ARM64 digest (see `OPENEMR_VERSION` and `OPENEMR_ARM64_DIGEST` in
  [`openemr_ecs/constants.py`](../../openemr_ecs/constants.py)).
- **Size per task:** 2 vCPU and 4 GB of memory by default
  (`openemr_service_fargate_cpu` and `openemr_service_fargate_memory`).
- **Count:** 2 to 100 tasks by default
  (`openemr_service_fargate_minimum_capacity` and
  `openemr_service_fargate_maximum_capacity`).
- **Placement:** private subnets in two Availability Zones, with Availability
  Zone rebalancing turned on so ECS spreads tasks back out after a zone
  recovers.
- **Safe deployments:** the ECS deployment circuit breaker is enabled with
  automatic rollback, so a release that can't become healthy is rolled back.
  The service keeps 100% of its healthy tasks running while it replaces them.
- **Container Insights:** enabled in enhanced mode on the ECS cluster.

**Health checks.** Two checks decide whether a task is healthy:

| Check | What it does | Settings |
|---|---|---|
| Container health check (ECS) | While OpenEMR's first-time setup is still running, the check passes so ECS doesn't restart the container mid-install. If setup appears stuck for more than 20 minutes, it fails so ECS replaces the task. After setup completes, it runs `curl -f -k https://localhost:443/`. | Start period 120 s, interval 60 s, timeout 10 s, 3 retries |
| Load balancer target health check | HTTPS request to `/` on port 443; a `302` redirect (to the login page) counts as healthy | Interval 60 s, timeout 10 s, healthy after 2 successes, unhealthy after 5 failures |

The ECS service also has a 20-minute (1200 s) health check grace period so a
brand-new installation has time to finish setting up before the load
balancer's result counts.

Other short-lived Fargate tasks are created alongside the main service: one
that generates the internal TLS certificates, a one-off
[credential rotation](#credential-rotation-architecture) task, and optional
tasks for the analytics environment and the guarded OpenEMR import.

### Storage layer

**Amazon EFS (Elastic File System).** Two encrypted file systems are mounted
into every OpenEMR task, with encryption in transit turned on:

1. **Sites file system**, mounted at `/var/www/localhost/htdocs/openemr/sites/`.
   It holds OpenEMR's `sites` directory: uploaded patient documents, site
   configuration (including `sqlconf.php`), and generated files. All tasks
   share it. It is included in the AWS Backup plan.
2. **TLS file system**, mounted at `/etc/ssl/`. It holds the self-signed
   certificates used between the load balancer and the containers. A scheduled
   job regenerates them every 2 days (`DEFAULT_SSL_REGENERATION_DAYS` in
   [`constants.py`](../../openemr_ecs/constants.py)). It is also in the backup
   plan.

**Aurora Serverless v2 (MySQL).** The primary database.

- **Engine:** Aurora MySQL 3.12.0 (`AURORA_MYSQL_ENGINE_VERSION` in
  [`constants.py`](../../openemr_ecs/constants.py)).
- **Instances:** one writer and one reader in the same cluster. The reader is
  set to scale with the writer, and the stack outputs both the writer endpoint
  (`DatabaseEndpoint`) and the reader endpoint (`DatabaseReaderEndpoint`).
- **Capacity:** 0.5 to 256 Aurora Capacity Units (ACUs). The 0.5 ACU minimum
  keeps the database awake so connections are instant; scaling all the way to
  zero would cause 3–5 minute "cold start" delays. See
  [Costs](costs.md#aurora-serverless-v2-pricing-note) for what that minimum
  costs.
- **Encryption:** storage is encrypted at rest; TLS is required for every
  connection (`require_secure_transport=ON` in the parameter group).
- **Observability:** CloudWatch Database Insights in Advanced mode
  (Performance Insights with 15-month retention), and the `audit`, `error`,
  `general`, and `slowquery` logs exported to CloudWatch Logs. Server audit
  logging records connections and queries (`CONNECT, QUERY, QUERY_DCL,
  QUERY_DDL, QUERY_DML, TABLE`), and queries that don't use indexes are logged.
- **Backups:** included in the AWS Backup plan (daily, weekly, and monthly,
  with up to 7-year retention).
- **Deletion protection:** controlled by `rds_deletion_protection`. The
  shipped `cdk.json` sets it to `false` so `cdk destroy` works; if the key is
  missing from context the code defaults to `true`. When the stack is deleted,
  Aurora takes a final snapshot.

### Caching layer

**ElastiCache Serverless (Valkey).** Valkey is an open-source, Redis-compatible
cache. OpenEMR uses it for PHP sessions and application caching.

- Serverless, so there is no capacity to choose or manage.
- Runs in the private subnets; only the OpenEMR tasks' security group can reach
  it, on port 6379.
- All connections use TLS.

### Network layer

**VPC (Virtual Private Cloud).**

- **Address range:** `10.0.0.0/16`. This is the `DEFAULT_CIDR` constant in
  [`constants.py`](../../openemr_ecs/constants.py); it is not a `cdk.json`
  setting.
- **Availability Zones:** two.
- **Subnets:** public subnets hold the load balancer and the NAT gateways;
  private subnets hold the ECS tasks, Aurora, ElastiCache, and EFS mount
  targets. Public subnets don't assign public IP addresses automatically.
- **NAT gateways:** two (one per Availability Zone) so private resources can
  make outbound connections, for example to download certificate bundles.
- **VPC Flow Logs:** all traffic is logged to a KMS-encrypted CloudWatch Logs
  group.
- **Security groups:** each tier has its own security group, and the database,
  cache, EFS, and load balancer groups don't allow unrestricted outbound
  traffic.

**Application Load Balancer (ALB).**

- Internet-facing, in the public subnets.
- Listens on **HTTPS (443) only**. There is no HTTP (80) listener and no
  HTTP-to-HTTPS redirect; the security group only opens port 443 to the CIDR
  ranges in `security_group_ip_range_ipv4` / `security_group_ip_range_ipv6`.
- Uses your ACM certificate (from `route53_domain` or `certificate_arn`) and
  re-encrypts traffic to the containers over HTTPS.
- Drops invalid HTTP header fields, writes access logs to an S3 bucket, and has
  deletion protection enabled (except in guarded live E2E test stacks).

**AWS WAF.** Always created and attached to the ALB. The web ACL contains:

1. AWS Managed Rules Common Rule Set
2. AWS Managed Rules SQL Injection (SQLi) Rule Set
3. AWS Managed Rules Known Bad Inputs Rule Set
4. A rate limit of 2,000 requests per 5 minutes per IP address
5. A rule that blocks user agents containing `bot`, `scraper`, `crawler`, or
   `spider`

WAF logs go to a KMS-encrypted CloudWatch Logs group kept for one week.

**AWS Global Accelerator (optional).** When `enable_global_accelerator` is
`"true"`, an accelerator listens on port 443 in front of the ALB and its URL is
output as `GlobalAcceleratorUrl`.

### Security layer

**AWS Secrets Manager** stores credentials that the stack generates. All of
them are encrypted with the stack's central KMS key.

| Secret | Created when | Contents |
|---|---|---|
| Database admin secret | Always | Aurora admin user `dbadmin` and its password (output `DatabaseSecretARN`) |
| OpenEMR admin password | Always | Password for the OpenEMR `admin` user (name starts with `Password`) |
| RDS slot secret | Always | Two application database users (`A`/`B` slots plus an `active_slot` pointer) used by [credential rotation](#credential-rotation-architecture) (output `RdsSlotSecretARN`) |
| SMTP secrets | `configure_ses` is `"true"` | SES SMTP credentials |

**AWS KMS** keys, all with automatic key rotation enabled:

| Key | Created when | Used for |
|---|---|---|
| Central key | Always | CloudWatch Logs (application, VPC Flow Logs, WAF), Secrets Manager, SNS |
| S3 key | Always (the key is always created) | The import staging bucket when `openemr_import_target` is `true` |
| CloudTrail key | `enable_long_term_cloudtrail_monitoring` is `"true"` (the default) | CloudTrail logs and their buckets |
| ECS Exec key | `enable_ecs_exec` is `"true"` | ECS Exec session logs and bucket |
| Analytics key | `create_serverless_analytics_environment` is `"true"` | SageMaker domain, analytics EFS, export buckets |

Aurora storage and both EFS file systems are encrypted at rest with
AWS-managed keys. The ALB access-log buckets and the SES incoming-mail bucket
use S3-managed encryption (SSE-S3) because the AWS services writing to them
require it; see
[cdk-nag suppressions](cdk-nag-suppressions.md#s3-bucket-configuration).

**TLS certificates.**

| Connection | Certificate |
|---|---|
| Browser to ALB | Your ACM certificate. Required: set `route53_domain` (issued, validated, and renewed automatically) or `certificate_arn`. |
| ALB to containers | Self-signed certificates generated automatically, shared through the TLS EFS file system, and regenerated every 2 days |
| Containers to MySQL | TLS using the Amazon RDS CA bundle, downloaded automatically at container start |
| Containers to Valkey | TLS using the Amazon Root CA, downloaded automatically at container start |

> [!NOTE]
> A certificate is required to deploy. Synthesis fails with a configuration
> error if neither `route53_domain` nor `certificate_arn` is set. See
> [HTTPS and DNS](../guides/https-and-dns.md).

### Monitoring and logging

- **CloudWatch Logs:** application container logs (KMS-encrypted, one-week
  retention), VPC Flow Logs, WAF logs, and Aurora logs.
- **CloudWatch Database Insights** (Advanced) for Aurora and **Container
  Insights** (enhanced) for ECS.
- **CloudTrail (default on):** when `enable_long_term_cloudtrail_monitoring`
  is `"true"`, a trail records all management events in the Region, sends them
  to CloudWatch Logs with 9-year retention, and stores them in an encrypted S3
  bucket that moves objects to Glacier after 90 days and expires them after 7
  years.
- **Alarms (optional):** when `enable_monitoring_alarms` is `"true"`, CloudWatch
  alarms watch ECS CPU and memory, running task count, unhealthy ALB targets,
  HTTP 5xx responses, response time, and stopped tasks (deployment failures),
  and notify through SNS email topics. See
  [Optional features](../guides/optional-features.md).
- **ALB access logs** are written to S3.

## Network flows

### Ingress (user traffic)

```text
Internet
  ↓
AWS WAF (always attached)
  ↓
Application Load Balancer (HTTPS 443)
  ↓
ECS Fargate tasks (HTTPS 443)
  ↓
OpenEMR application
```

### Database access

```text
ECS Fargate task
  ↓
Security group (port 3306)
  ↓
Aurora MySQL (TLS required)
```

### Cache access

```text
ECS Fargate task
  ↓
Security group (port 6379)
  ↓
ElastiCache Valkey (TLS)
```

The ECS task security group is also allowed to reach the EFS mount targets on
NFS port 2049.

## Security architecture

This section describes the technical controls. For compliance obligations,
disclaimers, and how security findings are checked, see
[Security and compliance](security-and-compliance.md).

### Encryption in transit

- **Browser to ALB:** TLS using your ACM certificate.
- **ALB to containers:** HTTPS on port 443 with automatically generated
  self-signed certificates.
- **Database:** TLS with the RDS CA certificate; required by the database.
- **Cache:** TLS with the Amazon Root CA.
- **EFS:** encryption in transit enabled on both mounts.
- **HTTP is never exposed:** no port 80 listener exists and the security group
  only allows 443.

### Encryption at rest

- **EFS:** encrypted.
- **Aurora:** storage encrypted.
- **Secrets Manager:** encrypted with the central customer-managed KMS key.
- **CloudWatch Logs:** application, VPC Flow Log, WAF, CloudTrail, and ECS Exec
  log groups are encrypted with customer-managed KMS keys.
- **S3:** CloudTrail, ECS Exec, analytics export, and import staging buckets
  use customer-managed KMS keys; the ALB access-log and SES incoming-mail
  buckets use SSE-S3 because those services require it.

### Network security

- Security groups with least-privilege rules for each tier.
- The database, cache, EFS mount targets, and ECS tasks are in private subnets.
- NAT gateways provide outbound-only internet access for private resources.
- ECS tasks can't receive traffic directly from the internet; only the ALB can
  reach them.
- Load balancer access is limited to the CIDR ranges you configure
  (`"auto"` by default, which detects and allows only your current public IP).

### Access control

- IAM roles scoped to each task and function. Where a wildcard is unavoidable,
  the reason is recorded as a cdk-nag acknowledgment; see
  [cdk-nag suppressions](cdk-nag-suppressions.md).
- Secrets are injected into containers from Secrets Manager, not stored in the
  task definition.
- ECS Exec (shell access into running containers) is off by default
  (`enable_ecs_exec`).

## Data flow

### Application startup

1. The container starts and downloads the RDS CA bundle and the Amazon Root CA.
2. The certificates are copied into OpenEMR's certificates directory
   (`/var/www/localhost/htdocs/openemr/sites/default/documents/certificates/`).
3. OpenEMR's configuration script runs (on the very first start this performs
   the OpenEMR installation).
4. The database connection is established over TLS.
5. The task passes its health checks and the ALB starts sending it traffic.

### User request

1. The user makes an HTTPS request.
2. AWS WAF filters the request.
3. The ALB routes it to a healthy ECS task.
4. OpenEMR processes the request.
5. Database queries run over TLS.
6. The cache is read and written over TLS.
7. The response is returned to the user.

### Where data lives

| Data | Stored in | Protection |
|---|---|---|
| Patient records and other structured data | Aurora MySQL | Encrypted at rest; TLS in transit; AWS Backup |
| Documents and site configuration | EFS sites file system | Encrypted at rest and in transit; AWS Backup |
| Sessions | ElastiCache Valkey | TLS in transit |
| Backups | AWS Backup vault | Managed by AWS Backup |

## Credential rotation architecture

The stack includes a one-off ECS task that rotates the database credentials
OpenEMR uses without downtime. It rotates **database** credentials only; the
Valkey cache doesn't use stored credentials. Operating instructions are in the
[credential rotation guide](../guides/credential-rotation.md).

- **Model:** a dual-slot secret (`RdsSlotSecretARN`) holds two application
  database users, `openemr_a` and `openemr_b`, plus an `active_slot` pointer.
- **Flip:** the task rewrites OpenEMR's database configuration
  (`sites/default/sqlconf.php` on EFS) to point at the standby slot.
- **Refresh:** it forces a rolling ECS deployment so every task reloads the
  configuration without downtime.
- **Validation gates:** database connectivity with the new credentials and an
  OpenEMR health check.
- **Rollback:** if validation after the flip fails, the previous configuration
  is restored and the service is redeployed.
- **Fail closed:** if rotating the old slot fails, the service stays on the
  currently active slot and the task exits with an error.

The rotation flow:

1. Read the active slot (`A` or `B`).
2. Select the standby slot.
3. Point the application's database configuration at the standby credentials.
4. Trigger a rolling ECS deployment.
5. Validate the database and application health.
6. Rotate the credentials in the old slot.
7. Validate the rotated old slot independently.
8. Save the new active slot.

The task also rotates the Aurora admin (`dbadmin`) password; see the guide for
the complete sequence, including self-healing behavior.

## Scaling

### Application (ECS tasks)

- **Trigger:** target-tracking on average CPU and memory utilization, 40% each
  by default (`openemr_service_fargate_cpu_autoscaling_percentage` and
  `openemr_service_fargate_memory_autoscaling_percentage`).
- **Scale out / in:** add tasks above the target, remove them below it.
- **Range:** 2–100 tasks by default.
- **Time:** typically a few minutes per scaling event.

### Database (Aurora Serverless v2)

- **Trigger:** automatic, based on workload.
- **Range:** 0.5–256 ACUs.
- **Time:** seconds to minutes.
- **Why not zero:** the 0.5 ACU minimum prevents the 3–5 minute cold start
  that happens when the database has scaled to zero.

### Cache (ElastiCache Serverless)

Scales automatically with demand; there is nothing to configure.

## Disaster recovery and high availability

### Backups

- **What:** Aurora and both EFS file systems.
- **Plan:** AWS Backup's `daily_weekly_monthly7_year_retention` plan stores
  daily, weekly, and monthly recovery points in a dedicated backup vault, with
  monthly backups kept for 7 years.
- **Recovery time:** minutes to hours, depending on data size.

> [!WARNING]
> When the stack is destroyed, a cleanup function deletes the recovery points
> in the stack's backup vault so the vault can be removed. Copy any backups you
> want to keep before running `cdk destroy`. See
> [Backup and restore](../guides/backup-and-restore.md).

### High availability

- **Multi-AZ:** ECS tasks, NAT gateways, and the Aurora writer and reader are
  spread across two Availability Zones.
- **Automatic failover:** Aurora promotes the reader if the writer fails; the
  ALB routes around unhealthy targets.
- **Task replacement:** ECS replaces unhealthy tasks automatically.
- **Load distribution:** the ALB spreads traffic across healthy tasks.

## Design decisions

### Why Fargate?

- No servers (EC2 instances) to manage or patch.
- Pay only for running tasks.
- Built-in autoscaling.
- AWS manages the underlying hosts.

### Why Aurora Serverless v2?

- Scales from 0.5 ACU to the maximum based on demand.
- The 0.5 ACU minimum means connections are always instant.
- Pay for capacity used (about $44/month at the 0.5 ACU minimum for one
  instance; see [Costs](costs.md)).
- Multi-AZ with automatic failover.
- Suits workloads whose load varies through the day.

### Why ElastiCache Serverless?

- Fully managed, with automatic scaling.
- Pay only for usage.
- Redis-compatible (Valkey), which OpenEMR supports for sessions.

### Why EFS?

- Many containers can share the same files.
- Grows and shrinks automatically.
- Highly durable and available.
- Native AWS Backup support.

Cost-related design choices (Graviton, serverless services, and ideas such as
VPC endpoints instead of NAT traffic) are covered in
[Costs: ways to reduce cost](costs.md#ways-to-reduce-cost).

## Performance considerations

- **Database:** use Database Insights to find slow queries; the slow query log
  is exported to CloudWatch Logs. A reader endpoint is available for read-only
  connections.
- **Application:** sessions and application data are cached in Valkey. For
  heavy static-asset traffic, consider putting Amazon CloudFront in front (not
  included in this stack).
- **Network:** turn on AWS Global Accelerator for users far from the
  deployment Region. VPC endpoints can reduce NAT gateway traffic. Reuse
  connections (keep-alive) in API clients.

Measured results are in [Load testing](../guides/load-testing.md).

## Full resource diagram

This diagram shows every resource in the synthesized stack. It is generated by
the same script as the compact diagram above.

![Full OpenEMR on ECS resource diagram](../../diagrams/architecture-full.png)

## Where each piece is defined in code

| File | Defines |
|---|---|
| [`app.py`](../../app.py) | CDK app entry point; enables the cdk-nag checks |
| [`openemr_ecs/stack.py`](../../openemr_ecs/stack.py) | Orchestrates the stack, the admin password, and stack outputs |
| [`openemr_ecs/network.py`](../../openemr_ecs/network.py) | VPC, flow logs, security groups, ALB, Global Accelerator |
| [`openemr_ecs/compute.py`](../../openemr_ecs/compute.py) | ECS cluster, OpenEMR service, health checks, autoscaling, ECS Exec, credential rotation task |
| [`openemr_ecs/database.py`](../../openemr_ecs/database.py) | Aurora cluster, parameter group, Valkey cache, rotation slot secret, Bedrock integration |
| [`openemr_ecs/storage.py`](../../openemr_ecs/storage.py) | EFS, S3 log buckets, CloudTrail, AWS Backup plan |
| [`openemr_ecs/security.py`](../../openemr_ecs/security.py) | WAF, certificates and DNS, SES, internal TLS material |
| [`openemr_ecs/kms_keys.py`](../../openemr_ecs/kms_keys.py) | Central and S3 KMS keys |
| [`openemr_ecs/monitoring.py`](../../openemr_ecs/monitoring.py) | Optional CloudWatch alarms and SNS topics |
| [`openemr_ecs/analytics.py`](../../openemr_ecs/analytics.py) | Optional SageMaker / EMR Serverless analytics environment |
| [`openemr_ecs/cleanup.py`](../../openemr_ecs/cleanup.py) | Custom resource that clears blockers during stack deletion |
| [`openemr_ecs/nag_suppressions.py`](../../openemr_ecs/nag_suppressions.py) | cdk-nag acknowledgment helpers |
| [`openemr_ecs/validation.py`](../../openemr_ecs/validation.py) | `cdk.json` configuration validation |
| [`openemr_ecs/constants.py`](../../openemr_ecs/constants.py) | Versions, ports, and fixed values |
