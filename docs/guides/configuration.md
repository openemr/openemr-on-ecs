# Configuration

This guide explains every setting you can change in `cdk.json`: what it does, its default, and
when you'd want to change it. It's for anyone deploying this project who wants to control who
can reach OpenEMR, how big the servers are, or which optional features are turned on.

## Before you begin

- You've cloned the repository and installed its dependencies. If not, follow the
  [Get Started guide](../get-started/README.md) first.
- **Time:** a few minutes to edit a setting; a redeploy usually takes 10–40 minutes depending on
  what changed.
- **Cost:** most settings here are free to change, but bigger servers, more servers, and
  optional features increase your AWS bill. Each optional feature's guide calls out its cost.
  See [Costs](../reference/costs.md) for the baseline.

> [!TIP]
> New to terms like *CIDR*, *Fargate task*, or *context*? See the
> [glossary](../get-started/glossary.md).

## How settings work

All settings live in the `"context"` block of [`cdk.json`](../../cdk.json) at the root of the
repository. The AWS CDK (the tool that turns this project's Python code into AWS resources) reads
them every time you run `synth`, `diff`, or `deploy`.

A few rules apply to every setting:

- **On/off settings are strings.** Most feature flags are written as `"true"` or `"false"` (with
  quotes). The code treats a value as "on" only when it's the word `true` (any capitalization,
  quoted or not). Anything else, including `"yes"`, `"1"`, or leaving the key out, means "off".
- **`null` means "not set".** For example, `"route53_domain": null` means you're not using a
  Route 53 domain.
- **You can override a setting for one command** without editing the file, using `-c`:

  ```bash
  node_modules/.bin/cdk deploy -c enable_ecs_exec=true
  ```

  The override only applies to that one command. The next `deploy` without `-c` goes back to
  whatever `cdk.json` says, so for lasting changes, edit the file.
