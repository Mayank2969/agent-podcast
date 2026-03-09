---
name: sdk_compatibility_check
description: Validate SDK works across curl/Python/Node.js before declaring any integration complete. Prevents shipping integrations that only work on the developer's machine.
---

# Skill: SDK Compatibility Check

Run this when: finishing a new SDK feature, before releasing an SDK version, or before declaring an external agent integration complete.

---

## Level 0 — curl (No SDK, No Language)

If this doesn't work, nothing else will. Test the raw API first.

```bash
# 1. Register (no auth required)
AGENT_ID=$(curl -sf -X POST $AGENTCAST_URL/v1/register \
  -H "Content-Type: application/json" \
  -d '{"public_key": "dGVzdGtleWZvcnNtb2tldGVzdA=="}' | python3 -c "import sys,json; print(json.load(sys.stdin)['agent_id'])")
echo "Registered: $AGENT_ID"

# 2. Poll (requires signed request — use SDK for signing, or test endpoint auth separately)
# At minimum: confirm the endpoint responds
curl -o /dev/null -w "%{http_code}" $AGENTCAST_URL/v1/interview/next \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "X-Timestamp: $(date +%s)" \
  -H "X-Signature: placeholder"
# Expected: 401 (auth fails correctly) — proves the endpoint exists and responds
```

---

## Level 1 — Python SDK (Clean venv)

**Must work from a fresh environment with zero pre-installed packages.**

```bash
python3 -m venv /tmp/agentcast_test_env
source /tmp/agentcast_test_env/bin/activate
pip install -e sdk/python/

# Verify zero external LLM dependency:
python3 -c "from agentcast import AgentCastClient, generate_keypair; print('OK')"

# Run the example without any API keys:
AGENTCAST_URL=$AGENTCAST_URL python3 sdk/python/examples/run_agent.py \
  --generate --key-file /tmp/test_agent.key
# Expected: registers, prints agent_id, exits normally

deactivate
rm -rf /tmp/agentcast_test_env /tmp/test_agent.key
```

**Failure conditions to check:**
- `ModuleNotFoundError: No module named 'anthropic'` → example has a hidden LLM dep
- `pip install` fails → SDK has bad `setup.py` / `pyproject.toml`
- Registers but agent_id doesn't match SHA256(public_key) → crypto bug

---

## Level 2 — Node.js Adapter (When sdk/node/ Exists)

```bash
cd sdk/node/
npm install

# Verify zero external LLM or API key dependency:
node -e "const {AgentCastClient} = require('.'); console.log('OK')"

# Basic registration smoke test:
AGENTCAST_URL=$AGENTCAST_URL node examples/register.js
# Expected: prints agent_id, exits 0
```

---

## Level 3 — Environment Variable Validation

**Test that AGENTCAST_URL is required (no localhost default):**

```bash
# Unset URL and try to run — should fail with a clear error:
unset AGENTCAST_URL
python3 sdk/python/examples/run_agent.py --key-file my_agent.key
# Expected: "Error: AGENTCAST_URL is not set" (not a silent connection error)
```

If instead it silently tries `localhost:8000`, the default is wrong and must be removed.

---

## Level 4 — Cross-Environment Matrix

Before declaring integration done, confirm the SDK was tested against these combinations:

| Environment | Python SDK | Node.js SDK | curl only |
|---|---|---|---|
| macOS (dev machine) | ✅ | ✅ | ✅ |
| Linux VPS (Ubuntu 22.04) | ✅ | ✅ | ✅ |
| Linux VM (restricted pip/venv) | N/A | ✅ | ✅ |
| Inside Docker container | ✅ | ✅ | ✅ |

If a cell is ❌, document it as a known limitation in `AGENT_INTEGRATION.md` before shipping.

---

## Level 5 — No Secret Dependencies

```bash
# Check SDK has no hardcoded API keys or external LLM calls in core code:
grep -rn "ANTHROPIC\|OPENAI\|sk-\|api.openai\|api.anthropic" sdk/ \
  --include="*.py" --include="*.js" --include="*.ts" | grep -v example | grep -v "#"
# Expected: no matches (examples may use them, core must not)
```

---

## Pass Criteria

- Level 0: Platform endpoints reachable and auth responds correctly
- Level 1: Python SDK installs cleanly in fresh venv, runs without any external API keys
- Level 2: Node.js SDK (when available) runs equivalently
- Level 3: Unset `AGENTCAST_URL` produces clear error (not silent localhost fallback)
- Level 5: No hardcoded secrets in non-example SDK code
