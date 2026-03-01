"""
Transcript storage service.

Builds a structured transcript from interview_messages and stores it
in the transcripts table when an interview is COMPLETED.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db import Interview, InterviewMessage, Transcript

logger = logging.getLogger(__name__)


async def build_and_store_transcript(interview_id: str, db: AsyncSession) -> dict:
    """
    Build a structured transcript from interview messages and store it.

    Called when interview transitions to COMPLETED.

    Returns the transcript dict.
    """
    interview_uuid = uuid.UUID(interview_id)

    # Fetch the interview record
    result = await db.execute(
        select(Interview).where(Interview.interview_id == interview_uuid)
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise ValueError(f"Interview not found: {interview_id}")

    # Fetch all messages ordered by sequence_num
    msgs_result = await db.execute(
        select(InterviewMessage)
        .where(InterviewMessage.interview_id == interview_uuid)
        .order_by(InterviewMessage.sequence_num.asc())
    )
    messages = msgs_result.scalars().all()

    # Build transcript structure
    turns = [
        {
            "sequence_num": msg.sequence_num,
            "sender": msg.sender,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
        }
        for msg in messages
    ]

    transcript_data = {
        "interview_id": interview_id,
        "agent_id": interview.agent_id,
        "topic": interview.topic,
        "status": interview.status,
        "created_at": interview.created_at.isoformat() if interview.created_at else None,
        "completed_at": interview.completed_at.isoformat() if interview.completed_at else None,
        "turns": turns,
        "turn_count": len(turns),
    }

    # Check if transcript already exists (idempotent)
    existing = await db.execute(
        select(Transcript).where(Transcript.interview_id == interview_uuid)
    )
    existing_transcript = existing.scalar_one_or_none()

    if existing_transcript:
        logger.info("Transcript already exists for interview %s — updating", interview_id)
        existing_transcript.content = json.dumps(transcript_data)
        await db.flush()
        return transcript_data

    # Store new transcript
    transcript = Transcript(
        interview_id=interview_uuid,
        agent_id=interview.agent_id,
        content=json.dumps(transcript_data),
    )
    db.add(transcript)
    await db.flush()

    logger.info(
        "Transcript stored for interview %s (%d turns)",
        interview_id, len(turns)
    )
    return transcript_data
