# Documentation images

This folder holds the screenshots and illustrations used across the
documentation. This page catalogs every image so contributors can find,
reuse, or replace one, and explains how to reference and add images.

## Contents

- [Architecture diagrams](#architecture-diagrams)
- [Image catalog](#image-catalog)
- [Using images in documentation](#using-images-in-documentation)
- [Adding new images](#adding-new-images)

## Architecture diagrams

The architecture diagrams aren't stored here. They live in
[`diagrams/`](../../diagrams/README.md) and are generated from the CDK code by
[`diagrams/generate.py`](../../diagrams/generate.py):

- [`architecture.png`](../../diagrams/architecture.png): high-level system
  architecture
- [`architecture-full.png`](../../diagrams/architecture-full.png): full
  resource-level diagram

## Image catalog

### Analytics architecture

- [`sagemaker_studio_architecture.png`](sagemaker_studio_architecture.png): serverless analytics environment architecture

### Backup Manager TUI

- [`backup_tui_screenshot_1.png`](backup_tui_screenshot_1.png): backup list view
- [`backup_tui_screenshot_2.png`](backup_tui_screenshot_2.png): backup detail view
- [`backup_tui_screenshot_3.png`](backup_tui_screenshot_3.png): restore confirmation
- [`backup_tui_screenshot_4.png`](backup_tui_screenshot_4.png): restore monitoring

### Deployment

- [`cdk_deploy.png`](cdk_deploy.png): CDK deployment terminal output
- [`landing_page.png`](landing_page.png): OpenEMR landing page
- [`OpenEMR.png`](OpenEMR.png): OpenEMR application interface
- [`OpenEMR_Auth.png`](OpenEMR_Auth.png): authentication screen

### Load balancer and load testing metrics

- [`load_balancer_metrics.png`](load_balancer_metrics.png): load balancer metrics dashboard
- [`load_balancer_metrics_2.png`](load_balancer_metrics_2.png): additional load balancer metrics
- [`load_testing_cpu_and_memory_metrics.png`](load_testing_cpu_and_memory_metrics.png): performance metrics during load testing

### Database, cache, and storage

- [`RDS_console_writer_instance.png`](RDS_console_writer_instance.png): RDS Aurora writer instance
- [`rds_metrics.png`](rds_metrics.png): RDS performance metrics
- [`elasticache_metrics.png`](elasticache_metrics.png): ElastiCache (Valkey) metrics
- [`accessing_db_secret.png`](accessing_db_secret.png): accessing the database secret in Secrets Manager
- [`accessing_the_database_remotely.png`](accessing_the_database_remotely.png): remote database access setup

### API integration

- [`RegisterAPIClient.png`](RegisterAPIClient.png): API client registration
- [`APIClientsMenu.png`](APIClientsMenu.png): API clients menu
- [`FindingCorrectClientID.png`](FindingCorrectClientID.png): finding the client ID
- [`EnableClientButton.png`](EnableClientButton.png): enabling the API client
- [`ClientEnabled.png`](ClientEnabled.png): enabled client confirmation
- [`RunningPythonScript.png`](RunningPythonScript.png): running the API test script
- [`1stOutputFromScript.png`](1stOutputFromScript.png): first script output
- [`OutputFromScriptClientID.png`](OutputFromScriptClientID.png): client ID output
- [`2ndOutputFromScript.png`](2ndOutputFromScript.png): second script output
- [`CodeInTerminal.png`](CodeInTerminal.png): authorization code in the terminal
- [`Success.png`](Success.png): successful API authentication
- [`LoginPrompt.png`](LoginPrompt.png): OAuth login prompt
- [`SelectPatient.png`](SelectPatient.png): patient selection for authorization
- [`TopOfAuthorizationPage.png`](TopOfAuthorizationPage.png): authorization page header
- [`ClickAuthorize.png`](ClickAuthorize.png): Authorize button
- [`403Forbidden.png`](403Forbidden.png): 403 error page (expected in the OAuth flow)

### SageMaker and analytics

- [`create_jupyterlab_space.png`](create_jupyterlab_space.png): creating a JupyterLab space
- [`space_settings.png`](space_settings.png): space configuration
- [`running_the_space.png`](running_the_space.png): running space status
- [`successfully_created_jupyterlab_application.png`](successfully_created_jupyterlab_application.png): JupyterLab application created
- [`creating_jupyterlab_application.png`](creating_jupyterlab_application.png): creating the JupyterLab application
- [`jupyterlab_app_location.png`](jupyterlab_app_location.png): app location in SageMaker
- [`opening_jupyterlab.png`](opening_jupyterlab.png): opening the JupyterLab interface
- [`jupyterlab.png`](jupyterlab.png): JupyterLab interface
- [`jupyterlab_notebook.png`](jupyterlab_notebook.png): JupyterLab notebook example
- [`home_directory_on_shared_encrypted_efs.png`](home_directory_on_shared_encrypted_efs.png): EFS mount verification
- [`default_applications.png`](default_applications.png): default SageMaker applications
- [`canvas.png`](canvas.png): SageMaker Canvas interface
- [`code_editor.png`](code_editor.png): Code Editor interface
- [`studio_classic.png`](studio_classic.png): Studio Classic interface
- [`rstudio.png`](rstudio.png): RStudio interface
- [`MLFlow.png`](MLFlow.png): MLflow interface
- [`data_wrangler.png`](data_wrangler.png): Data Wrangler interface
- [`emr_serverless_cluster.png`](emr_serverless_cluster.png): EMR Serverless cluster

### Data export and transfer

- [`successful_invocation.png`](successful_invocation.png): successful invocation of an export Lambda function
- [`rds_to_s3_export.png`](rds_to_s3_export.png): RDS export to S3
- [`efs_to_s3_export.png`](efs_to_s3_export.png): EFS export to S3
- [`contents_trasnferred_to_S3.png`](contents_trasnferred_to_S3.png): S3 transfer confirmation

### Email

- [`activating_email_credentials.png`](activating_email_credentials.png): email credentials activation
- [`testemail.php_output.png`](testemail.php_output.png): email test output

### Terminal, console, and secrets

- [`ConsoleOutputALBDNS.png`](ConsoleOutputALBDNS.png): CloudFormation console output
- [`TerminalOutputALBDNS.png`](TerminalOutputALBDNS.png): load balancer DNS name in terminal output
- [`retrieve_secret_value.png`](retrieve_secret_value.png): retrieving a secret value
- [`SecretsManager.png`](SecretsManager.png): AWS Secrets Manager interface
- [`username_and_password.png`](username_and_password.png): credentials display
- [`navigate_to_database.png`](navigate_to_database.png): database navigation
- [`copy_name_of_ecs_cluster.png`](copy_name_of_ecs_cluster.png): copying the ECS cluster name
- [`run_port_forwarding_script.png`](run_port_forwarding_script.png): port forwarding script execution

### Patient management

- [`AddNewPatient.png`](AddNewPatient.png): add new patient interface
- [`CreateNewPatient.png`](CreateNewPatient.png): create patient form

## Using images in documentation

Reference images with a relative path from the Markdown file you're editing,
and always include descriptive alt text:

| Where the page lives | Example |
|---|---|
| Repository root (such as `README.md`) | `![OpenEMR landing page](docs/images/landing_page.png)` |
| `docs/` | `![OpenEMR landing page](images/landing_page.png)` |
| `docs/get-started/`, `docs/guides/`, `docs/reference/`, `docs/maintainers/` | `![OpenEMR landing page](../images/landing_page.png)` |
| `scripts/backup-tui/` | `![Backup list view](../../docs/images/backup_tui_screenshot_1.png)` |

Architecture diagrams use the same rule against `diagrams/`; for example, from
`docs/reference/` use `../../diagrams/architecture.png`.

## Adding new images

1. **Name it descriptively.** Use lowercase, descriptive names such as
   `api-client-setup.png`, not `Image1.png` or `screenshot_2024.png`.
2. **Pick the format.** Prefer PNG for screenshots and SVG for diagrams where
   supported.
3. **Keep it small.** Compress images to reduce file size while keeping them
   readable; aim for less than 500 KB per image where possible.
4. **Write alt text.** Every Markdown reference needs descriptive alt text.
5. **Update this catalog.** Add the image under the right category, or add a
   new category, and update the pages that use it.
