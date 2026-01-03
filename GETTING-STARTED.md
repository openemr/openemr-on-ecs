# Getting Started Guide for Beginners

This guide will help you deploy OpenEMR on AWS, even if you're new to Python, AWS, or development tools.

## Table of Contents

- [What You'll Learn](#what-youll-learn)
- [What We're Building](#what-were-building)
- [Step 1: Install Required Tools](#step-1-install-required-tools)
  - [Tool 1: Python](#tool-1-python-the-programming-language)
  - [Tool 2: Node.js](#tool-2-nodejs-for-cdk)
  - [Tool 3: AWS CLI](#tool-3-aws-cli-command-line-interface)
  - [Tool 4: AWS CDK](#tool-4-aws-cdk-cloud-development-kit)
- [Step 2: Set Up AWS Account and Credentials](#step-2-set-up-aws-account-and-credentials)
- [Step 3: Prepare Your Computer](#step-3-prepare-your-computer)
- [Step 4: Configure Your Deployment](#step-4-configure-your-deployment)
- [Step 5: Validate Everything is Ready](#step-5-validate-everything-is-ready)
- [Step 6: Bootstrap CDK (First Time Only)](#step-6-bootstrap-cdk-first-time-only)
- [Step 7: Deploy OpenEMR](#step-7-deploy-openemr)
- [Step 8: Get Your Application URL and Login](#step-8-get-your-application-url-and-login)
- [Step 9: Understanding What You Deployed](#step-9-understanding-what-you-deployed)
- [Common Questions](#common-questions)
- [Clean Up (Delete Everything)](#clean-up-delete-everything)
- [Next Steps](#next-steps)
- [Getting Help](#getting-help)
- [Troubleshooting Quick Reference](#troubleshooting-quick-reference)

## What You'll Learn

By the end of this guide, you'll understand:
- What each tool does (in plain language)
- How to set everything up step-by-step
- How to deploy OpenEMR to AWS
- How to access your application
- Common issues and how to fix them

## What We're Building

You'll be creating an **OpenEMR system** (Electronic Health Records software) that runs on AWS. Think of it like renting a fully-equipped office in the cloud - AWS manages all the servers, and you just use the application.

**Time required:** About 1 hour (including 40 minutes of waiting for AWS to set everything up)

## Step 1: Install Required Tools

You'll need four tools installed on your computer:

### Tool 1: Python (The Programming Language)

**What it is:** Python is a programming language. We need it to run the deployment scripts.

**How to install:**
1. Go to https://www.python.org/downloads/
2. Download Python 3.14 (the latest version)
3. Run the installer
4. **Important:** Check the box that says "Add Python to PATH"
5. Complete the installation

**Verify it worked:**
Open a terminal/command prompt and type:
```bash
python --version
```
You should see something like `Python 3.14.0` or higher.

**Troubleshooting:**
- If you get "command not found", you may need to restart your terminal
- On Mac/Linux, try `python3` instead of `python`

---

### Tool 2: Node.js (For CDK)

**What it is:** Node.js runs JavaScript outside a browser. AWS CDK (the deployment tool) needs it.

**How to install:**
1. Go to https://nodejs.org/
2. Download the "LTS" version (recommended)
3. Run the installer with default settings
4. Complete the installation

**Verify it worked:**
Open a terminal and type:
```bash
node --version
```
You should see something like `v18.17.0` or higher.

**Also verify npm (comes with Node.js):**
```bash
npm --version
```
You should see something like `9.6.0` or higher.

---

### Tool 3: AWS CLI (Command Line Interface)

**What it is:** This lets you control AWS from your computer's terminal.

**How to install:**

**Windows:**
1. Download the installer: https://awscli.amazonaws.com/AWSCLIV2.msi
2. Run the installer
3. Complete the installation

**Mac:**
```bash
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /
```

**Linux:**
```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
```

**Verify it worked:**
```bash
aws --version
```
You should see something like `aws-cli/2.13.0`.

---

### Tool 4: AWS CDK (Cloud Development Kit)

**What it is:** This is AWS's tool for building and deploying cloud applications. It's like a blueprint that tells AWS how to set everything up.

**How to install:**
Open a terminal and type:
```bash
npm install -g aws-cdk
```

**Verify it worked:**
```bash
cdk --version
```
You should see something like `2.100.0` or higher.

**Troubleshooting:**
- If you get "command not found", Node.js may not be installed correctly
- On some systems, you may need to use `sudo` before the command (Mac/Linux)

---

## Step 2: Set Up AWS Account and Credentials

### Create an AWS Account

1. Go to https://aws.amazon.com/
2. Click "Create an AWS Account"
3. Follow the signup process
4. You'll need a credit card, but AWS won't charge you until you use resources (and you can set up billing alerts)

### Get Your AWS Credentials

1. Log into AWS Console (https://console.aws.amazon.com/)
2. Click your name in the top right
3. Click "Security credentials"
4. Scroll down to "Access keys"
5. Click "Create access key"
6. Select "Command Line Interface (CLI)"
7. Check the confirmation box and click "Next"
8. Add a description (like "OpenEMR Deployment")
9. Click "Create access key"
10. **Important:** Copy both the Access Key ID and Secret Access Key - you won't see the secret again!

### Configure AWS CLI

Open a terminal and type:
```bash
aws configure
```

You'll be asked for four things:
1. **AWS Access Key ID:** Paste your Access Key ID from above
2. **AWS Secret Access Key:** Paste your Secret Access Key
3. **Default region name:** Enter `us-east-1` (or your preferred region like `us-west-2`, `eu-west-1`)
4. **Default output format:** Just press Enter (uses JSON by default)

**Verify it worked:**
```bash
aws sts get-caller-identity
```
You should see your AWS account ID and user information.

---

## Step 3: Prepare Your Computer

### Open Terminal/Command Prompt

- **Windows:** Press `Win + R`, type `cmd`, press Enter
- **Mac:** Press `Cmd + Space`, type `Terminal`, press Enter
- **Linux:** Press `Ctrl + Alt + T`

### Navigate to the Project Folder

1. Download or clone this repository to your computer
2. In your terminal, navigate to the folder:
   ```bash
   cd /path/to/openemr-on-ecs
   ```
   
   **Example:**
   ```bash
   cd ~/Documents/openemr-on-ecs
   ```

### Create a Python Virtual Environment

**What is this?** A virtual environment is like a separate workspace for this project. It keeps the tools this project needs separate from other projects.

**How to create it:**
```bash
python -m venv .venv
```

**Or on Mac/Linux if that doesn't work:**
```bash
python3 -m venv .venv
```

This creates a hidden folder called `.venv` with a clean Python environment.

### Activate the Virtual Environment

**Windows:**
```bash
.venv\Scripts\activate
```

**Mac/Linux:**
```bash
source .venv/bin/activate
```

**How do you know it worked?** You should see `(.venv)` at the beginning of your terminal prompt, like this:
```
(.venv) username@computer:~/openemr-on-ecs$
```

**Important:** Every time you open a new terminal to work on this project, you'll need to activate the virtual environment again.

### Install Required Packages

```bash
pip install -r requirements.txt
```

This downloads all the Python tools this project needs. It may take a few minutes.

**Troubleshooting:**
- If you get "pip not found", make sure your virtual environment is activated
- Try `python -m pip install -r requirements.txt` instead

---

## Step 4: Configure Your Deployment

### Open the Configuration File

Open `cdk.json` in any text editor (Notepad, TextEdit, VS Code, etc.).

### Set Your IP Address (Important for Security)

Find this line:
```json
"security_group_ip_range_ipv4": "0.0.0.0/0",
```

**What does this mean?** This controls who can access your OpenEMR application. `0.0.0.0/0` means "everyone on the internet" - which is **not secure** for production but okay for testing.

**For testing (quick setup):** Leave it as `0.0.0.0/0`

**For better security (recommended):** Change it to your IP address:
1. Find your public IP: Go to https://whatismyipaddress.com/
2. Copy your IPv4 address (looks like `203.0.113.131`)
3. Change the line to:
   ```json
   "security_group_ip_range_ipv4": "203.0.113.131/32",
   ```
   (Replace `203.0.113.131` with your actual IP address)

**Note:** If your IP changes (common with home internet), you'll need to update this or redeploy.

### Configure Certificate for HTTPS (Required)

**Important:** A certificate is required for deployment. You must provide either:

1. **`route53_domain`** (recommended for automated setup): Set this to a domain you own in Route53. The architecture will automatically issue, validate, and manage the SSL certificate. For example:
   ```json
   "route53_domain": "example.com"
   ```
   The application will be accessible at `https://openemr.example.com`.

2. **`certificate_arn`**: If you already have a certificate in AWS Certificate Manager, provide its ARN:
   ```json
   "certificate_arn": "arn:aws:acm:us-east-1:123456789012:certificate/12345678-1234-1234-1234-123456789012"
   ```

**Note:** HTTPS is required for all deployments. HTTP is never exposed. For more information, see the [HTTPS Setup Guide in DETAILS.md](DETAILS.md#enabling-https-for-client-to-load-balancer-communication).

### Other Configuration Options (Optional)

You can leave everything else as default for now. The other options are explained in [DETAILS.md](DETAILS.md) if you want to customize later.

---

## Step 5: Validate Everything is Ready

Before deploying, let's check that everything is set up correctly:

```bash
./scripts/validate-deployment-prerequisites.sh
```

**On Windows:** You may need to use Git Bash or WSL to run this script.

**💡 Tip:** You can run this script from any directory - it automatically finds the project root by looking for `cdk.json`.

This script checks:
- ✅ Your AWS credentials work
- ✅ CDK is installed
- ✅ Python packages are installed
- ✅ CDK is bootstrapped in your region
- ✅ Your configuration is valid
- ✅ CDK stack synthesis works
- ✅ Route53 hosted zone exists (if configured)

If you see any errors, fix them before proceeding.

**What if it says "cdk.json not found"?**
- Make sure you've downloaded/cloned the repository
- The script searches up from your current directory to find the project root
- Try running it from a subdirectory closer to the project root

---

## Step 6: Bootstrap CDK (First Time Only)

**What is this?** CDK needs some tools stored in your AWS account before it can deploy anything. This is a one-time setup.

```bash
cdk bootstrap
```

You'll see output showing it's creating resources in AWS. This takes about 2-3 minutes.

**If you get an error:** Make sure your AWS credentials are configured correctly (see Step 2).

---

## Step 7: Deploy OpenEMR

Now the exciting part - deploying your application!

```bash
cdk deploy
```

**What happens:**
1. CDK will show you what it's going to create
2. It will ask: "Do you wish to deploy these changes (y/n)?"
3. Type `y` and press Enter
4. AWS will start creating resources (this takes about 40 minutes)

**During deployment, AWS creates:**
- A virtual network (VPC)
- A database (Aurora MySQL)
- A cache (ElastiCache)
- File storage (EFS)
- Container hosting (ECS)
- Load balancer (ALB)
- Security settings (WAF, certificates)

**Important:** Don't close your terminal during deployment. You can watch the progress in the terminal or check the AWS CloudFormation console.

**What if something goes wrong?**
- The deployment will automatically roll back
- Check the error message in the terminal
- See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues

---

## Step 8: Get Your Application URL and Login

### Find Your URL

After deployment completes, you'll see output like this:

```
Outputs:
OpenemrEcsStack.LoadBalancerDNS = openemr-alb-123456789.us-east-1.elb.amazonaws.com
OpenemrEcsStack.ApplicationURL = https://openemr-alb-123456789.us-east-1.elb.amazonaws.com
```

Copy the `ApplicationURL` - that's your OpenEMR address!

### Get Your Admin Password

1. Open AWS Console: https://console.aws.amazon.com/
2. Go to "Secrets Manager"
3. Find a secret named something like `OpenemrEcsStack-Password...`
4. Click on it
5. Click "Retrieve secret value"
6. You'll see JSON with username and password:
   ```json
   {
     "username": "admin",
     "password": "your-password-here"
   }
   ```

### Log In to OpenEMR

1. Open your ApplicationURL in a web browser
2. Username: `admin`
3. Password: (the password from Secrets Manager above)

**Congratulations!** You now have OpenEMR running in the cloud! 🎉

---

## Step 9: Understanding What You Deployed

Here's what each part does:

### Application Load Balancer (ALB)
- Routes internet traffic to your OpenEMR containers
- Handles SSL/TLS encryption
- Like a receptionist directing visitors

### ECS Fargate
- Runs your OpenEMR application in containers
- Automatically scales up/down based on usage
- Like workers that come and go as needed

### Aurora MySQL Database
- Stores all your patient data, appointments, etc.
- Automatically backs up every day
- Like a secure filing cabinet

### ElastiCache (Valkey/Redis)
- Speeds up your application by caching frequently used data
- Like having quick-access notes on your desk

### Amazon EFS
- Stores shared files (patient documents, images, etc.)
- Accessible from all containers
- Like a shared network drive

### AWS WAF
- Protects your application from attacks
- Like a security guard checking visitors

---

## Common Questions

### "How much will this cost?"

See the [Costs section in README.md](README.md#costs). Rough estimate: ~$320/month for base deployment, but varies with usage.

**Key cost note:** The database is configured to always run at minimum 0.5 ACU (~$44/month base) to ensure instant connections. This prevents 3-5 minute cold start delays but adds ~$34/month compared to scaling to zero.

### "Can I turn it off when not using it?"

The database and other resources continue running, so you'll pay even when not using it. To stop costs, you'll need to destroy the deployment (see Clean Up below).

**Note:** The database is configured to always maintain a minimum capacity (0.5 ACU) to ensure instant connectivity. This costs ~$43/month base charge but prevents 3-5 minute connection delays.

### "How do I update OpenEMR?"

Just run `cdk deploy` again - it will update to the latest configuration.

### "What if I forget my password?"

Get it from AWS Secrets Manager (see Step 8 above).

### "Can multiple people use it?"

Yes! The application can handle multiple users. Just share the URL (and make sure security_group_ip_range_ipv4 allows their IP addresses if you restricted it).

---

## Clean Up (Delete Everything)

**⚠️ Warning:** This deletes ALL resources and data. Make sure you have backups!

```bash
cdk destroy
```

Type `y` when asked to confirm.

**Note:** Some resources (like backups) may need manual cleanup. See [README.md](README.md#clean-up) for details.

---

## Next Steps

- **Customize your deployment:** See [DETAILS.md](DETAILS.md) for advanced options
- **Test locally first:** See [README-TESTING.md](README-TESTING.md)
- **Troubleshoot issues:** See [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- **Understand the architecture:** See [ARCHITECTURE.md](ARCHITECTURE.md)
- **Explore helper scripts:** See [scripts/README.md](scripts/README.md) for all available scripts including validation and stress testing tools

---

## Getting Help

- **Documentation:** Check [README.md](README.md) and other `.md` files in this repository
- **GitHub Issues:** https://github.com/openemr/host-openemr-on-aws-fargate
- **OpenEMR Community:** https://community.open-emr.org/

---

## Troubleshooting Quick Reference

| Problem | Solution |
|---------|----------|
| "python: command not found" | Install Python and check "Add to PATH" |
| "aws: command not found" | Install AWS CLI |
| "cdk: command not found" | Run `npm install -g aws-cdk` |
| "Access Denied" in AWS | Check your AWS credentials with `aws configure` |
| Deployment fails | Run `./scripts/validate-deployment-prerequisites.sh` |
| Can't access OpenEMR URL | Check security_group_ip_range_ipv4 in cdk.json includes your IP |
| Forgot admin password | Get it from AWS Secrets Manager |

---

**Remember:** Take your time, read error messages carefully, and don't hesitate to ask for help in the GitHub issues or OpenEMR community!

