#!/bin/bash
# Test script to simulate ECS container startup WITH SSL (like production RDS)

set -e

echo "========================================="
echo "Testing OpenEMR Container Startup WITH SSL"
echo "Simulating RDS require_secure_transport=ON"
echo "========================================="
echo ""

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null && ! command -v docker &> /dev/null; then
    echo "Error: docker-compose or docker is not installed"
    exit 1
fi

# Use docker compose (v2) if available, otherwise docker-compose (v1)
if docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

echo "Using: $COMPOSE_CMD"
echo ""

# Set default environment variables if not already set
export MYSQL_HOST=${MYSQL_HOST:-mysql-test-ssl}
export MYSQL_ROOT_USER=${MYSQL_ROOT_USER:-root}
export MYSQL_ROOT_PASS=${MYSQL_ROOT_PASS:-testpass}
export MYSQL_USER=${MYSQL_USER:-openemr}
export MYSQL_PASS=${MYSQL_PASS:-openemr}
export MYSQL_DATABASE=${MYSQL_DATABASE:-openemr}
export MYSQL_SSL=ON
export SWARM_MODE=${SWARM_MODE:-yes}
export AUTHORITY=${AUTHORITY:-yes}

echo "Environment variables:"
echo "  MYSQL_HOST=$MYSQL_HOST"
echo "  MYSQL_ROOT_USER=$MYSQL_ROOT_USER"
echo "  MYSQL_DATABASE=$MYSQL_DATABASE"
echo "  MYSQL_SSL=$MYSQL_SSL (SSL enabled like production)"
echo "  SWARM_MODE=$SWARM_MODE"
echo "  AUTHORITY=$AUTHORITY"
echo ""

# Stop any existing containers
echo "Cleaning up any existing containers..."
$COMPOSE_CMD -f compose/docker-compose.test-ssl.yml down -v 2>/dev/null || true

# Start MySQL first to generate SSL certificates
echo ""
echo "Starting MySQL container with SSL enabled..."
$COMPOSE_CMD -f compose/docker-compose.test-ssl.yml up -d mysql-test-ssl

# Wait for MySQL to be ready and SSL certificates to be generated
echo "Waiting for MySQL to initialize and generate SSL certificates..."
echo "This may take 30-60 seconds..."
sleep 10

# Check if MySQL is healthy
echo "Checking MySQL health..."
for i in {1..30}; do
    if docker exec mysql-test-ssl mysqladmin ping -h localhost -u root -ptestpass --silent 2>/dev/null; then
        echo "MySQL is ready!"
        break
    fi
    echo "Waiting for MySQL... ($i/30)"
    sleep 2
done

# Verify SSL certificates exist
if docker exec mysql-test-ssl test -f /etc/mysql/ssl/ca-cert.pem; then
    echo "✓ MySQL SSL certificates generated successfully"
else
    echo "⚠ Warning: MySQL SSL certificates not found. MySQL may still be initializing."
fi

# Start OpenEMR container and follow logs
echo ""
echo "Starting OpenEMR container with SSL enabled..."
echo "Press Ctrl+C to stop"
echo "========================================="
echo ""

$COMPOSE_CMD -f compose/docker-compose.test-ssl.yml up openemr-test-ssl

