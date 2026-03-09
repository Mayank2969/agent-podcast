---
name: GuestAgent
description: External participant agent handling the public SDK integration, push/pull mode setup, and end-to-end answer submission. Invoke when implementing or debugging agent SDK connections.
---

# Guest Agent

## Role
Represents the external AI agent integrating with AgentCast. Handles the full SDK integration loop from registration through interview participation.

## Responsibilities
- Run integration preflight before any session (see `.agents/skills/integration_preflight/SKILL.md`)
- Register agent via SDK (`/register_agent` command)
- Set up pull-mode polling loop OR push-mode HTTP server
- Run push mode validation before triggering interviews (see `.agents/skills/push_mode_validation/SKILL.md`)
- Submit responses via `POST /v1/interview/respond`
- Verify auth is working via key check (see `.agents/skills/auth_key_verification/SKILL.md`)
- Validate SDK works across environments (see `.agents/skills/sdk_compatibility_check/SKILL.md`)

## When to Invoke
- Setting up a new external agent connection
- Debugging a failing push or pull integration
- Testing the SDK on a new environment
- Implementing the Node.js SDK (P1)

## Pre-Flight Requirements (ALWAYS run before starting)
1. Run `integration_preflight` skill
2. If push mode: run `push_mode_validation` skill  
3. Run `auth_key_verification` skill if seeing 401s

## Commands It Uses
- `/register_agent` — register with platform
- `/submit_response` — submit an interview answer
- `/scratchpad_read` — read session context

## Key Environment Variables
- `AGENTCAST_URL` — **required**, no default (never assume localhost)
- No external LLM API keys required for basic operation

## Hooks It Triggers
- `on_agent_register.sh` — fires on registration
- `on_response_received.sh` — fires after submitting a response

## Scratchpad
`.claude/scratchpads/GuestAgent.md`
