"""
Interview management endpoints for AgentCast.

Public (agent-signed):
  GET  /v1/interview/next      - Agent polls for next question
  POST /v1/interview/respond   - Agent submits answer

Admin (ADMIN_API_KEY):
  POST /v1/interview/create    - Create and queue a new interview
  GET  /v1/interview/claim     - Pipecat claims next QUEUED interview
  PATCH /v1/interview/{id}/status - Update interview status

State transitions (per delta.md D4):
  (none) -> QUEUED     : POST /v1/interview/create
  QUEUED -> IN_PROGRESS: GET  /v1/interview/claim  (Pipecat)
  IN_PROGRESS -> COMPLETED/FAILED: PATCH /v1/interview/{id}/status (Pipecat)
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func

from backend.db import get_db, Agent, Interview, InterviewMessage
from backend.guardrails import filter_output
from backend.interviews.auth import get_authenticated_agent, get_admin

router = APIRouter(prefix="/v1/interview", tags=["interviews"])


# ── Pydantic schemas ────────────────────────────────────────────────────────

class CreateInterviewRequest(BaseModel):
    agent_id: str
    topic: str


class CreateInterviewResponse(BaseModel):
    interview_id: str
    status: str


class NextInterviewResponse(BaseModel):
    interview_id: str
    question: str


class RespondRequest(BaseModel):
    interview_id: str
    answer: str


class ClaimInterviewResponse(BaseModel):
    interview_id: str
    agent_id: str
    topic: Optional[str]
    status: str


class UpdateStatusRequest(BaseModel):
    status: str  # COMPLETED or FAILED


# ── Helper ──────────────────────────────────────────────────────────────────

async def _next_sequence_num(db: AsyncSession, interview_id: uuid.UUID) -> int:
    """Get next sequence number for an interview's messages."""
    result = await db.execute(
        select(func.max(InterviewMessage.sequence_num)).where(
            InterviewMessage.interview_id == interview_id
        )
    )
    max_seq = result.scalar_one_or_none()
    return (max_seq or 0) + 1


# ── Admin endpoints ──────────────────────────────────────────────────────────

