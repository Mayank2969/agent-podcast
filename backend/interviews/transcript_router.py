"""
Transcript retrieval endpoint.

GET /v1/transcript/{interview_id}  — public, no auth required
POST /v1/transcript/build          — internal, admin only (called by Pipecat on COMPLETED)
"""
import json
import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from backend.db import get_db, Transcript
from backend.interviews.auth import get_admin
from backend.interviews.transcript import build_and_store_transcript

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/transcript", tags=["transcripts"])


class BuildTranscriptRequest(BaseModel):
    interview_id: str


@router.get("/{interview_id}")
async def get_transcript(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the full interview transcript. Public endpoint — no auth required."""
    try:
        interview_uuid = uuid.UUID(interview_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview_id format")

    result = await db.execute(
        select(Transcript).where(Transcript.interview_id == interview_uuid)
    )
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return json.loads(transcript.content)


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
