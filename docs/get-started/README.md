# Get started: deploy OpenEMR on AWS

This guide takes you step by step from a computer with nothing installed to a working OpenEMR
login running in your own AWS account, and then shows you how to delete it all again. It's
written for people who are new to AWS, Python, or the command line. If a word is unfamiliar,
check the [glossary](glossary.md).

**On this page**

1. [Before you begin](#before-you-begin)
2. [Install the tools](#step-1-install-the-tools)
3. [Set up your AWS account and credentials](#step-2-set-up-your-aws-account-and-credentials)
4. [Get the code and install dependencies](#step-3-get-the-code-and-install-dependencies)
5. [Configure your deployment](#step-4-configure-your-deployment)
6. [Bootstrap CDK](#step-5-bootstrap-cdk-once-per-account-and-region)
7. [Run the pre-flight check](#step-6-run-the-pre-flight-check)
8. [Deploy](#step-7-deploy)
9. [Find your URL and admin password](#step-8-find-your-url-and-admin-password)
10. [Log in](#step-9-log-in)
11. [What you just deployed](#what-you-just-deployed)
12. [Turning it off: clean up](#turning-it-off-clean-up)
13. [Common questions](#common-questions)
14. [Next steps](#next-steps)
15. [Quick troubleshooting](#quick-troubleshooting)

## Before you begin

### What you'll build

A complete OpenEMR installation that runs on AWS-managed services: application containers on
[Fargate](glossary.md#fargate), an [Aurora MySQL](glossary.md#aurora-serverless-v2-and-acu)
database, shared file storage, a cache, a firewall, automatic backups, and encryption
everywhere. When you're done you'll open `https://...` in your browser and log in as `admin`.
The [architecture overview](../reference/architecture.md) has the full picture.

### How long it takes

About **one hour**. Most of that is waiting: the deployment itself runs for roughly 40 minutes
while AWS creates the database, network, and other resources. You can leave it running in the
background.

### What it costs

> [!WARNING]
> This is not a free-tier project. With the default settings, expect roughly **$320 per month
> (about $10–11 per day)** while it's running, and more under heavy use. AWS bills by the hour,
> so a one-afternoon trial costs a few dollars, but **charges continue until you delete it**.
> See [Costs](../reference/costs.md) for the breakdown, and
> [Turning it off](#turning-it-off-clean-up) for how to stop paying.
>
> Consider setting up an [AWS Budget alert](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html)
> before you start, so AWS emails you if spending passes a limit you choose.

### What you need

- **An AWS account** where you're allowed to create resources. A fresh account dedicated to
  this project is ideal. Sign up at [aws.amazon.com](https://aws.amazon.com/) (you'll need a
  credit card).
- **A domain name or an existing certificate.** OpenEMR holds health records, so this project
  only ever serves it over HTTPS (encrypted). HTTPS needs a TLS
  [certificate](glossary.md#acm-certificate), and certificates are issued for domain names
  you control. You need one of:
  - **A domain in [Route 53](glossary.md#route-53-and-hosted-zone)** in the same AWS account
    (easiest: the deployment creates and renews the certificate for you). If you don't own a
    domain yet, you can
    [register one through Route 53](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-register.html)
    for a yearly fee; Route 53 creates the hosted zone automatically.
  - **An existing, issued certificate in AWS Certificate Manager (ACM)** in the Region you'll
    deploy to, for a domain whose DNS you can edit.
- **A computer running macOS, Linux, or Windows** with administrator rights to install
  software and a reliable internet connection.

> [!IMPORTANT]
> If you'll store real patient information, read [Security and compliance](../reference/security-and-compliance.md)
> first. You must sign a [Business Associate Addendum (BAA)](glossary.md#hipaa-and-baa) with AWS
> before putting protected health information in any AWS account. For a first try, use only
> fake test data.

## Step 1: Install the tools

You'll install five tools. Each one has a short "verify" command; run it before moving on.

| Tool | Version | What it's for |
|---|---|---|
| Git | Any recent version | Downloads this project's code |
| Python | 3.14 | Runs the infrastructure code |
| Node.js | 24 | Runs the AWS CDK command-line tool |
| AWS CLI | Version 2 | Lets your computer talk to your AWS account |
| Docker | Docker Desktop or Docker Engine | Builds a small helper container image during deployment |

> [!NOTE]
> **Windows users:** the helper scripts in this project are Bash scripts, so the smoothest path
> on Windows is [WSL 2](https://learn.microsoft.com/windows/wsl/install) (Windows Subsystem for
> Linux) with Ubuntu. Install WSL 2, open the Ubuntu terminal, and follow the **Linux**
> instructions throughout this guide. Install Docker Desktop for Windows and turn on its WSL 2
> integration.

**Opening a terminal:** on macOS press `Cmd + Space`, type `Terminal`, and press Enter. On
Linux press `Ctrl + Alt + T`. On Windows, open **Ubuntu** from the Start menu (after installing
WSL 2).

### Git

- **macOS:** run `xcode-select --install` and accept the prompt.
- **Linux (Ubuntu/Debian):** `sudo apt update && sudo apt install -y git`
- Other systems: [git-scm.com/downloads](https://git-scm.com/downloads)

Verify: `git --version` prints a version such as `git version 2.x`.

### Python 3.14

- **macOS:** download the Python 3.14 installer from
  [python.org/downloads](https://www.python.org/downloads/) and run it.
- **Linux:** use your distribution's package if it offers 3.14, or a version manager such as
  [pyenv](https://github.com/pyenv/pyenv) or [uv](https://docs.astral.sh/uv/). Make sure the
  `venv` module is included (on Ubuntu it's a separate `python3.14-venv` package).

Verify:

```bash
python3 --version
```

You should see `Python 3.14.x`. If you see an older version, try `python3.14 --version`; if that
works, use `python3.14` wherever this guide says `python3`.

### Node.js 24

Download the **Node.js 24** installer from [nodejs.org](https://nodejs.org/), or, if you use
[nvm](https://github.com/nvm-sh/nvm), run `nvm install 24 && nvm use 24`.

Verify:

```bash
node --version
npm --version
```

`node --version` should print `v24.x.x`. This project's `package.json` requires Node 24. You do
**not** need to install the CDK globally; you'll install the project's pinned copy in Step 3.

### AWS CLI version 2

- **macOS:**

  ```bash
  curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
  sudo installer -pkg AWSCLIV2.pkg -target /
  ```

- **Linux (x86_64):**

  ```bash
  curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
  unzip awscliv2.zip
  sudo ./aws/install
  ```

  On ARM Linux, replace `x86_64` with `aarch64`. Full instructions:
  [Installing the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).

Verify: `aws --version` prints something starting with `aws-cli/2.`.

### Docker

The deployment builds a small container image (the credential-rotation helper) on your
computer and uploads it to your account, so Docker must be installed and running when you
deploy.

- **macOS or Windows:** install [Docker Desktop](https://docs.docker.com/desktop/) and start it.
- **Linux:** install [Docker Engine](https://docs.docker.com/engine/install/). The image is
  built for 64-bit ARM (the containers run on AWS Graviton processors). If your Linux machine is
  x86_64, enable ARM emulation once:

  ```bash
  docker run --privileged --rm tonistiigi/binfmt --install arm64
  ```

Verify:

```bash
docker run --rm hello-world
```

You should see `Hello from Docker!`.

**If this fails:** make sure Docker Desktop is open (look for the whale icon), or on Linux that
your user can run Docker (`sudo usermod -aG docker $USER`, then log out and back in).

## Step 2: Set up your AWS account and credentials

Your computer needs credentials so the AWS CLI and CDK can act in your account. You'll also
pick an AWS [Region](glossary.md#aws-account-and-region), the geographic location where
everything is created (for example `us-east-1` or `us-west-2`).

> [!WARNING]
> Never create access keys for your AWS account's **root user** (the email address you signed
> up with). Root keys can do anything, including closing the account, and can't be limited.
> Use one of the options below instead.

### Option A (recommended): IAM Identity Center and `aws configure sso`

[IAM Identity Center](glossary.md#iam-and-iam-identity-center) gives you a normal sign-in with
short-lived credentials that expire automatically, so there are no long-lived keys to leak.

1. Sign in to the [AWS Console](https://console.aws.amazon.com/) and open **IAM Identity
   Center**. Choose **Enable** if it isn't on yet.
2. Create a user for yourself (**Users → Add user**) and accept the email invitation.
3. Give that user access to your account: **AWS accounts →** select your account **→ Assign
   users or groups →** pick your user **→** create or choose the **AdministratorAccess**
   permission set. (Deploying this project creates many kinds of resources, so broad
   permissions are simplest in a dedicated account.)
4. On the Identity Center **Dashboard**, copy the **AWS access portal URL**.
5. In your terminal, run:

   ```bash
   aws configure sso
   ```

   Answer the prompts: any session name (for example `openemr`), the portal URL from step 4,
   the Region where Identity Center lives, then pick your account and role in the browser
   window that opens. When asked for the **default client Region**, enter the Region you want
   to deploy OpenEMR into. For the profile name, enter `openemr`.
6. Tell the CLI and CDK to use that profile in this terminal:

   ```bash
   export AWS_PROFILE=openemr
   ```

   Run this again in every new terminal window. When your sign-in expires (usually after a
   few hours), run `aws sso login` to refresh it.

Full details: [Configuring IAM Identity Center authentication with the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).

### Option B: An IAM user with access keys

If you can't use Identity Center, create a dedicated IAM user instead of using root.

1. In the AWS Console open **IAM → Users → Create user**. Name it something like
   `openemr-deployer` and attach the **AdministratorAccess** policy.
2. Open the user, go to **Security credentials → Create access key**, choose **Command Line
   Interface (CLI)**, and finish the wizard. Copy the **Access key ID** and **Secret access
   key** now; you can't see the secret again.
3. Run `aws configure` and enter the key ID, the secret, your deployment Region (for example
   `us-east-1`), and press Enter for the output format.

> [!TIP]
> Treat access keys like passwords: never paste them into code, chat, or email, and delete the
> key in IAM when you're finished with this project.

### Verify your credentials

```bash
aws sts get-caller-identity
aws configure get region
```

**Success looks like:** the first command prints JSON with your 12-digit `Account` number, and
the second prints the Region you chose.

**If this fails:** "Unable to locate credentials" means the profile isn't active (run
`export AWS_PROFILE=openemr` for Option A, or re-run `aws configure` for Option B). "Token has
expired" means you need `aws sso login`. If the Region is blank, run
`aws configure set region us-east-1` (with your Region).

### One-time setup for brand-new accounts

ECS and its autoscaling rely on two AWS-managed
[service-linked roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/using-service-linked-roles.html).
In a brand-new account they may not exist yet, which can make the first deployment fail.
Creating them ahead of time is harmless:

```bash
aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com
aws iam create-service-linked-role --aws-service-name ecs.application-autoscaling.amazonaws.com
```

An error saying the role "has been taken" or "already exists" is fine; it means the role is
already there.

## Step 3: Get the code and install dependencies

You'll download the project, create a Python [virtual environment](glossary.md#virtual-environment)
(a private folder of Python packages just for this project), and install the pinned CDK tool.

1. Download the code and move into its folder:

   ```bash
   git clone https://github.com/openemr/openemr-on-ecs.git
   cd openemr-on-ecs
   ```

2. Create and activate the virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   Your prompt now starts with `(.venv)`. Run `source .venv/bin/activate` again whenever you
   open a new terminal for this project.

   > [!NOTE]
   > If you're using native Windows PowerShell rather than WSL, the activation command is
   > `.venv\Scripts\activate`. The rest of this guide assumes a Bash shell.

3. Install the Python packages and the project's pinned Node tools (including the CDK CLI):

   ```bash
   pip install -r requirements.txt
   npm ci
   ```

4. Verify the CDK tool:

   ```bash
   node_modules/.bin/cdk --version
   ```

**Success looks like:** a version number such as `2.1143.0 (build ...)`. Always run CDK as
`node_modules/.bin/cdk` from the project folder; that's the exact version this project is
tested with. Don't install or use a global `cdk`.

**If this fails:** "pip: command not found" usually means the virtual environment isn't active;
re-run `source .venv/bin/activate` or use `python3 -m pip install -r requirements.txt`. If
`npm ci` complains about the Node version, check `node --version` shows v24. Don't use `sudo`
with `pip` or `npm`.

## Step 4: Configure your deployment

All settings live in the `context` section of [`cdk.json`](glossary.md#cdkjson-and-context) in
the project folder. For a first deployment you only need to look at two things: the
certificate and who's allowed to connect. Open `cdk.json` in any text editor.

> [!TIP]
> `cdk.json` is JSON: text values need double quotes (`"example.com"`), and "not set" is written
> as `null` without quotes. Don't remove the commas at the ends of lines.

### 4a. Choose how you'll get a certificate (required)

You must set **exactly one** of these two settings. If neither is set, the deployment stops
immediately with a configuration error.

**Option 1: `route53_domain` (recommended).** Use this if your domain's public
[hosted zone](glossary.md#route-53-and-hosted-zone) is in Route 53 in this same AWS account.

```json
"route53_domain": "example.com",
```

Use the bare domain, without `https://` and without a trailing dot. The deployment will:

- request a certificate for `*.example.com` from ACM and validate it automatically through DNS,
- create the record `openemr.example.com` pointing at your OpenEMR load balancer,
- renew the certificate automatically.

Your OpenEMR address will be `https://openemr.example.com`.

**Option 2: `certificate_arn`.** Use this if you already have an **issued** public certificate
in ACM, in the **same Region** you're deploying to, that covers the hostname you'll use (for
example `emr.example.org`).

```json
"certificate_arn": "arn:aws:acm:us-east-1:123456789012:certificate/12345678-1234-1234-1234-123456789012",
```

With this option, the project doesn't manage DNS for you. After deployment you'll create a DNS
record at your DNS provider that points your hostname at the load balancer (see
[Step 8](#step-8-find-your-url-and-admin-password)).

More detail on both options, plus optional email (SES) setup:
[HTTPS and DNS](../guides/https-and-dns.md).

### 4b. Decide who can connect (IP allow-list)

The load balancer only accepts HTTPS connections from the IP addresses you allow, set by
`security_group_ip_range_ipv4`. The default is:

```json
"security_group_ip_range_ipv4": "auto",
```

`"auto"` looks up the public IP address of the computer running the deployment (using
`https://checkip.amazonaws.com/`) and allows only that one address, written as a
[`/32` CIDR](glossary.md#cidr-and-32). This is a safe default for a first try.

Change it if:

- **Other people or offices need access:** set your office's range, for example
  `"203.0.113.0/24"`, or a single address such as `"203.0.113.131/32"`. Ask your network
  administrator for the right range.
- **Your home IP address changes:** with `"auto"`, re-running the deploy updates the rule to
  your new address.
- **You want it open to the whole internet:** `"0.0.0.0/0"`. Avoid this for anything holding
  real patient data; the login page would be reachable by anyone.

> [!NOTE]
> If you set this to `null`, no IPv4 addresses are allowed and nobody can reach the site.
> IPv6 access is controlled separately by `security_group_ip_range_ipv6`, which is `null`
> (off) by default.

### 4c. Leave everything else alone for now

The other settings (container size, autoscaling, monitoring alarms, APIs, patient portal, and
so on) have sensible defaults. Change them later; see [Configuration](../guides/configuration.md)
for the full list.

## Step 5: Bootstrap CDK (once per account and Region)

[Bootstrapping](glossary.md#bootstrap) creates a small helper stack named `CDKToolkit` in your
account, with a storage bucket and roles that CDK uses to upload files and container images.
You do this once per account and Region.

```bash
node_modules/.bin/cdk bootstrap
```

**Success looks like:** a message ending in `Environment aws://123456789012/us-east-1 bootstrapped.`
after a few minutes.

**If this fails:** credential errors mean Step 2 isn't working in this terminal (check
`AWS_PROFILE` and `aws sts get-caller-identity`). A "CONFIGURATION ERROR" box means `cdk.json`
has a problem; read the message, fix the setting from Step 4, and run the command again.

## Step 6: Run the pre-flight check

This script catches most setup problems before you spend 40 minutes deploying. It checks the
AWS CLI and your credentials, the pinned CDK tool, Python packages, that CDK is bootstrapped,
whether a stack named `OpenemrEcsStack` already exists, that the app synthesizes cleanly
(including your `cdk.json` settings and the cdk-nag security checks), and, if you set
`route53_domain`, that a matching hosted zone exists.

```bash
./scripts/validate-deployment-prerequisites.sh
```

**Success looks like:** `✓ All checks passed!` followed by `You're ready to deploy.`

**If this fails:** the script stops at the first error and tells you what to do. Common ones:

- *"CDK is not bootstrapped"*: go back to [Step 5](#step-5-bootstrap-cdk-once-per-account-and-region).
  Make sure the Region shown matches the one you bootstrapped.
- *"Stack exists in ROLLBACK state"*: a previous attempt failed. Delete it with
  `node_modules/.bin/cdk destroy` and try again.
- *"CDK synthesis failed"*: the output below the error explains why, usually a `cdk.json`
  value (a typo in a domain, a malformed ARN, or a missing certificate setting).
- *"hosted zone may not exist"*: the domain in `route53_domain` doesn't match a hosted zone in
  this account. Check **Route 53 → Hosted zones** in the console.

## Step 7: Deploy

Now create everything:

```bash
node_modules/.bin/cdk deploy
```

What happens:

1. CDK builds the stack and the helper container image (Docker must be running), then lists
   the security-related changes (IAM roles and network rules) it's about to make.
2. It asks `Do you wish to deploy these changes (y/n)?`. Type `y` and press Enter.
3. [CloudFormation](glossary.md#cloudformation) creates the resources. Progress lines scroll by
   for about 40 minutes. The database and the first OpenEMR startup take the longest.

**Success looks like:** a line with `✅  OpenemrEcsStack` followed by an `Outputs:` list:

![Terminal output after cdk deploy finishes, showing the stack outputs](../images/cdk_deploy.png)

> [!TIP]
> If your terminal closes or your laptop sleeps, the deployment keeps going in AWS. Open the
> [CloudFormation console](https://console.aws.amazon.com/cloudformation/), select
> `OpenemrEcsStack`, and watch the **Events** tab. When the status reaches `CREATE_COMPLETE`,
> continue with Step 8.

**If this fails:** CloudFormation automatically rolls back (undoes) a failed deployment. In the
CloudFormation console, open the stack's **Events** tab and find the *first* event with status
`CREATE_FAILED`; its reason is the real cause. Then see
[Troubleshooting](../reference/troubleshooting.md). Before retrying, delete the rolled-back
stack with `node_modules/.bin/cdk destroy`.

## Step 8: Find your URL and admin password

### Your OpenEMR address

The deploy output includes these [stack outputs](glossary.md#stack-and-stack-outputs):

| Output | What it is |
|---|---|
| `OpenemrEcsStack.ApplicationURL` | The address to open in your browser |
| `OpenemrEcsStack.LoadBalancerDNS` | The load balancer's own AWS hostname |
| `OpenemrEcsStack.OpenEMRPasswordSecretARN` | Where the admin password is stored |

You can print them again at any time:

```bash
aws cloudformation describe-stacks --stack-name OpenemrEcsStack \
  --query "Stacks[0].Outputs[?OutputKey=='ApplicationURL'].OutputValue" --output text
```

- With **`route53_domain`**, `ApplicationURL` is `https://openemr.<your domain>`, ready to use.
- With **`certificate_arn`**, `ApplicationURL` shows the load balancer hostname, which doesn't
  match your certificate, so browsers will warn. Instead, at your DNS provider create a
  **CNAME** record from your certificate's hostname (for example `emr.example.org`) to the
  `LoadBalancerDNS` value, wait for DNS to update, and browse to `https://emr.example.org`.

### Your admin password

The `admin` password was generated randomly and stored in
[Secrets Manager](glossary.md#secrets-manager). Retrieve it with:

```bash
SECRET_ARN=$(aws cloudformation describe-stacks --stack-name OpenemrEcsStack \
  --query "Stacks[0].Outputs[?OutputKey=='OpenEMRPasswordSecretARN'].OutputValue" --output text)
aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" \
  --query SecretString --output text
```

**Success looks like:** `{"username":"admin","password":"..."}`.

Prefer the console? Open [Secrets Manager](https://console.aws.amazon.com/secretsmanager/),
choose the secret whose name starts with `Password`, and click **Retrieve secret value**.

![The Secrets Manager console listing the stack's secrets](../images/SecretsManager.png)

## Step 9: Log in

1. Open your OpenEMR address in a browser.
2. Enter username `admin` and the password from Step 8.

![The OpenEMR login page](../images/OpenEMR_Auth.png)

**Success looks like:** the OpenEMR main screen.

![The OpenEMR main screen after logging in](../images/OpenEMR.png)

Congratulations, you're running OpenEMR on AWS.

**If this fails:**

- *The page never loads (times out):* your current IP isn't on the allow-list. Your IP may have
  changed, or you're on a different network or VPN. See [Step 4b](#4b-decide-who-can-connect-ip-allow-list),
  then run `node_modules/.bin/cdk deploy` again.
- *Browser certificate warning:* you're using a hostname the certificate doesn't cover (see
  the `certificate_arn` note in Step 8).
- *"502 Bad Gateway" or "503 Service Unavailable":* OpenEMR containers are still starting or
  restarting. Wait 5–10 minutes and refresh. If it persists, see
  [Troubleshooting](../reference/troubleshooting.md).
- *"Invalid username or password":* copy the password again without surrounding quotes or
  spaces.

## What you just deployed

Here's what each piece does, in plain terms. The [architecture overview](../reference/architecture.md)
goes deeper.

- **Application Load Balancer ([ALB](glossary.md#application-load-balancer-alb))**: the front
  door. It accepts HTTPS connections from allowed IP addresses and spreads them across the
  OpenEMR containers.
- **[AWS WAF](glossary.md#aws-waf)**: a firewall in front of the load balancer that blocks
  common attacks (such as SQL injection) and rate-limits any single IP address that sends too
  many requests.
- **[ECS on Fargate](glossary.md#ecs-cluster-service-and-task)**: runs OpenEMR in containers
  without servers for you to manage. Two copies run all the time; more start automatically
  under load (up to 100 by default) and stop when things quiet down.
- **[Aurora MySQL Serverless v2](glossary.md#aurora-serverless-v2-and-acu)**: the database
  holding patient records, appointments, and settings. It scales its capacity up and down
  automatically and never fully shuts off, so there's no wait on the first login of the day.
- **[Amazon EFS](glossary.md#amazon-efs)**: a shared network drive for documents, images, and
  OpenEMR's site configuration, used by every container.
- **[Valkey (ElastiCache Serverless)](glossary.md#valkey-and-elasticache)**: a fast in-memory
  store for login sessions, so users stay signed in no matter which container answers.
- **[KMS](glossary.md#kms) and [Secrets Manager](glossary.md#secrets-manager)**: encryption
  keys for data at rest, and safe storage for generated passwords.
- **[AWS Backup](glossary.md#aws-backup-and-recovery-point)**: automatic daily, weekly, and
  monthly backups of the database and file systems, with monthly backups kept for 7 years.
- **[VPC](glossary.md#vpc-subnet-and-nat-gateway)**: a private network across two Availability
  Zones. The database, cache, and containers sit in private subnets that the internet can't
  reach directly; NAT gateways let them make outbound connections (for example, to download
  updates).
- **CloudTrail and CloudWatch**: an audit trail of actions taken in your AWS account, plus
  container logs and metrics.

## Turning it off: clean up

There's no "pause" button: the database, NAT gateways, and containers bill by the hour whether
or not anyone is using OpenEMR. To stop paying, delete the stack.

> [!WARNING]
> Deleting the stack permanently deletes your OpenEMR files, **all AWS Backup recovery points
> made by this stack**, and the running database. Only a final database snapshot is kept (see
> below). If you've entered anything you want to keep, export it first; see
> [Backup and restore](../guides/backup-and-restore.md).

```bash
node_modules/.bin/cdk destroy
```

Type `y` to confirm. Deletion takes a while (often 20 minutes or more).

**Success looks like:** `✅  OpenemrEcsStack: destroyed`.

**What `cdk destroy` leaves behind,** which you can delete by hand if you don't need it:

| Left behind | Why | Where to delete it |
|---|---|---|
| A final Aurora **database snapshot** | The database is set to snapshot on deletion as a safety net. It incurs storage charges. | RDS console → **Snapshots** |
| Some **CloudWatch log groups** (OpenEMR container logs, VPC flow logs, the CloudTrail log group, and the ECS Exec log group if you enabled it) | Logs are kept for auditing. Small storage charges. | CloudWatch console → **Log groups** |
| The **`CDKToolkit`** bootstrap stack | Shared by any future CDK deployment in this account and Region. | Keep it, or delete it in the CloudFormation console (empty its S3 bucket first) |
| Your **Route 53 hosted zone and domain**, or your own ACM certificate | You created these, not the stack. | Route 53 / ACM console |

**If this fails:**

- *The backup vault won't delete:* the stack tries to empty the vault automatically, but any
  recovery point it couldn't remove (for example, one still being created) keeps the vault
  from being deleted. In the AWS Backup console,
  open the vault named `OpenemrEcsStack-vault-...`, delete the remaining recovery points, and
  run `node_modules/.bin/cdk destroy` again.
- *Termination protection or database deletion protection:* if you turned on
  `enable_stack_termination_protection` or `rds_deletion_protection`, see
  [Configuration](../guides/configuration.md) for how to switch them off before destroying.
- Anything else: [Troubleshooting](../reference/troubleshooting.md).

## Common questions

**Can I turn it off overnight to save money?** Not without deleting it. The database keeps a
minimum capacity so it's always ready, and the NAT gateways and two containers run around the
clock. For occasional use, deploy, test, and destroy. See [Costs](../reference/costs.md).

**How do I change a setting later?** Edit `cdk.json` and run `node_modules/.bin/cdk deploy`
again. CDK changes only what's different.

**How do I update to a newer version?** Get the newer version of this repository (for example,
`git pull`), read the [release notes](https://github.com/openemr/openemr-on-ecs/releases),
re-run `pip install -r requirements.txt` and `npm ci`, then run `node_modules/.bin/cdk deploy`.
Take an on-demand backup first; see [Backup and restore](../guides/backup-and-restore.md).

**Can other people use it?** Yes. Share the address and create OpenEMR user accounts for them.
Make sure their IP addresses are allowed (see [Step 4b](#4b-decide-who-can-connect-ip-allow-list)).

**I lost the admin password.** Retrieve it again from Secrets Manager (Step 8).

## Next steps

- **Customize it:** [Configuration](../guides/configuration.md) and
  [Optional features](../guides/optional-features.md) (patient portal, monitoring alarms, APIs).
- **Protect your data:** [Backup and restore](../guides/backup-and-restore.md), and test a
  restore before you rely on it.
- **Move in existing data:** [Importing an existing OpenEMR](../guides/importing-openemr.md).
- **Connect other systems:** [REST and FHIR APIs](../guides/apis.md).
- **Understand the design:** [Architecture](../reference/architecture.md) and
  [Security and compliance](../reference/security-and-compliance.md).
- **Browse all docs:** [Documentation home](../README.md).

## Quick troubleshooting

| Problem | What to do |
|---|---|
| `python3: command not found` or wrong version | Install Python 3.14 ([Step 1](#python-314)); try `python3.14` |
| `aws: command not found` | Install AWS CLI v2 ([Step 1](#aws-cli-version-2)) and open a new terminal |
| `Unable to locate credentials` / token expired | `export AWS_PROFILE=openemr`, then `aws sso login` ([Step 2](#step-2-set-up-your-aws-account-and-credentials)) |
| `node_modules/.bin/cdk: No such file or directory` | Run `npm ci` in the project folder ([Step 3](#step-3-get-the-code-and-install-dependencies)) |
| `No module named aws_cdk` | Activate the virtual environment and `pip install -r requirements.txt` |
| "CONFIGURATION ERROR … route53_domain or certificate_arn" | Set one of them in `cdk.json` ([Step 4a](#4a-choose-how-youll-get-a-certificate-required)) |
| "Failed to resolve current IP address for 'auto' mode" | Check your internet connection, or set an explicit CIDR ([Step 4b](#4b-decide-who-can-connect-ip-allow-list)) |
| "CDK is not bootstrapped" | Run `node_modules/.bin/cdk bootstrap` in the same Region ([Step 5](#step-5-bootstrap-cdk-once-per-account-and-region)) |
| Docker errors during deploy (`Cannot connect to the Docker daemon`) | Start Docker Desktop or the Docker service ([Step 1](#docker)) |
| Site times out in the browser | Your IP isn't allowed; update `security_group_ip_range_ipv4` and redeploy |
| 502/503 errors after deploying | Wait 5–10 minutes for containers to finish starting |
| Forgot admin password | Retrieve it from Secrets Manager ([Step 8](#your-admin-password)) |

Still stuck? See [Troubleshooting](../reference/troubleshooting.md), search
[GitHub issues](https://github.com/openemr/openemr-on-ecs/issues), or ask the
[OpenEMR community forum](https://community.open-emr.org/).
