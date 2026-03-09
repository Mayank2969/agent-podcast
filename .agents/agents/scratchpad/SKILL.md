---
name: ScratchpadAgent
description: Manages isolated per-agent scratchpad memory in .claude/scratchpads/. Each agent has its own scratchpad file for working context that persists across tool calls.
---

# Scratchpad Agent

## Role
Memory manager. Provides each Claude Code agent with a persistent, isolated scratchpad so context is not lost across long sessions.

## Responsibilities
- Write working context, decisions, and state to agent-specific scratchpad files
- Read scratchpads at the start of a session to restore context
- Keep scratchpads concise — decisions and state, not full conversation logs

## Scratchpad Files
| Agent | Scratchpad Path |
|---|---|
| OrchestratorAgent | `.claude/scratchpads/OrchestratorAgent.md` |
| CoderAgent | `.claude/scratchpads/CoderAgent.md` |
| GuestAgent | `.claude/scratchpads/GuestAgent.md` |
| QAAgent | `.claude/scratchpads/QAAgent.md` |
| PodcastHostAgent | `.claude/scratchpads/PodcastHostAgent.md` |
| ProgressAgent | `.claude/scratchpads/ProgressAgent.md` |
| Session (shared) | `.claude/scratchpads/session.md` |

## When to Invoke
- At the start of any long-running session (read context)
- At the end of a session (write what was done and what's next)
- When an agent needs to hand off state to another agent

## Commands It Uses
- `/scratchpad_read` — reads a scratchpad by agent name
- `/scratchpad_write` — writes to a scratchpad

## Note
`session.md` is the shared session log, written by lifecycle hooks (`on_agent_register.sh`, `on_interview_completed.sh`, etc.). Individual agent scratchpads are only written by their respective agents.
