#!/bin/bash
# Generate SSL certificates for Redis to simulate ElastiCache Valkey TLS requirements
# This script runs in the Redis container during initialization

set -e

SSL_DIR="/etc/redis/ssl"
mkdir -p "$SSL_DIR"

# Generate CA private key
openssl genrsa -out "$SSL_DIR/ca-key.pem" 2048

# Generate CA certificate (valid for 10 years)
openssl req -new -x509 -nodes -days 3650 -key "$SSL_DIR/ca-key.pem" \
    -out "$SSL_DIR/ca-cert.pem" \
    -subj "/C=US/ST=State/L=City/O=Test/CN=Redis-CA"

# Generate server private key
openssl genrsa -out "$SSL_DIR/server-key.pem" 2048

# Generate server certificate signing request
openssl req -new -key "$SSL_DIR/server-key.pem" \
    -out "$SSL_DIR/server.csr" \
    -subj "/C=US/ST=State/L=City/O=Test/CN=redis-test-ssl"

# Generate server certificate signed by CA (valid for 10 years)
openssl x509 -req -in "$SSL_DIR/server.csr" \
    -CA "$SSL_DIR/ca-cert.pem" \
    -CAkey "$SSL_DIR/ca-key.pem" \
    -CAcreateserial \
    -out "$SSL_DIR/server-cert.pem" \
    -days 3650

# Set proper permissions
chmod 644 "$SSL_DIR/ca-cert.pem"
chmod 644 "$SSL_DIR/server-cert.pem"
chmod 644 "$SSL_DIR/server-key.pem"
chmod 644 "$SSL_DIR/ca-key.pem"

# Clean up CSR
rm -f "$SSL_DIR/server.csr"

# Copy CA cert to volume for OpenEMR to use
if [ -d /redis-ssl-certs ]; then
    cp "$SSL_DIR/ca-cert.pem" /redis-ssl-certs/ca-cert.pem 2>/dev/null || true
fi

echo "✓ Redis SSL certificates generated successfully"
echo "CA certificate: $SSL_DIR/ca-cert.pem"
echo "Server certificate: $SSL_DIR/server-cert.pem"
echo "Server key: $SSL_DIR/server-key.pem"

