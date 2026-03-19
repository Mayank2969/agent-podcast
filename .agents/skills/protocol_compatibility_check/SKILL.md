---
name: protocol_compatibility_check
description: Validate raw HTTP/ED25519 integration works across curl/Python before declaring any integration complete. Prevents shipping integrations that only work on the developer's machine.
---

# Skill: Protocol Compatibility Check

Run this when: finishing a new platform feature, or before declaring an external agent integration complete.

---

## Level 0 — curl (Raw API)

If this doesn't work, nothing else will. Test the raw API first.

```bash
# 1. Register (no auth required)
# Generates a valid agent_id from the public key
AGENT_ID=$(curl -sf -X POST $AGENTCAST_URL/v1/register \
  -H "Content-Type: application/json" \
  -d '{"public_key": "dGVzdGtleWZvcnNtb2tldGVzdA=="}' | python3 -c "import sys,json; print(json.load(sys.stdin)['agent_id'])")
echo "Registered: $AGENT_ID"

# 2. Poll (requires signed request)
# Verified using scripts/heartbeat.sh or skill.md examples
bash scripts/heartbeat.sh
```

---

## Level 1 — Standalone Python (No SDK)

**Must work with only `cryptography` and `httpx` installed.**

```bash
# Run the registration script
bash scripts/register.sh

# Run the heartbeat script
bash scripts/heartbeat.sh
```

**Failure conditions to check:**
- `ModuleNotFoundError: No module named 'cryptography'` → environment missing required crypto libs
- Registers but agent_id doesn't match SHA256(public_key) → backend crypto bug

---

## Level 2 — Environment Variable Validation

**Test that AGENTCAST_URL is required (no localhost default):**

```bash
# Unset URL and try to run — should fail with a clear error:
unset AGENTCAST_URL
bash scripts/register.sh
# Expected: usage error or clear failure
```

---

## Pass Criteria

- Level 0: Platform endpoints reachable and respond correctly.
- Level 1: Registration and Heartbeat scripts work without the `agentcast` SDK.
- Level 2: `AGENTCAST_URL` requirement is enforced.
