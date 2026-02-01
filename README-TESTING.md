# Local Testing with Docker Compose

This guide explains how to test the OpenEMR container startup locally without deploying to AWS.

## Table of Contents

- [Quick Start](#quick-start)
  - [Basic Test (No SSL)](#basic-test-no-ssl)
  - [SSL Test (Simulates Production RDS)](#ssl-test-simulates-production-rds)
- [File Structure](#file-structure)
- [What This Tests](#what-this-tests)
- [Leadership Recovery](#leadership-recovery)
- [Unique Database Names](#unique-database-names)
- [Environment Variables](#environment-variables)
- [Troubleshooting](#troubleshooting)
  - [Issue: `sqlconf.php` not found](#issue-sqlconfphp-not-found)
  - [Issue: MySQL connection fails](#issue-mysql-connection-fails)
  - [Issue: Certificate download fails](#issue-certificate-download-fails)
  - [Issue: SSL certificate verification fails (SSL test only)](#issue-ssl-certificate-verification-fails-ssl-test-only)
- [Inspecting the Container](#inspecting-the-container)
- [Comparing with ECS](#comparing-with-ecs)
- [Next Steps](#next-steps)
- [Related Documentation](#related-documentation)

## Quick Start

### Basic Test (No SSL)

1. **Start the test environment:**
   ```bash
   ./scripts/test-startup.sh
   ```

2. **Or manually with docker-compose:**
   ```bash
   docker-compose -f compose/docker-compose.test.yml up
   ```

### SSL Test (Simulates Production RDS)

To test with SSL enabled (like production RDS with `require_secure_transport=ON`):

1. **Start the SSL test environment:**
   ```bash
   ./scripts/test-startup-ssl.sh
   ```

2. **Or manually with docker-compose:**
   ```bash
   docker-compose -f compose/docker-compose.test-ssl.yml up
   ```

The SSL test setup:
- Generates SSL certificates for MySQL
- Enables `require-secure-transport=ON` (like RDS)
- Configures OpenEMR to use SSL with the MySQL CA certificate
- More accurately simulates the production environment

3. **View logs:**
   ```bash
   docker-compose -f compose/docker-compose.test.yml logs -f openemr-test
   ```

4. **Stop the test environment:**
   ```bash
   docker-compose -f compose/docker-compose.test.yml down
   ```

## File Structure

The testing files are organized as follows:

```
openemr-on-ecs/
├── compose/
│   ├── docker-compose.test.yml          # Basic test (no SSL)
│   └── docker-compose.test-ssl.yml     # SSL test (simulates RDS)
└── scripts/
    ├── test-startup.sh                  # Basic test runner
    ├── test-startup-ssl.sh              # SSL test runner
    ├── mysql-ssl-setup.sh               # MySQL SSL certificate generator
    └── setup-mysql-ssl-ca.sh           # Helper to extract MySQL CA cert
```

**Note:** The Docker Compose files reference the OpenEMR container image and scripts. The OpenEMR DevOps repository (which contains the Docker configuration) can be found at: https://github.com/openemr/openemr-devops

## What This Tests

The `compose/docker-compose.test.yml` file simulates the exact container startup command used in ECS (from `compute.py`):

1. **Ensures Site Structure:** Checks for the presence of `sites/default` and restores it from the image if missing (essential for fresh EFS volumes).
2. **Directory Creation:** Creates necessary certificate and document directories.
3. **Certificate Download:** Downloads Amazon Root CA1 (for Redis/Valkey) and RDS CA bundle (for MySQL) with retry logic.
4. **Certificate Setup:** Sets proper ownership and permissions for SSL materials.
5. **Initialization:** Runs `openemr.sh` to perform automated setup or upgrades.

## Leadership Recovery

In ECS, multiple tasks might start simultaneously. OpenEMR's `openemr.sh` handles leadership using `sites/docker-leader` and `sites/docker-completed` files. 

If a leadership container fails *before* completion, it may leave a stale `docker-leader` file. The current fix for this is ensuring the lead container succeeds by providing correct SSL materials and environment configuration. If you need to force a reset during testing, you can delete these files from the shared storage (EFS in AWS, or local volumes in Docker).

## Unique Database Names

To avoid collisions with existing data during rapid redeployments, you can specify a unique database name:

```bash
export MYSQL_DATABASE=openemr_v2
./scripts/test-startup.sh
```

In AWS, the database name is always "openemr" (hardcoded for consistency).

## Environment Variables

You can customize the test by setting environment variables:

```bash
export MYSQL_HOST=mysql-test
export MYSQL_ROOT_PASS=testpass
export MYSQL_USER=openemr
export MYSQL_PASS=openemr
export MYSQL_DATABASE=openemr
export SWARM_MODE=yes
export AUTHORITY=yes
export OE_USER=admin
export OE_PASS=pass

./scripts/test-startup.sh
```

## Troubleshooting

### Issue: `sqlconf.php` not found

If you see the error:
```
PHP Warning: require_once(/var/www/localhost/htdocs/openemr/sites/default/sqlconf.php): Failed to open stream
```

This means the sites directory structure hasn't been initialized. The `openemr.sh` script should restore it from `/swarm-pieces/sites` when `SWARM_MODE=yes` and `AUTHORITY=yes`.

**Solution:** Ensure `SWARM_MODE=yes` and `AUTHORITY=yes` are set in the environment.

### Issue: MySQL connection fails

If OpenEMR can't connect to MySQL:

1. Check that MySQL container is running: `docker-compose -f compose/docker-compose.test.yml ps`
2. Check MySQL logs: `docker-compose -f compose/docker-compose.test.yml logs mysql-test`
3. Verify environment variables match between containers

### Issue: Certificate download fails

If certificate downloads fail, check network connectivity:
```bash
docker-compose -f compose/docker-compose.test.yml exec openemr-test curl -I https://www.amazontrust.com/repository/AmazonRootCA1.pem
```

### Issue: SSL certificate verification fails (SSL test only)

If you see SSL certificate verification errors in the SSL test:

1. Ensure MySQL container has finished initializing (check health status)
2. Verify SSL certificates were generated: `docker exec mysql-test-ssl ls -la /etc/mysql/ssl/`
3. Check that the CA certificate is being used correctly

## Inspecting the Container

To inspect the container after startup:

```bash
# Enter the container
docker-compose -f compose/docker-compose.test.yml exec openemr-test sh

# Check if sites directory exists
ls -la /var/www/localhost/htdocs/openemr/sites/default/

# Check if sqlconf.php exists
ls -la /var/www/localhost/htdocs/openemr/sites/default/sqlconf.php

# Check certificate files
ls -la /var/www/localhost/htdocs/openemr/sites/default/documents/certificates/
ls -la /root/certs/mysql/server/
ls -la /root/certs/redis/
```

## Comparing with ECS

The local test environment uses:
- Same Docker image: `openemr/openemr:7.0.5`
- Same startup command from `compute.py`
- Same environment variable structure

Differences:
- Local MySQL instead of RDS Aurora
- No EFS volumes (uses container volumes)
- No AWS Secrets Manager (uses environment variables)
- No SSL termination at ALB (direct container access)

## Next Steps

After testing locally and fixing issues:

1. Update `openemr_ecs/compute.py` if needed
2. Test the changes locally again
3. Deploy to AWS with `cdk deploy`

## Related Documentation

- [Test Results](TEST-RESULTS.md) - Results from local testing
- [Troubleshooting Guide](TROUBLESHOOTING.md) - Common issues and solutions
- [Architecture Documentation](ARCHITECTURE.md) - Understanding the full architecture
