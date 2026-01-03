# Troubleshooting Guide

This guide helps you diagnose and resolve common issues with the OpenEMR on AWS Fargate deployment.

## Table of Contents
- [Database Connection Issues](#database-connection-issues)
- [Container Health Check Failures](#container-health-check-failures)
- [SSL/TLS Certificate Issues](#ssltls-certificate-issues)
- [Performance Issues](#performance-issues)
- [Deployment Failures](#deployment-failures)

## Database Connection Issues

### Error: "ERROR 3159 (HY000): Connections using insecure transport are prohibited"

**Problem:** The Aurora MySQL database has `require_secure_transport` enabled, but OpenEMR is attempting to connect without SSL.

**Solution:** This has been fixed in the latest version. The deployment automatically:
1. Downloads the RDS CA certificate bundle
2. Copies it to the OpenEMR certificates directory
3. Configures OpenEMR to use SSL for all database connections

**Verification:**
- Check CloudWatch logs for the container to ensure the certificate was downloaded
- Verify the certificate exists at: `/var/www/localhost/htdocs/openemr/sites/default/documents/certificates/mysql-ca`
- Check that OpenEMR is connecting successfully (look for successful database connection messages in logs)

**If the issue persists:**
1. Ensure the certificate was downloaded successfully (check container startup logs)
2. Verify the certificate file has correct permissions (744)
3. Check that the `require_secure_transport` parameter is set to "ON" in the RDS parameter group
4. Review OpenEMR logs for any SSL-related errors

### Database Connection Timeout

**Problem:** OpenEMR cannot connect to the database, timing out after several attempts. Container logs show repeated retry messages like:

```
[2026-01-03 18:44:04] Database not ready yet, waiting 2s before retry (attempt 1/30)...
[2026-01-03 18:44:06] Database not ready yet, waiting 4s before retry (attempt 2/30)...
[2026-01-03 18:44:10] Database not ready yet, waiting 8s before retry (attempt 3/30)...
```

**Possible Causes:**

1. **Aurora Serverless v2 min_capacity=0 (MOST COMMON)**
   - Database scaled down to completely stopped
   - Takes 3-5 minutes to scale back up
   - ECS container times out before database is available
   - **Solution:** See [docs/AURORA-CAPACITY-CONFIGURATION.md](docs/AURORA-CAPACITY-CONFIGURATION.md)
   - Run diagnostic: `./scripts/diagnose_db_connectivity.sh`

2. **Security group rules blocking database access**
   - Verify security group rules allow traffic from ECS tasks to RDS on port 3306
   - Check both ingress and egress rules

3. **Database is still initializing**
   - Check RDS cluster status in the AWS Console
   - Wait for status to show "available"

4. **Incorrect database credentials**
   - Verify database credentials in AWS Secrets Manager
   - Ensure secret is accessible by ECS task role

5. **Network connectivity issues**
   - Review VPC routing and NAT gateway configuration
   - Verify database is in private subnet with proper routing

**Quick Diagnosis:**

```bash
# Run the diagnostic tool
./scripts/diagnose_db_connectivity.sh
```

This will check:
- RDS cluster status
- Current min/max capacity settings
- Security group rules
- Recent connection logs

If the tool reports "Min capacity is 0", this is likely your issue. Change to 0.5+ in `openemr_ecs/database.py` and redeploy.

## Container Health Check Failures

### Container Failing Health Checks

**Problem:** ECS tasks are failing health checks and being replaced.

**Common Causes:**
1. Application not responding on the expected port
2. Health check endpoint returning non-200 status
3. Container taking too long to start
4. Resource constraints (CPU/memory)

**Solution:**
1. Check CloudWatch logs for application errors
2. Verify the health check configuration matches your application
3. Increase health check start period if container needs more time to initialize
4. Review container resource allocation (CPU/memory)

**Health Check Configuration:**
- Current health check: `curl -f http://localhost:80/swagger || exit 1`
- Start period: 300 seconds (5 minutes)
- Interval: 120 seconds (2 minutes)

## SSL/TLS Certificate Issues

### Certificate Not Found Errors

**Problem:** Container fails to start due to missing SSL certificates.

**Solution:**
1. Verify the SSL materials Lambda function executed successfully
2. Check EFS file system is properly mounted
3. Review the one-time SSL setup Lambda logs
4. Ensure the EFS volume has the correct permissions

### Certificate Expiration

**Problem:** SSL certificates expire, causing connection failures.

**Solution:**
- The deployment automatically regenerates SSL materials every 2 days (configurable)
- Check the EventBridge rule for SSL maintenance
- Verify the SSL maintenance Lambda has proper IAM permissions

## Performance Issues

### High CPU/Memory Utilization

**Problem:** Containers are using excessive CPU or memory resources.

**Solution:**
1. Review autoscaling configuration in `cdk.json`
2. Adjust `openemr_service_fargate_cpu_autoscaling_percentage` and `openemr_service_fargate_memory_autoscaling_percentage`
3. Consider increasing minimum task count if load is consistently high
4. Review application-level optimizations (caching, query optimization)

### Slow Database Queries

**Problem:** Database queries are slow, affecting application performance.

**Solution:**
1. Enable Performance Insights on the RDS cluster (already enabled)
2. Review slow query logs in CloudWatch
3. Check RDS ACU utilization and consider scaling up
4. Review database indexes and query optimization

## Deployment Failures

### CDK Deployment Fails

**Problem:** `cdk deploy` fails with various errors.

**Common Issues:**
1. **IAM Permissions:** Ensure your AWS credentials have sufficient permissions
2. **Service Limits:** Check for AWS service limits (VPCs, NAT gateways, etc.)
3. **Resource Conflicts:** Verify no naming conflicts with existing resources
4. **Region Availability:** Ensure all required services are available in your region

**Solution:**
1. Review CloudFormation stack events for specific error messages
2. Check AWS Service Quotas for any limit issues
3. Verify all prerequisites are met (see README.md)
4. Review CDK synthesis output: `cdk synth`

### Stack Update Failures

**Problem:** Stack updates fail, leaving resources in an inconsistent state.

**Solution:**
1. Review CloudFormation rollback events
2. Check for resources that cannot be updated in place
3. Consider destroying and recreating the stack if necessary
4. Always test updates in a non-production environment first

## Getting Help

If you encounter issues not covered in this guide:

1. **Check Logs:**
   - CloudWatch Logs for ECS tasks
   - CloudWatch Logs for Lambda functions
   - RDS logs (if enabled)

2. **Review Documentation:**
   - [README.md](README.md) - Quick start and overview
   - [Local Testing Guide](README-TESTING.md) - Test container startup locally before deploying
   - [DETAILS.md](DETAILS.md) - Detailed configuration options
   - [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)

3. **Community Support:**
   - OpenEMR Community: https://community.open-emr.org/
   - GitHub Issues: https://github.com/openemr/host-openemr-on-aws-fargate

4. **AWS Support:**
   - AWS Support Center (if you have a support plan)
   - AWS Forums

## Common Error Messages and Solutions

### "Resource limit exceeded"
- **Solution:** Request limit increases in AWS Service Quotas or reduce resource usage

### "InvalidParameterException"
- **Solution:** Check parameter values in `cdk.json` match expected formats

### "AccessDenied"
- **Solution:** Verify IAM permissions for the AWS account/user

### "ResourceNotFoundException"
- **Solution:** Ensure all dependencies are created before dependent resources

