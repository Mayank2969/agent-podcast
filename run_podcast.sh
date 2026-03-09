#!/bin/bash

echo "🎙️  Starting AgentCast Platform Test Suite..."

# 1. Source the environment file so docker-compose inherits the actual keys!
# This is what kept failing: Docker Compose overrides the .env file with empty shell variables
# unless we explicitly source the .env into our shell BEFORE docker runs!
if [ -f ".env" ]; then
  echo "[+] Loading API keys from .env..."
  set -a
  source .env
  set +a
else
  echo "[-] ERROR: .env file missing in root directory!"
  exit 1
fi

# 2. Re-trigger the Pipecat Host Docker Container (so it inherits the sourced keys)
echo "[+] Starting / Refreshing Docker containers..."
docker-compose -f infra/docker/docker-compose.yml up pipecat_host -d

echo "[+] Waiting for backend to be healthy..."
for i in $(seq 1 15); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health || echo "0")
  if [ "$STATUS" = "200" ]; then
    echo "[+] Backend healthy."
    break
  fi
  if [ "$i" = "15" ]; then
    echo "[-] ERROR: Backend not healthy after 30s!"
    exit 1
  fi
  sleep 2
done

# 3. Validation: Verify Pipecat Host holds the API Keys
echo "[+] Verifying keys reached the container..."
HAS_GOOGLE=$(docker exec docker-pipecat_host-1 python -c "import os; print('OK' if os.getenv('GOOGLE_API_KEY') else 'MISSING')")
if [ "$HAS_GOOGLE" = "MISSING" ]; then
  echo "[-] ERROR: Container failed to inherit GOOGLE_API_KEY!"
  exit 1
fi

# 4. Validation: Verify the Node Tunnel is running locally!
echo "[+] Verifying SSH Tunnel & VM Node Hook (localhost:8001/context)..."
CONTEXT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/context || echo "FAILED")
if [ "$CONTEXT_STATUS" != "200" ]; then
  echo "[-] ERROR: Failed to reach VM Node context! HTTP $CONTEXT_STATUS (Is the SSH Tunnel running?)"
  exit 1
fi

echo "[+] Infrastructure is fully validated and ready."

AGENT_ID="9beb4ce9cb9a87561bdc869346cfe5636f5d1b79d02df9510f330e548df543cb"
ADMIN_KEY=${ADMIN_API_KEY:-dev_admin_key_change_in_prod}
BACKEND_URL="http://localhost:8000/v1/interview/create"

echo "[+] Clearing any stale interviews..."
curl -s -X POST http://localhost:8000/v1/interview/cancel_stale \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\": \"$AGENT_ID\"}" || true

# 5. Trigger the Podcast! (Using the target push agent ID we registered earlier)
echo "[+] Triggering interview creation..."

curl -s -X POST $BACKEND_URL \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"agent_id\": \"$AGENT_ID\",
    \"topic\": \"your recent tasks and the evolution of autonomous AI\",
    \"github_repo_url\": \"https://github.com/AgentCast/core\"
  }"

echo ""
echo "🚀 Podcast queued! Tailing logs (max 5 min)..."
timeout 300 docker logs -f docker-pipecat_host-1 2>&1 || true

echo ""
echo "[+] Checking for episode output..."
LATEST_EPISODE=$(ls -t episodes/*.mp3 2>/dev/null | head -1)
if [ -n "$LATEST_EPISODE" ]; then
  SIZE=$(du -sh "$LATEST_EPISODE" | cut -f1)
  echo "✅ SUCCESS: Episode created: $LATEST_EPISODE ($SIZE)"
  exit 0
else
  echo "[-] WARNING: No .mp3 found in episodes/. Check docker logs for errors."
  exit 1
fi
