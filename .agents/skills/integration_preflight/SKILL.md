---
name: integration_preflight
description: Verify all networking, URL, and connectivity assumptions before starting any AgentCast integration session. Prevents localhost-footgun, wrong tunnel direction, and Docker networking failures.
---

# Skill: Integration Preflight

Run this checklist **before** any agent integration session, before creating interviews, and before asking an agent to start polling or running a push server.

---

## Step 1 — Confirm AGENTCAST_URL Is Set Explicitly

```bash
echo $AGENTCAST_URL
```

**Expected:** A non-empty, non-localhost URL pointing at the actual platform.

**Rules:**
| Scenario | Correct AGENTCAST_URL |
|---|---|
| Agent on same machine as platform | `http://localhost:8000` |
| Agent on VPS, platform on separate server | `http://<platform-ip>:8000` |
| Agent on VM, platform via **reverse SSH tunnel** | `http://127.0.0.1:<tunnel-port>` |
| Agent inside Docker, platform on host machine | `http://host.docker.internal:8000` |

**If not set**, stop and have the user set it. Never assume `localhost` in multi-host setups.

---

## Step 2 — Test Platform Reachability From Agent's Host

```bash
curl -sf $AGENTCAST_URL/health && echo "OK" || echo "UNREACHABLE"
```

If no `/health` endpoint exists yet, use any known endpoint:
```bash
curl -o /dev/null -w "%{http_code}" $AGENTCAST_URL/v1/register -X OPTIONS
```

**Expected:** Any non-connection-refused response (200, 404, 405 are all fine — they prove the platform is up).

**If unreachable:** Check tunnel / firewall before proceeding. Do not start an interview against an unreachable platform.

---

## Step 3 — Verify Tunnel Direction (If Using SSH Tunnel)

Reverse tunnel (`-R`) vs forward tunnel (`-L`) confusion was the cause of C2:

```
# CORRECT for VM agent → platform:
# On the PLATFORM machine, run:
ssh -R <tunnel-port>:127.0.0.1:8000 user@vm-ip
# Then on VM: AGENTCAST_URL=http://127.0.0.1:<tunnel-port>

# WRONG (forward tunnel — makes platform's port available on the platform side):
ssh -L <tunnel-port>:127.0.0.1:8000 user@vm-ip
```

**Quick verification:** From the VM, curl the tunnel port:
```bash
curl http://127.0.0.1:9000/health
```
If this times out or refuses, the tunnel is down or direction is wrong.

---

## Step 4 — Docker Networking Check (If Platform or Agent Runs in Docker)

```bash
# From inside a Docker container, to reach the host machine:
curl http://host.docker.internal:8000/health

# NOT: curl http://localhost:8000   ← this hits the container itself
```

If the pipecat host runs in Docker and needs to POST to an agent's `callback_url` on the host:
- `callback_url` **must** use `host.docker.internal`, not `localhost`
- Example: `http://host.docker.internal:8001/question`

---

## Step 5 — Verify Agent Push Server or Poll Server Is Running

```bash
# For push-mode agents:
curl http://<agent-callback-base>/health

# For pull-mode: verify the SDK polling loop is running
ps aux | grep "run_agent\|agentcast"
```

**Expected:** HTTP 200 from `/health`, or the process is visible in `ps`.

---

## Success Criteria

All 5 checks pass before starting any interview session. If any fails, stop and fix before creating an interview — failed delivery marks the interview `FAILED` with no automatic retry in P0.
