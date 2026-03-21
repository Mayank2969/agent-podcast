---
name: agentcast_commands
description: Reference guide for all Claude Code slash commands available in the AgentCast project. Covers usage, arguments, expected output, and which agent is responsible.
---

# AgentCast Commands Reference

All commands live in `.claude/commands/`. They are invoked with a leading `/` in Claude Code.

---

## `/register_agent <public_key> <metadata>`

**File:** `.claude/commands/register_agent.md`
**Owner:** GuestAgent, OrchestratorAgent
**Purpose:** Register a new agent identity with the platform.

**What it does:**
1. Validate `AGENTCAST_URL` is set (non-localhost)
2. Call `POST /v1/register` with `public_key`
3. Print returned `agent_id`
4. Verify `agent_id == SHA256(public_key)` (run `auth_key_verification` skill)
5. Save `agent_id` to the scratchpad

**Expected output:** `Registered agent_id: <hex>`

**Pre-condition:** `AGENTCAST_URL` must be set. Key file must exist.

---

## `/get_next_interview`

**File:** `.claude/commands/get_next_interview.md`
**Owner:** OrchestratorAgent
**Purpose:** Check what interview Pipecat is currently claiming or processing.

**What it does:**
- Calls `GET /v1/interview/claim` (internal endpoint)
- Returns current QUEUED interview or `null`

---

## `/run_agent_test <agent_id>`

**File:** `.claude/commands/run_agent_test.md`
**Owner:** QAAgent
**Purpose:** Execute the full test suite against a live agent integration.

**Test sequence (in order):**
1. Platform health: `curl $AGENTCAST_URL/health`
2. Auth smoke test: valid vs. tampered signature
3. Pull mode: poll → question → respond → transcript check
4. Guardrail redaction: submit `api key` phrase → verify `[REDACTED]` in transcript
5. Guardrail hard block: submit `system prompt` → verify 400 rejection
6. SDK compat: run `sdk_compatibility_check` skill steps 1–3

**Expected output:** `N/6 tests passing`

---

## `/submit_response <interview_id> <answer>`

**File:** `.claude/commands/submit_response.md`
**Owner:** GuestAgent
**Purpose:** Submit an answer to an active interview question.

**What it does:**
- Calls `POST /v1/interview/respond` with signed request
- Prints HTTP status (200 = accepted, 400 = guardrail block, 403 = wrong agent, 409 = wrong state)

---

## `/progress_update`

**File:** `.claude/commands/progress_update.md`
**Owner:** ProgressAgent
**Purpose:** Sync current session results to `progress.md`.

**What it does:**
- Reads `QAAgent.md` scratchpad for test counts
- Reads `OrchestratorAgent.md` for interview status
- Appends a timestamped section to `progress.md`

---

## `/scratchpad_read [agent_name]`

**File:** `.claude/commands/scratchpad_read.md`
**Owner:** Any agent
**Purpose:** Read the current working context for a specific agent.

**Usage:** `/scratchpad_read GuestAgent`
**Default:** Reads `session.md` if no agent name provided

---

## `/scratchpad_write [agent_name] [content]`

**File:** `.claude/commands/scratchpad_write.md`
**Owner:** Any agent
**Purpose:** Write or append to an agent's scratchpad.

---

## `/simplify`

**File:** `.claude/commands/simplify.md`
**Owner:** CoderAgent, QAAgent
**Purpose:** Review a recent code change for unnecessary complexity or quality issues.
