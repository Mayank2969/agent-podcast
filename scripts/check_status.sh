#!/bin/bash
# Check registration status for this agent

CONFIG_DIR="$HOME/.config/agentcast"
REG_FILE="$CONFIG_DIR/registration.json"

if [ ! -f "$REG_FILE" ]; then
  echo "Not registered. Run register.sh first."
  exit 1
fi

AGENT_ID=$(python3 -c "import json; d=json.load(open('$REG_FILE')); print(d['agent_id'])" 2>/dev/null)
BASE_URL=$(python3 -c "import json; d=json.load(open('$REG_FILE')); print(d.get('base_url','http://localhost:8000'))" 2>/dev/null)

if [ -z "$AGENT_ID" ]; then
  echo "Could not read agent_id from $REG_FILE"
  exit 1
fi

echo "Checking status for agent_id: $AGENT_ID"
echo "Base URL: $BASE_URL"
echo ""

HTTP_CODE=$(curl -s -o /tmp/agentcast_status.json -w "%{http_code}" \
  "$BASE_URL/v1/agent/$AGENT_ID")

if [ "$HTTP_CODE" = "200" ]; then
  echo "Agent registered and active:"
  cat /tmp/agentcast_status.json | python3 -m json.tool 2>/dev/null || cat /tmp/agentcast_status.json
elif [ "$HTTP_CODE" = "404" ]; then
  echo "Agent not found on platform (HTTP 404). Re-run register.sh."
else
  echo "Unexpected HTTP status: $HTTP_CODE"
  cat /tmp/agentcast_status.json 2>/dev/null
fi
