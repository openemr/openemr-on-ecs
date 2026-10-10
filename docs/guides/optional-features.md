# Optional features

This guide explains the optional features you can switch on or off in `cdk.json`: the patient
portal, monitoring alarms with email alerts, long-term CloudTrail audit logging, AWS Global
Accelerator, and Aurora machine learning with Amazon Bedrock. It's for anyone who has a working
deployment and wants to add capabilities.

Every feature follows the same pattern: change one setting in `cdk.json`, then run
`node_modules/.bin/cdk deploy`. See [Configuration](configuration.md#change-a-setting) for the
details of changing settings.

## Before you begin

- A deployed stack (see [Get Started](../get-started/README.md)).
- **Cost:** each feature lists its own cost impact below. Most add a small fixed monthly charge
  plus usage. See [Costs](../reference/costs.md).
- **Time:** each one is a single redeploy, usually 10–20 minutes.

## Which features are where

| Feature | Setting | Where it's documented |
|---|---|---|
| Patient portal | `enable_patient_portal` | [Below](#patient-portal) |
| Monitoring alarms and email alerts | `enable_monitoring_alarms` | [Below](#monitoring-alarms-and-email-alerts) |
| Long-term CloudTrail audit logging | `enable_long_term_cloudtrail_monitoring` | [Below](#long-term-cloudtrail-audit-logging) |
| AWS Global Accelerator | `enable_global_accelerator` | [Below](#aws-global-accelerator) |
| Aurora ML with Amazon Bedrock | `enable_bedrock_integration` | [Below](#aurora-machine-learning-with-amazon-bedrock) |
| REST and FHIR APIs | `activate_openemr_apis` | [REST and FHIR APIs](apis.md) |
| Shell access and database port forwarding | `enable_ecs_exec` | [Database access](database-access.md) |
| RDS Data API | `enable_data_api` | [Database access](database-access.md#use-the-rds-data-api) |
| Email through Amazon SES | `configure_ses` | [HTTPS and DNS](https-and-dns.md#optional-send-email-with-amazon-ses) |
| Serverless analytics | `create_serverless_analytics_environment` | [Analytics](analytics.md) |
| Stack termination and database deletion protection | `enable_stack_termination_protection`, `rds_deletion_protection` | [Configuration](configuration.md#protecting-against-accidental-deletion) |
| Credential rotation | always deployed; run on demand | [Credential rotation](credential-rotation.md) |
| Import into a new stack | `openemr_import_target` | [Importing an existing OpenEMR](importing-openemr.md) |

## Patient portal

The [OpenEMR patient portal](https://www.open-emr.org/wiki/index.php/Patient_Portal) lets patients
sign in to see their records, messages, and appointments.

**Cost:** none beyond normal usage.

1. In `cdk.json`, set:

   ```json
   "enable_patient_portal": "true",
   ```

2. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

3. Open `<ApplicationURL>/portal/` (for example `https://openemr.example.com/portal/`). Success
   looks like the patient portal login page.

The stack sets these OpenEMR settings for you: the portal address (`<ApplicationURL>/portal/`),
the portal turned on, the alternate CCDA service setting (`ccda_alt_service_enable` set to `3`),
the portal REST API turned on, and OpenEMR's OAuth2 base URL (`site_addr_oath`) set to the
`ApplicationURL`.

> [!IMPORTANT]
> Patients can only reach the portal from IP addresses you allow in
> `security_group_ip_range_ipv4` / `ipv6`. For patients to use it from home, you'd have to allow
> wider access, which also exposes the staff login page. Think this through with your security
> team; see [Configuration](configuration.md#who-can-reach-openemr-ip-access).

> [!NOTE]
> Setting `enable_patient_portal` back to `"false"` stops the stack from applying these settings.
> OpenEMR may keep values it has already saved, so also check **Admin** > **Config** >
> **Portal** in OpenEMR.

## Monitoring alarms and email alerts

This creates Amazon CloudWatch alarms that watch the OpenEMR service and load balancer, and emails
you when something goes wrong (and again when it recovers).

**Cost:** a small monthly charge per alarm, plus SNS email notifications. See
[CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/).

1. In `cdk.json`, set:

   ```json
   "enable_monitoring_alarms": "true",
   "monitoring_email": "ops-team@example.com",
   ```

   Optionally, also set `deployment_notification_email` (see below).

2. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

3. **Confirm the subscription.** AWS sends an email titled "AWS Notification - Subscription
   Confirmation" to each address. Choose **Confirm subscription** in it, or you won't get alerts.

Which address gets what:

- Alarm emails go to `monitoring_email`. If that's not set, they go to `email_forwarding_address`.
- `deployment_notification_email` is subscribed to a separate deployment-events topic. If it's
  not set, `monitoring_email` (or its fallback) is used.
- If no address is set at all, the alarms are still created and visible in CloudWatch, but don't
  send any notifications.

The alarms (`<stack>` is your stack name in lowercase, such as `openemrecsstack`):

| Alarm name | Goes off when |
|---|---|
| `openemr-ecs-high-cpu-<stack>` | Average CPU of the OpenEMR service is above 85% for two 5-minute periods, or there's no CPU data |
| `openemr-ecs-high-memory-<stack>` | Average memory of the OpenEMR service is above 85% for two 5-minute periods, or there's no memory data |
| `openemr-ecs-low-tasks-<stack>` | Fewer than 1 OpenEMR task is running (checked every minute) |
| `openemr-alb-unhealthy-targets-<stack>` | At least one task fails the load balancer's health check for two periods in a row |
| `openemr-alb-high-5xx-<stack>` | Tasks return more than 10 server errors (HTTP 5xx) in each of two 5-minute periods |
| `openemr-alb-high-response-time-<stack>` | Average response time is above 5 seconds for two periods in a row |
| `openemr-ecs-deployment-failure-<stack>` | 2 or more tasks stop within 5 minutes, which usually means a failed deployment |

If you scale the service to zero on purpose (for example during a restore), expect the CPU,
memory, and low-task alarms to go off.

Notifications go through two encrypted SNS topics that only accept secure (TLS) connections:
`openemr-monitoring-alarms-<stack>` and `openemr-deployment-events-<stack>`.

> [!NOTE]
> Nothing in the stack currently publishes to the deployment-events topic, so
> `deployment_notification_email` won't receive messages unless you connect something to that
> topic yourself (for example an EventBridge rule for ECS deployment events).

## Long-term CloudTrail audit logging

[AWS CloudTrail](https://aws.amazon.com/cloudtrail/) records API activity in your account: who did
what, and when. This feature is **on by default**, and we recommend leaving it on for production.

**Cost:** S3 and CloudWatch Logs storage for the logs, and a KMS key. See
[CloudTrail pricing](https://aws.amazon.com/cloudtrail/pricing/).

What you get:

- A trail that records **all** management events (reads and writes) from **all Regions**, including
  global services such as IAM, with log file integrity validation turned on.
- Logs in an encrypted S3 bucket, moved to Glacier after 90 days and deleted after 7 years.
- A copy in CloudWatch Logs, kept for 9 years.
- A dedicated KMS key for the trail.

To turn it off (for example in a short-lived test stack), set:

```json
"enable_long_term_cloudtrail_monitoring": "false",
```

and deploy.

> [!WARNING]
> The CloudTrail S3 bucket is emptied and deleted when you turn this feature off **or** delete the
> stack. Only the CloudWatch Logs copy is kept after that. If you need the S3 logs for compliance,
> copy them elsewhere first.

## AWS Global Accelerator

[AWS Global Accelerator](https://aws.amazon.com/global-accelerator/) gives you fixed IP addresses
and routes users' traffic onto AWS's global network at the edge location nearest to them, which
can make OpenEMR faster for people far from your Region. In the original author's testing,
performance improved noticeably. Consider it if your users are spread around the world, upload and
download large files, or are numerous.

**Cost:** a fixed hourly charge plus data transfer. See
[Global Accelerator pricing](https://aws.amazon.com/global-accelerator/pricing/).

1. In `cdk.json`, set:

   ```json
   "enable_global_accelerator": "true",
   ```

2. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

3. Use the new address. The stack outputs `GlobalAcceleratorUrl` (`https://<accelerator DNS
   name>`), which CDK prints at the end of the deploy.
   - With `route53_domain`, the `openemr.<domain>` record is pointed at the accelerator
     automatically, so keep using `https://openemr.<domain>`.
   - With `certificate_arn`, point your own DNS record at the accelerator's DNS name instead of
     the load balancer.
   - Without a domain, `ApplicationURL` becomes the accelerator URL. Your browser will warn that
     the certificate doesn't match that name.

The accelerator listens only on HTTPS (port 443) and forwards to the load balancer, so your IP
allowlist and the web application firewall still apply.

## Aurora machine learning with Amazon Bedrock

This lets you call Amazon Bedrock foundation models from SQL, using
[Aurora machine learning](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/mysql-ml.html#using-amazon-bedrock).
For example, you can create MySQL functions that ask a model questions about your data, such as
"How many patients have appointments today?" or "Based on patient X's history, what treatment
might be appropriate for these symptoms, and why?"

**Cost:** you pay for Bedrock model usage, plus a VPC endpoint. See
[Bedrock pricing](https://aws.amazon.com/bedrock/pricing/).

1. In the Bedrock console,
   [make sure you have access to the models](https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started.html)
   you want to use, in your Region.

2. In `cdk.json`, set:

   ```json
   "enable_bedrock_integration": "true",
   ```

   Optionally adjust `aurora_ml_inference_timeout` (milliseconds, default `"30000"`) for slow
   models.

3. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

What gets created: an IAM role for Aurora (`AuroraMLRole`) that may call `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` on Bedrock foundation models, attached to the database
cluster for the Bedrock feature; the `aws_default_bedrock_role` database parameter set to that
role; and a private VPC endpoint for Bedrock Runtime, so calls don't leave AWS's network.

4. Connect to the database (see [Database access](database-access.md)) and create a function. This
   example follows the AWS documentation; replace the model ID with one you have access to:

   ```sql
   CREATE FUNCTION invoke_model (request_body TEXT)
     RETURNS TEXT
     ALIAS AWS_BEDROCK_INVOKE_MODEL
     MODEL ID 'anthropic.claude-3-haiku-20240307-v1:0'
     CONTENT_TYPE 'application/json'
     ACCEPT 'application/json';
   ```

   Database users other than the administrator need the `AWS_BEDROCK_ACCESS` role granted to them.
   See the [AWS guide](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/mysql-ml.html#using-amazon-bedrock)
   for request formats and permissions.

> [!NOTE]
> New Aurora MySQL engine versions sometimes ship before features like the Bedrock integration
> are available for them. The project uses a recent engine version, set as
> `AURORA_MYSQL_ENGINE_VERSION` in [`openemr_ecs/constants.py`](../../openemr_ecs/constants.py). If
> the integration doesn't work on that version, you may need an earlier engine version. Changing
> the engine version of an existing database is a significant operation; test it on a separate
> stack first.

## If something goes wrong

| Problem | What to do |
|---|---|
| No alarm emails | Confirm the SNS subscription email, and check your spam folder. Check that `monitoring_email` (or `email_forwarding_address`) is set. |
| Portal page not found | Make sure you deployed with `enable_patient_portal` set to `"true"` and use the `/portal/` path. |
| Global Accelerator URL times out | Your IP address must still be allowed in `security_group_ip_range_ipv4`. |
| Bedrock function returns an access error | Check model access in the Bedrock console for your Region, and that the user has the `AWS_BEDROCK_ACCESS` role. |
| Bedrock calls time out | Raise `aurora_ml_inference_timeout` and deploy. |

More in [Troubleshooting](../reference/troubleshooting.md).

## Related

- [Configuration](configuration.md): every setting and its default
- [Security and compliance](../reference/security-and-compliance.md): how CloudTrail, WAF, and
  encryption fit together
- [Costs](../reference/costs.md)
- [Architecture](../reference/architecture.md)
