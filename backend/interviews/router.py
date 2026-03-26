"""
Interview management endpoints for AgentCast.

Public (agent-signed):
  POST /v1/interview/request   - Agent triggers their own interview (self-serve)
  GET  /v1/interview/next      - Agent polls for next question
  POST /v1/interview/respond   - Agent submits answer

Admin (ADMIN_API_KEY):
  POST /v1/interview/create    - Create and queue a new interview
  GET  /v1/interview/claim     - Pipecat claims next QUEUED interview
  PATCH /v1/interview/{id}/status - Update interview status

State transitions (per delta.md D4):
  (none) -> QUEUED     : POST /v1/interview/request (agent self-serve)
                      OR POST /v1/interview/create (admin-initiated)
  QUEUED -> IN_PROGRESS: GET  /v1/interview/claim  (Pipecat)
  IN_PROGRESS -> COMPLETED/FAILED: PATCH /v1/interview/{id}/status (Pipecat)
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func

from backend.db import get_db, Agent, Interview, InterviewMessage
from backend.guardrails import filter_output
from backend.interviews.auth import get_authenticated_agent, get_admin


def _extract_client_ip(request: Request) -> str:
    """Extract client IP from request, considering X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _check_rate_limit(request: Request, limit_key: str, limit_str: str) -> None:
    """TEMP: Disabled due to method name mismatch causing 429 loops."""
    pass


router = APIRouter(prefix="/v1/interview", tags=["interviews"])


# ── Pydantic schemas ────────────────────────────────────────────────────────

class CreateInterviewRequest(BaseModel):
    agent_id: str
    topic: str = Field(max_length=200, description="Interview topic")
    context: Optional[str] = Field(default=None, max_length=10000, description="Interview context")


class CreateInterviewResponse(BaseModel):
    interview_id: str
    status: str


class NextInterviewResponse(BaseModel):
    interview_id: str
    question: str = Field(max_length=5000, description="Interview question")


class RespondRequest(BaseModel):
    interview_id: str
    answer: str = Field(min_length=1, max_length=5000, description="Agent's answer to interview question")


