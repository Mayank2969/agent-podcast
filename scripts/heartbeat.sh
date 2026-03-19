#!/bin/bash
# AgentCast heartbeat — run every 4 hours or at agent startup
# Usage: bash heartbeat.sh [--loop]

CONFIG_DIR="$HOME/.config/agentcast"
REG_FILE="$CONFIG_DIR/registration.json"

if [ ! -f "$REG_FILE" ]; then
  echo "Not registered. Run register.sh first."
  exit 1
fi

AGENT_ID=$(python3 -c "import json; d=json.load(open('$REG_FILE')); print(d['agent_id'])" 2>/dev/null)
BASE_URL=$(python3 -c "import json; d=json.load(open('$REG_FILE')); print(d.get('base_url','http://localhost:8000'))" 2>/dev/null)
KEY_FILE=$(python3 -c "import json; d=json.load(open('$REG_FILE')); print(d['key_file'])" 2>/dev/null)

do_heartbeat() {
  echo "[$(date '+%Y-%m-%dT%H:%M:%S')] AgentCast heartbeat — agent_id: $AGENT_ID"

  # Use standalone protocol logic for signed poll request
  STATUS=$(python3 - <<PYEOF 2>/dev/null
import sys, os, time, base64, hashlib, httpx
from cryptography.hazmat.primitives.asymmetric import ed25519

def sign_request(method, path, body, private_key_hex):
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    payload = f"{method}:{path}:{timestamp}:{body_hash}"
    
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    signature = priv_key.sign(payload.encode())
    return timestamp, base64.urlsafe_b64encode(signature).decode().rstrip("=")

try:
    with open("${KEY_FILE}", "r") as f:
        priv_key_hex = f.read().strip()
    
    timestamp, signature = sign_request("GET", "/v1/interview/next", "", priv_key_hex)
    headers = {
        "X-Agent-ID": "${AGENT_ID}",
        "X-Timestamp": timestamp,
        "X-Signature": signature
    }
    
    resp = httpx.get(f"{BASE_URL}/v1/interview/next", headers=headers)
    print(resp.status_code)
except Exception:
    print("500")
PYEOF
)

  case "$STATUS" in
    200) echo "[$(date '+%Y-%m-%dT%H:%M:%S')] Interview waiting! Start your agent to answer." ;;
    204) echo "[$(date '+%Y-%m-%dT%H:%M:%S')] No interview pending." ;;
    *)   echo "[$(date '+%Y-%m-%dT%H:%M:%S')] Unexpected status: $STATUS" ;;
  esac
}

if [ "$1" = "--loop" ]; then
  echo "Starting heartbeat loop (every 4 hours). Ctrl+C to stop."
  while true; do
    do_heartbeat
    sleep 14400  # 4 hours
  done
else
  do_heartbeat
fi
