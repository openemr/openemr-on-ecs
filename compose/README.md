# Docker Compose Test Configurations

This directory contains Docker Compose configurations for local testing of OpenEMR containers before deploying to AWS ECS.

## Table of Contents

- [Overview](#overview)
- [Test Configurations](#test-configurations)
- [Usage](#usage)
- [Prerequisites](#prerequisites)
- [Testing Scenarios](#testing-scenarios)
- [Troubleshooting](#troubleshooting)

## Overview

These Docker Compose files allow you to test OpenEMR container startup locally, ensuring that:
- Container startup scripts work correctly
- Environment variables are properly configured
- SSL/TLS certificate handling functions as expected
- Container health checks pass
- Application initializes successfully

**Important**: These test configurations use the **same startup script** as production ECS deployments to ensure consistency.

## Test Configurations

### `docker-compose.test.yml`

**Purpose**: Basic OpenEMR container testing without SSL/TLS certificates.

**Features**:
- Tests core container startup
- Validates environment variable configuration
- Verifies database and cache connectivity (mock)
- Tests EFS volume mounting (local directories)
- Validates health check endpoints

**Use Case**: Quick validation of container functionality without SSL complexity.

### `docker-compose.test-ssl.yml`

**Purpose**: OpenEMR container testing with SSL/TLS certificates enabled.

**Features**:
- Tests SSL certificate handling
- Validates self-signed certificate generation
- Verifies certificate validation for MySQL and Redis connections
- Tests SSL certificate storage on EFS
- Includes MySQL and Redis containers with SSL enabled

**Use Case**: Testing production-like SSL configuration before deployment.

## Usage

### Basic Testing (No SSL)

```bash
# Start containers
docker-compose -f compose/docker-compose.test.yml up

# Run in detached mode
docker-compose -f compose/docker-compose.test.yml up -d

# View logs
docker-compose -f compose/docker-compose.test.yml logs -f

# Stop containers
docker-compose -f compose/docker-compose.test.yml down
```

### SSL Testing

```bash
# Start containers with SSL
docker-compose -f compose/docker-compose.test-ssl.yml up

# Run in detached mode
docker-compose -f compose/docker-compose.test-ssl.yml up -d

# View logs
docker-compose -f compose/docker-compose.test-ssl.yml logs -f

# Stop containers
docker-compose -f compose/docker-compose.test-ssl.yml down
```

### Using Helper Scripts

The project includes helper scripts for easier testing:

```bash
# Basic test (from project root)
./scripts/test-startup.sh

# SSL test (from project root)
./scripts/test-startup-ssl.sh
```

## Prerequisites

- Docker and Docker Compose installed
- Sufficient disk space (containers require several GB)
- Ports 80 and 443 available (or modify port mappings)
- Network access for downloading OpenEMR image (if not cached)

## Testing Scenarios

### Scenario 1: Container Startup Validation

**Goal**: Verify container starts and initializes correctly.

**Steps**:
1. Start containers: `docker-compose -f compose/docker-compose.test.yml up`
2. Monitor logs for startup script execution
3. Verify health check endpoint responds: `curl http://localhost/`
4. Check container logs for any errors

**Success Criteria**:
- Container starts without errors
- Startup script completes successfully
- Health check endpoint returns 200 OK
- No error messages in logs

### Scenario 2: SSL Certificate Testing

**Goal**: Verify SSL certificate handling works correctly.

**Steps**:
1. Start SSL containers: `docker-compose -f compose/docker-compose.test-ssl.yml up`
2. Verify SSL certificates are generated/loaded
3. Check MySQL and Redis SSL connections
4. Verify certificates are stored on EFS mount

**Success Criteria**:
- SSL certificates are present in expected locations
- MySQL connection uses SSL
- Redis connection uses SSL
- No SSL-related errors in logs

### Scenario 3: Environment Variable Testing

**Goal**: Verify environment variables are correctly passed and used.

**Steps**:
1. Modify environment variables in docker-compose file
2. Start containers
3. Verify variables are accessible in container
4. Check application configuration reflects variables

**Success Criteria**:
- Environment variables are set correctly
- Application reads variables as expected
- Configuration matches environment variables

## Troubleshooting

### Container Won't Start

**Issue**: Container exits immediately or fails to start.

**Solutions**:
- Check Docker logs: `docker-compose logs`
- Verify Docker has sufficient resources (CPU, memory, disk)
- Ensure ports are not already in use
- Check Docker daemon is running: `docker ps`

### Health Check Fails

**Issue**: Health check endpoint doesn't respond.

**Solutions**:
- Wait longer (containers may need 2-5 minutes to start)
- Check container logs for errors: `docker-compose logs openemr`
- Verify network connectivity between containers
- Check if application is still initializing

### SSL Certificate Errors

**Issue**: SSL-related errors in logs.

**Solutions**:
- Ensure SSL certificates are in expected mount points
- Verify certificate file permissions
- Check MySQL/Redis containers are running with SSL enabled
- Review certificate validation logic in startup script

### Volume Mount Issues

**Issue**: EFS volumes (local directories) not accessible.

**Solutions**:
- Verify local directories exist and are readable
- Check directory permissions
- Ensure Docker has access to mounted directories
- Review volume mount paths in docker-compose file

## Configuration Files

### `docker-compose.test.yml`

Key components:
- **openemr**: Main OpenEMR application container
- **Environment variables**: Database, cache, and application configuration
- **Volume mounts**: Local directories simulating EFS volumes
- **Health checks**: HTTP endpoint validation

### `docker-compose.test-ssl.yml`

Key components:
- **openemr**: Main OpenEMR application container with SSL
- **mysql**: MySQL container with SSL enabled
- **redis**: Redis container with SSL enabled
- **SSL certificates**: Self-signed certificates for testing
- **Volume mounts**: Including SSL certificate storage

## Related Documentation

- [README-TESTING.md](../README-TESTING.md) - Comprehensive local testing guide
- [scripts/README.md](../scripts/README.md) - Helper script documentation
- [DEPLOYMENT-RELIABILITY.md](../DEPLOYMENT-RELIABILITY.md) - Production deployment reliability

## Notes

- These configurations are for **testing only**, not production use
- SSL certificates in test-ssl.yml are self-signed and should not be used in production
- Test data is ephemeral and will be lost when containers are stopped
- For production deployment, use `cdk deploy` to create AWS infrastructure

