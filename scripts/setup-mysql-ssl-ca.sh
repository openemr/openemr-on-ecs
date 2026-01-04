#!/bin/bash
# Extract the CA certificate from MySQL SSL setup for use by OpenEMR
# This creates a CA certificate file that OpenEMR can use to verify MySQL SSL connections

set -e

SSL_DIR="./mysql-ssl-ca"
mkdir -p "$SSL_DIR"

# Wait for MySQL container to generate SSL certificates
echo "Waiting for MySQL container to generate SSL certificates..."
sleep 5

# Copy CA certificate from MySQL container
if docker ps | grep -q mysql-test-ssl; then
    echo "Copying CA certificate from MySQL container..."
    docker cp mysql-test-ssl:/etc/mysql/ssl/ca-cert.pem "$SSL_DIR/mysql-ca.pem" || {
        echo "Warning: Could not copy CA certificate from container."
        echo "The MySQL container may still be initializing."
        echo "You can manually copy it later with:"
        echo "  docker cp mysql-test-ssl:/etc/mysql/ssl/ca-cert.pem $SSL_DIR/mysql-ca.pem"
    }
    
    if [ -f "$SSL_DIR/mysql-ca.pem" ]; then
        echo "CA certificate copied to $SSL_DIR/mysql-ca.pem"
        echo "This certificate should match what OpenEMR uses for SSL verification."
    fi
else
    echo "MySQL container not running. Start it first with:"
    echo "  docker-compose -f docker-compose.test-ssl.yml up -d mysql-test-ssl"
fi

