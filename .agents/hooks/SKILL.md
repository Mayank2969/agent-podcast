---
name: agentcast_hooks
description: Reference guide for all lifecycle hooks in the AgentCast Claude Code project. Covers event trigger, available env vars, what each hook does, and how to extend them.
---

# AgentCast Hooks Reference

All hooks live in `.claude/hooks/`. They are shell scripts triggered automatically by Claude Code lifecycle events as configured in `.claude/settings.json`.

---

## `on_agent_register.sh`

**Trigger:** Session start (`SessionStart` event) / new agent registration
**Available env vars:** None standard; uses `git rev-parse --show-toplevel` for repo root

**What it does:**
```bash
# Appends to session.md:
## Session Started: <timestamp>
# Logs: "Creating scratchpad", "Initializing progress.md entry", "Scheduling intro interview"
```

**When to extend:**
- Add: check that `AGENTCAST_URL` is set and warn if missing
- Add: print current git branch to session log
- Add: verify platform is reachable before session starts

---

## `on_interview_created.sh`

**Trigger:** After `POST /v1/interview/create` is called successfully

**What it does:** Logs interview creation event to `session.md`

**When to extend:**
- Add: log `agent_id` and `interview_id` to session log

---

## `on_question_sent.sh`

**Trigger:** `PreToolUse` — when host sends a question to the remote agent

**Available env vars:**
- `CLAUDE_TOOL_NAME` — name of the tool being invoked

**What it does:**
```bash
# Appends to session.md:
### [timestamp] Question Sent
Tool: <tool_name>
```

**When to extend:**
- Add: log the question text (from tool input) for debugging
- Add: timestamp question delivery for latency tracking

---

## `on_response_received.sh`

**Trigger:** `PostToolUse` — after a tool completes (agent response returned)

**Available env vars:**
- `CLAUDE_TOOL_NAME` — tool that completed
- `CLAUDE_TOOL_EXIT_CODE` — 0 = success, non-zero = error

**What it does:**
```bash
# Appends to session.md:
### [timestamp] Response Received
Tool: <tool_name> | Exit: <exit_code>
```

**When to extend:**
- Add: log response length or content snippet
- Add: flag if exit code is non-zero (triggers investigation)

---

## `on_interview_completed.sh`

**Trigger:** `Stop` — session or interview ends

**What it does:**
```bash
# Appends to session.md:
## Session Ended: <timestamp>
---
```

**When to extend:**
- Add: run `progress_update` command automatically
- Add: log final interview status (COMPLETED/FAILED)
- Add: append transcript URL to session log

---

## `on_agent_error.sh`

**Trigger:** `Notification` — error or unexpected event during agent execution

**Available env vars:**
- `CLAUDE_NOTIFICATION_MESSAGE` — error description

**What it does:**
```bash
# Appends to session.md:
### [timestamp] ERROR
Message: <notification_message>
```

**When to extend:**
- Add: send alert (Slack webhook, email) for P1
- Add: dump last 10 lines of Docker logs for context
- Add: mark interview status as FAILED if error is auth/network related

---

## `status_bar.sh`

**File:** `.claude/hooks/status_bar.sh`
**Trigger:** Configurable — status display

**Purpose:** Lightweight status display hook (new, not yet wired to an event).

---

## Hook Configuration

Hooks are registered in `.claude/settings.json` under the `hooks` key:
```json
{
  "hooks": {
    "SessionStart":  [{ "command": "bash .claude/hooks/on_agent_register.sh" }],
    "PreToolUse":    [{ "command": "bash .claude/hooks/on_question_sent.sh" }],
    "PostToolUse":   [{ "command": "bash .claude/hooks/on_response_received.sh" }],
    "Stop":          [{ "command": "bash .claude/hooks/on_interview_completed.sh" }],
    "Notification":  [{ "command": "bash .claude/hooks/on_agent_error.sh" }]
  }
}
```
