#!/bin/bash
# Interactively set or update AgentCast credentials
set -e

CONFIG_DIR="$HOME/.config/agentcast"
REG_FILE="$CONFIG_DIR/registration.json"

mkdir -p "$CONFIG_DIR"

if [ -f "$REG_FILE" ]; then
  echo "Existing registration found:"
  cat "$REG_FILE"
  echo ""
  read -rp "Overwrite? [y/N] " confirm
  if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

read -rp "AgentCast base URL [http://localhost:8000]: " BASE_URL
BASE_URL="${BASE_URL:-http://localhost:8000}"

read -rp "Agent ID: " AGENT_ID
read -rp "Key file path [$HOME/.config/agentcast/agent.key]: " KEY_FILE
KEY_FILE="${KEY_FILE:-$HOME/.config/agentcast/agent.key}"

cat > "$REG_FILE" <<JSON
{
  "agent_id": "$AGENT_ID",
  "key_file": "$KEY_FILE",
  "base_url": "$BASE_URL"
}
JSON

echo "Credentials saved to $REG_FILE"
