# OpenEMR on ECS — AWS Cost Estimate

> **Last Updated:** February 2026
> **Region:** us-east-1 (N. Virginia)
> **Pricing Source:** AWS public pricing pages, February 2026
> **Status:** Estimate — actual costs vary with usage patterns

## Table of Contents

- [OpenEMR on ECS — AWS Cost Estimate](#openemr-on-ecs--aws-cost-estimate)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Environment Sizing Assumptions](#environment-sizing-assumptions)
  - [Detailed Cost Breakdown](#detailed-cost-breakdown)
    - [Fargate ARM64 Per-Task Cost Reference](#fargate-arm64-per-task-cost-reference)
    - [QA Environment](#qa-environment)
    - [Staging Environment](#staging-environment)
    - [Production Environment — 4 vCPU / 8 GB](#production-environment--4-vcpu--8-gb)
    - [Production Environment — 8 vCPU / 32 GB](#production-environment--8-vcpu--32-gb)
  - [Summary Comparison](#summary-comparison)
    - [All-Environment Totals (QA + Staging + Prod)](#all-environment-totals-qa--staging--prod)
  - [Annual Estimates](#annual-estimates)
  - [Key Cost Drivers](#key-cost-drivers)
  - [Cost Optimization Opportunities](#cost-optimization-opportunities)
    - [Potential Savings Impact](#potential-savings-impact)
  - [Optional Add-ons](#optional-add-ons)
  - [Notes and Caveats](#notes-and-caveats)

---

## Overview

This document provides monthly cost estimates for deploying the OpenEMR on ECS reference architecture across QA, Staging, and Production environments. The stack deploys on **AWS Fargate (ARM64)** with **Aurora MySQL Serverless v2** (writer + reader), **ElastiCache Serverless Valkey**, full encryption at rest and in transit, WAF, AWS Backup with 7-year retention, and CloudTrail audit logging.

All prices are in **USD**, based on **us-east-1** pricing, assuming **730 hours/month** (24×7 operation).

---

## Environment Sizing Assumptions

| Parameter | QA | Staging | Production (4v/8G) | Production (8v/32G) |
|---|---|---|---|---|
| Fargate tasks (average running) | 2 | 2 | 3–4 | 3–4 |
| Fargate CPU / Memory per task | 2 vCPU / 4 GB | 2 vCPU / 4 GB | 4 vCPU / 8 GB | 8 vCPU / 32 GB |
| Aurora ACU (average, writer+reader) | ~1 (0.5+0.5) | ~2.5 (1.5+1.0) | ~8 (5+3) | ~12 (8+4) |
| Aurora storage | 10 GB | 20 GB | 50 GB | 100 GB |
| ElastiCache Valkey data | ~1 GB | ~2 GB | ~5 GB | ~10 GB |
| Monthly HTTP requests | ~100K | ~500K | ~2M | ~5M |
| CloudTrail logging | Yes | Yes | Yes | Yes |
| Monitoring alarms | No | Yes | Yes | Yes |
| SES email | No | No | Optional | Optional |

**CDK context defaults** (from `cdk.json`):

| Context Key | Default Value |
|---|---|
| `openemr_service_fargate_cpu` | `2048` |
| `openemr_service_fargate_memory` | `4096` |
| `openemr_service_fargate_minimum_capacity` | `2` |
| `openemr_service_fargate_maximum_capacity` | `100` |

To use 4 vCPU / 8 GB or 8 vCPU / 32 GB, override in `cdk.json`:

```json
{
  "context": {
    "openemr_service_fargate_cpu": 4096,
    "openemr_service_fargate_memory": 8192
  }
}
```

or for 8 vCPU / 32 GB:

```json
{
  "context": {
    "openemr_service_fargate_cpu": 8192,
    "openemr_service_fargate_memory": 32768
  }
}
```

---

## Detailed Cost Breakdown

### Fargate ARM64 Per-Task Cost Reference

| Size | vCPU Cost/hr | Memory Cost/hr | Monthly per Task |
|---|--:|--:|--:|
| 2 vCPU / 4 GB | $0.06476 | $0.01424 | **$57.67** |
| 4 vCPU / 8 GB | $0.12952 | $0.02848 | **$115.34** |
| 8 vCPU / 32 GB | $0.25904 | $0.11392 | **$271.86** |

> ARM64 Fargate pricing: **$0.03238/vCPU-hr** + **$0.00356/GB-hr**

---

### QA Environment

**Configuration:** 2 tasks × 2 vCPU / 4 GB, Aurora min ACUs, monitoring off

| Service | Monthly Cost | Notes |
|---|--:|---|
| **Networking** | | |
| VPC NAT Gateways (×2) | $66.00 | $0.045/hr × 2 gateways |
| NAT GW data processing | $1.00 | $0.045/GB processed |
| **Compute** | | |
| ECS Fargate ARM64 (2 tasks) | $115.00 | 2 × $57.67/task |
| Container Insights (enhanced) | $5.00 | CloudWatch metrics |
| **Database** | | |
| Aurora Serverless v2 (1 ACU avg) | $88.00 | $0.12/ACU-hr; writer 0.5 + reader 0.5 |
| Aurora storage (10 GB) | $1.00 | $0.10/GB-month |
| Aurora I/O | $0.50 | $0.20/million requests |
| Performance Insights (731-day) | $5.00 | ~$4.86/vCPU-month |
| **Cache** | | |
| ElastiCache Serverless Valkey | $10.00 | Storage $0.125/GB + ECPUs |
| **Storage** | | |
| EFS (2 file systems) | $1.00 | $0.30/GB-month |
| S3 (ALB + CloudTrail logs) | $1.00 | Standard + lifecycle |
| **Security** | | |
| WAF v2 | $11.00 | $5 ACL + $1/rule × 5 + requests |
| KMS keys (2–3) | $2.00 | $1/key/month + API calls |
| Secrets Manager (2 secrets) | $1.00 | $0.40/secret/month |
| ACM Certificate | $0.00 | Free with Route53 |
| **Observability** | | |
| CloudWatch Logs (ingestion) | $3.00 | $0.50/GB |
| CloudWatch Logs (storage) | $1.00 | $0.03/GB-month |
| CloudWatch Metrics | $5.00 | Custom metrics |
| CloudTrail | $2.00 | CW Logs + S3 storage |
| **Load Balancing** | | |
| ALB (hourly) | $16.00 | $0.0225/hr |
| ALB (LCU charges) | $3.00 | $0.008/LCU-hr |
| **Backup** | | |
| AWS Backup | $2.00 | Daily/weekly/monthly retention |
| **Other** | | |
| Lambda (SSL, cleanup) | $0.00 | Within free tier |
| SSM Parameter Store | $0.00 | Standard params free |
| Data transfer | $3.00 | Inter-AZ + egress |
| | | |
| **QA TOTAL** | **~$343/mo** | |

---

### Staging Environment

**Configuration:** 2 tasks × 2 vCPU / 4 GB, moderate Aurora ACUs, monitoring on

| Service | Monthly Cost | Notes |
|---|--:|---|
| **Networking** | | |
| VPC NAT Gateways (×2) | $66.00 | $0.045/hr × 2 gateways |
| NAT GW data processing | $5.00 | $0.045/GB processed |
| **Compute** | | |
| ECS Fargate ARM64 (2 tasks) | $115.00 | 2 × $57.67/task |
| Container Insights (enhanced) | $8.00 | CloudWatch metrics |
| **Database** | | |
| Aurora Serverless v2 (2.5 ACU avg) | $219.00 | $0.12/ACU-hr; writer 1.5 + reader 1.0 |
| Aurora storage (20 GB) | $2.00 | $0.10/GB-month |
| Aurora I/O | $1.00 | $0.20/million requests |
| Performance Insights (731-day) | $5.00 | ~$4.86/vCPU-month |
| **Cache** | | |
| ElastiCache Serverless Valkey | $15.00 | Storage + ECPUs |
| **Storage** | | |
| EFS (2 file systems) | $1.00 | $0.30/GB-month |
| S3 (ALB + CloudTrail logs) | $2.00 | Standard + lifecycle |
| **Security** | | |
| WAF v2 | $12.00 | ACL + rules + requests |
| KMS keys (2–3) | $3.00 | $1/key/month + API calls |
| Secrets Manager (2 secrets) | $1.00 | $0.40/secret/month |
| ACM Certificate | $0.00 | Free with Route53 |
| **Observability** | | |
| CloudWatch Logs (ingestion) | $5.00 | $0.50/GB |
| CloudWatch Logs (storage) | $1.00 | $0.03/GB-month |
| CloudWatch Metrics | $8.00 | Custom metrics |
| CloudTrail | $3.00 | CW Logs + S3 storage |
| SNS (monitoring alarms) | $1.00 | Topics + subscriptions |
| **Load Balancing** | | |
| ALB (hourly) | $16.00 | $0.0225/hr |
| ALB (LCU charges) | $8.00 | $0.008/LCU-hr |
| **Backup** | | |
| AWS Backup | $5.00 | Daily/weekly/monthly retention |
| **Other** | | |
| Lambda (SSL, cleanup) | $0.00 | Within free tier |
| SSM Parameter Store | $0.00 | Standard params free |
| Data transfer | $5.00 | Inter-AZ + egress |
| | | |
| **STAGING TOTAL** | **~$527/mo** | |

---

### Production Environment — 4 vCPU / 8 GB

**Configuration:** 3–4 tasks × 4 vCPU / 8 GB, higher Aurora ACUs, full monitoring

| Service | Monthly Cost | Notes |
|---|--:|---|
| **Networking** | | |
| VPC NAT Gateways (×2) | $66.00 | $0.045/hr × 2 gateways |
| NAT GW data processing | $15.00 | $0.045/GB processed |
| **Compute** | | |
| ECS Fargate ARM64 (3.5 avg tasks) | **$404.00** | 3.5 × $115.34/task |
| Container Insights (enhanced) | $15.00 | CloudWatch metrics |
| **Database** | | |
| Aurora Serverless v2 (8 ACU avg) | $701.00 | $0.12/ACU-hr; writer 5 + reader 3 |
| Aurora storage (50 GB) | $5.00 | $0.10/GB-month |
| Aurora I/O | $5.00 | $0.20/million requests |
| Performance Insights (731-day) | $10.00 | ~$4.86/vCPU-month |
| **Cache** | | |
| ElastiCache Serverless Valkey | $40.00 | Storage + ECPUs |
| **Storage** | | |
| EFS (2 file systems) | $2.00 | $0.30/GB-month |
| S3 (ALB + CloudTrail logs) | $5.00 | Standard + lifecycle |
| **Security** | | |
| WAF v2 | $16.00 | ACL + rules + 2M requests |
| KMS keys (2–3) | $3.00 | $1/key/month + API calls |
| Secrets Manager (2 secrets) | $1.00 | $0.40/secret/month |
| ACM Certificate | $0.00 | Free with Route53 |
| **Observability** | | |
| CloudWatch Logs (ingestion) | $12.00 | $0.50/GB |
| CloudWatch Logs (storage) | $2.00 | $0.03/GB-month |
| CloudWatch Metrics | $12.00 | Custom metrics |
| CloudTrail | $5.00 | CW Logs + S3 storage |
| SNS (monitoring alarms) | $2.00 | Topics + subscriptions |
| **Load Balancing** | | |
| ALB (hourly) | $16.00 | $0.0225/hr |
| ALB (LCU charges) | $20.00 | $0.008/LCU-hr |
| **Backup** | | |
| AWS Backup | $15.00 | Daily/weekly/monthly retention |
| **Other** | | |
| Lambda (SSL, cleanup) | $0.00 | Within free tier |
| SSM Parameter Store | $0.00 | Standard params free |
| Data transfer | $15.00 | Inter-AZ + egress |
| | | |
| **PROD (4v/8G) TOTAL** | **~$1,387/mo** | |

---

### Production Environment — 8 vCPU / 32 GB

**Configuration:** 3–4 tasks × 8 vCPU / 32 GB, higher Aurora ACUs, full monitoring

| Service | Monthly Cost | Notes |
|---|--:|---|
| **Networking** | | |
| VPC NAT Gateways (×2) | $66.00 | $0.045/hr × 2 gateways |
| NAT GW data processing | $20.00 | $0.045/GB processed |
| **Compute** | | |
| ECS Fargate ARM64 (3.5 avg tasks) | **$952.00** | 3.5 × $271.86/task |
| Container Insights (enhanced) | $18.00 | CloudWatch metrics |
| **Database** | | |
| Aurora Serverless v2 (12 ACU avg) | $1,051.00 | $0.12/ACU-hr; writer 8 + reader 4 |
| Aurora storage (100 GB) | $10.00 | $0.10/GB-month |
| Aurora I/O | $10.00 | $0.20/million requests |
| Performance Insights (731-day) | $15.00 | ~$4.86/vCPU-month |
| **Cache** | | |
| ElastiCache Serverless Valkey | $60.00 | Storage + ECPUs |
| **Storage** | | |
| EFS (2 file systems) | $3.00 | $0.30/GB-month |
| S3 (ALB + CloudTrail logs) | $8.00 | Standard + lifecycle |
| **Security** | | |
| WAF v2 | $19.00 | ACL + rules + 5M requests |
| KMS keys (2–3) | $3.00 | $1/key/month + API calls |
| Secrets Manager (2 secrets) | $1.00 | $0.40/secret/month |
| ACM Certificate | $0.00 | Free with Route53 |
| **Observability** | | |
| CloudWatch Logs (ingestion) | $15.00 | $0.50/GB |
| CloudWatch Logs (storage) | $3.00 | $0.03/GB-month |
| CloudWatch Metrics | $15.00 | Custom metrics |
| CloudTrail | $7.00 | CW Logs + S3 storage |
| SNS (monitoring alarms) | $2.00 | Topics + subscriptions |
| **Load Balancing** | | |
| ALB (hourly) | $16.00 | $0.0225/hr |
| ALB (LCU charges) | $30.00 | $0.008/LCU-hr |
| **Backup** | | |
| AWS Backup | $20.00 | Daily/weekly/monthly retention |
| **Other** | | |
| Lambda (SSL, cleanup) | $0.00 | Within free tier |
| SSM Parameter Store | $0.00 | Standard params free |
| Data transfer | $20.00 | Inter-AZ + egress |
| | | |
| **PROD (8v/32G) TOTAL** | **~$2,364/mo** | |

---

## Summary Comparison

| Environment | Fargate Size | Compute | Database | Everything Else | **Monthly Total** |
|---|---|--:|--:|--:|--:|
| **QA** | 2 vCPU / 4 GB × 2 | $120 | $95 | $128 | **~$343** |
| **Staging** | 2 vCPU / 4 GB × 2 | $123 | $227 | $177 | **~$527** |
| **Production** | 4 vCPU / 8 GB × 3.5 | $419 | $721 | $247 | **~$1,387** |
| **Production** | 8 vCPU / 32 GB × 3.5 | $970 | $1,086 | $308 | **~$2,364** |

### All-Environment Totals (QA + Staging + Prod)

| Production Size | Monthly | Annual |
|---|--:|--:|
| With Prod at **4 vCPU / 8 GB** | **~$2,257** | **~$27,084** |
| With Prod at **8 vCPU / 32 GB** | **~$3,234** | **~$38,808** |

---

## Annual Estimates

| Environment | Monthly | Annual |
|---|--:|--:|
| **QA** | ~$343 | ~$4,116 |
| **Staging** | ~$527 | ~$6,324 |
| **Production (4v/8G)** | ~$1,387 | ~$16,644 |
| **Production (8v/32G)** | ~$2,364 | ~$28,368 |

---

## Key Cost Drivers

Ranked by impact on the bill:

1. **Aurora Serverless v2** — Largest variable cost. At minimum (0.5 ACU writer + 0.5 ACU reader = $88/mo), but production workloads at 8–12 ACUs drive $700–1,050/mo. Aurora auto-scales based on query load.

2. **ECS Fargate** — Fixed cost while tasks are running. Scales linearly with task count and resource allocation:
   - 2 vCPU / 4 GB = **$57.67/task/mo**
   - 4 vCPU / 8 GB = **$115.34/task/mo** (2× baseline)
   - 8 vCPU / 32 GB = **$271.86/task/mo** (4.7× baseline)

3. **NAT Gateways** — Fixed ~$66/mo per environment regardless of traffic. Required for private subnet internet access (container image pulls, certificate downloads, AWS API calls).

4. **WAF + ALB** — Combined ~$30–50/mo baseline. WAF charges per rule and per million requests; ALB charges per LCU-hour.

5. **AWS Backup** — Low initially but **accumulates over time** with 7-year retention. After years of operation, stored backups could add $50–200+/mo.

---

## Cost Optimization Opportunities

| Strategy | Potential Saving | Applies To | Implementation |
|---|---|---|---|
| **Fargate Savings Plans** (1yr/3yr) | Up to 50% on compute | All environments | [AWS Savings Plans](https://aws.amazon.com/savingsplans/) |
| **Aurora Reserved Capacity** (1yr) | ~30% if baseline is predictable | Staging / Prod | Commit to minimum ACUs |
| **Shut down QA off-hours** | ~65% of QA Fargate + Aurora scales to min | QA | Scale to 0 tasks nights/weekends |
| **Single NAT Gateway** for non-prod | ~$33/mo per environment | QA / Staging | Reduce AZs or use NAT instance |
| **Remove Aurora reader** in QA | ~$44/mo | QA | Single writer-only cluster |
| **Reduce to 1 Fargate task** in QA | ~$58/mo | QA | Set `minimum_capacity: 1` |
| **Fargate Spot** for non-prod | Up to 70% off Fargate | QA / Staging | ECS Spot capacity provider |
| **EFS Infrequent Access** | Up to 92% on storage | All | Enable lifecycle policy |

### Potential Savings Impact

If all non-prod optimizations are applied (single NAT GW, 1 task, no reader, off-hours shutdown):

| Environment | Before | After | Saving |
|---|--:|--:|--:|
| QA | ~$343/mo | ~$130/mo | **~$213/mo** |
| Staging | ~$527/mo | ~$350/mo | **~$177/mo** |

---

## Optional Add-ons

These features are disabled by default and add cost only when enabled via `cdk.json` context flags:

| Feature | Context Flag | Additional Cost/mo | Notes |
|---|---|--:|---|
| Global Accelerator | `enable_global_accelerator` | ~$18+ | $0.025/hr fixed + $0.015/GB data |
| SES Email Integration | `configure_ses` | ~$7+ | VPC endpoint ~$7 + $0.10/1000 emails |
| SageMaker + EMR Analytics | `create_serverless_analytics_environment` | ~$50–200+ | Highly variable by usage |
| ECS Exec (debugging) | `enable_ecs_exec` | ~$3 | KMS key + S3 bucket + log group |
| Bedrock Integration | `enable_bedrock_integration` | ~$0 fixed | Per-inference costs only |
| Patient Portal | `enable_patient_portal` | $0 | No additional infra cost |
| OpenEMR APIs | `activate_openemr_apis` | $0 | No additional infra cost |

---

## Notes and Caveats

1. **Estimates, not guarantees** — Actual costs depend on traffic patterns, query complexity, data growth, and autoscaling behavior. Use [AWS Cost Explorer](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/) after deployment for real numbers.

2. **First-month costs** may be lower because backup vaults start empty and some services have free-tier allowances (Lambda, SNS, CloudWatch partial).

3. **Backup costs accumulate** — With 7-year retention (daily/weekly/monthly backups per the `daily_weekly_monthly7_year_retention` plan), stored recovery points grow over time. After several years, backup storage alone could add $50–200+/mo depending on data size and change rate.

4. **Aurora Serverless v2 scaling** — The cluster scales between `serverless_v2_min_capacity: 0.5` and `serverless_v2_max_capacity: 256` ACUs. Average usage depends heavily on query patterns. The estimates above assume steady-state averages; peak-hour costs may be higher.

5. **Data transfer** — Inter-AZ transfer ($0.01/GB each direction) and internet egress ($0.09/GB after first 100 GB) are estimated conservatively. High-traffic deployments will see higher transfer costs.

6. **Pricing may change** — AWS adjusts pricing periodically. Verify current rates at [aws.amazon.com/pricing](https://aws.amazon.com/pricing/).

7. **Region matters** — Prices vary by region. us-east-1 is typically the cheapest US region. Other regions (e.g., ap-southeast-2) may be 5–15% higher.