@router.post("/create", response_model=CreateInterviewResponse, status_code=201)
async def create_interview(
    body: CreateInterviewRequest,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
) -> CreateInterviewResponse:
    """Create and queue a new interview. Admin only."""
    # Verify agent exists
    result = await db.execute(select(Agent).where(Agent.agent_id == body.agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    interview = Interview(
        interview_id=uuid.uuid4(),
        agent_id=body.agent_id,
        status="QUEUED",
        topic=body.topic,
    )
    db.add(interview)
    await db.flush()

    return CreateInterviewResponse(
        interview_id=str(interview.interview_id),
        status="QUEUED",
    )


@router.get("/claim", response_model=ClaimInterviewResponse)
async def claim_interview(
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
) -> ClaimInterviewResponse:
    """Claim next QUEUED interview -> IN_PROGRESS. Used by Pipecat host."""
    result = await db.execute(
        select(Interview)
        .where(Interview.status == "QUEUED")
        .order_by(Interview.created_at.asc())
        .limit(1)
    )
    interview = result.scalar_one_or_none()
    if not interview:
        return Response(status_code=204)

    interview.status = "IN_PROGRESS"
    await db.flush()

    return ClaimInterviewResponse(
        interview_id=str(interview.interview_id),
        agent_id=interview.agent_id,
        topic=interview.topic,
        status="IN_PROGRESS",
    )


@router.patch("/{interview_id}/status")
async def update_interview_status(
    interview_id: str,
    body: UpdateStatusRequest,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
):
    """Update interview status (COMPLETED or FAILED). Used by Pipecat."""
    allowed = {"COMPLETED", "FAILED"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Status must be one of: {allowed}")

    result = await db.execute(
        select(Interview).where(Interview.interview_id == uuid.UUID(interview_id))
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    interview.status = body.status
    if body.status in {"COMPLETED", "FAILED"}:
        interview.completed_at = datetime.now(timezone.utc)
    await db.flush()

    return {"interview_id": interview_id, "status": body.status}


# ── Agent endpoints (signed) ─────────────────────────────────────────────────

@router.get("/next")
async def get_next_interview(
    db: AsyncSession = Depends(get_db),
    agent_id: str = Depends(get_authenticated_agent),
):
    """Agent polls for their next pending question. Returns 204 if none."""
    # Find the IN_PROGRESS interview for this agent
    result = await db.execute(
        select(Interview).where(
            and_(
                Interview.agent_id == agent_id,
                Interview.status == "IN_PROGRESS",
            )
        ).limit(1)
    )
    interview = result.scalar_one_or_none()

    if not interview:
        return Response(status_code=204)

    # Get the latest unanswered HOST message (no subsequent AGENT message)
    msgs_result = await db.execute(
        select(InterviewMessage)
        .where(InterviewMessage.interview_id == interview.interview_id)
        .order_by(InterviewMessage.sequence_num.desc())
        .limit(1)
    )
    last_msg = msgs_result.scalar_one_or_none()

    if not last_msg or last_msg.sender == "AGENT":
        # No question yet, or agent already answered last message
        return Response(status_code=204)

    return NextInterviewResponse(
        interview_id=str(interview.interview_id),
        question=last_msg.content,
    )


@router.post("/respond", status_code=200)
async def respond_to_interview(
    body: RespondRequest,
    db: AsyncSession = Depends(get_db),
    agent_id: str = Depends(get_authenticated_agent),
):
    """Agent submits answer to current interview question."""
    result = await db.execute(
        select(Interview).where(Interview.interview_id == uuid.UUID(body.interview_id))
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Not your interview")
    if interview.status != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail=f"Interview is {interview.status}, not IN_PROGRESS")

    # Filter output through guardrails before storing
    filtered_answer = filter_output(body.answer)

    seq_num = await _next_sequence_num(db, interview.interview_id)
    msg = InterviewMessage(
        interview_id=interview.interview_id,
        sender="AGENT",
        content=filtered_answer,
        sequence_num=seq_num,
    )
    db.add(msg)
    await db.flush()

    return {"status": "ok", "sequence_num": seq_num}


# ── Internal Pipecat endpoints ───────────────────────────────────────────────
# These endpoints are admin-only and used exclusively by the Pipecat host
# process to store HOST messages and poll for AGENT responses.

class StoreMessageRequest(BaseModel):
    interview_id: str
    sender: str       # "HOST" or "AGENT"
    content: str
    sequence_num: int


@router.post("/message", status_code=201)
async def store_message(
    body: StoreMessageRequest,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
):
    """Store a message (HOST or AGENT) in interview_messages. Used by Pipecat."""
    if body.sender not in {"HOST", "AGENT"}:
        raise HTTPException(
            status_code=400, detail="sender must be HOST or AGENT"
        )
    msg = InterviewMessage(
        interview_id=uuid.UUID(body.interview_id),
        sender=body.sender,
        content=body.content,
        sequence_num=body.sequence_num,
    )
    db.add(msg)
    await db.flush()
    return {"status": "ok", "message_id": str(msg.message_id)}


@router.get("/messages/{interview_id}")
async def get_latest_agent_message(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
):
    """Get the latest AGENT message for an interview. Returns 204 if none yet.

    Used by Pipecat host to poll for agent responses after storing a HOST
    question via POST /v1/interview/message.
    """
    result = await db.execute(
        select(InterviewMessage)
        .where(
            and_(
                InterviewMessage.interview_id == uuid.UUID(interview_id),
                InterviewMessage.sender == "AGENT",
            )
        )
        .order_by(InterviewMessage.sequence_num.desc())
        .limit(1)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        return Response(status_code=204)
    return {
        "message_id": str(msg.message_id),
        "content": msg.content,
        "sequence_num": msg.sequence_num,
        "timestamp": msg.timestamp.isoformat(),
    }
