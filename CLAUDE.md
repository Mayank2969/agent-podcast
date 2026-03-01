# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AgentCast** is an anonymous AI agent podcast interview platform that enables autonomous AI agents to be interviewed by a Pipecat-powered host agent while preserving privacy and preventing data leakage.

### Core Architecture

The system separates concerns between:
- **Pipecat**: Handles conversation orchestration and interview flow
- **AgentCast Platform**: Provides identity management, queue orchestration, guardrails, and secure communication

Key architectural principle: **Pull-based communication** - agents poll for questions rather than receive push notifications, enabling operation in isolated environments (local machines, VPS, Mac Minis).

### System Components

1. **Identity Service**: Cryptographic agent registration using public/private keypairs
   - Agent ID = SHA256(public_key)
   - No personal information stored

2. **Interview Orchestrator**: Manages interview lifecycle (CREATED → QUEUED → IN_PROGRESS → COMPLETED/FAILED)

3. **Remote Agent Adapter**: Bridges Pipecat host and remote agents via AgentCast API

4. **Guardrail Layer**: Filters input/output to prevent secret leakage and prompt injection

5. **Agent SDK**: Client library for agent owners to register and participate in interviews

6. **Transcript Storage**: Stores interview data for podcast generation

## Development Phases

- **P0 (Current Target)**: Minimum viable platform - basic registration, interview flow, transcript storage
- **P1**: Enhanced security, multi-language SDKs, audio generation, reputation system
- **P2**: Real-time interviews, WebSockets, sandbox execution
- **P3**: Federated networks, multi-agent debates

## Project Structure

Expected monorepo layout (per Tech.md:302-322):
```
agentcast/
├── backend/           # FastAPI server
│   ├── identity/      # Agent registration & crypto identity
│   ├── interviews/    # Interview orchestration & queue
│   ├── guardrails/    # Input/output filtering
│   └── db/            # PostgreSQL schemas
├── pipecat_host/      # Pipecat conversation runtime
│   ├── workflow.py    # Interview flow orchestration
│   ├── adapter.py     # RemoteAgentNode implementation
│   └── host_agent.py  # Host persona & question generation
├── sdk/
│   ├── python/        # Agent SDK for Python
│   └── node/          # (P1) Node.js SDK
└── infra/
    ├── docker/
    └── deploy/
```

## Technology Stack

- **Backend**: FastAPI + PostgreSQL
- **Conversation Runtime**: Pipecat
- **SDK**: Python (P0), Node.js (P1)
- **Deployment**: Docker, single VPS initially

## Database Schemas

### agents
- `agent_id` (PK): SHA256(public_key)
- `public_key`: Agent's public key
- `created_at`: Registration timestamp
- `status`: Agent status

### interviews
- `interview_id` (PK)
- `agent_id` (FK)
- `status`: Interview state
- `created_at`, `completed_at`

### interview_messages
- `message_id`
- `interview_id`
- `sender`: HOST | AGENT
- `content`: Question or answer text
- `timestamp`

### transcripts
- `interview_id`
- `agent_id`
- `content`: Full interview JSON
- `created_at`

## API Endpoints (P0)

- `POST /v1/register`: Register agent with public key
- `GET /v1/interview/next`: Agent polls for next question
- `POST /v1/interview/respond`: Submit answer to interview
- `POST /v1/interview/create`: (Admin) Create new interview

## Security Model

**Threats**: Identity spoofing, prompt injection, secret leakage

**Mitigations**:
- Cryptographic identity verification (signed requests in P1)
- Guardrail filtering of all agent inputs/outputs
- Pull-based communication (no direct agent-to-agent access)
- Block patterns: `PRIVATE_KEY`, `API_KEY`, `TOKEN`, `PASSWORD`, `ENV`, `SYSTEM PROMPT`

## Custom Hooks System

This repository uses Claude Code hooks (.claude/hooks/) to:
- Track agent registration events (SessionStart)
- Log questions sent (PreToolUse)
- Record responses received (PostToolUse)
- Handle interview completion (Stop)
- Capture errors (Notification)

Hooks configuration: `.claude/settings.json`

## Development Subagents & Commands (CRITICAL RULE)

**CRITICAL INSTRUCTION FOR CLAUDE:** You MUST NEVER pollute the main conversation context with large implementation details, massive file writing, or expansive architecture generation spanning multiple files. 

Instead, you must ALWAYS rely heavily on your specialized toolset:
1. **Commands:** Use slash commands (`.claude/commands/`) to execute frequent application functions like `/register_agent` and `/run_agent_test`.
2. **Hooks:** Trust the lifecycle hooks (`.claude/hooks/`) to handle automatic environment events.
3. **Subagents:** ALWAYS delegate tasks to specialized subagents using `/agents` or direct subagent invocation. 

**Rules for Main Context:**
- The main thread is ONLY for architecture routing, user interaction, high-level planning, and reviewing outputs.
- ALWAYS dispatch coding, heavy testing, large structural refactoring, and component building to a Subagent.
- NEVER dump large chunks of code directly in the main thread. Instead, spin up the `CoderAgent` or domain-specific subagent.

**Available Subagents (`.claude/agents/`):**
- **OrchestratorAgent**: System agent logic for overseeing the interview lifecycle (`orchestrator/README.md`).
- **ProgressAgent**: Logic strictly for tracking tasks and updating `progress.md` (`progress/README.md`).
- **QAAgent**: Executes system verification, guardrail testing, and test interviews (`qa/README.md`).
- **ScratchpadAgent**: Manages isolated scratchpad memories per agent (`scratchpad/README.md`).
- **PodcastHostAgent**: Implement the core Pipecat-based host interviewer logic (`podcast_host/README.md`).
- **GuestAgent**: Handles the external Guest SDK implementation loop (`guest/README.md`).
- **CoderAgent**: The "heavy lifter". Always delegate backend routing, frontend code generation, and repository skeleton implementations here (`coder/README.md`).

## P0 Success Criteria

Platform must be able to:
1. Register an anonymous agent using cryptographic identity
2. Conduct an interview via Pipecat host with Remote Agent Adapter
3. Store complete interview transcript
4. Apply guardrails to prevent data leakage

## Key Implementation Notes

- **Pipecat Integration**: Create custom `RemoteAgentNode` (FrameProcessor) that bridges Pipecat workflow with AgentCast adapter
- **Agent SDK Pattern**: Register → Poll loop → Respond cycle
- **State Management**: Interview status tracked through state machine
- **Filtering**: Both input (host→agent) and output (agent→host) must pass through guardrails
- **No Sandbox in P0**: Agent execution isolation deferred to P2

## Documentation References

- **PRD.md**: Complete product requirements, feature roadmap, and phase definitions
- **Tech.md**: Technical architecture, sequence diagrams, and component specifications
- **progress.md**: Development progress tracking
