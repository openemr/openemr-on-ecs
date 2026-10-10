# Serverless analytics environment

This guide shows you how to turn on the optional analytics environment, copy OpenEMR's data into
it, and start working with that data in Amazon SageMaker Studio. It's for data scientists and
developers who want to run reports, Spark jobs, or machine learning on their OpenEMR data, without
touching the live system.

The environment's goal is large-scale analysis and machine learning on your EMR data, in a way
that's HIPAA-eligible and costs very little unless you actually use it. Providers could, for
example, train models on their own data and release or license them.

## Before you begin

- A deployed stack. You'll need console access with IAM permissions for SageMaker.
- **Cost:** while it sits idle, the environment costs little: a KMS key, S3 storage for any
  exported data, and storage on the SageMaker domain's EFS volume. You pay for compute only when
  you run it (SageMaker apps, EMR Serverless jobs, export tasks). See
  [SageMaker pricing](https://aws.amazon.com/sagemaker/pricing/) and
  [EMR Serverless pricing](https://aws.amazon.com/emr/serverless/pricing/).
- **Time:** about 10 extra minutes of deployment; a few minutes to export a small dataset.
- **Compliance:** SageMaker is a
  [HIPAA-eligible service](https://docs.aws.amazon.com/whitepapers/latest/architecting-hipaa-security-and-compliance-on-aws/amazon-sagemaker.html).
  If you're a HIPAA covered entity, see [Security and compliance](../reference/security-and-compliance.md).

## What gets created

![SageMaker Studio and EMR Serverless architecture](../images/sagemaker_studio_architecture.png)

(The diagram is adapted from
[this AWS blog post](https://aws.amazon.com/blogs/machine-learning/use-langchain-with-pyspark-to-process-documents-at-massive-scale-with-amazon-sagemaker-studio-and-amazon-emr-serverless/).)

- **A SageMaker Studio domain** named `<stack name>-SageMakerDomain`, in the same private subnets
  as OpenEMR, with no direct internet access (`VpcOnly`) and IAM sign-in. RStudio support is
  turned on.
- **One user profile**, `<stack name>-AnalyticsUser` (for example `OpenemrEcsStack-AnalyticsUser`).
- **An EMR Serverless Spark application** named `<stack name>-EMRServerlessApp` (release
  `emr-7.14.0`) that Studio can send Spark jobs to.
- **Two export S3 buckets**: one for database exports (`sagemaker-rds-export-...`, logical ID
  `S3ExportBucket`) and one for file exports (`sagemaker-efs-export-...`, logical ID
  `EFSExportBucket`).
- **Two export Lambda functions**, `EFStoS3ExportLambda` and `RDStoS3ExportLambda` (their logical
  IDs), described below.
- **An AWS Glue service role** with read and write access to both export buckets.
- **A dedicated customer-managed KMS key** that encrypts the SageMaker domain, its EFS storage,
  shared notebook output, and both export buckets. Data in SageMaker Studio is
  [encrypted at rest](https://docs.aws.amazon.com/whitepapers/latest/sagemaker-studio-admin-best-practices/data-protection.html).

## Step 1: Turn it on

1. In `cdk.json`, set:

   ```json
   "create_serverless_analytics_environment": "true",
   ```

2. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

## Step 2: Open SageMaker Studio

1. Sign in to the AWS console as a user with SageMaker permissions and go to
   `https://<region>.console.aws.amazon.com/sagemaker/home?region=<region>#/studio-landing`
   (replace `<region>` with your Region, such as `us-east-1`). The console's layout changes over
   time; if that page has moved, open **Amazon SageMaker AI** and find **Studio**.

2. Choose the `<stack name>-AnalyticsUser` profile and choose **Open Studio**:

   ![SageMaker Studio landing page with the user profile selector](../images/landing_page.png)

3. In Studio, open **Data** in the side menu to see the EMR Serverless application you can send
   Spark jobs to:

   ![The EMR Serverless application in SageMaker Studio](../images/emr_serverless_cluster.png)

## Step 3: Copy OpenEMR data into S3

Studio works most easily with data in S3, so there are two pipelines that copy OpenEMR's data into
the export buckets. Your Studio profile can run both, check their progress, and read and write both
buckets. Both can run while OpenEMR is live, with no downtime.

### Export files (EFS to S3)

The `EFStoS3ExportLambda` function starts a small Fargate task (0.25 vCPU, 0.5 GB, ARM64) that
copies OpenEMR's entire `sites/` folder (patient documents and site files), mounted read-only,
into the file export bucket with `aws s3 sync`. The function returns the task's ARN.

![The EFS to S3 export pipeline](../images/efs_to_s3_export.png)

1. Find and invoke the function. In the Lambda console, search for `EFStoS3ExportLambda` and
   choose **Test**. Or from the CLI:

   ```bash
   FN=$(aws lambda list-functions \
     --query "Functions[?contains(FunctionName, 'EFStoS3ExportLambda')].FunctionName | [0]" --output text)
   aws lambda invoke --function-name "$FN" response.json && cat response.json
   ```

   It finishes in a few seconds:

   ![A successful invocation of the export function](../images/successful_invocation.png)

2. Wait for the task to finish. How long depends on how many files OpenEMR stores. When it's done,
   the files are in the export bucket:

   ![The OpenEMR sites folder copied into the S3 bucket](../images/contents_trasnferred_to_S3.png)

### Export the database (Aurora to S3)

The `RDStoS3ExportLambda` function starts an
[Aurora export to S3](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/export-cluster-data.html)
of the whole database cluster, encrypted with the analytics KMS key. The exported data arrives as
[Apache Parquet](https://github.com/apache/parquet-format) files, ready for
[Apache Spark](https://spark.apache.org/) jobs on EMR Serverless. The function returns the
`start_export_task` response, which includes the export task's details for checking progress.

![The Aurora to S3 export pipeline](../images/rds_to_s3_export.png)

1. Invoke it the same way (search for `RDStoS3ExportLambda`).
2. Check progress:

   ```bash
   aws rds describe-export-tasks --export-task-identifier aurora-to-s3-openemr-export \
     --query "ExportTasks[0].[Status,PercentProgress]" --output text
   ```

> [!NOTE]
> The database export always uses the same export task name, `aurora-to-s3-openemr-export`.
> Running it again may be rejected because that name has already been used.

## Step 4: Work with the data

Your Studio execution role can read and write both export buckets, so there are many ways to load
the data. One easy option is [Data Wrangler](https://aws.amazon.com/sagemaker/data-wrangler/), in
SageMaker Canvas, where you can pick the S3 bucket and items in a UI. You can also load data
programmatically from a JupyterLab notebook or other apps.

![Data Wrangler browsing the export bucket](../images/data_wrangler.png)

### Use JupyterLab with persistent storage

JupyterLab comes set up with persistent storage on a shared, encrypted EFS volume and common data
analysis tools.

1. In Studio's home screen, choose the **JupyterLab** app in the upper left:

   ![Where the JupyterLab app is in Studio](../images/jupyterlab_app_location.png)

2. Choose **Create JupyterLab space** in the upper right:

   ![The Create JupyterLab space button](../images/create_jupyterlab_space.png)

3. Choose a private or shared space. A private space's storage is only accessible to your user; a
   shared space's storage can be used by several users at once. The stack creates only one user
   profile, so this only matters if you add more profiles and want to share work between them.

   ![JupyterLab space settings](../images/space_settings.png)

4. Choose how much storage you want and choose **Run space**:

   ![Running the JupyterLab space](../images/running_the_space.png)

5. A status message says `Creating JupyterLab application for space: <name>` and then
   `Successfully created JupyterLab app for space: <name>`. This takes about 3–4 minutes.

   ![Creating the JupyterLab application](../images/creating_jupyterlab_application.png)

   ![JupyterLab application created successfully](../images/successfully_created_jupyterlab_application.png)

6. Choose **Open**:

   ![Opening JupyterLab](../images/opening_jupyterlab.png)

   The first start can take 4–5 minutes while the kernel starts in the background. Then you'll see
   JupyterLab:

   ![JupyterLab notebook view](../images/jupyterlab_notebook.png)

Your home directory is on the shared, KMS-encrypted EFS volume, which grows and shrinks
automatically and keeps your data between sessions. To see this, open a terminal in JupyterLab and
run:

```bash
printf "My home directory is on the EFS and here's proof:\n" && df -h
```

![df output showing the home directory on EFS](../images/home_directory_on_shared_encrypted_efs.png)

### Other apps

Studio's home screen lists the apps available to you. Anything that can send jobs to EMR
Serverless is set up to use the stack's Spark application.

![The default apps in SageMaker Studio](../images/default_applications.png)

1. **JupyterLab**

   ![JupyterLab](../images/jupyterlab.png)

2. **RStudio**, which requires an
   [RStudio license](https://docs.aws.amazon.com/sagemaker/latest/dg/rstudio-license.html) from
   Posit (formerly RStudio PBC)

   ![RStudio](../images/rstudio.png)

3. **Canvas**, where Data Wrangler is

   ![Canvas](../images/canvas.png)

4. **Code Editor**

   ![Code Editor](../images/code_editor.png)

5. **Studio Classic**, which reached end of maintenance on December 31, 2024 and may no longer be
   offered

   ![Studio Classic](../images/studio_classic.png)

6. **MLflow**

   ![MLflow](../images/MLFlow.png)

## Control who can use it

All access to the environment goes through AWS IAM. You can remove all or part of it by
restricting IAM permissions. See AWS's
[SageMaker Studio permissions best practices](https://docs.aws.amazon.com/whitepapers/latest/sagemaker-studio-admin-best-practices/permissions-management.html).

To remove the environment, set `create_serverless_analytics_environment` back to `"false"` and
deploy. Copy anything you want to keep out of the export buckets and Studio first. SageMaker won't
delete a domain that still has running apps or spaces, so stop and delete those in Studio before
you deploy.

## If something goes wrong

| Problem | What to do |
|---|---|
| The user profile isn't listed | Check you're in the right Region, and look for `<stack name>-AnalyticsUser`. |
| The file export bucket stays empty | Look up the Fargate task's status in the ECS console (the function returns its ARN) and check its logs. |
| The database export fails | Check `aws rds describe-export-tasks` for the failure reason. A name conflict means the fixed export name was already used. |
| JupyterLab is slow to open | The first start takes several minutes; later starts are faster. |
| Stack deletion is slow | The stack's cleanup step removes SageMaker's EFS file systems and network interfaces before deleting the network. This can take a while. |

## Related

- [Database access](database-access.md): querying the live database instead
- [Optional features](optional-features.md#aurora-machine-learning-with-amazon-bedrock): Bedrock from SQL
- [Costs](../reference/costs.md)
- [Security and compliance](../reference/security-and-compliance.md)
