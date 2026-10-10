# Costs

This page estimates what the default deployment costs to run each month, shows
the math behind the estimate, and explains how to lower the bill and what can
keep billing after you delete the stack. It is for anyone budgeting a
deployment or a test run.

> [!IMPORTANT]
> These are estimates, not quotes. AWS prices change and differ by Region, and
> your bill depends on usage. Check the linked AWS pricing pages or the
> [AWS Pricing Calculator](https://calculator.aws/) before committing.

## Contents

- [Summary](#summary)
- [Assumptions](#assumptions)
- [Monthly estimate](#monthly-estimate)
- [How each line is calculated](#how-each-line-is-calculated)
- [Aurora Serverless v2 pricing note](#aurora-serverless-v2-pricing-note)
- [What this estimate leaves out](#what-this-estimate-leaves-out)
- [Ways to reduce cost](#ways-to-reduce-cost)
- [What keeps billing after you destroy the stack](#what-keeps-billing-after-you-destroy-the-stack)
- [Smaller organizations](#smaller-organizations)

## Summary

The default deployment has a **base cost of about $320/month** in US West
(Oregon). That is the cost of the always-on minimum: two application
containers, a database that never fully sleeps, the load balancer, two NAT
gateways, and supporting services.

The true value of this architecture is that it scales up automatically to
support very large organizations; the cost grows with usage. You can't pause
it to stop charges; to stop paying, destroy the stack (see
[what keeps billing afterward](#what-keeps-billing-after-you-destroy-the-stack)).

## Assumptions

- **Region:** US West (Oregon), `us-west-2`.
- **Work week:** 40 hours (8 hours/day, 5 days/week).
- **Peak hours:** 8 AM–4 PM Eastern, Monday–Friday, which is 160 hours/month.
- **Off-peak hours:** all other time, 570 hours/month (730 hours total).
- **Load balancer traffic:** 25 requests per second.
- **Application:** the default minimum of 2 Fargate tasks, each with 2 vCPU and
  4 GB of memory, running all month.
- **Database:** Aurora Serverless v2 at its 0.5 ACU minimum all month, scaling
  to 2 ACUs during peak hours, with 10 GB of storage.
- **Optional features:** all off (the defaults in `cdk.json`), except
  CloudTrail, which is on by default but not priced here.

## Monthly estimate

| Service | What is counted | Monthly cost |
|---|---|---|
| AWS Fargate | 2 tasks × (2 vCPU + 4 GB), 730 hours | $144.16 |
| Aurora Serverless v2 | 0.5 ACU base, peak scaling, storage, I/O | $74.13 |
| NAT gateways | 2 gateways, 730 hours | $65.70 |
| Application Load Balancer | Fixed hourly charge plus 1 LCU | $22.27 |
| ElastiCache Serverless (Valkey) | Minimum hourly charge | $6.13 |
| AWS WAF | 1 web ACL base charge | $5.00 |
| Secrets Manager | 3 secrets | $1.20 |
| KMS | 1 key | $1.00 |
| Amazon EFS | 2 GB minimum (2 file systems) | $0.16 |
| AWS Backup | Storage and API requests | $0.04 |
| **Total** | | **$319.79 (about $320)** |

See [What this estimate leaves out](#what-this-estimate-leaves-out) for items
that are not counted and for places where the stack differs from these
assumptions.

## How each line is calculated

The rates below are the ones this estimate was originally built with. They are
kept here so the math can be checked; confirm current rates on the linked
pricing pages.

### AWS Fargate ([pricing](https://aws.amazon.com/fargate/pricing/))

A minimum of two tasks with 2 vCPU and 4 GB of memory run during both peak and
off-peak hours.

- Peak hours (160 hours):
  - vCPU: 2 tasks × 2 vCPU × 160 hours × $0.04048 = $25.91
  - Memory: 2 tasks × 4 GB × 160 hours × $0.004445 = $5.69
- Off-peak hours (570 hours):
  - vCPU: 2 tasks × 2 vCPU × 570 hours × $0.04048 = $92.29
  - Memory: 2 tasks × 4 GB × 570 hours × $0.004445 = $20.27
- Total: $118.20 (vCPU) + $25.96 (memory) = **$144.16/month**

> [!NOTE]
> The stack runs its tasks on ARM64 (Graviton), which is billed at a lower rate
> than the per-vCPU and per-GB rates above. Earlier documentation quoted a
> Graviton base cost of $0.079/hour per task; at that rate the line would be
> 2 tasks × 730 hours × $0.079 = **$115.34/month**. The higher figure is kept
> in the total as a conservative estimate.

### Application Load Balancer ([pricing](https://aws.amazon.com/elasticloadbalancing/pricing/))

- Fixed: 730 hours × $0.0225 = $16.43
- LCU: 1 LCU × $0.008 × 730 hours = $5.84
- Total: $16.43 + $5.84 = **$22.27/month**

### NAT gateways ([pricing](https://aws.amazon.com/vpc/pricing/))

- 2 gateways × 730 hours × $0.045 = **$65.70/month** ($0.09/hour for both)
- Data processed through the gateways is billed separately and not included.

### ElastiCache Serverless ([pricing](https://aws.amazon.com/elasticache/pricing/))

- 730 hours × $0.0084 = **$6.13/month**

### Amazon EFS

- Assumes minimum billing of 1 GB for each of the 2 file systems.
- 2 GB × $0.08 per GB-month = **$0.16/month**

### Aurora Serverless v2 ([pricing](https://aws.amazon.com/rds/aurora/pricing/))

- **Base (always on):** 730 hours × 0.5 ACU × $0.12 = $43.80. This is the
  minimum capacity that keeps connections instant.
- **Peak scaling:** 160 hours × 1.5 ACU × $0.12 = $28.80. This is the extra
  capacity during peak hours (scaling from 0.5 to 2 ACUs).
- **Storage:** 10 GB × $0.10 per GB-month = $1.00
- **Baseline I/O:** (730 hours × 3,600) × $0.0000002 = $0.53
- Total: $43.80 + $28.80 + $1.00 + $0.53 = **$74.13/month**

### AWS Backup

- Backup storage, assuming 0.005 GB per backup:
  - Daily backups (30 days): 0.005 GB × 30 = 0.15 GB
  - Weekly backups (52 weeks): 0.005 GB × 52 = 0.26 GB
  - Monthly backups (84 months, 7 years): 0.005 GB × 84 = 0.42 GB
  - Total backup storage: 0.83 GB
- Warm storage at $0.05 per GB-month: 0.83 GB × $0.05 = $0.0415
- API requests: about 35 backups per month (30 daily + 4 weekly + 1 monthly) at
  $0.05 per 1,000 requests: (35 / 1,000) × $0.05 = $0.00175
- Total: $0.0415 + $0.00175 = **about $0.04/month**

### Secrets Manager ([pricing](https://aws.amazon.com/secrets-manager/pricing/))

- 3 secrets (database admin, OpenEMR admin password, and the credential
  rotation slot secret) × $0.40 = **$1.20/month**

### AWS WAF ([pricing](https://aws.amazon.com/waf/pricing/))

- 1 web ACL: **$5/month base**, plus additional charges for rules, managed
  rule groups, and requests.
- The web ACL includes the AWS Managed Common Rule Set, SQL injection
  protection, Known Bad Inputs, rate limiting (2,000 requests per 5 minutes per
  IP), and suspicious user-agent blocking.

### KMS ([pricing](https://aws.amazon.com/kms/pricing/))

- 1 key × $1 = **$1/month** (see the note below about how many keys the
  default stack creates).

## Aurora Serverless v2 pricing note

The database is configured with a minimum of 0.5 ACU (`serverless_v2_min_capacity=0.5`
in [`openemr_ecs/database.py`](../../openemr_ecs/database.py)) so it never
scales to zero. That keeps it available with instant connections and avoids
the 3–5 minute delay ("cold start") that happens when a stopped database has to
wake up.

The always-on minimum costs about $44/month (730 hours × 0.5 ACU × $0.12 =
$43.80). Compared with letting the database scale to zero, it adds about
**$34/month**: with scale-to-zero you would still pay for 0.5 ACU during the
160 peak hours (160 × 0.5 × $0.12 = $9.60), so the difference is
$43.80 − $9.60 = $34.20.

## What this estimate leaves out

The estimate is a floor, not a full bill. Known gaps:

- **A second Aurora instance.** The estimate prices one instance, but the
  stack creates a writer and a reader that scales with the writer, and Aurora
  Serverless v2 bills ACUs per instance. At the same rate, a second instance at
  the 0.5 ACU minimum would add about $43.80/month, plus more during peak
  scaling.
- **More KMS keys.** The default stack creates three customer-managed keys
  (central, S3, and CloudTrail), not one. At $1 per key that adds about
  $2/month. Turning on ECS Exec or the analytics environment adds one key each.
- **CloudTrail and CloudWatch Logs.** CloudTrail is on by default and keeps
  logs in CloudWatch Logs for 9 years and in S3 for 7 years. Application, VPC
  Flow Log, WAF, and database logs also go to CloudWatch Logs. Log ingestion
  and storage are billed by volume. You can turn CloudTrail off for testing
  with `enable_long_term_cloudtrail_monitoring`, but we recommend leaving it on
  in production.
- **Usage-based charges:** data transfer out of AWS, NAT gateway data
  processing, extra load balancer LCUs, WAF rule and request charges, Aurora
  I/O beyond the baseline, EFS storage beyond the minimum, and additional
  Fargate tasks when the service scales out.
- **Optional features** (all off by default), each billed separately when you
  turn it on:
  - Route 53 hosted zone and domain, ACM, and SES for automated DNS and email
    ([Route 53 pricing](https://aws.amazon.com/route53/pricing/),
    [ACM pricing](https://aws.amazon.com/certificate-manager/pricing/),
    [SES pricing](https://aws.amazon.com/ses/pricing/))
  - AWS Global Accelerator
    ([pricing](https://aws.amazon.com/global-accelerator/pricing/))
  - RDS Data API
    ([pricing](https://aws.amazon.com/rds/aurora/pricing/))
  - Aurora ML with Amazon Bedrock
    ([pricing](https://aws.amazon.com/bedrock/pricing/))
  - Serverless analytics environment: no cost while idle; SageMaker and EMR
    Serverless compute is billed when you use it
    ([SageMaker pricing](https://aws.amazon.com/sagemaker/pricing/))
  - Monitoring alarms (CloudWatch alarms and SNS)

  See [Optional features](../guides/optional-features.md),
  [HTTPS and DNS](../guides/https-and-dns.md), and
  [Analytics](../guides/analytics.md).

## Ways to reduce cost

- **Destroy test deployments when you're done.** The stack can't be paused;
  most resources bill hourly whether or not anyone is using OpenEMR.
- **Run fewer or smaller tasks for testing.** Lower
  `openemr_service_fargate_minimum_capacity` (minimum 1), or reduce
  `openemr_service_fargate_cpu` and `openemr_service_fargate_memory` if your
  workload is light. One task removes the redundancy across Availability
  Zones, so keep at least two in production. See
  [Configuration](../guides/configuration.md).
- **Turn off CloudTrail in short-lived test stacks** with
  `enable_long_term_cloudtrail_monitoring` set to `"false"`. Keep it on in
  production.
- **Leave optional features off** unless you need them.
- **Reduce NAT gateway traffic.** The two NAT gateways are the largest fixed
  cost after compute and the database. VPC endpoints for heavily used AWS
  services can cut NAT data processing charges (not included in this stack).
- **Review data transfer patterns** for large uploads and downloads.
- **Already built in:** Graviton (ARM64) tasks, which AWS prices about 20%
  lower than x86; serverless services that bill for usage; and autoscaling that
  scales the application back down when load drops.
- **Watch spending** with Amazon CloudWatch, AWS Cost Explorer (spending by
  service), and AWS Budgets (alerts when spending passes a threshold).

## What keeps billing after you destroy the stack

`node_modules/.bin/cdk destroy` removes most resources, but some are kept on
purpose (so you don't lose audit data or your database by accident) or live
outside the stack. Check for these afterward:

| Item | Why it remains | What to do |
|---|---|---|
| Aurora final snapshot | The database uses a snapshot removal policy, so deletion takes a final snapshot | Keep it as an archive or delete it in the RDS console |
| CloudWatch log groups | The application, VPC Flow Log, and CloudTrail log groups (and the ECS Exec log group, if enabled) are retained. CloudTrail logs are set to 9-year retention | Delete them in the CloudWatch console when you no longer need them |
| Aurora log groups | Created by RDS outside the stack (`audit`, `error`, `general`, `slowquery` exports) | Delete them in the CloudWatch console |
| AWS Backup vault and recovery points | A cleanup function deletes the recovery points so the vault can be removed. If any recovery point can't be deleted (for example, a backup job is still running), the vault and those points remain | Check AWS Backup and delete leftovers |
| KMS keys | Deleted keys enter a mandatory waiting period (7–30 days) and show as `PendingDeletion` | No action needed; they are removed when the waiting period ends |
| CDK bootstrap assets | Container images and files in the account's shared CDK bootstrap bucket and repository may be used by other stacks | Leave them unless you are removing CDK from the account |
| Your Route 53 domain and hosted zone | Not created by this stack | Keep or remove separately |

> [!WARNING]
> Destroying the stack deletes the backups in its backup vault. If you need to
> keep any backups, copy them elsewhere first. See
> [Backup and restore](../guides/backup-and-restore.md).

If you set `rds_deletion_protection` to `true`, pass
`-c disable_rds_deletion_protection_on_destroy=true` to the destroy command.
If you enabled `enable_stack_termination_protection`, turn termination
protection off first. See [Configuration](../guides/configuration.md) and
[Troubleshooting](troubleshooting.md#stack-deletion-fails-or-leaves-resources-behind).

## Smaller organizations

This architecture is built to scale to very large organizations. For smaller
organizations, some of
[OpenEMR's offerings in the AWS Marketplace](https://aws.amazon.com/marketplace/seller-profile?id=bec33905-edcb-4c30-b3ae-e2960a9a5ef4)
may be more affordable.
