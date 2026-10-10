# Backup and restore

This guide explains what the stack backs up automatically, how to see and create backups, and
how to restore the database or files when something goes wrong. It also covers copying backups
to another Region or AWS account. It's for anyone responsible for keeping an OpenEMR deployment's
data safe.

## Before you begin

- You have a deployed stack and the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  configured for the same account.
- **Cost:** AWS Backup charges for backup storage, restores, and cross-Region or cross-account
  copies. See [AWS Backup pricing](https://aws.amazon.com/backup/pricing/).
- **Time:** listing backups takes seconds. Restores take from about 15 minutes (small file
  systems) to several hours (large databases).

Set these once in your terminal so the commands below work as written. Use the Region you
deployed to:

```bash
export STACK_NAME=OpenemrEcsStack
export AWS_DEFAULT_REGION=us-east-1   # change to your Region
```

> [!IMPORTANT]
> The backup scripts in `scripts/` default to `us-west-2` if `AWS_DEFAULT_REGION` isn't set. They
> don't read the Region from your AWS CLI profile, so always set it (or pass `-r <region>`).

## What gets backed up

[AWS Backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/whatisbackup.html) is set up
automatically when you deploy. It backs up three things:

| Resource | What's in it | Stack output with its ID |
|---|---|---|
| Aurora MySQL database cluster | All OpenEMR data: patients, encounters, users, settings | `DatabaseEndpoint` (the cluster name is the first part) |
| "Sites" EFS file system | Each OpenEMR site's files: uploaded patient documents, `sqlconf.php`, custom templates | `EFSSitesFileSystemId` |
| "SSL" EFS file system | The internal TLS certificates used between the load balancer and containers | `EFSSSLFileSystemId` |

> [!NOTE]
> The SSL file system's certificates are regenerated automatically every 2 days, so you rarely
> need to restore it.

Backups are stored in a backup vault named `<stack name>-vault-<suffix>` (for example
`OpenemrEcsStack-vault-a1b2c3`) in the same Region as the stack. Its name is in the
`BackupVaultName` stack output.

### Backup schedule and retention

The stack uses AWS Backup's built-in "daily, weekly, monthly, 7-year retention" plan. Each rule
takes its own backup; weekly and monthly backups aren't built from daily ones.

| Rule | When it runs (UTC) | Kept for |
|---|---|---|
| Daily | Every day at 05:00 | 35 days |
| Weekly | Every Saturday at 05:00 | 90 days |
| Monthly | The 1st of each month at 05:00 | 7 years (2,555 days); moved to cheaper cold storage after 90 days |

This means you can usually recover to any day in the last 5 weeks, any week in the last 3 months,
and any month in the last 7 years. Your *recovery point objective* (how much recent data you
could lose) is up to 24 hours with this schedule.

> [!TIP]
> A brand-new stack has no backups until 05:00 UTC comes around. To take one right away, see
> [Create a backup now](#create-a-backup-now).

## Before you delete a stack

> [!CAUTION]
> When the stack is deleted, a cleanup step in the stack **deletes every recovery point in its
> backup vault**, and the EFS file systems are deleted too. Only a final Aurora snapshot is kept,
> as an ordinary RDS snapshot outside AWS Backup.
>
> If you might ever need those backups, copy them to another vault first (see
> [Copy backups to another Region](#copy-backups-to-another-region) or
> [another account](#copy-backups-to-another-aws-account)), and copy any files you need off the
> EFS file systems.

## Ways to work with backups

You have three options. They do the same underlying AWS Backup calls.

- **Backup Manager TUI** (recommended): an interactive terminal app for browsing and restoring.
  Needs Go installed.
- **Shell scripts** in `scripts/`: `list-backups.sh`, `create-backup.sh`, and
  `restore-from-backup.sh`. Need only Bash and the AWS CLI.
- **AWS CLI or console**, for full control, such as restoring to a new database name.

## See your backups

### With the Backup Manager TUI

The TUI finds your stack and backup vault automatically, shows each backup's age with a colored
dot (green: less than 24 hours old; yellow: 1–7 days; red: more than 7 days), and lets you
restore with a confirmation step.

1. Install [Go](https://go.dev/doc/install) 1.27 or later.
2. Build and start it:

   ```bash
   cd scripts/backup-tui
   go build -o backup-tui .
   ./backup-tui -region "$AWS_DEFAULT_REGION"
   ```

   Success looks like a list of backups:

   ![Backup Manager list view with color-coded backups](../images/backup_tui_screenshot_1.png)

3. Use the arrow keys to choose a backup and press **Enter** to see its details:

   ![Backup Manager detail view for one recovery point](../images/backup_tui_screenshot_2.png)

Options you can pass:

| Option | What it does |
|---|---|
| `-stack NAME` | Stack name (found automatically if you leave it out) |
| `-vault NAME` | Backup vault name (found automatically if you leave it out) |
| `-region REGION` | AWS Region (default `us-west-2`) |
| `-type RDS` or `-type EFS` | Show only database or only file system backups |
| `-help` | Show help |

You can also use a named AWS profile: `AWS_PROFILE=my-profile ./backup-tui`.

Keyboard controls:

| Key | Action |
|---|---|
| `↑` / `↓` or `k` / `j` | Move through the list |
| `PgUp` / `PgDn` | Page up or down |
| `g` / `G` | Jump to the first or last backup |
| `Enter` | Open a backup, or start a restore |
| `f` | Cycle the filter: All, RDS, EFS |
| `r` | Refresh the list |
| `b`, `←`, or `Backspace` | Go back |
| `?` | Show or hide help |
| `y` / `n` | Confirm or cancel a restore |
| `Esc` / `q` | Back, or quit |

Developer details (tests, cross-compiling) are in
[`scripts/backup-tui/README.md`](../../scripts/backup-tui/README.md).

### With the script

```bash
./scripts/list-backups.sh
```

It prints one table of database (RDS) recovery points and one of file system (EFS) recovery
points, with each one's ARN, creation date, status, and size. Options: `-s` / `--stack-name`,
`-v` / `--vault-name`, `-r` / `--region`, `-h` / `--help`. The same settings can come from the
`STACK_NAME`, `BACKUP_VAULT_NAME`, and `AWS_DEFAULT_REGION` environment variables.

> [!NOTE]
> The script currently prints "No ... recovery points found" after each table even when the table
> lists backups. Trust the table.

### With the AWS CLI

This shows which resource each backup came from, which matters for EFS because both file systems
are in the same vault:

```bash
BACKUP_VAULT=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='BackupVaultName'].OutputValue" --output text)

aws backup list-recovery-points-by-backup-vault \
  --backup-vault-name "$BACKUP_VAULT" \
  --by-resource-type EFS \
  --query "RecoveryPoints[].[CreationDate,Status,ResourceArn,RecoveryPointArn]" \
  --output table
```

Use `--by-resource-type RDS` for the database. Each recovery point has an ARN (its unique ID), a
creation date, a status (such as `COMPLETED`, `EXPIRED`, or `DELETING`), a resource type, and the
ARN of the resource it was taken from. Recovery points can't be changed after they're created.

## Create a backup now

Run this before risky changes (upgrades, imports, bulk edits), or on a new stack that hasn't had
its first scheduled backup:

```bash
./scripts/create-backup.sh
```

It starts on-demand backup jobs for the Aurora cluster **and** both EFS file systems. Success
looks like `Successfully created 3 backup job(s)`. The jobs then run in the background; the new
recovery points appear in `./scripts/list-backups.sh` once they finish, usually within minutes for
a small stack. On-demand backups have no expiry date, so delete them yourself when you no longer
need them (they're also deleted if the stack is deleted).

The script takes no arguments (any argument just prints its help). To target a different stack or
Region, use environment variables:

```bash
STACK_NAME=MyOtherStack AWS_DEFAULT_REGION=eu-west-1 ./scripts/create-backup.sh
```

You can also set `BACKUP_VAULT_NAME` to skip vault discovery.

## Restore files (EFS)

Use this when documents or site files were deleted or damaged.

> [!NOTE]
> EFS restores into the **existing** file system don't overwrite anything. AWS Backup puts the
> restored files in a new folder at the root of the file system named
> `aws-backup-restore_<timestamp>`. You then copy back only what you need.

1. Find the recovery point you want with the
   [AWS CLI command above](#with-the-aws-cli), and check that its `ResourceArn` ends with the
   ID in `EFSSitesFileSystemId` (or `EFSSSLFileSystemId`).

2. Start the restore. Pass the recovery point ARN explicitly:

   ```bash
   SITES_EFS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
     --query "Stacks[0].Outputs[?OutputKey=='EFSSitesFileSystemId'].OutputValue" --output text)

   ./scripts/restore-from-backup.sh EFS "$SITES_EFS" \
     "arn:aws:backup:us-east-1:123456789012:recovery-point:..."
   ```

   The script checks the job every 30 seconds. Success looks like
   `Restore job completed successfully`.

   Or, in the Backup Manager TUI, choose the EFS backup, press **Enter**, review the details,
   and press **y**:

   ![Backup Manager restore confirmation showing restore parameters](../images/backup_tui_screenshot_3.png)

   The TUI then shows live progress, checking every 5 seconds. Press **Esc** to go back to the
   list; the restore keeps running in AWS.

   ![Backup Manager live restore monitoring](../images/backup_tui_screenshot_4.png)

3. Copy the restored files back into place. With [ECS Exec](database-access.md#open-a-shell-in-a-running-container)
   turned on, open a shell in an OpenEMR container, find the restore folder, and copy what you need.
   For the sites file system, the restore folder appears inside the container's sites directory
   (`/var/www/localhost/htdocs/openemr/sites/`):

   ```bash
   ls -la /var/www/localhost/htdocs/openemr/sites/aws-backup-restore_*/
   ```

4. After checking that everything's back, delete the `aws-backup-restore_*` folder to save
   storage.

To restore into a **brand-new** file system instead, use the AWS CLI:

```bash
aws backup start-restore-job \
  --recovery-point-arn "$RECOVERY_POINT_ARN" \
  --iam-role-arn "arn:aws:iam::<account-id>:role/service-role/AWSBackupDefaultServiceRole" \
  --metadata "{\"newFileSystem\":\"true\",\"Encrypted\":\"true\",\"PerformanceMode\":\"generalPurpose\",\"CreationToken\":\"restored-efs-$(date +%s)\"}"
```

A new file system isn't connected to the stack. You'd mount it somewhere to copy files out.

## Restore the database (Aurora)

Use this when database data is corrupted or wrongly changed.

> [!WARNING]
> An Aurora restore always creates a **new** database cluster; it never rewinds the existing one.
> Both `restore-from-backup.sh RDS` and the Backup Manager TUI ask AWS to create the new cluster
> with the **same name as your current cluster**, so they fail while the current cluster still
> exists. Use the manual steps below and give the restored cluster a new name.

1. Stop OpenEMR from writing more data while you investigate, by scaling the service to zero:

   ```bash
   CLUSTER=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
     --query "Stacks[0].Outputs[?OutputKey=='ECSClusterName'].OutputValue" --output text)
   SERVICE=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
     --query "Stacks[0].Outputs[?OutputKey=='ECSServiceName'].OutputValue" --output text)
   aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0
   ```

2. Pick a recovery point from before the problem:

   ```bash
   aws backup list-recovery-points-by-backup-vault \
     --backup-vault-name "$BACKUP_VAULT" --by-resource-type RDS \
     --query "RecoveryPoints[].[CreationDate,Status,RecoveryPointArn]" --output table
   RECOVERY_POINT_ARN="arn:aws:rds:...:cluster-snapshot:awsbackup:..."
   ```

3. Get the restore settings AWS recorded for it:

   ```bash
   aws backup get-recovery-point-restore-metadata \
     --backup-vault-name "$BACKUP_VAULT" \
     --recovery-point-arn "$RECOVERY_POINT_ARN"
   ```

4. Start the restore with a new cluster name, using the current cluster's subnet group and
   security groups:

   ```bash
   DB_CLUSTER=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
     --query "Stacks[0].Outputs[?OutputKey=='DatabaseEndpoint'].OutputValue" --output text | cut -d. -f1)
   SUBNET_GROUP=$(aws rds describe-db-clusters --db-cluster-identifier "$DB_CLUSTER" \
     --query "DBClusters[0].DBSubnetGroup" --output text)
   SECURITY_GROUPS=$(aws rds describe-db-clusters --db-cluster-identifier "$DB_CLUSTER" \
     --query "DBClusters[0].VpcSecurityGroups[].VpcSecurityGroupId" --output text | tr '\t' ',')
   ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

   aws backup start-restore-job \
     --recovery-point-arn "$RECOVERY_POINT_ARN" \
     --iam-role-arn "arn:aws:iam::${ACCOUNT_ID}:role/service-role/AWSBackupDefaultServiceRole" \
     --metadata "{\"DBClusterIdentifier\":\"${DB_CLUSTER}-restored\",\"DBSubnetGroupName\":\"${SUBNET_GROUP}\",\"VpcSecurityGroupIds\":\"${SECURITY_GROUPS}\"}"
   ```

   The command prints a `RestoreJobId`. The stack's own backup role (named like
   `OpenemrEcsStack-BackupServiceRole...`, with AWS's backup and restore policies) also works
   for `--iam-role-arn`. If `AWSBackupDefaultServiceRole` doesn't exist in your account, use that
   one.

5. Watch progress until the status is `COMPLETED`:

   ```bash
   watch -n 30 "aws backup describe-restore-job --restore-job-id <RestoreJobId> \
     --query '[Status,PercentDone,StatusMessage]' --output text"
   ```

   Status values are `PENDING`, `RUNNING`, `COMPLETED`, `ABORTED` (cancelled), and `FAILED`
   (check `StatusMessage`).

6. Add a database instance to the restored cluster so you can connect to it. Restored Aurora
   clusters start without instances (see
   [Restoring an Aurora cluster](https://docs.aws.amazon.com/aws-backup/latest/devguide/restoring-aur.html)):

   ```bash
   aws rds modify-db-cluster --db-cluster-identifier "${DB_CLUSTER}-restored" \
     --serverless-v2-scaling-configuration MinCapacity=0.5,MaxCapacity=16 --apply-immediately
   aws rds create-db-instance --db-instance-identifier "${DB_CLUSTER}-restored-writer" \
     --db-cluster-identifier "${DB_CLUSTER}-restored" \
     --engine aurora-mysql --db-instance-class db.serverless
   ```

7. Connect to the restored cluster to check the data (see
   [Database access](database-access.md); use the restored cluster's endpoint as the host and the
   same admin credentials as the original at the time of the backup).

8. Bring the good data back into the live database. The simplest, safest route is to export the
   tables you need from the restored cluster (for example with `mysqldump`) and load them into
   the live cluster. Then scale the service back up:

   ```bash
   aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 2
   ```

9. Delete the restored cluster and its instance when you're done, so you stop paying for them.

> [!NOTE]
> OpenEMR finds its database through the stack's database secret and through `sqlconf.php` on the
> sites file system. Pointing a running stack at a different cluster permanently isn't automated
> by this project and isn't recommended; copy the data back instead.

## Full disaster recovery

If you've lost the whole environment:

1. Deploy a new stack (see [Get Started](../get-started/README.md)).
2. Restore the database to a new cluster as above, and the sites file system's recovery point
   into the new stack's sites file system.
3. Move the restored files out of the `aws-backup-restore_*` folder into place, and load the
   database data into the new stack's cluster.
4. Check that OpenEMR starts, logs in, and shows the expected patients and documents.

For moving a whole existing OpenEMR into a brand-new stack with safety checks, the
[guarded import tool](importing-openemr.md) may be a better fit.

> [!TIP]
> Practice this regularly (for example quarterly) by restoring into a separate test stack, such as
> `./scripts/restore-from-backup.sh -s OpenemrEcsStackTest ...`, and writing down what you did and
> how long it took.

Typical recovery times, which depend heavily on data size:

| What | Typical time |
|---|---|
| Aurora restore | 30–60 minutes for under 50 GB; 2–4 hours or more for over 100 GB |
| EFS restore | 15–30 minutes for typical file systems; longer for large ones |
| Full environment, with checks | at least 1–2 hours |

### Lower your recovery point objective

For data loss of minutes instead of up to a day, AWS Backup can take *continuous backups* of
Aurora, which allow point-in-time recovery:

1. Open the [AWS Backup console](https://console.aws.amazon.com/backup) and go to **Backup plans**.
2. Edit or create a plan and, in a backup rule, turn on **Continuous backup for supported
   resources**.
3. Set the point-in-time recovery window (1–35 days).

Continuous backup isn't available for EFS, which is always snapshot-based.

> [!NOTE]
> The stack manages its own backup plan. Changes you make to that plan in the console can be
> undone the next time you run `cdk deploy`. Put extra rules in a separate plan.

## Copy backups to another Region

Copies in another Region protect you from a regional outage and can satisfy rules about
geographic separation. See
[cross-Region backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/cross-region-backup.html).

Things to know first:

- RDS and EFS both support cross-Region copies.
- Copies are re-encrypted with the destination vault's key.
- Backups that have moved to cold storage (monthly backups older than 90 days) can't be copied.
- RDS copies are incremental after the first one. **EFS copies are always full copies**, so
  they cost more and take longer.
- Option groups can't be copied between Regions. If a restore in another Region fails with
  `The snapshot requires a target option group with the following options: ...`, create matching
  option groups in that Region and specify them when restoring.

### Copy one backup now

In the console: open **AWS Backup** > **Backup vaults**, choose your vault, select a recovery
point, and choose **Actions** > **Copy**. Pick the destination Region and vault, an optional cold
storage transition (at least 1 day before transitioning, then at least 90 days in cold storage),
the retention period, and an IAM role. Then choose **Copy**.

With the CLI:

```bash
aws backup start-copy-job \
  --recovery-point-arn "$RECOVERY_POINT_ARN" \
  --source-backup-vault-name "$BACKUP_VAULT" \
  --destination-backup-vault-arn "arn:aws:backup:us-west-2:123456789012:backup-vault:my-dr-vault" \
  --iam-role-arn "arn:aws:iam::123456789012:role/service-role/AWSBackupDefaultServiceRole"
```

### Copy automatically

Create a separate backup plan (or a rule in one) with a **Copy to destination** setting:
**AWS Backup** > **Backup plans** > **Create backup plan** > **Build a new plan**, add a rule, and
in **Copy to destination** pick the Region, vault, and retention. To edit a plan from the CLI,
use `aws backup get-backup-plan --backup-plan-id <id>` to get its JSON, add a copy action, and
apply it with `aws backup update-backup-plan --backup-plan-id <id> --backup-plan file://updated-plan.json`.

### Check on copies

```bash
aws backup list-copy-jobs
aws backup describe-copy-job --copy-job-id <job-id> \
  --query "[State,StateMessage,ResourceType]" --output table
```

## Copy backups to another AWS account

Copies in a separate account protect you if the production account is compromised. See
[cross-account backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/create-cross-account-backup.html).

Requirements:

- Both accounts must be in the same AWS Organization, with cross-account backup turned on.
- The destination vault must allow `backup:CopyIntoBackupVault`, and can't be the destination
  account's default vault.
- Cold-storage backups can't be copied.

> [!WARNING]
> This stack encrypts the Aurora database with the AWS managed RDS key. AWS doesn't allow
> snapshots encrypted with an AWS managed key to be shared with another account, so cross-account
> copies of the **database** backups are likely to fail as deployed. Test this before relying on
> it. EFS backups don't have this limitation.

1. **Turn on cross-account backup** from the Organization's management account: open the
   [AWS Backup console](https://console.aws.amazon.com/backup), go to **Settings**, and under
   **Cross-account backup** choose **Enable**.

2. **Prepare the destination vault** in the destination account and give it an access policy.
   To allow one source account:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": { "AWS": "arn:aws:iam::SOURCE_ACCOUNT_ID:root" },
         "Action": "backup:CopyIntoBackupVault",
         "Resource": "*"
       }
     ]
   }
   ```

   Or to allow your whole organization:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": "*",
         "Action": "backup:CopyIntoBackupVault",
         "Resource": "*",
         "Condition": { "StringEquals": { "aws:PrincipalOrgID": "o-xxxxxxxxxx" } }
       }
     ]
   }
   ```

   Apply it:

   ```bash
   aws backup put-backup-vault-access-policy \
     --backup-vault-name DestinationBackupVault \
     --policy file://vault-policy.json \
     --region us-east-1
   ```

3. **Share customer managed KMS keys**, if your backups use them:

   ```bash
   aws kms create-grant \
     --key-id <source-key-id> \
     --grantee-principal "arn:aws:iam::DESTINATION_ACCOUNT_ID:root" \
     --operations Decrypt DescribeKey
   ```

4. **Copy a backup.** In the source account's console, select a recovery point, choose
   **Actions** > **Copy**, turn on **Copy to another account's vault**, and enter the destination
   account ID and vault. Or with the CLI:

   ```bash
   aws backup start-copy-job \
     --recovery-point-arn "$RECOVERY_POINT_ARN" \
     --source-backup-vault-name "$BACKUP_VAULT" \
     --destination-backup-vault-arn "arn:aws:backup:us-east-1:DESTINATION_ACCOUNT_ID:backup-vault:DestinationBackupVault" \
     --iam-role-arn "arn:aws:iam::SOURCE_ACCOUNT_ID:role/AWSBackupCrossAccountRole"
   ```

   To copy on a schedule, edit a backup plan rule in the source account, expand **Copy to
   destination**, turn on **Copy to another account's vault**, and enter the account and vault.

The IAM role used for copies needs at least these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["backup:StartCopyJob", "backup:DescribeCopyJob"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["ec2:ModifySnapshotAttribute", "ec2:DescribeSnapshots"], "Resource": "*" },
    {
      "Effect": "Allow",
      "Action": "kms:CreateGrant",
      "Resource": "*",
      "Condition": { "StringEquals": { "kms:ViaService": "backup.amazonaws.com" } }
    }
  ]
}
```

To restrict copies to approved vaults, you can use a service control policy (SCP) that denies
`backup:CopyIntoBackupVault` unless the destination vault has a tag:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Deny",
      "Action": "backup:CopyIntoBackupVault",
      "Resource": "*",
      "Condition": { "Null": { "aws:ResourceTag/DestinationBackupVault": "true" } }
    }
  ]
}
```

Then tag approved vaults:

```bash
aws backup tag-resource \
  --resource-arn "arn:aws:backup:us-east-1:DESTINATION_ACCOUNT_ID:backup-vault:DestinationBackupVault" \
  --tags DestinationBackupVault=true
```

Good to know:

- If the destination account leaves the organization, its copies are kept. Consider an SCP that
  prevents accounts from leaving.
- After you turn cross-account backup off, operations can continue for about 15 minutes.
- If the copy role is deleted mid-copy, snapshots may not be unshared automatically.
- **To restore from another account**, copy the recovery point back into the source account and
  restore it there as usual. You may need to share keys back as well.

## If something goes wrong

| Problem | What to do |
|---|---|
| `Could not find backup vault` | Check the stack name and Region (`AWS_DEFAULT_REGION`). List vaults with `aws backup list-backup-vaults`. |
| No recovery points | Backups run at 05:00 UTC; a new stack may not have one yet. Run `./scripts/create-backup.sh`. Check jobs with `aws backup list-backup-jobs --by-state COMPLETED` (or `FAILED`). |
| Restore job `FAILED` | Read `StatusMessage` from `aws backup describe-restore-job --restore-job-id <id>`. For Aurora, the most common cause is reusing the existing cluster name; see [Restore the database](#restore-the-database-aurora). Also check the IAM role and that subnet and security groups exist. |
| Restore script restores the wrong thing or says no recovery point was selected | Its interactive picker is unreliable when there's more than one recovery point and can't tell the two file systems apart. Always pass the recovery point ARN as the last argument. |
| Restore is slow | Normal for large databases. Watch with the `watch` command in step 5 above. |
| Can't find restored EFS files | Look in the `aws-backup-restore_<timestamp>` folder at the root of the file system. |
| Want alerts for failed backup jobs | Create an Amazon EventBridge rule or CloudWatch alarm for AWS Backup job failures; the stack doesn't create one. |

Best practices: test restores regularly, write down procedures specific to your environment,
alert on backup job failures, check that retention meets your compliance needs, keep a copy in
another Region or account, manage KMS keys carefully when copying, and give destination vaults
least-privilege access policies.

## Related

- [AWS Backup documentation](https://docs.aws.amazon.com/aws-backup/)
- [Restoring Aurora with AWS Backup](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-backup-restore.html)
- [EFS and AWS Backup](https://docs.aws.amazon.com/efs/latest/ug/awsbackup.html)
- [Database access](database-access.md): connect to a restored cluster
- [Importing an existing OpenEMR](importing-openemr.md)
- [Configuration](configuration.md#protecting-against-accidental-deletion): deletion protection
- [`scripts/README.md`](../../scripts/README.md): all helper scripts