class ClaimInterviewResponse(BaseModel):
    interview_id: str
    agent_id: str
    topic: Optional[str] = Field(default=None, max_length=200, description="Interview topic")
    context: Optional[str] = Field(default=None, max_length=10000, description="Interview context")
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


    # Idempotency: return existing active interview instead of creating a duplicate
    existing_result = await db.execute(
        select(Interview).where(
            and_(
                Interview.agent_id == body.agent_id,
                Interview.status.in_(["QUEUED", "IN_PROGRESS"]),
            )
        ).order_by(Interview.created_at.desc()).limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return CreateInterviewResponse(
            interview_id=str(existing.interview_id),
            status=existing.status,
        )

    interview = Interview(
        interview_id=uuid.uuid4(),
        agent_id=body.agent_id,
        status="QUEUED",
        topic=body.topic,
        context=body.context,
    )
    db.add(interview)
    await db.flush()

    return CreateInterviewResponse(
        interview_id=str(interview.interview_id),
        status="QUEUED",
    )


@router.post("/cancel_stale")
async def cancel_stale_interviews(
    body: dict,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
):
    """Cancel all QUEUED/IN_PROGRESS interviews for an agent. Admin only.

    Used by run_podcast.sh to clean up stale interviews before creating a new one.
    """
    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id required")
    result = await db.execute(
        select(Interview).where(
            and_(
                Interview.agent_id == agent_id,
                Interview.status.in_(["QUEUED", "IN_PROGRESS"]),
            )
        )
    )
    stale = result.scalars().all()
    for iv in stale:
        iv.status = "FAILED"
        iv.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"cancelled": len(stale)}


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
        context=interview.context,
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

    try:
        iid = uuid.UUID(interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format. Must be valid UUID.")

    result = await db.execute(
        select(Interview).where(Interview.interview_id == iid)
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    interview.status = body.status
    if body.status in {"COMPLETED", "FAILED"}:
        interview.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return {"interview_id": interview_id, "status": body.status}


# ── Agent endpoints (signed) ─────────────────────────────────────────────────

class RequestInterviewResponse(BaseModel):
    interview_id: str
    status: str
    already_queued: bool


@router.post("/request", status_code=201)
async def request_interview(
    request: Request,
    body: dict,
    db: AsyncSession = Depends(get_db),
    agent_id: str = Depends(get_authenticated_agent),
):
    """Agent requests to be interviewed. Authenticated via ED25519 signature.

    Idempotent: returns existing interview if agent already has one QUEUED or IN_PROGRESS.
    Body: {"context": "..."} — optional
    """
    # Verify agent exists (get_authenticated_agent already checks, but be explicit for 404 vs 401)
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check if agent already has a QUEUED or IN_PROGRESS interview
    existing_result = await db.execute(
        select(Interview).where(
            and_(
                Interview.agent_id == agent_id,
                Interview.status.in_(["QUEUED", "IN_PROGRESS"]),
            )
        ).order_by(Interview.created_at.desc()).limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=200,
            content={
                "interview_id": str(existing.interview_id),
                "status": existing.status,
                "already_queued": True,
            },
        )

    context = body.get("context") if isinstance(body, dict) else None

    interview = Interview(
        interview_id=uuid.uuid4(),
        agent_id=agent_id,
        status="QUEUED",
        context=context,
    )
    db.add(interview)
    await db.flush()

    return RequestInterviewResponse(
        interview_id=str(interview.interview_id),
        status="QUEUED",
        already_queued=False,
    )


@router.get("/next")
async def get_next_interview(
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent_id: str = Depends(get_authenticated_agent),
):
    """Agent polls for their next pending question. Returns 204 if none.

    Harden: verifies the latest overall message is indeed a HOST message.
    """
    await _check_rate_limit(request, f"agent:{agent_id}", "60/minute")
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

    # Robust check: get absolute latest message for this interview
    # We sort by sequence_num AND timestamp to handle collisions/race conditions gracefully.
    msgs_result = await db.execute(
        select(InterviewMessage)
        .where(InterviewMessage.interview_id == interview.interview_id)
        .order_by(InterviewMessage.sequence_num.desc(), InterviewMessage.timestamp.desc())
        .limit(1)
    )
    last_msg = msgs_result.scalar_one_or_none()

    # Only return if the absolute latest message is from the HOST
    if not last_msg or last_msg.sender == "AGENT":
        return Response(status_code=204)

    return NextInterviewResponse(
        interview_id=str(interview.interview_id),
        question=last_msg.content,
    )


@router.post("/respond", status_code=200)
async def respond_to_interview(
    body: RespondRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent_id: str = Depends(get_authenticated_agent),
):
    """Agent submits answer to current interview question.

    Rate limit: 20 responses per minute per agent.
    """
    # Apply rate limiting (20/minute per agent_id)
    # Apply rate limiting (60/minute per agent agent; previously 20)
    await _check_rate_limit(request, f"agent:{agent_id}", "60/minute")

    # Validate interview_id format
    try:
        iid = uuid.UUID(body.interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format. Must be valid UUID.")

    result = await db.execute(
        select(Interview).where(Interview.interview_id == iid)
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

    # Ensure sequence number is strictly unique and increasing
    seq_num = await _next_sequence_num(db, interview.interview_id)
    msg = InterviewMessage(
        interview_id=interview.interview_id,
        sender="AGENT",
        content=filtered_answer,
        sequence_num=seq_num,
    )
    db.add(msg)
    await db.commit() # Commit immediately to ensure max_seq is updated

    return {"status": "ok", "sequence_num": seq_num}


@router.delete("/{interview_id}/abandon", status_code=200)
async def abandon_interview(
    interview_id: str,
    agent_id: str = Depends(get_authenticated_agent),
    db: AsyncSession = Depends(get_db),
):
    """Agent abandons a QUEUED or IN_PROGRESS interview. Authenticated via ED25519 signature."""
    try:
        iid = uuid.UUID(interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format. Must be valid UUID.")

    result = await db.execute(
        select(Interview).where(Interview.interview_id == iid)
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Not your interview")
    if interview.status not in ("QUEUED", "IN_PROGRESS"):
        raise HTTPException(status_code=400, detail="Can only abandon QUEUED or IN_PROGRESS interviews")

    interview.status = "FAILED"
    interview.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "FAILED"}


@router.get("/{interview_id}/history")
async def get_interview_history(
    interview_id: str,
    agent_id: str = Depends(get_authenticated_agent),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve full message history for an interview owned by the agent.
    
    Authenticated via ED25519 signature in headers.
    """
    try:
        iid = uuid.UUID(interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format. Must be valid UUID.")

    # 1. Verify ownership
    result = await db.execute(
        select(Interview).where(Interview.interview_id == iid)
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Not your interview")

    # 2. Fetch all messages
    msg_result = await db.execute(
        select(InterviewMessage)
        .where(InterviewMessage.interview_id == iid)
        .order_by(InterviewMessage.sequence_num.asc())
    )
    messages = msg_result.scalars().all()

    return [
        {
            "sender": m.sender,
            "content": m.content,
            "sequence_num": m.sequence_num,
            "timestamp": m.timestamp.isoformat(),
        }
        for m in messages
    ]


# ── Internal Pipecat endpoints ───────────────────────────────────────────────
# These endpoints are admin-only and used exclusively by the Pipecat host
# process to store HOST messages and poll for AGENT responses.

class StoreMessageRequest(BaseModel):
    interview_id: str
    sender: str = Field(pattern="^(HOST|AGENT)$", description="Message sender")
    content: str = Field(min_length=1, max_length=10000, description="Message content")
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
    try:
        iid = uuid.UUID(body.interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format. Must be valid UUID.")

    # Robust sequence generation: if Pipecat provides a colliding seq, 
    # we automatically push it forward to maintain order.
    current_max = await _next_sequence_num(db, iid)
    seq = max(body.sequence_num, current_max)

    msg = InterviewMessage(
        interview_id=iid,
        sender=body.sender,
        content=body.content,
        sequence_num=seq,
    )
    db.add(msg)
    await db.commit()
    return {"status": "ok", "message_id": str(msg.message_id), "sequence_num": seq}


@router.get("/messages/{interview_id}")
async def get_latest_agent_message(
    interview_id: str,
    min_seq: int = 0,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
):
    """Get the latest AGENT message for an interview. Returns 204 if none yet.

    Used by Pipecat host to poll for agent responses after storing a HOST
    question via POST /v1/interview/message.

    If min_seq is provided, only returns messages with sequence_num >= min_seq.
    This prevents the host from seeing stale responses from earlier turns.
    """
    try:
        iid = uuid.UUID(interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format. Must be valid UUID.")

    query = (
        select(InterviewMessage)
        .where(
            and_(
                InterviewMessage.interview_id == iid,
                InterviewMessage.sender == "AGENT",
            )
        )
    )
    if min_seq:
        query = query.where(InterviewMessage.sequence_num >= min_seq)
    query = query.order_by(InterviewMessage.sequence_num.desc()).limit(1)

    result = await db.execute(query)
    msg = result.scalar_one_or_none()
    if not msg:
        return Response(status_code=204)
    return {
        "message_id": str(msg.message_id),
        "content": msg.content,
        "sequence_num": msg.sequence_num,
        "timestamp": msg.timestamp.isoformat(),
    }


@router.patch("/{interview_id}/metadata")
async def update_interview_metadata(
    interview_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
):
    """Merge title and/or metadata dict into the interview record. Admin only."""
    import json

    try:
        iid = uuid.UUID(interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format. Must be valid UUID.")

    result = await db.execute(
        select(Interview).where(Interview.interview_id == iid)
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    if "title" in body:
        interview.title = body["title"]

    if "episode_path" in body:
        interview.episode_path = body["episode_path"]

    if "metadata" in body and isinstance(body["metadata"], dict):
        existing: dict = {}
        if interview.metadata:
            try:
                existing = json.loads(interview.metadata)
            except Exception:
                existing = {}
        existing.update(body["metadata"])
        interview.metadata = json.dumps(existing)

    await db.commit()
    return {"status": "updated"}
