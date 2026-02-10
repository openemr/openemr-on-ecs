# Documentation Assets

This directory contains documentation, images, diagrams, and visual documentation used throughout the project.

## Table of Contents

- [Overview](#overview)
- [Documents](#documents)
- [Image Categories](#image-categories)
- [Usage in Documentation](#usage-in-documentation)
- [Adding New Images](#adding-new-images)

## Overview

The `docs/` directory contains documentation and visual assets that enhance the project, including:
- Cost estimates and planning guides
- CDK Nag suppressions reference
- Screenshots of the deployment process
- Architecture diagrams
- Configuration examples
- UI walkthroughs
- Testing and validation examples

## Documents

- [cost-estimate.md](cost-estimate.md) - AWS monthly cost estimates for QA, Staging, and Production environments
- [cdk-nag-suppressions.md](cdk-nag-suppressions.md) - CDK Nag suppression rules and justifications

## Image Categories

### Architecture Diagrams

- `Architecture.png` - High-level system architecture
- `sagemaker_studio_architecture.png` - Serverless analytics environment architecture

### Deployment Screenshots

- `cdk_deploy.png` - CDK deployment terminal output
- `landing_page.png` - OpenEMR landing page
- `OpenEMR.png` - OpenEMR application interface
- `OpenEMR_Auth.png` - Authentication screen

### Load Balancer and Metrics

- `load_balancer_metrics.png` - ALB metrics dashboard
- `load_balancer_metrics_2.png` - Additional ALB metrics
- `load_testing_cpu_and_memory_metrics.png` - Performance metrics during load testing

### Database and Storage

- `RDS_console_writer_instance.png` - RDS Aurora writer instance
- `rds_metrics.png` - RDS performance metrics
- `accessing_db_secret.png` - Accessing database secret in Secrets Manager
- `accessing_the_database_remotely.png` - Remote database access setup

### API Integration

- `RegisterAPIClient.png` - API client registration
- `APIClientsMenu.png` - API clients menu
- `FindingCorrectClientID.png` - Finding client ID
- `EnableClientButton.png` - Enabling API client
- `ClientEnabled.png` - Enabled client confirmation
- `RunningPythonScript.png` - Running API test script
- `1stOutputFromScript.png` - First script output
- `OutputFromScriptClientID.png` - Client ID output
- `2ndOutputFromScript.png` - Second script output
- `CodeInTerminal.png` - Authorization code in terminal
- `Success.png` - Successful API authentication
- `LoginPrompt.png` - OAuth login prompt
- `SelectPatient.png` - Patient selection for authorization
- `TopOfAuthorizationPage.png` - Authorization page header
- `ClickAuthorize.png` - Authorize button
- `403Forbidden.png` - 403 error page (expected in OAuth flow)

### SageMaker and Analytics

- `create_jupyterlab_space.png` - Creating JupyterLab space
- `space_settings.png` - Space configuration
- `running_the_space.png` - Running space status
- `successfully_created_jupyterlab_application.png` - JupyterLab app creation
- `jupyterlab_app_location.png` - App location in SageMaker
- `opening_jupyterlab.png` - Opening JupyterLab interface
- `jupyterlab.png` - JupyterLab interface
- `jupyterlab_notebook.png` - JupyterLab notebook example
- `home_directory_on_shared_encrypted_efs.png` - EFS mount verification
- `create_jupyterlab_space.png` - Space creation workflow
- `default_applications.png` - Default SageMaker applications
- `canvas.png` - SageMaker Canvas interface
- `code_editor.png` - Code Editor interface
- `studio_classic.png` - Studio Classic interface
- `rstudio.png` - RStudio interface
- `MLFlow.png` - MLFlow interface
- `data_wrangler.png` - Data Wrangler interface
- `emr_serverless_cluster.png` - EMR Serverless cluster

### Data Export and Transfer

- `rds_to_s3_export.png` - RDS export to S3
- `efs_to_s3_export.png` - EFS export to S3
- `contents_trasnferred_to_S3.png` - S3 transfer confirmation

### Email Configuration

- `activating_email_credentials.png` - Email credentials activation
- `testemail.php_output.png` - Email test output

### Monitoring and Metrics

- `elasticache_metrics.png` - ElastiCache/Valkey metrics
- `rds_metrics.png` - RDS performance metrics

### Terminal and CLI

- `ConsoleOutputALBDNS.png` - CloudFormation console output
- `TerminalOutputALBDNS.png` - Terminal ALB DNS output
- `retrieve_secret_value.png` - Retrieving secret value
- `SecretsManager.png` - AWS Secrets Manager interface
- `username_and_password.png` - Credentials display
- `navigate_to_database.png` - Database navigation
- `copy_name_of_ecs_cluster.png` - Copying ECS cluster name
- `run_port_forwarding_script.png` - Port forwarding script execution

### Patient Management

- `AddNewPatient.png` - Add new patient interface
- `CreateNewPatient.png` - Create patient form

## Usage in Documentation

Images are referenced in markdown files using relative paths:

```markdown
![Alt text](./docs/images/image-name.png)
```

**Examples**:
- In `README.md`: `![Architecture](./docs/images/Architecture.png)`
- In `DETAILS.md`: `![Load Test Results](./docs/images/load_testing_cpu_and_memory_metrics.png)`

## Adding New Images

When adding new images:

1. **Naming Convention**: Use descriptive, lowercase names with hyphens:
   - ✅ Good: `api-client-setup.png`
   - ❌ Bad: `Image1.png`, `screenshot_2024.png`

2. **Format**: Prefer PNG for screenshots, SVG for diagrams (if supported)

3. **Optimization**: Compress images to reduce file size while maintaining quality

4. **Alt Text**: Always include descriptive alt text in markdown references

5. **Documentation**: Update this README when adding new image categories

## Organization Tips

- Group related images by feature/functionality
- Use consistent naming patterns within categories
- Keep file sizes reasonable (< 500KB per image when possible)
- Update relevant documentation files when adding images

## Related Documentation

- [README.md](../README.md) - Main project documentation
- [DETAILS.md](../DETAILS.md) - Detailed configuration guide (uses many images)
- [GETTING-STARTED.md](../GETTING-STARTED.md) - Beginner guide (may reference images)

