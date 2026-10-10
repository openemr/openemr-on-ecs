# Glossary

Plain-language definitions of the AWS, infrastructure, and healthcare terms used throughout
this documentation. It's written for readers who are new to AWS; each entry says what the thing
is and how this project uses it. Terms are grouped by topic and alphabetical within each group.

**Jump to:** [General and tools](#general-and-tools) ·
[AWS basics](#aws-basics) ·
[Deploying with CDK](#deploying-with-cdk) ·
[Networking](#networking) ·
[Compute](#compute) ·
[Data and storage](#data-and-storage) ·
[Security](#security) ·
[Healthcare and compliance](#healthcare-and-compliance) ·
[Project-specific terms](#project-specific-terms)

## General and tools

### CLI

A *command-line interface*: a program you control by typing commands in a terminal instead of
clicking buttons. This project uses the AWS CLI (`aws ...`) and the CDK CLI
(`node_modules/.bin/cdk ...`).

### Container and Docker

A *container* packages an application with everything it needs to run, so it behaves the same
everywhere. *Docker* is the tool that builds and runs containers. OpenEMR runs as a container;
this project also builds a small helper container image on your computer during deployment,
which is why Docker must be installed.

### Git

Version-control software. You use `git clone` to download this project and `git pull` to get
updates.

### JSON

A simple text format for structured data, used by `cdk.json`. Text values go in double quotes,
`null` means "not set", and items are separated by commas.

### Terminal

The text window where you type commands: **Terminal** on macOS, a shell such as Bash on Linux,
or the **Ubuntu** app under WSL on Windows.

### Virtual environment

A private folder (`.venv`) holding the Python packages for one project, so they don't clash
with other projects on your computer. You *activate* it with `source .venv/bin/activate` in
each new terminal; your prompt then starts with `(.venv)`.

### WSL

*Windows Subsystem for Linux*. It lets you run a real Linux environment (such as Ubuntu) inside
Windows. Recommended for Windows users of this project because the helper scripts are written
for Bash.

## AWS basics

### ARN

*Amazon Resource Name*: a unique ID for an AWS resource, for example
`arn:aws:acm:us-east-1:123456789012:certificate/...`. You may paste a certificate ARN into
`cdk.json`.

### AWS account and Region

Your *AWS account* is the container for everything you create in AWS, and it's what gets billed.
It's identified by a 12-digit number. A *Region* is a geographic location with its own copy of
AWS services, such as `us-east-1` (N. Virginia) or `eu-west-1` (Ireland). This project deploys
into one Region; your data stays there.

### Availability Zone

One of several separate data centers (or groups of them) inside a Region. This project spreads
its network, database, and containers across two Availability Zones so a single data-center
problem doesn't take OpenEMR down.

### AWS Console

The AWS website where you can see and manage your resources with a browser:
[console.aws.amazon.com](https://console.aws.amazon.com/).

### IAM and IAM Identity Center

*IAM* (Identity and Access Management) controls who can do what in your AWS account, using
users, roles, and permission policies. *IAM Identity Center* (formerly AWS SSO) is the
recommended way for people to sign in: you log in through a portal and get temporary
credentials, set up on your computer with `aws configure sso`. Avoid using the **root user**
(the sign-up email) for day-to-day work, and never create access keys for it.

### Service-linked role

An IAM role that an AWS service creates and manages for itself, such as the one ECS uses to
manage networking for your containers. New accounts sometimes need these created before the
first deployment.

## Deploying with CDK

### Bootstrap

A one-time setup per account and Region that creates a helper stack called `CDKToolkit`: a
storage bucket, a container registry, and roles that CDK uses to upload files and container
images during deployments. Run with `node_modules/.bin/cdk bootstrap`.

### CDK

The *AWS Cloud Development Kit*: a framework for describing cloud infrastructure in a
programming language (Python, in this project) instead of clicking through the console. CDK
turns that code into a CloudFormation template and deploys it. This project pins a specific CDK
version, installed with `npm ci` and run as `node_modules/.bin/cdk`.

### cdk-nag

A tool that checks CDK infrastructure against security rule packs. This project runs the *AWS
Solutions* and *HIPAA Security* packs on every synth; any finding that isn't fixed or explicitly
acknowledged with a reason stops the deployment. See
[cdk-nag suppressions](../reference/cdk-nag-suppressions.md).

### cdk.json and context

`cdk.json` is the project's settings file. Its `context` section holds your deployment
settings, such as `route53_domain`, `security_group_ip_range_ipv4`, and container sizes. You can
also override a setting for one command with `-c name=value`. Every setting is documented in
[Configuration](../guides/configuration.md).

### CloudFormation

The AWS service that creates, updates, and deletes groups of resources from a template. CDK
generates the template; CloudFormation does the actual work and shows progress in its console
(**Events** tab). If a deployment fails, CloudFormation *rolls back*, undoing the partial
changes.

### Stack and stack outputs

A *stack* is a group of AWS resources managed together by CloudFormation. Everything this project
creates belongs to one stack named **`OpenemrEcsStack`**, so you can deploy, update, or delete
it as a unit. *Stack outputs* are useful values the stack reports when it finishes, such as
`ApplicationURL`, `LoadBalancerDNS`, and `OpenEMRPasswordSecretARN`.

### Synthesize (synth)

The step where CDK runs the Python code and produces a CloudFormation template, without
changing anything in AWS. Configuration checks and cdk-nag run at this point, so many mistakes
are caught before deployment. `cdk deploy` synthesizes automatically.

## Networking

### Application Load Balancer (ALB)

The front door for web traffic. It accepts HTTPS connections, decrypts them using your
certificate, and spreads requests across the healthy OpenEMR containers. Its AWS-assigned
hostname is the `LoadBalancerDNS` stack output.

### CIDR and /32

*CIDR notation* describes a range of IP addresses: an address followed by a slash and a number.
The number says how many leading bits are fixed, so a bigger number means a smaller range.
`203.0.113.131/32` means exactly one address; `203.0.113.0/24` means the 256 addresses
`203.0.113.0` to `203.0.113.255`; `0.0.0.0/0` means every IPv4 address on the internet. This
project uses CIDR in `security_group_ip_range_ipv4` to decide who can connect.

### DNS

The internet's phone book: it turns names like `openemr.example.com` into IP addresses. A
*record* is one entry, such as an *A record* or a *CNAME* (an alias pointing one name at another).

### Global Accelerator

An optional AWS service that routes users onto AWS's private global network close to where they
are, which can speed things up for distant users. Off by default; see
[Optional features](../guides/optional-features.md).

### Route 53 and hosted zone

*Route 53* is AWS's DNS service, and you can also register domain names with it. A *public
hosted zone* holds the DNS records for one domain. If you set `route53_domain`, the hosted zone
must be in the same AWS account; the project then adds the records it needs (certificate
validation and `openemr.<your domain>`).

### Security group

A virtual firewall attached to a resource that lists which traffic is allowed in and out.
Here, the load balancer's security group allows HTTPS (port 443) only from your allowed IP
range, and the database, cache, and file systems accept connections only from the project's
own containers.

### VPC, subnet, and NAT gateway

A *VPC* (Virtual Private Cloud) is your own isolated network inside AWS. It's divided into
*subnets*: **public** subnets can be reached from the internet (only the load balancer lives
there), while **private** subnets can't (the containers, database, and cache live there). A
*NAT gateway* lets resources in private subnets make outbound connections, for example to
download software, without being reachable from outside. This project creates two, one per
Availability Zone; they're a noticeable part of the monthly cost.

## Compute

### ECS: cluster, service, and task

*Amazon ECS* (Elastic Container Service) runs containers. A *task* is one running copy of the
application (one OpenEMR container). A *service* keeps the right number of tasks running,
replaces unhealthy ones, and adds or removes tasks as load changes. A *cluster* is the logical
group they belong to. The `ECSClusterName` and `ECSServiceName` stack outputs name them.

### ECS Exec

A feature that lets an authorized person open a command prompt inside a running container,
without SSH or open ports. It's off by default (`enable_ecs_exec`) and is what makes secure
[database access](../guides/database-access.md) possible.

### Fargate

The "serverless" way to run ECS tasks: you say how much CPU and memory each task needs and AWS
supplies the machines, so there are no servers to patch or size. By default each OpenEMR task
gets 2 vCPU and 4 GB of memory, with at least 2 tasks and up to 100.

### Graviton (ARM64)

AWS's own ARM-based processors. This project's containers run on 64-bit ARM, which is generally
cheaper for the same performance. It's also why Docker on an Intel/AMD Linux machine needs ARM
emulation turned on to build the helper image.

### Lambda

AWS's service for running small pieces of code on demand without a server. This project uses a
few Lambda functions behind the scenes, for example to generate TLS materials and to clean up
during stack deletion.

## Data and storage

### Amazon EFS

*Elastic File System*: a shared network drive that many containers can use at once. OpenEMR
keeps its `sites` folder there (uploaded documents, images, site configuration), plus a second
small file system for TLS certificates shared by the containers.

### Aurora Serverless v2 and ACU

*Amazon Aurora* is AWS's managed, MySQL-compatible database. *Serverless v2* means its capacity
adjusts automatically. Capacity is measured in *ACUs* (Aurora Capacity Units; one ACU is about
2 GB of memory plus matching CPU). This project sets a minimum of 0.5 ACU, so the database never
fully shuts off and the first login of the day is instant, and a maximum of 256 ACU.

### AWS Backup and recovery point

*AWS Backup* takes scheduled backups of the database and both file systems and stores them in a
*backup vault*. Each saved backup is a *recovery point* you can restore from. This project takes
daily, weekly, and monthly backups and keeps monthly ones for 7 years. Deleting the stack
deletes its recovery points. See [Backup and restore](../guides/backup-and-restore.md).

### RDS Data API

An optional way to run SQL against the database over HTTPS calls instead of a regular database
connection. Off by default (`enable_data_api`).

### S3

*Simple Storage Service*: AWS's object storage ("buckets" of files). This project uses
encrypted S3 buckets for load balancer access logs, CloudTrail audit logs, and some optional
features.

### Snapshot

A point-in-time copy of a database or disk. When you delete this stack, Aurora keeps one final
snapshot as a safety net, which you can delete yourself.

### Valkey and ElastiCache

*Valkey* is an open-source, Redis-compatible in-memory data store. *Amazon ElastiCache
Serverless* runs it for you. OpenEMR uses it to store login sessions, so a user stays signed in
whichever container answers their request.

## Security

### ACM certificate

A TLS certificate from *AWS Certificate Manager*. A certificate proves to browsers that your
site really is, say, `openemr.example.com`, and enables encrypted HTTPS. ACM certificates are
free and can renew automatically. This project needs one, either created for you
(`route53_domain`) or supplied by you (`certificate_arn`), and it must be in the same Region as
the deployment.

### AWS WAF

A *web application firewall* in front of the load balancer. It inspects requests and blocks
common attacks (such as SQL injection and known-bad inputs), suspicious user agents, and any
single IP address sending more than 2,000 requests in five minutes.

### CloudTrail and CloudWatch

*CloudTrail* records who did what in your AWS account (an audit log). *CloudWatch* collects logs
and metrics from your resources and can send alarms. This project keeps CloudTrail logs for
years by default and sends container logs to CloudWatch.

### HTTPS and TLS

*HTTPS* is web traffic encrypted with *TLS*, so nobody between the browser and the server can
read or change it. This project only ever serves OpenEMR over HTTPS, and traffic stays encrypted
from the load balancer to the containers too.

### KMS

*AWS Key Management Service* creates and guards encryption keys. This project uses KMS keys to
encrypt the database, file systems, logs, secrets, and backups ("encryption at rest").

### Secrets Manager

An AWS service for storing passwords and other secrets securely, encrypted with KMS and
access-controlled with IAM. The OpenEMR `admin` password and the database credentials are
generated at deployment and stored here; the `OpenEMRPasswordSecretARN` output points to the
admin password.

## Healthcare and compliance

### EHR / EMR

*Electronic health record* / *electronic medical record*: software that stores patients' medical
information. OpenEMR is an open-source EHR and practice-management system.

### FHIR and REST APIs

Standard ways for other software to read and write OpenEMR data over HTTPS. *FHIR* is the
healthcare-specific standard. Off by default; see [REST and FHIR APIs](../guides/apis.md).

### HIPAA and BAA

*HIPAA* is the US law that governs protecting patients' health information (PHI). A *BAA*
(Business Associate Addendum) is the contract with AWS that you must accept, separately for each
AWS account, before storing PHI there. You can accept it in **AWS Artifact** in the console.
This project uses HIPAA-eligible services and security checks, but deploying it doesn't make you
compliant on its own; see [Security and compliance](../reference/security-and-compliance.md).

### PHI

*Protected health information*: health data that can identify a person. Don't put real PHI in a
test deployment.

## Project-specific terms

### Backup Manager TUI

A *text user interface* (an interactive app that runs in the terminal) for browsing and
restoring this project's AWS Backup recovery points. See
[Backup and restore](../guides/backup-and-restore.md).

### Credential rotation

Replacing passwords with new ones on a schedule. This project includes a task that rotates the
database credentials without downtime; see
[Credential rotation](../guides/credential-rotation.md).

### Guarded import

This project's workflow for migrating an existing OpenEMR installation into a fresh, empty
deployment, with checks that prevent overwriting real data. See
[Importing an existing OpenEMR](../guides/importing-openemr.md).

### MCP

The *Model Context Protocol*, a standard way for AI assistants to call tools and read
information. This repository includes an optional, read-only MCP server that describes the
project to MCP-capable assistants; it can't change files or contact AWS. See
[Knowledge MCP server](../maintainers/knowledge-mcp.md).

### Pre-flight check

The script `scripts/validate-deployment-prerequisites.sh`, which checks your tools, credentials,
bootstrap status, and settings before you deploy.
