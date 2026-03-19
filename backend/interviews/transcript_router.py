"""
Transcript retrieval endpoint.

GET /v1/transcript/{interview_id}  — requires dashboard token auth
POST /v1/transcript/build          — internal, admin only (called by Pipecat on COMPLETED)
"""
import json
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from backend.db import get_db, Transcript
from backend.db.models import Interview, Agent
from backend.interviews.auth import get_admin, validate_dashboard_token
from backend.interviews.transcript import build_and_store_transcript

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/transcript", tags=["transcripts"])


class BuildTranscriptRequest(BaseModel):
    interview_id: str


@router.get("/{interview_id}")
async def get_transcript(
    interview_id: str,
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Retrieve the full interview transcript. Requires dashboard token authentication.

    Token can be provided via:
    - Query param: ?token=XXX
    - Authorization header: Bearer XXX
    """
    try:
        interview_uuid = uuid.UUID(interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format")

    # Fetch interview to get agent_id for token validation
    interview_result = await db.execute(
        select(Interview).where(Interview.interview_id == interview_uuid)
    )
    interview = interview_result.scalar_one_or_none()

    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Extract token from Authorization header or query param
    token_to_check = token
    if not token_to_check and authorization:
        # Extract from "Bearer XXX" format
        if authorization.startswith("Bearer "):
            token_to_check = authorization[7:]

    if not token_to_check:
        raise HTTPException(status_code=401, detail="Dashboard token required (use ?token=XXX or Authorization: Bearer XXX)")

    # Validate token against agent_id
    await validate_dashboard_token(interview.agent_id, token_to_check, db)

    # Now fetch transcript
    result = await db.execute(
        select(Transcript).where(Transcript.interview_id == interview_uuid)
    )
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    agent = None
    if interview:
        agent_result = await db.execute(
            select(Agent).where(Agent.agent_id == interview.agent_id)
        )
        agent = agent_result.scalar_one_or_none()

    guest_name = (
        agent.display_name
        if agent and agent.display_name
        else (interview.agent_id[:12] if interview else "unknown")
    )

    metadata: dict = {}
    if interview and interview.metadata:
        try:
            metadata = json.loads(interview.metadata)
        except Exception:
            metadata = {}

    content = json.loads(transcript.content)

    return {
        "title": interview.title if interview else None,
        "guest_name": guest_name,
        "episode_path": interview.episode_path if interview else None,
        "metadata": metadata,
        **content,
        "messages": content.get("turns", []),  # Backward compatibility for dashboard
    }


@router.post("/build", status_code=200)
async def build_transcript(
    body: BuildTranscriptRequest,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
):
    """Build and store transcript for a completed interview. Called by Pipecat host."""
    try:
        transcript_data = await build_and_store_transcript(body.interview_id, db)
        return {"status": "ok", "interview_id": body.interview_id, "turn_count": transcript_data["turn_count"]}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to build transcript for %s: %s", body.interview_id, e)
        raise HTTPException(status_code=500, detail="Failed to build transcript")
