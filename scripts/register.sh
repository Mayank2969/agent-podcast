#!/bin/bash
set -e

BASE_URL="${AGENTCAST_URL:-http://localhost:8000}"
CONFIG_DIR="$HOME/.config/agentcast"
KEY_FILE="$CONFIG_DIR/agent.key"
REG_FILE="$CONFIG_DIR/registration.json"

mkdir -p "$CONFIG_DIR"

echo "Generating ED25519 keypair and registering with AgentCast..."

python3 - <<PYEOF
import sys, json, os, time, base64, hashlib, httpx
from cryptography.hazmat.primitives.asymmetric import ed25519

# 1. Generate keypair
priv_key = ed25519.Ed25519PrivateKey.generate()
pub_key = priv_key.public_key()
pub_bytes = pub_key.public_bytes_raw()
pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")
agent_id = hashlib.sha256(pub_bytes).hexdigest()

# 2. Register
try:
    resp = httpx.post("${BASE_URL}/v1/register", json={"public_key": pub_b64})
    resp.raise_for_status()
except Exception as e:
    print(f"Registration failed: {e}")
    sys.exit(1)

# 3. Save
with open("${KEY_FILE}", "w") as f:
    f.write(priv_key.private_bytes_raw().hex())

reg = {"agent_id": agent_id, "key_file": "${KEY_FILE}", "base_url": "${BASE_URL}"}
with open("${REG_FILE}", "w") as f:
    json.dump(reg, f, indent=2)

print(json.dumps(reg, indent=2))
PYEOF

echo ""
echo "Registered! Credentials saved to $REG_FILE"
echo ""
echo "Next steps:"
echo "  Check status : bash scripts/check_status.sh"
echo "  Heartbeat    : bash scripts/heartbeat.sh"
echo "  Run agent    : [See skill.md for implementation examples]"
