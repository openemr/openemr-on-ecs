#!/bin/bash
# Test script to simulate ECS container startup locally using docker-compose

set -e

echo "========================================="
echo "Testing OpenEMR Container Startup"
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
export MYSQL_HOST=${MYSQL_HOST:-mysql-test}
export MYSQL_ROOT_USER=${MYSQL_ROOT_USER:-root}
export MYSQL_ROOT_PASS=${MYSQL_ROOT_PASS:-testpass}
export MYSQL_USER=${MYSQL_USER:-openemr}
export MYSQL_PASS=${MYSQL_PASS:-openemr}
export MYSQL_DATABASE=${MYSQL_DATABASE:-openemr}
export SWARM_MODE=${SWARM_MODE:-yes}
export AUTHORITY=${AUTHORITY:-yes}

echo "Environment variables:"
echo "  MYSQL_HOST=$MYSQL_HOST"
echo "  MYSQL_ROOT_USER=$MYSQL_ROOT_USER"
echo "  MYSQL_DATABASE=$MYSQL_DATABASE"
echo "  SWARM_MODE=$SWARM_MODE"
echo "  AUTHORITY=$AUTHORITY"
echo ""

# Stop any existing containers
echo "Cleaning up any existing containers..."
$COMPOSE_CMD -f compose/docker-compose.test.yml down -v 2>/dev/null || true

# Start the containers
echo ""
echo "Starting containers..."
$COMPOSE_CMD -f compose/docker-compose.test.yml up -d mysql-test

# Wait for MySQL to be ready
echo "Waiting for MySQL to be ready..."
sleep 5

# Start OpenEMR container and follow logs
echo ""
echo "Starting OpenEMR container and following logs..."
echo "Press Ctrl+C to stop"
echo "========================================="
echo ""

$COMPOSE_CMD -f compose/docker-compose.test.yml up --build openemr-test

