# HTTPS and DNS

This guide shows you how to give OpenEMR a TLS certificate (required for every deployment), how
to use your own domain name, and how to turn on outgoing email through Amazon SES. It's for
anyone setting up a deployment for the first time or moving from a test URL to a real domain.

## Before you begin

You need **one** of the following:

- **A domain in Amazon Route 53** in the same AWS account you deploy to (recommended). If you
  don't have one, you can
  [register a domain with Route 53](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-register.html#domain-register-procedure-section),
  which also creates the hosted zone for you.
- **An existing certificate in AWS Certificate Manager (ACM)** in the same Region you deploy to.

**Time:** 5 minutes to configure. Certificate validation adds a few minutes to the deployment.

**Cost:** Route 53 hosted zones and queries, and SES email, are billed separately. ACM public
certificates used with a load balancer don't have a separate certificate charge. See pricing for
[Route 53](https://aws.amazon.com/route53/pricing/),
[AWS Certificate Manager](https://aws.amazon.com/certificate-manager/pricing/), and
[Amazon SES](https://aws.amazon.com/ses/pricing/).

## How HTTPS works in this project

Traffic is encrypted for the whole trip, and plain HTTP is never offered:

- **Your browser to the load balancer:** HTTPS on port 443, using the ACM certificate you
  provide. The load balancer's security group only opens port 443; port 80 is never opened.
- **Load balancer to the OpenEMR containers:** HTTPS on port 443, using self-signed certificates
  that the stack generates, stores on a dedicated encrypted EFS file system, and regenerates
  every 2 days.

Because of this, the stack refuses to build unless you set `route53_domain` or
`certificate_arn`. If you set both, `certificate_arn` is used and the stack doesn't create a
certificate or DNS record (see [Option 2](#option-2-use-an-existing-acm-certificate)).

## Option 1: Let the stack manage a Route 53 domain (recommended)

With this option, the stack issues, validates, attaches, and renews the certificate for you, and
creates the DNS record that points your domain at OpenEMR.

1. Confirm the hosted zone exists in the account and Region you're deploying with:

   ```bash
   aws route53 list-hosted-zones --query "HostedZones[].Name" --output text
   ```

   Your domain should be listed, with a trailing dot (for example `example.com.`). It must be a
   **public** hosted zone.

2. In `cdk.json`, set your domain. Use the bare domain, with no `https://` and no trailing dot:

   ```json
   "route53_domain": "example.com",
   "certificate_arn": null,
   ```

3. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

4. When the deployment finishes, open the `ApplicationURL` stack output. It will be
   `https://openemr.example.com`.

What the stack creates:

- An ACM certificate for `*.example.com` (a wildcard that covers `openemr.example.com`),
  validated automatically by adding DNS records to your hosted zone.
- An alias `A` record named `openemr.example.com` that points to the load balancer, or to the
  Global Accelerator if you turned on
  [Global Accelerator](optional-features.md#aws-global-accelerator).
- Automatic renewal: ACM renews the certificate before it expires for as long as the validation
  records stay in your hosted zone. See
  [ACM managed renewal](https://docs.aws.amazon.com/acm/latest/userguide/managed-renewal.html).

> [!NOTE]
> The CDK looks up your hosted zone by name when it synthesizes, so the command needs AWS
> credentials for the account that owns the zone. The result is cached in `cdk.context.json`.

## Option 2: Use an existing ACM certificate

Choose this if your DNS is hosted somewhere other than Route 53, or you already manage
certificates.

The certificate must:

- be a public certificate in ACM, in the **same Region** you deploy to;
- be **issued** (validated), not pending validation; and
- cover the hostname people will use to reach OpenEMR (for example `emr.example.org` or
  `*.example.org`).

To issue or import a certificate, see
[Getting started with ACM](https://docs.aws.amazon.com/acm/latest/userguide/gs.html) and
[Importing certificates](https://docs.aws.amazon.com/acm/latest/userguide/import-certificate.html).
ACM-issued certificates renew automatically; imported certificates don't, so you must re-import
before they expire.

1. Find your certificate's ARN:

   ```bash
   aws acm list-certificates --query "CertificateSummaryList[].[DomainName,CertificateArn]" --output table
   ```

2. In `cdk.json`, set it (and leave `route53_domain` as `null`):

   ```json
   "certificate_arn": "arn:aws:acm:us-east-1:123456789012:certificate/12345678-1234-1234-1234-123456789012",
   "route53_domain": null,
   ```

3. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

4. **Create the DNS record yourself.** With this option the stack doesn't touch DNS. At your DNS
   provider, create a `CNAME` record from your chosen hostname to the `LoadBalancerDNS` stack
   output (or to the `GlobalAcceleratorUrl` hostname if you use Global Accelerator).

   ```bash
   aws cloudformation describe-stacks --stack-name OpenemrEcsStack \
     --query "Stacks[0].Outputs[?OutputKey=='LoadBalancerDNS'].OutputValue" --output text
   ```

5. Browse to `https://<your hostname>`. Until DNS is in place, the `ApplicationURL` output
   (`https://<load balancer DNS name>`) also works, but your browser will warn that the
   certificate doesn't match that name.

> [!NOTE]
> If you set both `route53_domain` and `certificate_arn`, the stack uses your certificate and
> creates no certificate and no `openemr` DNS record, yet the `ApplicationURL` output still shows
> `https://openemr.<route53_domain>`. Create that record yourself, or set only one of the two.

## Optional: Send email with Amazon SES

OpenEMR can send email (appointment reminders, password resets, and so on). With
`configure_ses`, the stack sets up Amazon SES and gives OpenEMR working SMTP settings, so you
don't have to configure a mail server.

### Before you start with SES

- You need **Option 1** (`route53_domain`). SES setup is skipped if `route53_domain` isn't set.
- New AWS accounts start in the SES *sandbox*, which only sends to verified addresses.
  [Request production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html)
  before sending to real patients.
- Your Region must support SES, including email receiving if you want forwarding.

> [!WARNING]
> SES setup adds DNS records to your hosted zone, including an **MX record for the root of your
> domain** (pointing at `inbound-smtp.<region>.amazonaws.com`) and a `_dmarc` TXT record. If your
> domain already receives mail somewhere else (for example Google Workspace or Microsoft 365),
> those records conflict with your existing mail setup. Use a domain or subdomain zone dedicated
> to OpenEMR.

### Turn on SES

1. In `cdk.json`, set:

   ```json
   "route53_domain": "example.com",
   "configure_ses": "true",
   ```

2. Optional: to forward mail that people send to `help@example.com` on to a real mailbox, also
   set:

   ```json
   "email_forwarding_address": "target-email@example.org",
   ```

3. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

What the stack creates:

- An SES domain identity for `example.com`, with DKIM `CNAME` records, a custom MAIL FROM domain
  `services.example.com`, a `_dmarc` record, and the root MX record mentioned above.
- An IAM user named `ses-smtp-user-<stack name in lowercase>` whose access key is converted into
  an SMTP password by a one-time Lambda function (`SMTPSetup`) and stored in Secrets Manager.
- SMTP settings passed into OpenEMR: host `email-smtp.<region>.amazonaws.com`, port `587`, TLS.
  The sender address is `notifications@services.example.com` with the name `OpenEMR`.
- A private VPC endpoint for SES SMTP, so mail leaves through AWS's network.
- A receipt rule for `help@example.com` that stores incoming mail in an S3 bucket. If you set
  `email_forwarding_address`, a Lambda function also forwards each message to that address.

### Activate the SMTP settings in OpenEMR

The settings are pre-filled, but OpenEMR only uses them after you save them once:

1. Log in to OpenEMR as `admin`.
2. Go to **Admin** > **Config**, then choose **Notifications** in the sidebar.
3. Choose **Save**. You don't need to change any values.

![OpenEMR Notifications settings with the Save button](../images/activating_email_credentials.png)

### Test that email works

Sherwin Gaddis shared a small `testmail.php` script in the OpenEMR community forum. You can
[read about it and download it here](https://community.open-emr.org/t/how-do-i-actually-send-an-email-to-my-client-from-within-openemr/20647/9).
After following those instructions, browse to
`https://openemr.example.com/interface/testmail.php`. A working setup looks like this:

![testmail.php output showing a successfully sent message](../images/testemail.php_output.png)

### Rotate the SMTP credentials

The SMTP password is derived from the access key of the `ses-smtp-user-...` IAM user. The
original procedure is:

1. Rotate the access key for the `ses-smtp-user-<stack name>` IAM user.
2. Invoke the `SMTPSetup` Lambda function to regenerate the SMTP password.
3. Update the OpenEMR ECS service (force a new deployment) so tasks pick up the new secret.
4. In OpenEMR, save the notification settings again with the updated SMTP password
   (**Admin** > **Config** > **Notifications** > **Save**).

> [!NOTE]
> The `SMTPSetup` function reads the access key secret from a Secrets Manager secret created by
> the stack (logical name `secret-access-key`), and OpenEMR reads the SMTP user name (the access
> key ID) from the `smtp_user` SSM parameter. When you rotate the key, both must hold the new
> key's values before you run step 2, or the regenerated password won't match.

To force the new deployment in step 3:

```bash
CLUSTER=$(aws cloudformation describe-stacks --stack-name OpenemrEcsStack \
  --query "Stacks[0].Outputs[?OutputKey=='ECSClusterName'].OutputValue" --output text)
SERVICE=$(aws cloudformation describe-stacks --stack-name OpenemrEcsStack \
  --query "Stacks[0].Outputs[?OutputKey=='ECSServiceName'].OutputValue" --output text)
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment
```

## If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| `Failed to create DNS and certificates for domain ...` during synth | The hosted zone doesn't exist in this account, isn't public, or your credentials are for a different account. Check step 1 of Option 1. |
| Deployment waits a long time on the certificate | ACM is waiting for DNS validation. Make sure the hosted zone is the one your domain registrar actually uses (its name servers match). |
| Browser says the certificate is invalid | You're using a hostname the certificate doesn't cover, such as the raw load balancer name. Use the domain on the certificate. |
| `A record ... already exists` | An `openemr` record already exists in the zone. Remove it or use a different zone. |
| SES deployment fails on an MX or `_dmarc` record | Records already exist at those names. See the SES warning above. |
| Email isn't sent | The account is still in the SES sandbox, or you haven't saved the Notifications settings in OpenEMR. |

More fixes are in [Troubleshooting](../reference/troubleshooting.md).

## Related

- [Configuration](configuration.md): every `cdk.json` setting
- [Optional features](optional-features.md#aws-global-accelerator): Global Accelerator and DNS
- [Security and compliance](../reference/security-and-compliance.md): encryption in transit and at rest
- [Architecture](../reference/architecture.md)
