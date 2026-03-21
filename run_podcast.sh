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

# Force a rebuild to ensure backend and host code changes are applied
docker-compose -f infra/docker/docker-compose.yml up backend pipecat_host --build -d

# 2.5 Ensure episodes directory exists
mkdir -p episodes

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

# 4. Optional: Check SSH tunnel for push-mode interviews
echo "[+] Checking SSH Tunnel (only needed for push-mode)..."
CONTEXT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/context 2>/dev/null || echo "0")
if [ "$CONTEXT_STATUS" = "200" ]; then
  echo "[+] SSH tunnel active — push-mode interviews supported."
else
  echo "[!] WARNING: SSH tunnel not reachable (HTTP $CONTEXT_STATUS)."
  echo "    Pull-mode interviews will work fine."
  echo "    For push-mode, run: ssh -N -L 8001:127.0.0.1:8000 maya6969@100.90.129.59"
fi

echo "[+] Infrastructure is fully validated and ready."
echo ""
echo "🚀 Services are running! Tailing pipecat_host logs to see interview processing..."
echo "To trigger an interview, use the Agent Portal UI or the API directly."
echo ""

# Tailing logs to see interviews as they happen
docker logs -f docker-pipecat_host-1 2>&1
