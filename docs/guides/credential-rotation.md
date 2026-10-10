# Credential rotation

This guide shows you how to change (rotate) the database passwords OpenEMR uses, without any
downtime, and how to run rotation on a schedule. It's for administrators who need to rotate
credentials regularly for security or compliance.

Rotation covers the Aurora MySQL database credentials only: the two OpenEMR application database
users and the database administrator (`dbadmin`). It doesn't rotate the Valkey cache, the OpenEMR
`admin` web login, or SES SMTP credentials (see
[HTTPS and DNS](https-and-dns.md#rotate-the-smtp-credentials) for those).

## Before you begin

- A deployed stack. The rotation task is always deployed; there's nothing to turn on.
- The AWS CLI configured for the stack's account and Region, with permission to run ECS tasks and
  read CloudFormation outputs and CloudWatch logs.
- `python3` on your computer (the helper script uses it to build the task's command).
- **Cost:** a short-lived Fargate task (0.5 vCPU, 1 GB) per run, and a rolling replacement of the
  OpenEMR tasks.
- **Time:** a rotation takes about 15–25 minutes, mostly waiting for the rolling deployment. OpenEMR
  stays available the whole time.

## How it works

OpenEMR reads its database login from `sqlconf.php` on the shared EFS `sites` volume
(`/var/www/localhost/htdocs/openemr/sites/default/sqlconf.php` inside the container). The stack
keeps **two** application database users, `openemr_a` and `openemr_b`, and one is active at a
time. Rotating means switching OpenEMR to the standby user, then changing the old user's password.
At no point is OpenEMR using a password that's about to change.

Both users are stored in one Secrets Manager secret with two "slots" (its ARN is the
`RdsSlotSecretARN` stack output):

```json
{
  "active_slot": "A",
  "A": {"username": "openemr_a", "password": "...", "host": "...", "port": "3306", "dbname": "openemr"},
  "B": {"username": "openemr_b", "password": "...", "host": "...", "port": "3306", "dbname": "openemr"}
}
```

The database administrator's credentials are in a separate secret (the `DatabaseSecretARN` output).

Each rotation run:

1. reads the active slot (`A` or `B`) and picks the other one as standby;
2. checks the administrator credentials, and repairs drift if a previous run changed the database
   password but failed to save the secret;
3. makes sure both `openemr_a` and `openemr_b` in the database match the passwords in the slot
   secret, so switching to a drifted slot can't cause "Access denied";
4. updates `sqlconf.php` to use the standby user;
5. forces a rolling ECS deployment so every OpenEMR task picks up the change;
6. checks that the database and the application are healthy;
7. changes the old slot's password;
8. checks the old slot's new password on its own;
9. saves the new `active_slot` in the secret; and
10. generates a new `dbadmin` password, applies it in the database (`ALTER USER`), checks it, and
    saves it to the administrator secret.

If something fails:

- **If the switch fails its health check**, the task puts the previous `sqlconf.php` back, forces
  another rolling deployment, checks that OpenEMR recovered, and exits with an error.
- **If changing the old slot's password fails**, OpenEMR stays on the current (working) slot and the
  task exits with an error.

It's safe to run again after any failure:

- **First run, or neither slot matches:** the task creates `openemr_a` and `openemr_b` with fresh
  passwords, then rotates normally.
- **`sqlconf.php` points at the standby but the secret says otherwise:** the task updates the
  secret to match reality, then rotates the old slot.
- **The app and secret both say slot B is active:** the run continues straight to rotating the old
  slot.
- **Administrator password drift:** if a previous run changed the `dbadmin` password but failed to
  update the secret, the next run notices and repairs it before failing.

## Step 1: Try a dry run

A dry run walks through the flow without changing anything:

```bash
./scripts/run-credential-rotation.sh --dry-run
```

The script finds your stack automatically (the one with a `CredentialRotationTaskDefinitionArn`
output). If you have more than one, add `--stack-name OpenemrEcsStack`. It starts the rotation task
in the same private subnets and security groups as OpenEMR, then prints:

```text
Started credential rotation task: arn:aws:ecs:...
Check task status: aws ecs describe-tasks --cluster ... --tasks ...
Tail logs: aws logs tail ... --follow --log-stream-name-prefix ecs/credential-rotation
```

The script returns as soon as the task starts. Run the printed `aws logs tail` command to watch it.

To check the result, wait for the task to stop and read its exit code (`0` means success):

```bash
aws ecs wait tasks-stopped --cluster <cluster> --tasks <task-arn>
aws ecs describe-tasks --cluster <cluster> --tasks <task-arn> \
  --query "tasks[0].containers[0].exitCode"
```

> [!NOTE]
> `scripts/verify-credential-rotation.sh` is meant to do this check for you, but it currently
> passes an option (`--target both`) that `run-credential-rotation.sh` rejects, so it fails. Use the
> commands above instead.

## Step 2: Rotate

```bash
./scripts/run-credential-rotation.sh
```

Follow the logs as in step 1. Success looks like the task stopping with exit code `0`, OpenEMR
staying reachable throughout, and the slot secret's `active_slot` changing to the other letter.

Script options:

| Option | What it does |
|---|---|
| `--stack-name NAME` | Use this stack instead of auto-detecting |
| `--dry-run` | Go through the flow without changing anything |
| `--sync-db-users` | Only make `openemr_a` and `openemr_b` in the database match the slot secret; no switch and no `sqlconf.php` change. Use it to recover from database connection errors. Overrides `--dry-run`. |
| `--no-json` | Plain-text logs instead of JSON |
| `-h`, `--help` | Show help |

> [!TIP]
> After a rotation, anything else that logs in as `dbadmin` (such as your MySQL Workbench
> connection) needs the new password from the `DatabaseSecretARN` secret.

## Step 3: Run it on a schedule

Because rotation is safe to rerun and repairs itself, you can run it as often as you like; a failed
run is fixed by the next one. Even daily rotation has little operational impact. Pick one of these.

### Option 1: Amazon EventBridge Scheduler (recommended)

EventBridge Scheduler can start the rotation task on a schedule with no extra infrastructure.
Follow AWS's guide,
[Using Amazon EventBridge Scheduler to schedule Amazon ECS tasks](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/tasks-scheduled-eventbridge-scheduler.html),
and target `ECS RunTask` with:

- the task definition from the `CredentialRotationTaskDefinitionArn` output;
- the cluster from `ECSClusterName`;
- launch type `FARGATE`, the OpenEMR service's private subnets and security groups, and no public
  IP.

No container override is needed: the task definition's default command already runs a full
rotation with JSON logs (`--log-json`).

The scheduler's execution role needs `ecs:RunTask`, and `iam:PassRole` for the rotation task's
execution role and task role. The AWS guide shows how to create it.

### Option 2: GitHub Actions

If you'd rather keep scheduling with your repository, use GitHub's OpenID Connect (OIDC) provider to
assume an IAM role, so there are no long-lived AWS keys. See
[Configuring OpenID Connect in Amazon Web Services](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services).

```yaml
# .github/workflows/rotate-credentials.yml
name: Rotate credentials
on:
  schedule:
    - cron: '0 3 1 * *' # 03:00 UTC on the 1st of every month
  workflow_dispatch:    # allow manual runs

env:
  AWS_REGION: us-west-2
  # IAM role with OIDC trust for this repository (no static keys)
  AWS_ROLE_ARN: arn:aws:iam::123456789012:role/GitHubActions-CredentialRotation

jobs:
  rotate:
    runs-on: ubuntu-latest
    permissions:
      id-token: write       # required for the OIDC token exchange
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ env.AWS_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}
      - run: ./scripts/run-credential-rotation.sh --stack-name OpenemrEcsStack
```

Restrict the role's trust policy to your repository and branch:

```json
{
  "Effect": "Allow",
  "Principal": {"Federated": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"},
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
    },
    "StringLike": {
      "token.actions.githubusercontent.com:sub": "repo:YOUR_ORG/YOUR_REPO:ref:refs/heads/main"
    }
  }
}
```

The role needs permission to read the stack's outputs (`cloudformation:DescribeStacks`, and
`cloudformation:ListStacks` if you don't pass `--stack-name`), `ecs:DescribeServices`,
`ecs:RunTask`, and `iam:PassRole` for the rotation task's roles.

### Option 3: Systems Manager Maintenance Windows

[AWS Systems Manager Maintenance Windows](https://docs.aws.amazon.com/systems-manager/latest/userguide/maintenance-windows.html)
can run a Lambda function or Run Command that calls `ecs:RunTask`. This fits well if you already
schedule patching there.

## If something goes wrong

| Problem | What to do |
|---|---|
| Apache logs show `Failed to open stream: Permission denied` for `sqlconf.php` | Run `./scripts/fix-sqlconf-permissions.sh` (add `--stack-name NAME` if needed). It runs a one-off task that sets the file's mode to `644`. No redeploy needed. The rotation tool itself sets the owner to `apache` (UID 1000) and mode `0644` whenever it writes the file. |
| `Access denied for user 'dbadmin'` | The task repairs administrator drift on startup. If that fails, the secret really doesn't match the database. See [Reset the administrator password](#reset-the-administrator-password). |
| `Access denied` for `openemr_a` or `openemr_b` | Run `./scripts/run-credential-rotation.sh --sync-db-users`. |
| The task can't mount EFS | Check the task's networking and that its security group can reach the EFS mount targets. |
| OpenEMR doesn't pick up new credentials | Check that the ECS deployment was forced and the service became stable (`aws ecs wait services-stable`). |
| Database checks fail | Check the slot user exists and has privileges on the `openemr` database. |
| `Could not auto-detect OpenEMR ECS stack` | Pass `--stack-name`. |
| `Required stack outputs not found` | Deploy the latest version of the stack. |

### Reset the administrator password

1. In the RDS console, open your cluster, choose **Modify**, set a new **Master password**, and
   apply it.
2. Update the administrator secret, keeping its other fields:

   ```bash
   SECRET_ARN=$(aws cloudformation describe-stacks --stack-name OpenemrEcsStack \
     --query "Stacks[0].Outputs[?OutputKey=='DatabaseSecretARN'].OutputValue" --output text)
   aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --query SecretString --output text \
     | jq --arg p '<new_password>' '.password = $p' > /tmp/db-secret.json
   aws secretsmanager put-secret-value --secret-id "$SECRET_ARN" --secret-string file:///tmp/db-secret.json
   rm /tmp/db-secret.json
   ```

## Reference: the rotation tool

The task runs the Python package in
[`tools/credential-rotation/`](../../tools/credential-rotation/). Its container is named
`credential-rotation`, and its Python version is set by
`StackConstants.CREDENTIAL_ROTATION_PYTHON_VERSION` in
[`openemr_ecs/constants.py`](../../openemr_ecs/constants.py). The monthly version check workflow
reports when a newer `python:X-slim` image is available.

Command line (`python -m credential_rotation.cli`) options:

| Option | What it does |
|---|---|
| `--dry-run` | Evaluate the flow without changing anything |
| `--log-json` | Write logs as JSON |
| `--sync-db-users` | Make the database users match the slot secret; no switch, no `sqlconf.php` change |
| `--fix-permissions` | Only set `sqlconf.php` to mode `644`, then exit (used by `fix-sqlconf-permissions.sh`) |

Environment variables (the stack sets these on the task definition):

| Variable | Meaning |
|---|---|
| `AWS_REGION` | Region of the stack |
| `RDS_SLOT_SECRET_ID` | The dual-slot secret |
| `RDS_ADMIN_SECRET_ID` | The administrator secret |
| `OPENEMR_SITES_MOUNT_ROOT` | Where the `sites` EFS volume is mounted in the task |
| `OPENEMR_ECS_CLUSTER` | The OpenEMR ECS cluster |
| `OPENEMR_ECS_SERVICE` | The OpenEMR ECS service |
| `OPENEMR_HEALTHCHECK_URL` | Optional; the URL checked for application health (the stack sets it to the load balancer's HTTPS address) |

The task role can use `secretsmanager:GetSecretValue`, `DescribeSecret`, `PutSecretValue`, and
`UpdateSecretVersionStage`; `ecs:UpdateService` and `ecs:DescribeServices`; and `kms:Decrypt` and
`kms:GenerateDataKey*` for secrets encrypted with a customer managed key (`PutSecretValue` needs
`GenerateDataKey` to encrypt new values).

Developer notes and tests are in
[`tools/credential-rotation/README.md`](../../tools/credential-rotation/README.md).

## Related

- [Database access](database-access.md): connecting with the current credentials
- [Security and compliance](../reference/security-and-compliance.md)
- [Troubleshooting](../reference/troubleshooting.md)
