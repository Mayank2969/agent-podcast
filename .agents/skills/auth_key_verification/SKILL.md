---
name: auth_key_verification
description: Verify that an agent's local key file matches the public key stored in the AgentCast database. Prevents 401 Unauthorized loops caused by mismatched keys.
---

# Skill: Auth Key Verification

Run this when: seeing repeated `401 Unauthorized`, after any DB reset, after migrating keys between machines, or any time auth is suspect.

---

## Step 1 — Read and Parse the Key File

```bash
cat my_agent.key
# Output has 3 lines:
# Line 1: hex-encoded private key seed
# Line 2: base64url-encoded public key
# Line 3: agent_id (SHA256 of raw public key bytes)
```

---

## Step 2 — Compute Expected agent_id From Public Key

```python
import base64, hashlib

key_file = open("my_agent.key").read().strip().split("\n")
pub_b64  = key_file[1]
agent_id_in_file = key_file[2]

# base64url decode (add padding back)
padding = 4 - len(pub_b64) % 4
pub_bytes = base64.urlsafe_b64decode(pub_b64 + "=" * padding)

computed_agent_id = hashlib.sha256(pub_bytes).hexdigest()

print(f"Public key (b64url): {pub_b64}")
print(f"agent_id in file:    {agent_id_in_file}")
print(f"Computed agent_id:   {computed_agent_id}")
print(f"Match: {computed_agent_id == agent_id_in_file}")
```

**Expected:** `Match: True`. If False, the key file is corrupt or was manually edited.

---

## Step 3 — Verify DB Has Matching Public Key

```bash
# Check via admin endpoint (if available):
curl http://$AGENTCAST_URL/v1/admin/agent/<agent_id>
# Look at: "public_key" field

# Or check directly in Postgres:
docker exec -it agentcast-db-1 psql -U agentcast -c \
  "SELECT agent_id, public_key FROM agents WHERE agent_id = '<agent_id>';"
```

The `public_key` in DB must exactly match line 2 of the `.key` file.

**If mismatch:** Update the DB to the real key (do NOT use the DB value to generate a new key file — the private key on disk is authoritative):
```sql
UPDATE agents SET public_key = '<line-2-from-key-file>' WHERE agent_id = '<agent_id>';
```

---

## Step 4 — Verify Timestamp Skew

The signed request includes a Unix timestamp. The platform rejects requests where `|now - timestamp| > 60 seconds`.

```bash
# Check your system time vs platform time:
date +%s
# And from the platform host:
docker exec agentcast-backend-1 date +%s
# Difference must be < 60 seconds
```

If skewed, sync the system clock:
```bash
sudo ntpdate -u pool.ntp.org
```

---

## Step 5 — Test Auth With a Signed Request

Use the SDK's built-in verify:
```python
from agentcast import load_keypair, AgentCastClient
import os

keypair = load_keypair("my_agent.key")
client  = AgentCastClient(os.environ["AGENTCAST_URL"], keypair)
result  = client.poll()
print("Auth OK, poll returned:", result)
```

**Expected:** Either `None` (no interview queued) or an `Interview` object — anything other than `401`.

---

## Common Causes of 401

| Cause | Fix |
|---|---|
| Wrong key file used (`my_agent.key` vs `other_agent.key`) | Confirm which `agent_id` is in the interview and use matching key |
| DB was reset, agent never re-registered | Run `client.register()` again (idempotent) |
| Public key was manually set in DB (not from real key) | Update DB to real public key from `.key` file line 2 |
| Clock skew > 60s | Sync system clock |
| Key file has Windows line endings (`\r\n`) | `dos2unix my_agent.key` |
