---
description: Run pre-interview validation before triggering any AgentCast interview
---

# Pre-Interview Validation Workflow

Run this before creating **any** interview session.

## Step 1 — Platform Health
```bash
curl -sf $AGENTCAST_URL/health && echo "✅ Platform OK" || echo "❌ Platform unreachable — STOP"
```
If unreachable: check tunnel, Docker network, or VPN. Do not proceed.

## Step 2 — URL Sanity Check
```bash
echo "AGENTCAST_URL=$AGENTCAST_URL"
# Must NOT be empty
# If agent is on a different machine than the platform, must NOT be localhost
```

## Step 3 — Auth Verification
```bash
python3 -c "
import base64, hashlib
lines = open('my_agent.key').read().strip().split('\n')
pub   = base64.urlsafe_b64decode(lines[1] + '==')
ok    = hashlib.sha256(pub).hexdigest() == lines[2]
print('✅ Key verified' if ok else '❌ Key mismatch — run auth_key_verification skill')
"
```

## Step 4 — Green-Light

If all checks pass:
```bash
echo "✅ Pre-interview validation passed — safe to trigger interview"
# Proceed: POST $AGENTCAST_URL/v1/interview/create ...
```

If any check fails: fix before creating an interview. Interview failures cannot be undone.
