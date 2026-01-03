#!/bin/bash
# Wrapper script that generates SSL certificates before starting MySQL

set -e

SSL_DIR="/etc/mysql/ssl"
mkdir -p "$SSL_DIR"

# Generate SSL certificates if they don't exist
if [ ! -f "$SSL_DIR/ca-cert.pem" ]; then
    echo "Generating SSL certificates for MySQL..."
    
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
    
    # Set proper permissions (MySQL needs to read these as mysql user)
    # The mysql user (UID 999) needs to read these files
    chmod 644 "$SSL_DIR/ca-cert.pem"
    chmod 644 "$SSL_DIR/server-cert.pem"
    chmod 644 "$SSL_DIR/server-key.pem"
    chmod 644 "$SSL_DIR/ca-key.pem"
    
    # Verify the key file is valid
    if ! openssl rsa -in "$SSL_DIR/server-key.pem" -check -noout 2>/dev/null; then
        echo "ERROR: Generated server key is invalid"
        exit 1
    fi
    
    # Clean up CSR
    rm -f "$SSL_DIR/server.csr"
    
    echo "✓ MySQL SSL certificates generated successfully"
fi

# Copy CA cert to volume for OpenEMR to use
if [ -d /mysql-ssl-certs ]; then
    cp "$SSL_DIR/ca-cert.pem" /mysql-ssl-certs/ca-cert.pem 2>/dev/null || true
fi

# Call the original entrypoint - it will handle the mysqld/mariadbd command
exec /usr/local/bin/docker-entrypoint.sh "$@"

