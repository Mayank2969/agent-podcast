#!/bin/bash
# Quick smoke test: register an agent and check empty poll
set -e

BASE_URL="${1:-http://localhost:8000}"

echo "=== AgentCast Smoke Test ==="
echo "Target: $BASE_URL"

# Health check
echo -n "1. Health check... "
HEALTH=$(curl -sf "$BASE_URL/health")
echo "OK: $HEALTH"

# Register a test agent using Python
echo -n "2. Register test agent... "
RESULT=$(python3 -c "
import hashlib, json, sys
from base64 import urlsafe_b64encode
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import urllib.request

priv = Ed25519PrivateKey.generate()
pub_bytes = priv.public_key().public_bytes_raw()
pub_b64 = urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()
agent_id = hashlib.sha256(pub_bytes).hexdigest()

req = urllib.request.Request(
    '${BASE_URL}/v1/register',
    data=json.dumps({'public_key': pub_b64}).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())
    print(data['agent_id'])
")
echo "OK: agent_id=$RESULT"

echo ""
echo "=== Smoke test PASSED ==="
