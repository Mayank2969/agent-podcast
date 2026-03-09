---
name: PodcastHostAgent
description: Implements the Pipecat-based host interviewer — question generation, interview flow control, GitHub README context fetching, and push/pull delivery branching.
---

# Podcast Host Agent

## Role
The AI interviewer. Runs as a long-lived Pipecat process (`pipecat_host/` service). Generates questions, controls interview pacing, and routes delivery based on agent mode.

## Responsibilities
- Run inside `pipecat_host/` Docker service (separate from FastAPI backend)
- Poll `GET /v1/interview/claim` every 5 seconds for QUEUED interviews
- If `github_repo_url` is set: fetch README (first 1500 chars) for project-specific questions
- Look up agent's `callback_url` via `GET /v1/agent/{agent_id}` (admin endpoint):
  - `callback_url` present → **push mode**: POST questions to `callback_url`
  - `callback_url` null → **pull mode**: save question to DB, wait for agent to poll
- Wait for agent response via async DB polling (`wait_for_response()`, 300s timeout)
- Mark interview COMPLETED or FAILED
- Generate transcript on completion

## Key Files
- `pipecat_host/workflow.py` — main interview loop, push/pull branching
- `pipecat_host/host_agent.py` — question generation, README fetch, host persona
- `pipecat_host/adapter.py` — RemoteAgentNode (Pipecat FrameProcessor bridge)

## Push Mode Delivery
- POST to agent's `callback_url`: `{"interview_id": "...", "question": "..."}`
- If HTTP error or timeout: mark interview `FAILED` immediately
- The agent's server must ACK with `200 OK` before doing any work (see `push_mode_validation` skill)

## Timeout Handling
- `wait_for_response()` polls DB every 2 seconds, timeout = 300 seconds
- On `InterviewTimeoutError`: calls `PATCH /v1/interview/{id}/status` → `FAILED`

## LLM Model
- Default: `claude-haiku-4-5-20251001` (via `AGENTCAST_HOST_MODEL` env var)
- Production override: `claude-sonnet-4-6`

## Scratchpad
`.claude/scratchpads/PodcastHostAgent.md`
