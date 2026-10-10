# Database and container access

This guide shows you how to get a command line inside a running OpenEMR container, how to connect
a MySQL tool on your own computer to the OpenEMR database, and how to run SQL over HTTPS with the
RDS Data API. It's for administrators and developers who need to troubleshoot, run queries, or
manage database users.

None of this is reachable from the internet. The database lives in private subnets, and access
goes through AWS Systems Manager (SSM), which checks your IAM permissions first.

## Before you begin

- You have a deployed stack and the
  [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  configured for its account and Region.
- Install the
  [Session Manager plugin for the AWS CLI](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).
  ECS Exec and port forwarding both need it.
- Your IAM identity needs permission to start SSM sessions on the OpenEMR tasks
  (`ecs:ExecuteCommand`, `ssm:StartSession`) and to read the database secret.
- **Cost:** ECS Exec and port forwarding add a KMS key, a log group, and an S3 bucket for session
  logs. The Data API is billed per request; see
  [Aurora pricing](https://aws.amazon.com/rds/aurora/pricing/).
- **Time:** about 15 minutes for the first setup.

Set these once so the commands below work as written:

```bash
export STACK_NAME=OpenemrEcsStack
output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
CLUSTER=$(output ECSClusterName)
SERVICE=$(output ECSServiceName)
```

## Turn on ECS Exec

[ECS Exec](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-exec.html) lets you
open a root shell in a running container. Port forwarding to the database uses the same mechanism,
so you need it for both.

> [!WARNING]
> ECS Exec gives root access to containers that handle patient data. For most production
> workloads, leave it off and turn it on only when you need it.

1. In `cdk.json`, set:

   ```json
   "enable_ecs_exec": "true",
   ```

2. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

3. Replace the running tasks so they pick up the setting (tasks started before the change can't
   accept sessions):

   ```bash
   aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment
   aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE"
   ```

What gets created: a KMS key with rotation turned on, an encrypted CloudWatch log group, and an
encrypted S3 bucket (prefix `exec-command-output`) that record ECS Exec shell sessions. The
service gets `EnableExecuteCommand`, and the task role gets the SSM messaging permissions it needs.
The log group is kept even if you later turn ECS Exec off or delete the stack.

To turn access off again, set `enable_ecs_exec` back to `"false"` and deploy. That blocks both
shells and port forwarding for everyone.

## Open a shell in a running container

1. Pick a running task:

   ```bash
   TASK=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
     --query "taskArns[0]" --output text)
   ```

2. Start a shell in the `openemr` container:

   ```bash
   aws ecs execute-command --cluster "$CLUSTER" \
     --task "$TASK" \
     --container openemr \
     --interactive \
     --command "/bin/sh"
   ```

   Success looks like `Starting session with SessionId: ...` followed by a `#` prompt. OpenEMR's
   files are under `/var/www/localhost/htdocs/openemr`, and the shared site files are in its
   `sites/` folder. Type `exit` to leave.

You can also run this from [AWS CloudShell](https://docs.aws.amazon.com/cloudshell/latest/userguide/welcome.html),
which already has the AWS CLI and Session Manager plugin.

## Connect to the database from your computer

The `scripts/port_forward_to_rds.sh` script forwards port 3306 (MySQL's port) on your computer,
through a running OpenEMR task, to the database. Then any MySQL tool on your computer, such as
[MySQL Workbench](https://dev.mysql.com/downloads/workbench/), can connect to `127.0.0.1:3306`
as if the database were local.

1. [Turn on ECS Exec](#turn-on-ecs-exec) if you haven't.

2. Get the database's writer hostname. It's the `DatabaseEndpoint` stack output:

   ```bash
   DB_HOST=$(output DatabaseEndpoint)
   echo "$DB_HOST"
   ```

   Or in the console: open the stack in CloudFormation, follow the link to the database in RDS,

   ![CloudFormation resources tab with the link to the database](../images/navigate_to_database.png)

   and copy the endpoint of the **writer** instance:

   ![RDS console showing the writer instance endpoint](../images/RDS_console_writer_instance.png)

3. Get the ECS cluster name (the `ECSClusterName` output, already in `$CLUSTER`). In the console,
   it's on the CloudFormation stack:

   ![CloudFormation console showing the ECS cluster name](../images/copy_name_of_ecs_cluster.png)

4. Start port forwarding, and leave the terminal open:

   ```bash
   ./scripts/port_forward_to_rds.sh "$CLUSTER" "$DB_HOST"
   ```

   The script picks a random running task and starts an SSM port-forwarding session. Success looks
   like `Port 3306 opened for sessionId ...` and `Waiting for connections...`:

   ![Terminal running the port forwarding script](../images/run_port_forwarding_script.png)

   If something on your computer already uses port 3306 (such as a local MySQL), stop it first.

5. Get the database administrator credentials from AWS Secrets Manager. The secret's ARN is the
   `DatabaseSecretARN` output:

   ```bash
   aws secretsmanager get-secret-value --secret-id "$(output DatabaseSecretARN)" \
     --query SecretString --output text
   ```

   The user name is `dbadmin`. In the console, open the secret,

   ![Secrets Manager showing the database secret](../images/accessing_db_secret.png)

   choose **Retrieve secret value**,

   ![The Retrieve secret value button](../images/retrieve_secret_value.png)

   and copy the user name and password:

   ![The database user name and password in Secrets Manager](../images/username_and_password.png)

   > [!NOTE]
   > If you use [credential rotation](credential-rotation.md), the administrator password changes
   > with each rotation. Always read the current value from the secret.

6. Connect with TLS. The database **requires** encrypted connections. Download AWS's RDS
   certificate bundle and point your client at it:

   ```bash
   curl -o global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
   mysql -h 127.0.0.1 -P 3306 -u dbadmin -p \
     --ssl-mode=VERIFY_CA --ssl-ca=global-bundle.pem openemr
   ```

   (Use `VERIFY_CA` rather than `VERIFY_IDENTITY`, because you're connecting to `127.0.0.1`, not
   the hostname on the certificate.) In MySQL Workbench, set the host to `127.0.0.1`, port `3306`,
   and on the **SSL** tab choose **Require** and set the CA file to `global-bundle.pem`.

Once connected, you can manage the database however you like. For example, here's MySQL Workbench
connected remotely and creating a MySQL function that calls a Claude foundation model through
[Amazon Bedrock](optional-features.md#aurora-machine-learning-with-amazon-bedrock):

![MySQL Workbench connected through port forwarding and calling Bedrock from SQL](../images/accessing_the_database_remotely.png)

### Good practice for database access

- Don't hand out the `dbadmin` login. Use it to create separate MySQL users with only the
  permissions each person needs.
- Port forwarding only gets someone to the database. They still need database credentials.
- Everything sent through the port-forwarding session is encrypted, and the database itself only
  accepts TLS connections.
- Who started a session is recorded by AWS CloudTrail (as `StartSession` events) when CloudTrail is
  on. ECS Exec shell sessions are also recorded in the exec log group and S3 bucket.
- Turning ECS Exec off blocks this kind of access for everyone, everywhere.

## Use the RDS Data API

The [RDS Data API](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html)
lets you run SQL against the database through an HTTPS API call, with no database connection or
port forwarding. It suits scripts, serverless functions, and quick queries. Access is controlled
by IAM and by the database secret you pass. See
[the Data API for Aurora Serverless v2](https://aws.amazon.com/blogs/database/introducing-the-data-api-for-amazon-aurora-serverless-v2-and-amazon-aurora-provisioned-clusters/)
for details and limits.

1. In `cdk.json`, set:

   ```json
   "enable_data_api": "true",
   ```

2. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

3. Find the cluster ARN and secret ARN:

   ```bash
   DB_CLUSTER_ID=$(output DatabaseEndpoint | cut -d. -f1)
   CLUSTER_ARN=$(aws rds describe-db-clusters --db-cluster-identifier "$DB_CLUSTER_ID" \
     --query "DBClusters[0].DBClusterArn" --output text)
   SECRET_ARN=$(output DatabaseSecretARN)
   ```

4. Run a query:

   ```bash
   aws rds-data execute-statement \
     --resource-arn "$CLUSTER_ARN" \
     --secret-arn "$SECRET_ARN" \
     --database openemr \
     --sql "SELECT COUNT(*) FROM patient_data"
   ```

   Success looks like JSON with a `records` list.

To do the same from Python, edit [`scripts/test_data_api.py`](../../scripts/test_data_api.py):
set your Region on line 5 (it's `us-west-2` by default), the cluster ARN on line 8, the secret
ARN on line 9, and your SQL on line 13. Then run `python scripts/test_data_api.py` (it needs
`boto3`).

Turning the Data API off again is the same setting set to `"false"`.

## If something goes wrong

| Problem | What to do |
|---|---|
| `The execute command failed because execute command was not enabled when the task was run` | Force a new deployment (step 3 of [Turn on ECS Exec](#turn-on-ecs-exec)). |
| `SessionManagerPlugin is not found` | Install the Session Manager plugin (see [Before you begin](#before-you-begin)). |
| `AccessDeniedException` when starting a session | Your IAM identity needs `ecs:ExecuteCommand` and `ssm:StartSession`. |
| Port forwarding script fails right away | No tasks are running in the cluster, or the cluster name is wrong. Check `aws ecs list-tasks --cluster "$CLUSTER"`. |
| MySQL says `Connections using insecure transport are prohibited` | Turn on TLS in your client (step 6). |
| `Access denied for user 'dbadmin'` | Read the current password from the secret again; rotation may have changed it. |
| Data API returns `HttpEndpoint is not enabled` | Set `enable_data_api` to `"true"` and deploy. |

More in [Troubleshooting](../reference/troubleshooting.md).

## Related

- [Credential rotation](credential-rotation.md): rotating the database passwords
- [Optional features](optional-features.md#aurora-machine-learning-with-amazon-bedrock): calling
  Bedrock from SQL
- [Backup and restore](backup-and-restore.md): connecting to a restored database
- [Security and compliance](../reference/security-and-compliance.md)
