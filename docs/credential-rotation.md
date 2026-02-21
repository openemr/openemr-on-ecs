# Credential Rotation (RDS)

This starter kit includes zero-downtime credential rotation for OpenEMR on ECS + EFS.

## Table of Contents
- [Runtime contract](#runtime-contract)
- [Secrets model](#secrets-model)
- [Rotation task](#rotation-task)
  - [CLI flags](#cli-flags)
- [Rotation flow](#rotation-flow)
- [Rollback behavior](#rollback-behavior)
- [Idempotency and safety](#idempotency-and-safety)
- [IAM permissions used by the rotation task](#iam-permissions-used-by-the-rotation-task)
- [Manual run](#manual-run)
- [Automated scheduling](#automated-scheduling)
- [Troubleshooting](#troubleshooting)

## Runtime contract

- Application database config file: `/var/www/localhost/htdocs/openemr/sites/default/sqlconf.php`
- Shared application storage: EFS-mounted `sites` directory
- Runtime refresh strategy: forced ECS rolling deployment (`forceNewDeployment`) for safe config pickup

## Secrets model

The implementation uses one secret with dual slots.

### RDS slot secret

- CloudFormation output: `RdsSlotSecretARN`

```json
{
  "active_slot": "A",
  "A": {"username": "openemr_a", "password": "...", "host": "...", "port": "3306", "dbname": "openemr"},
  "B": {"username": "openemr_b", "password": "...", "host": "...", "port": "3306", "dbname": "openemr"}
}
```

## Rotation task

- One-off ECS task definition output: `CredentialRotationTaskDefinitionArn`
- Trigger script: `scripts/run-credential-rotation.sh`

### CLI flags

- `--dry-run`
- `--log-json`
- `--sync-db-users`
- `--fix-permissions`

## Rotation flow

1. Read active slot (`A`/`B`)
2. Select standby slot
3. **Validate admin credentials** (auto-heals drift if the DB password was changed but the secret update failed on a prior run)
4. **Auto-sync**: Ensure both `openemr_a` and `openemr_b` in RDS match the slot secret passwords (prevents "Access denied" when flipping to a drifted slot)
5. Update `sqlconf.php` to standby DB credentials
6. Force rolling ECS deployment
7. Validate DB + app health
8. Rotate old-slot credentials
9. Validate old slot independently
10. Persist updated `active_slot`
11. **Rotate admin password**: Generate new password for `dbadmin`, `ALTER USER` in the database, validate, update `DbSecretArn`

## Rollback behavior

- If flip validation fails:
  - restore previous `sqlconf.php`
  - force rolling deployment again
  - validate recovery
  - exit non-zero
- If old-slot rotation fails:
  - keep application on current active slot
  - exit non-zero

## Idempotency and safety

- **First run / neither slot matches**: Auto-bootstrap creates dedicated app users (`openemr_a`, `openemr_b`) with fresh passwords, then proceeds with normal rotation.
- **sqlconf points at standby, secrets say active**: Auto-reconcile flips `active_slot` in secrets to match reality, then rotates the old slot.
- **App and secret both indicate slot B active**: Rerun continues directly to old-slot rotation.
- **Admin credential drift**: If a prior rotation changed the DB admin password but the Secrets Manager update failed, the next run auto-detects the mismatch and self-heals by probing slot passwords before failing.

## IAM permissions used by the rotation task

- `secretsmanager:GetSecretValue`
- `secretsmanager:DescribeSecret`
- `secretsmanager:PutSecretValue`
- `secretsmanager:UpdateSecretVersionStage`
- `ecs:UpdateService`
- `ecs:DescribeServices`
- `kms:Decrypt` and `kms:GenerateDataKey*` (for CMK-encrypted secrets; PutSecretValue requires GenerateDataKey to encrypt new values)

## Manual run

```bash
./scripts/run-credential-rotation.sh
./scripts/run-credential-rotation.sh --dry-run
```

## Automated scheduling

The rotation task is designed to be run on a recurring schedule. Because it is
idempotent and self-healing, it is safe to run frequently -- a failed run will
be corrected on the next invocation.

### Option 1 -- Amazon EventBridge Scheduler (recommended)

EventBridge Scheduler can launch the ECS rotation task directly on a cron
schedule with no extra infrastructure. Follow the AWS guide to create a
schedule that targets `ECS RunTask`:

> **[Using Amazon EventBridge Scheduler to schedule Amazon ECS tasks](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/tasks-scheduled-eventbridge-scheduler.html)**

The EventBridge Scheduler execution role needs `ecs:RunTask` and `iam:PassRole`
(for the task execution role and task role). The AWS guide above walks through
creating this role.

### Option 2 -- GitHub Actions schedule

If you prefer keeping scheduling close to the repository, use GitHub's OIDC
identity provider to assume an IAM role -- no long-lived AWS keys required.
See [Configuring OpenID Connect in Amazon Web Services](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services)
for IAM trust-policy setup.

```yaml
# .github/workflows/rotate-credentials.yml
name: Rotate credentials
on:
  schedule:
    - cron: '0 3 1 * *' # 03:00 UTC on the 1st of every month
  workflow_dispatch:    # allow manual trigger

env:
  AWS_REGION: us-west-2
  # IAM role with OIDC trust for this repo (no static keys)
  AWS_ROLE_ARN: arn:aws:iam::123456789012:role/GitHubActions-CredentialRotation

jobs:
  rotate:
    runs-on: ubuntu-latest
    permissions:
      id-token: write       # required for OIDC token exchange
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ env.AWS_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}
      - run: ./scripts/run-credential-rotation.sh
```

The IAM role's trust policy should restrict to your repository and branch:

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

### Option 3 -- Systems Manager Maintenance Window

[AWS Systems Manager Maintenance Windows](https://docs.aws.amazon.com/systems-manager/latest/userguide/maintenance-windows.html) can invoke a Lambda or Run Command that
calls `ecs:RunTask`. This approach integrates with existing patching/maintenance
schedules.

A rotation takes 15-25 minutes (dominated by the ECS rolling deployment) and
causes zero application downtime, so even daily rotation has negligible
operational impact.

## Troubleshooting

- **Permission denied on sqlconf.php**: If Apache logs show `Failed to open stream: Permission denied` for `sqlconf.php`, run `./scripts/fix-sqlconf-permissions.sh` (no redeploy needed). The rotation tool sets file ownership to `apache` (UID 1000) and mode `0o644` during writes to prevent this.
- **Access denied for user 'dbadmin'**: The rotation now auto-heals admin credential drift on startup. If auto-heal fails, the RDS admin secret truly doesn't match the cluster. Fix: In RDS Console -> your cluster -> Modify -> Master password, set a new password. Then update the secret: `aws secretsmanager put-secret-value --secret-id <DbSecretArn> --secret-string '{"username":"dbadmin","password":"<new_password>","host":"<cluster_endpoint>"}'`.
- Task cannot mount EFS: verify task networking and SG access to EFS mount targets.
- App not picking new credentials: verify ECS deployment was forced and service stabilized.
- DB validation fails: verify slot user exists and has privileges on `openemr` database.