- **Settings are checked before anything is built.** The project validates your settings first
  (see [`openemr_ecs/validation.py`](../../openemr_ecs/validation.py)) and stops with a
  `Context validation failed: ...` message if something is wrong. See
  [If something goes wrong](#if-something-goes-wrong).

> [!IMPORTANT]
> Always use the repository's pinned CDK command, `node_modules/.bin/cdk`, not a globally
> installed `cdk`. The pinned version is the one this project is tested with.

## Change a setting

1. Open `cdk.json` and find the setting inside `"context"`. Change its value. For example, to
   allow two OpenEMR servers minimum and four maximum:

   ```json
   "openemr_service_fargate_minimum_capacity": 2,
   "openemr_service_fargate_maximum_capacity": 4,
   ```

2. Check that the settings are valid and the project still builds:

   ```bash
   node_modules/.bin/cdk synth > /dev/null
   ```

   Success looks like the command finishing without a `Context validation failed` error.
   (Security-check warnings from cdk-nag are explained in
   [cdk-nag suppressions](../reference/cdk-nag-suppressions.md).)

3. Preview what will change in your deployed stack:

   ```bash
   node_modules/.bin/cdk diff
   ```

   Read the output. Lines marked `[-]` or `[~] ... (replacement)` mean AWS will delete or replace
   a resource. Be careful with anything that replaces the database or file systems.

4. Apply the change:

   ```bash
   node_modules/.bin/cdk deploy
   ```

   Success looks like `✅  OpenemrEcsStack` followed by the stack outputs.

## Required: a TLS certificate

Every deployment serves OpenEMR only over HTTPS, so you must set **one** of these two settings
or validation fails:

| Setting | Default | What it does |
|---|---|---|
| `route53_domain` | `null` | A domain you host in Route 53 in the same AWS account, such as `example.com`. The stack creates and renews a certificate for you and publishes OpenEMR at `https://openemr.example.com`. Don't include `http://`, `https://`, or a trailing dot. |
| `certificate_arn` | `null` | The ARN of an existing, issued AWS Certificate Manager (ACM) certificate in the same Region you deploy to. Must start with `arn:aws:acm:`. |

If you set both, `certificate_arn` wins and no new certificate or DNS record is created. The full
walkthrough, including optional email, is in [HTTPS and DNS](https-and-dns.md).

## Who can reach OpenEMR (IP access)

The load balancer (the public entry point to OpenEMR) only accepts HTTPS (port 443) connections
from the address ranges you allow. Nothing else on the internet can reach it.

| Setting | Default | What it does |
|---|---|---|
| `security_group_ip_range_ipv4` | `"auto"` | Which IPv4 addresses may connect, written as a CIDR range (an address plus a `/number` saying how many addresses it covers). `"auto"` looks up the public IP address of the computer running the CDK command and allows only that one address (`/32`). |
| `security_group_ip_range_ipv6` | `null` | Which IPv6 addresses may connect, such as `"2001:db8::/32"` or `"::/0"` for all. `null` allows no IPv6 access. |

Common choices:

| You want to allow | Set `security_group_ip_range_ipv4` to |
|---|---|
| Just the computer you deploy from | `"auto"` |
| One fixed address, such as `203.0.113.10` | `"203.0.113.10/32"` |
| Your office network, such as `203.0.113.0`–`203.0.113.255` | `"203.0.113.0/24"` |
| Everyone on the internet | `"0.0.0.0/0"` |
| No IPv4 access at all | `null` |

To find your current public IPv4 address, run:

```bash
curl https://checkip.amazonaws.com
```

> [!WARNING]
> `"auto"` is resolved again **every time** anyone runs `synth`, `diff`, or `deploy`. If a
> teammate deploys from a different network, or your home IP address changes and you redeploy,
> access moves to the new address and the old one is locked out. For anything longer-lived than
> a quick test, set an explicit CIDR range. `"auto"` also needs internet access when you synthesize.

> [!CAUTION]
> `"0.0.0.0/0"` exposes the OpenEMR login page to the whole internet. The web application
> firewall still applies, but only do this if you understand the risk and have strong passwords.

Only one IPv4 range and one IPv6 range are supported. Each must be a single CIDR string, not a
list.

## Server size and automatic scaling

OpenEMR runs as containers on AWS Fargate (serverless compute for containers). Each running copy
is called a *task*. These settings control how big each task is and how many run. The tasks use
ARM64 (AWS Graviton) processors.

| Setting | Default | What it does |
|---|---|---|
| `openemr_service_fargate_cpu` | `2048` | CPU per task, in CPU units (1024 = 1 vCPU). Allowed: `256`, `512`, `1024`, `2048`, `4096`. |
| `openemr_service_fargate_memory` | `4096` | Memory per task, in MiB (4096 = 4 GB). Must be a valid pairing with the CPU value; see the table below. |
| `openemr_service_fargate_minimum_capacity` | `2` | Fewest tasks that run at all times. Must be at least 1. This is also the number started on first deploy. |
| `openemr_service_fargate_maximum_capacity` | `100` | Most tasks autoscaling may add. Must be at least 1 and not less than the minimum. |
| `openemr_service_fargate_cpu_autoscaling_percentage` | `40` | Target average CPU use (1–100). Above it, tasks are added; well below it, tasks are removed. |
| `openemr_service_fargate_memory_autoscaling_percentage` | `40` | Target average memory use (1–100), working the same way as the CPU target. |

Valid CPU and memory pairings (these are Fargate's rules, enforced by validation):

| CPU units | Memory (MiB) you may choose |
|---|---|
| `256` | `512`, `1024`, `2048` |
| `512` | `1024`, `2048`, `3072`, `4096` |
| `1024` | `2048` through `8192`, in steps of `1024` |
| `2048` | `4096` through `16384`, in steps of `1024` |
| `4096` | `8192` through `30720`, in steps of `1024` |

When to change these:

- **Increase CPU** for heavy reporting, complex queries, or lots of concurrent users.
- **Increase memory** if tasks are stopped with out-of-memory errors, or for very large patient
  datasets.
- **Decrease either** to save money when usage is consistently low.
- **Lower the minimum to 1** to halve compute cost for a test environment. Keep at least 2 for
  production so one task can fail without an outage.
- **Lower the maximum** to cap how much autoscaling can ever cost you.

> [!TIP]
> Always set CPU and memory together. If you remove one of them from `cdk.json`, validation
> assumes a different default for the missing one and may reject the pair.

Changing CPU or memory creates a new task definition, and ECS replaces tasks one at a time
(a *rolling deployment*), so OpenEMR stays available.

## Optional features (on/off switches)

Each of these is off by default except long-term CloudTrail. Turning one on usually adds AWS
resources and cost. Follow the linked guide before enabling it.

| Setting | Default | What it turns on | Guide |
|---|---|---|---|
| `activate_openemr_apis` | `"false"` | OpenEMR's REST and FHIR APIs | [REST and FHIR APIs](apis.md) |
| `enable_patient_portal` | `"false"` | The OpenEMR patient portal at `/portal/` | [Optional features](optional-features.md#patient-portal) |
| `enable_ecs_exec` | `"false"` | Shell access into running containers, and database port forwarding | [Database access](database-access.md) |
| `enable_data_api` | `"false"` | The RDS Data API (run SQL over HTTPS) | [Database access](database-access.md#use-the-rds-data-api) |
| `enable_bedrock_integration` | `"false"` | Aurora machine learning calls to Amazon Bedrock | [Optional features](optional-features.md#aurora-machine-learning-with-amazon-bedrock) |
| `enable_global_accelerator` | `"false"` | An AWS Global Accelerator endpoint in front of the load balancer | [Optional features](optional-features.md#aws-global-accelerator) |
| `enable_monitoring_alarms` | `"false"` | CloudWatch alarms with email alerts | [Optional features](optional-features.md#monitoring-alarms-and-email-alerts) |
| `monitoring_email` | `null` | Email address for alarm notifications | [Optional features](optional-features.md#monitoring-alarms-and-email-alerts) |
| `deployment_notification_email` | `null` | Email address subscribed to a deployment-events topic | [Optional features](optional-features.md#monitoring-alarms-and-email-alerts) |
| `enable_long_term_cloudtrail_monitoring` | `"true"` | A CloudTrail trail with long-term, encrypted log retention | [Optional features](optional-features.md#long-term-cloudtrail-audit-logging) |
| `configure_ses` | `"false"` | Amazon SES email sending from OpenEMR (needs `route53_domain`) | [HTTPS and DNS](https-and-dns.md#optional-send-email-with-amazon-ses) |
| `email_forwarding_address` | `null` | Forwards mail sent to `help@<your domain>` to this address (needs `configure_ses`) | [HTTPS and DNS](https-and-dns.md#optional-send-email-with-amazon-ses) |
| `create_serverless_analytics_environment` | `"false"` | SageMaker Studio, EMR Serverless, and export pipelines | [Analytics](analytics.md) |
| `openemr_import_target` | `false` | Import-only resources for migrating an existing OpenEMR into a **brand-new** stack | [Importing an existing OpenEMR](importing-openemr.md) |

## Protecting against accidental deletion

| Setting | Default | What it does |
|---|---|---|
| `rds_deletion_protection` | `false` | Turns on Aurora deletion protection so the database can't be deleted by mistake. If you delete this key from `cdk.json` entirely, the code defaults to **on**. |
| `disable_rds_deletion_protection_on_destroy` | `false` | When `true`, forces Aurora deletion protection **off**, overriding `rds_deletion_protection`. It only takes effect when you **deploy** with it. |
| `enable_stack_termination_protection` | `false` | Turns on CloudFormation termination protection for the whole stack, so `cdk destroy` and console deletes are refused until you turn it off. Adds a `StackTerminationProtection` stack output. |

When to change these: turn on `rds_deletion_protection` and `enable_stack_termination_protection`
for production. Leave them off for short-lived test stacks so cleanup is easy.

How deleting works with protection on:

- **Database deletion protection.** When the stack is deleted, a cleanup step built into the
  stack turns Aurora deletion protection off first, so `cdk destroy` normally succeeds anyway. If
  you want to turn protection off explicitly beforehand, deploy with the override and then
  destroy:

  ```bash
  node_modules/.bin/cdk deploy -c disable_rds_deletion_protection_on_destroy=true
  node_modules/.bin/cdk destroy
  ```

  Passing `-c disable_rds_deletion_protection_on_destroy=true` to `cdk destroy` alone doesn't
  change the deployed database, because `destroy` doesn't update resources.
- **Stack termination protection.** Turn it off before deleting. Either set
  `enable_stack_termination_protection` back to `false` and run `cdk deploy`, or run:

  ```bash
  aws cloudformation update-termination-protection \
    --no-enable-termination-protection \
    --stack-name OpenemrEcsStack
  ```

> [!WARNING]
> Deleting the stack also deletes the AWS Backup recovery points in the stack's backup vault.
> Read [Backup and restore](backup-and-restore.md#before-you-delete-a-stack) before destroying
> anything you might need to recover.

## Database (MySQL) settings

These are passed to the Aurora MySQL parameter group. Each must be a positive whole number,
written as a string. Set one to `null` to leave MySQL's own default in place.

| Setting | Default | Unit | What it controls |
|---|---|---|---|
| `net_read_timeout` | `"30000"` | seconds | How long the server waits for more data from a connection. [MySQL docs](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_net_read_timeout) |
| `net_write_timeout` | `"30000"` | seconds | How long the server waits while writing to a connection. [MySQL docs](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_net_write_timeout) |
| `wait_timeout` | `"30000"` | seconds | How long an idle connection stays open. [MySQL docs](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_wait_timeout) |
| `connect_timeout` | `"30000"` | seconds | How long the server waits for a connection handshake. [MySQL docs](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_connect_timeout) |
| `max_execution_time` | `"3000000"` | milliseconds | Longest a `SELECT` may run before it's stopped. [MySQL docs](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_max_execution_time) |
| `aurora_ml_inference_timeout` | `"30000"` | milliseconds | Longest an Aurora ML call to Bedrock may take. Only used when `enable_bedrock_integration` is on. [AWS docs](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/mysql-ml.html#using-amazon-bedrock) |

The long defaults suit large OpenEMR reports and imports. You rarely need to change them.

## Resource naming

| Setting | Default | What it does |
|---|---|---|
| `openemr_resource_suffix` | not set | A short string added to some resource names (the Aurora cluster `openemr-cluster-<suffix>`, the Valkey cache, the backup vault `<stack>-vault-<suffix>`, and the IDs of the EFS file systems and ECS cluster) so several stacks can share an account and Region. If you don't set it, a 6-character suffix is derived from the stack name, so it stays the same on every deploy. |

> [!CAUTION]
> Don't add or change `openemr_resource_suffix` on a stack that's already deployed. Changing it
> renames the database, file systems, and cache, which makes CloudFormation create new, empty
> ones and remove the old ones. Pick a value (or leave it unset) before your first deploy.

## Settings you shouldn't change

- `live_e2e_run_id`, `live_e2e_availability_zones`, `live_e2e_emulated`, and
  `route53_hosted_zone_id` are reserved for the project's maintainer test runners. Setting
  `route53_hosted_zone_id` on a normal deployment fails on purpose. See
  [Live end-to-end tests](../maintainers/live-e2e.md).
- `aliases` is present in `cdk.json` but isn't read by the stack.
- Keys starting with `@aws-cdk/` are CDK feature flags that control how CDK generates resources.
  Leave them as they are.
- `"app"` and `"watch"` at the top of `cdk.json` tell the CDK how to run this project.

## If something goes wrong

Validation errors appear as `Context validation failed: ...` before anything is created. The
most common ones:

| Message contains | What to do |
|---|---|
| `Either 'route53_domain' or 'certificate_arn' must be provided` | Set one of them. See [HTTPS and DNS](https-and-dns.md). |
| `Invalid Fargate memory ... for CPU ...` | Pick a memory value from the [pairing table](#server-size-and-automatic-scaling). |
| `Invalid Fargate CPU value` | Use `256`, `512`, `1024`, `2048`, or `4096`. |
| `must be between 1 and 100` | Fix the autoscaling percentage. |
| `Minimum capacity ... cannot be greater than maximum capacity` | Make the minimum less than or equal to the maximum. |
| `security_group_ip_range_ipv4 must be a valid IPv4 CIDR block` | Use the form `203.0.113.10/32`, or `"auto"`. |
| `security_group_ip_range_ipv6 must be a valid IPv6 CIDR block` | Include a `/` prefix length, such as `::/0`. |
| `route53_domain should not include protocol` / `should not end with a dot` | Use just `example.com`. |
| `certificate_arn must be a valid ACM certificate ARN` | Copy the full ARN from the ACM console; it starts with `arn:aws:acm:`. |
| `email_forwarding_address must be a valid email address` | Fix the address, or set it to `null`. |
| `must be a positive integer` (timeouts) | Use a whole number above zero, in quotes, or `null`. |
| `Failed to resolve current IP address for 'auto' mode` | You're offline or `checkip.amazonaws.com` is blocked. Set an explicit CIDR instead. |

For deployment failures after validation passes, see
[Troubleshooting](../reference/troubleshooting.md).

## Related

- [HTTPS and DNS](https-and-dns.md): certificates, custom domains, and email
- [Optional features](optional-features.md): portal, alarms, CloudTrail, Global Accelerator,
  Bedrock
- [Database access](database-access.md): ECS Exec, port forwarding, and the Data API
- [Costs](../reference/costs.md): what each choice costs
- [Architecture](../reference/architecture.md): how the pieces fit together
- [`cdk.json`](../../cdk.json) and [`openemr_ecs/validation.py`](../../openemr_ecs/validation.py):
  the source of truth for settings and their checks
