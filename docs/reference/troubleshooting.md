# Troubleshooting

This page helps you work out what went wrong and how to fix it, organized by
what you see (the symptom). Start with the quick reference table; each row
links to a longer explanation below. It is for anyone deploying or running
OpenEMR on ECS, including first-time users.

## Quick reference

| Symptom | Likely fix |
|---|---|
| `python: command not found` | Install Python 3.14 and make sure it is on your `PATH`. See [Setup and tool problems](#setup-and-tool-problems). |
| `aws: command not found` | Install the AWS CLI. |
| CDK executable not found | Run `npm ci` in the repository. |
| `pip` not found | Activate the virtual environment, or use `python -m pip`. |
| A script won't run, or "Permission denied" when running it | `chmod +x scripts/<script>.sh`, or run it with `bash`. |
| "cdk.json not found" | Run the command from inside the repository folder. |
| `CONFIGURATION ERROR` when running `cdk synth` or `cdk deploy` | Fix the named setting in `cdk.json`. See [Configuration errors](#configuration-errors-during-synth-or-deploy). |
| "Access Denied" from AWS | Check your credentials with `aws sts get-caller-identity` and your IAM permissions. |
| `cdk deploy` fails | Run `./scripts/validate-deployment-prerequisites.sh`, then read the CloudFormation events. See [Deployment failures](#deployment-failures). |
| Can't open the OpenEMR URL | Make sure `security_group_ip_range_ipv4` includes your current IP, and allow up to 20 minutes for first-time setup. See [Can't reach OpenEMR](#cant-reach-openemr). |
| Forgot the admin password | Get it from AWS Secrets Manager. See [Forgot the admin password](#forgot-the-admin-password). |
| `ERROR 3159 (HY000): Connections using insecure transport are prohibited` | The database requires TLS. See [Insecure transport error](#error-3159-connections-using-insecure-transport-are-prohibited). |
| Logs repeat "Database not ready yet, waiting..." | See [Database connection timeout](#database-connection-timeout). Run `./scripts/diagnose_db_connectivity.sh`. |
| ECS tasks keep being replaced | See [Container health check failures](#container-health-check-failures). |
| `Permission denied` for `sqlconf.php` in Apache logs | Run `./scripts/fix-sqlconf-permissions.sh`. |
| Slow pages or high CPU/memory | See [Performance problems](#performance-problems). |
| `cdk destroy` fails or leaves things behind | See [Stack deletion fails or leaves resources behind](#stack-deletion-fails-or-leaves-resources-behind). |

## Contents

- [Setup and tool problems](#setup-and-tool-problems)
- [Configuration errors during synth or deploy](#configuration-errors-during-synth-or-deploy)
- [Deployment failures](#deployment-failures)
  - [`cdk deploy` fails](#cdk-deploy-fails)
  - [Stack update failures](#stack-update-failures)
  - [Common error messages](#common-error-messages)
- [Can't reach OpenEMR](#cant-reach-openemr)
- [Forgot the admin password](#forgot-the-admin-password)
- [Database connection problems](#database-connection-problems)
  - [ERROR 3159: Connections using insecure transport are prohibited](#error-3159-connections-using-insecure-transport-are-prohibited)
  - [Database connection timeout](#database-connection-timeout)
  - [Permission denied on sqlconf.php](#permission-denied-on-sqlconfphp)
- [Container health check failures](#container-health-check-failures)
- [TLS certificate problems](#tls-certificate-problems)
- [Performance problems](#performance-problems)
- [Stack deletion fails or leaves resources behind](#stack-deletion-fails-or-leaves-resources-behind)
- [Feature-specific troubleshooting](#feature-specific-troubleshooting)
- [Getting help](#getting-help)

## Setup and tool problems

These come up while installing tools, before anything is deployed. The
[Getting started guide](../get-started/README.md) walks through installation
step by step.

- **`python: command not found`.** Install Python 3.14 and, on Windows, check
  "Add Python to PATH" in the installer. Restart your terminal afterward. On
  macOS and Linux, try `python3` instead of `python`.
- **`aws: command not found`.** Install the
  [AWS CLI](https://aws.amazon.com/cli/), then restart your terminal.
- **CDK executable not found.** This project uses a pinned CDK CLI installed
  into `node_modules/` by `npm ci`; run `npm ci` from the repository folder and
  use `node_modules/.bin/cdk`. Don't use `sudo` or install a separate global
  CDK.
- **`pip` not found.** Activate the virtual environment
  (`source .venv/bin/activate` on macOS/Linux, `.venv\Scripts\activate` on
  Windows) or run `python -m pip install -r requirements.txt`.
- **A script won't run or says "Permission denied".** Make it executable with
  `chmod +x scripts/<script-name>.sh`, or run it as
  `bash scripts/<script-name>.sh`. On Windows, use Git Bash or WSL.
- **"cdk.json not found".** Make sure you have cloned the repository and are
  inside it. `scripts/validate-deployment-prerequisites.sh` searches parent
  directories for `cdk.json`, so it works from any subfolder of the
  repository.

## Configuration errors during synth or deploy

If `cdk synth` or `cdk deploy` prints a box titled `CONFIGURATION ERROR`, a
value in `cdk.json` failed validation. The message names the setting. Common
cases:

| Message mentions | What to do |
|---|---|
| `Either 'route53_domain' or 'certificate_arn' must be provided` | A certificate is required. Set one of them. See [HTTPS and DNS](../guides/https-and-dns.md). |
| `certificate_arn must be a valid ACM certificate ARN` | Use the full ARN, starting with `arn:aws:acm:`. |
| `route53_domain` | Use the bare domain (for example `example.com`), without `https://` and without a trailing dot. |
| `security_group_ip_range_ipv4` / `_ipv6` | Use a valid CIDR block (for example `203.0.113.131/32`) or `"auto"` for IPv4. |
| `Failed to resolve current IP address for 'auto' mode` | The deploy couldn't look up your public IP. Check your internet connection or set an explicit CIDR. |
| `Invalid Fargate CPU` / `Invalid Fargate memory` | Use a supported CPU and memory pair. See [Fargate task sizing](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html). |
| Minimum or maximum capacity | Both must be at least 1, and the minimum can't exceed the maximum. |
| Autoscaling percentage | Must be a whole number from 1 to 100. |

You can also pass values on the command line with
`node_modules/.bin/cdk deploy -c key=value`. All settings are described in
[Configuration](../guides/configuration.md).

## Deployment failures

### `cdk deploy` fails

**Common causes:**

1. **IAM permissions:** your AWS credentials don't have enough permissions.
2. **Service quotas:** you've hit an account limit (VPCs, NAT gateways, Elastic
   IPs, and so on).
3. **Resource conflicts:** a resource with the same name already exists.
4. **Region availability:** a required service isn't available in your Region.

**What to do:**

1. Run `./scripts/validate-deployment-prerequisites.sh`. It checks your AWS
   CLI and credentials, CDK installation and bootstrap, Python packages,
   existing stack status, stack synthesis, and the Route 53 hosted zone (if
   configured).
2. Open the CloudFormation console and read the stack's **Events** tab for the
   first error; later errors are usually side effects.
3. Check [Service Quotas](https://console.aws.amazon.com/servicequotas/) for
   limit problems.
4. Check that the prerequisites in the
   [Getting started guide](../get-started/README.md) are met.
5. Review the synthesized template with `node_modules/.bin/cdk synth`.

A failed first deployment rolls back automatically.

### Stack update failures

**Problem:** an update fails and CloudFormation rolls back, or leaves the stack
in a failed state.

1. Review the rollback events in the CloudFormation console.
2. Look for resources that can't be updated in place (some property changes
   require replacement).
3. Destroy and recreate the stack if necessary, after making sure your
   [backups](../guides/backup-and-restore.md) are safe.
4. Test updates in a non-production environment first.

The ECS service has a deployment circuit breaker: if new tasks can't become
healthy, ECS rolls back to the previous version automatically.

### Common error messages

| Error | What to do |
|---|---|
| "Resource limit exceeded" | Request a limit increase in Service Quotas, or reduce resource usage. |
| `InvalidParameterException` | Check that `cdk.json` values match the expected formats. |
| `AccessDenied` | Check the IAM permissions of the AWS user or role you're deploying with. |
| `ResourceNotFoundException` | A dependency doesn't exist yet or was deleted outside CloudFormation; make sure dependencies are created before the resources that use them. |

## Can't reach OpenEMR

1. **Check that your IP address is allowed.** The load balancer only accepts
   connections from `security_group_ip_range_ipv4` (and
   `security_group_ip_range_ipv6`). The default `"auto"` allows only the
   public IP of the computer that ran the deployment. If your IP has changed
   (common on home internet) or someone else needs access, update the setting
   and run `node_modules/.bin/cdk deploy` again.
2. **Use HTTPS.** There is no HTTP listener; use the `https://` URL from the
   `ApplicationURL` output.
3. **Give first-time setup time to finish.** On the first deployment OpenEMR
   installs itself, which can take up to about 20 minutes after the stack
   finishes. The load balancer won't send traffic until a task is healthy.
4. **If you use `route53_domain`,** the URL is `https://openemr.<your-domain>`
   and DNS can take a few minutes to propagate.
5. **Check the target group** in the EC2 console (Load Balancing > Target
   Groups) for healthy targets, then see
   [Container health check failures](#container-health-check-failures).

## Forgot the admin password

The OpenEMR username is `admin`. The password is in AWS Secrets Manager:

1. Open the [Secrets Manager console](https://console.aws.amazon.com/secretsmanager/)
   in the Region you deployed to.
2. Open the secret whose name starts with `Password` (for example
   `OpenemrEcsStack-Password...`).
3. Choose **Retrieve secret value**.

## Database connection problems

### ERROR 3159: Connections using insecure transport are prohibited

**Problem:** the Aurora MySQL database has `require_secure_transport` turned
on, and something tried to connect without TLS.

**How the stack handles it:** each OpenEMR container automatically:

1. Downloads the RDS CA certificate bundle.
2. Copies it into OpenEMR's certificates directory.
3. Configures OpenEMR to use TLS for all database connections.

**Check:**

- The container's startup logs in CloudWatch show the certificate download
  succeeding.
- The certificate exists at
  `/var/www/localhost/htdocs/openemr/sites/default/documents/certificates/mysql-ca`.
- The logs show OpenEMR connecting to the database.

**If it persists:**

1. Confirm the certificate downloaded (check container startup logs).
2. Confirm the certificate file permissions are correct (744).
3. Confirm `require_secure_transport` is `ON` in the RDS parameter group.
4. Look for TLS errors in the OpenEMR logs.

If you connect your own client (for example MySQL Workbench), it must use TLS.
The RDS CA bundle can be downloaded from
<https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem>. See
[Database access](../guides/database-access.md).

### Database connection timeout

**Problem:** OpenEMR can't connect to the database and retries repeatedly.
Container logs show messages like:

```text
[2026-01-03 18:44:04] Database not ready yet, waiting 2s before retry (attempt 1/30)...
[2026-01-03 18:44:06] Database not ready yet, waiting 4s before retry (attempt 2/30)...
[2026-01-03 18:44:10] Database not ready yet, waiting 8s before retry (attempt 3/30)...
```

**Run the diagnostic script first:**

```bash
./scripts/diagnose_db_connectivity.sh
```

It checks the RDS cluster status, the current minimum and maximum capacity,
security group rules, and recent connection logs.

**Possible causes:**

1. **Aurora minimum capacity set to 0.** The stack sets
   `serverless_v2_min_capacity=0.5` in
   [`openemr_ecs/database.py`](../../openemr_ecs/database.py). If it has been
   changed to 0, the database can shut down completely and takes 3–5 minutes
   to wake up, which can outlast the container's retries. If the diagnostic
   script reports "Min capacity is 0", set it back to 0.5 or higher and
   redeploy.
2. **Security group rules.** ECS tasks must be able to reach the database on
   port 3306. Check both the ingress and egress rules.
3. **The database is still starting.** Check the cluster status in the RDS
   console and wait for `available`.
4. **Wrong credentials.** Check the database credentials in Secrets Manager
   and that the ECS task role can read the secret. If you've used
   [credential rotation](../guides/credential-rotation.md), see its
   troubleshooting section.
5. **Network routing.** Check VPC routing and the NAT gateways, and that the
   database is in the private subnets.

### Permission denied on sqlconf.php

**Problem:** Apache logs show `Failed to open stream: Permission denied` for
`sqlconf.php`.

**Fix:** run `./scripts/fix-sqlconf-permissions.sh`. It starts a one-off task
that resets the file's permissions on EFS; no redeploy is needed.

## Container health check failures

**Problem:** ECS tasks fail their health checks and are replaced over and over.

**Common causes:**

1. The application isn't responding over HTTPS on port 443.
2. The page returns an unexpected status code.
3. First-time setup is taking a long time or has stalled.
4. The task doesn't have enough CPU or memory.

**What to do:**

1. Read the container logs in CloudWatch Logs for application errors.
2. Check whether the task is still in first-time setup (see below).
3. Review CPU and memory in ECS and increase `openemr_service_fargate_cpu` /
   `openemr_service_fargate_memory` if needed.

**How the health checks are configured:**

| Check | Behavior and settings |
|---|---|
| Container health check | Passes while first-time setup is running, unless setup has been running for more than 20 minutes (then it fails so ECS replaces the task). After setup, runs `curl -f -k https://localhost:443/`. Start period 120 s, interval 60 s, timeout 10 s, 3 retries. |
| Load balancer target health check | HTTPS `GET /` on port 443; healthy when it returns `302`. Interval 60 s, timeout 10 s; healthy after 2 successes, unhealthy after 5 failures. |
| ECS health check grace period | 1,200 s (20 minutes) before load balancer results count for a new task |

These are defined in [`openemr_ecs/compute.py`](../../openemr_ecs/compute.py).
See [Architecture](architecture.md#compute-layer) for more detail.

## TLS certificate problems

### Certificate not found

**Problem:** a container fails to start because the internal TLS certificates
are missing.

1. Check that the one-time certificate setup ran: look at the `OneTimeSSLSetup`
   Lambda's logs and the ECS task it started.
2. Check that the TLS EFS file system is mounted.
3. Check the permissions on the EFS volume.

### Certificate expiration

**Problem:** the internal certificates between the load balancer and the
containers expired.

- The stack regenerates them every 2 days.
- Check the EventBridge rule for scheduled certificate maintenance.
- Check that the `MaintainSSLMaterialsLambda` function has its IAM permissions
  and that its runs succeed.

Your public certificate (for the browser) is managed by ACM. If you used
`route53_domain`, ACM renews it automatically as long as the DNS validation
records remain. See [HTTPS and DNS](../guides/https-and-dns.md).

## Performance problems

### High CPU or memory use

1. Review the autoscaling settings in `cdk.json`.
2. Adjust `openemr_service_fargate_cpu_autoscaling_percentage` and
   `openemr_service_fargate_memory_autoscaling_percentage` (40% by default).
3. Increase `openemr_service_fargate_minimum_capacity` if load is consistently
   high.
4. Look for application-level improvements (caching, query optimization).

### Slow database queries

1. Use CloudWatch Database Insights (already enabled, Advanced mode).
2. Review the slow query log in CloudWatch Logs.
3. Check Aurora ACU utilization; capacity scales automatically up to 256 ACUs.
4. Review database indexes and queries.

See [Load testing](../guides/load-testing.md) for how to measure performance.

## Stack deletion fails or leaves resources behind

- **Deletion protection:** if `rds_deletion_protection` is `true`, run
  `node_modules/.bin/cdk destroy -c disable_rds_deletion_protection_on_destroy=true`.
- **Termination protection:** if you set `enable_stack_termination_protection`,
  turn termination protection off on the stack (CloudFormation console, or
  `aws cloudformation update-termination-protection --no-enable-termination-protection --stack-name <stack>`)
  before destroying.
- **Backup vault not deleted:** a cleanup function deletes the stack's backup
  recovery points during deletion. If one can't be deleted (for example, a
  backup job is still running), the vault remains; wait for running jobs to
  finish, then delete the leftover recovery points and the vault in the AWS
  Backup console.
- **Retained resources:** some resources are kept on purpose (the Aurora final
  snapshot, several CloudWatch log groups). See
  [Costs: what keeps billing after you destroy the stack](costs.md#what-keeps-billing-after-you-destroy-the-stack).

## Feature-specific troubleshooting

Problems specific to one feature are covered in that feature's guide:

- Backups and restores: [Backup and restore](../guides/backup-and-restore.md)
- Credential rotation: [Credential rotation](../guides/credential-rotation.md)
- Testing the container locally with Docker Compose:
  [Local testing](../guides/local-testing.md) and
  [`compose/README.md`](../../compose/README.md)
- Importing an existing OpenEMR installation:
  [Importing OpenEMR](../guides/importing-openemr.md)
- Database access through ECS Exec: [Database access](../guides/database-access.md)
- Helper scripts: [`scripts/README.md`](../../scripts/README.md)
- Lambda functions: [`lambda/README.md`](../../lambda/README.md)
- Repository knowledge MCP server: [Knowledge MCP](../maintainers/knowledge-mcp.md#troubleshooting)

## Getting help

If your problem isn't covered here:

1. **Check the logs:**
   - CloudWatch Logs for the ECS tasks
   - CloudWatch Logs for the Lambda functions
   - Aurora logs in CloudWatch Logs (`audit`, `error`, `general`, `slowquery`)
2. **Read the documentation:** start at the [documentation home](../README.md),
   or see the [AWS CDK documentation](https://docs.aws.amazon.com/cdk/).
3. **Ask the community:**
   - OpenEMR community forum: <https://community.open-emr.org/>
   - GitHub issues: <https://github.com/openemr/openemr-on-ecs/issues>
4. **AWS Support:** the AWS Support Center (if you have a support plan) or
   [AWS re:Post](https://repost.aws/).
