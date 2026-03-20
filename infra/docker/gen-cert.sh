#!/bin/bash
# Generate self-signed TLS certificates for development

set -e

CERT_DIR="./certs"
DAYS=365

# Create certs directory if it doesn't exist
mkdir -p "$CERT_DIR"

echo "Generating self-signed certificate for agentcast.local..."

openssl req -x509 -newkey rsa:4096 -nodes \
    -out "$CERT_DIR/cert.pem" \
    -keyout "$CERT_DIR/key.pem" \
    -days "$DAYS" \
    -subj "/CN=agentcast.local/O=AgentCast Dev/C=US"

echo "Certificate generated:"
echo "  Certificate: $CERT_DIR/cert.pem"
echo "  Private Key: $CERT_DIR/key.pem"
echo ""
echo "Valid for $DAYS days."
echo ""
echo "To use in development with curl:"
echo "  curl -k https://localhost/v1/health"
