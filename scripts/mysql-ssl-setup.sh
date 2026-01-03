#!/bin/bash
# Generate SSL certificates for MySQL to simulate RDS SSL requirements
# This script runs in the MySQL container during initialization

set -e

SSL_DIR="/etc/mysql/ssl"
mkdir -p "$SSL_DIR"

# Generate CA private key
openssl genrsa -out "$SSL_DIR/ca-key.pem" 2048

# Generate CA certificate (valid for 10 years)
openssl req -new -x509 -nodes -days 3650 -key "$SSL_DIR/ca-key.pem" \
    -out "$SSL_DIR/ca-cert.pem" \
    -subj "/C=US/ST=State/L=City/O=Test/CN=MySQL-CA"

# Generate server private key
openssl genrsa -out "$SSL_DIR/server-key.pem" 2048

# Generate server certificate signing request
openssl req -new -key "$SSL_DIR/server-key.pem" \
    -out "$SSL_DIR/server.csr" \
    -subj "/C=US/ST=State/L=City/O=Test/CN=mysql-test-ssl"

# Generate server certificate signed by CA (valid for 10 years)
openssl x509 -req -in "$SSL_DIR/server.csr" \
    -CA "$SSL_DIR/ca-cert.pem" \
    -CAkey "$SSL_DIR/ca-key.pem" \
    -CAcreateserial \
    -out "$SSL_DIR/server-cert.pem" \
    -days 3650

# Set proper permissions
chmod 600 "$SSL_DIR"/*.pem
chmod 644 "$SSL_DIR/ca-cert.pem"
chmod 644 "$SSL_DIR/server-cert.pem"

# Clean up CSR
rm -f "$SSL_DIR/server.csr"

echo "MySQL SSL certificates generated successfully"
echo "CA certificate: $SSL_DIR/ca-cert.pem"
echo "Server certificate: $SSL_DIR/server-cert.pem"
echo "Server key: $SSL_DIR/server-key.pem"

