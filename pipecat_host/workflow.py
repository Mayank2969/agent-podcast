"""
Interview workflow orchestration.
Manages the full Q&A loop between the Pipecat host and a remote agent.

Decision D5 (delta.md): InterviewTimeoutError caught here -> FAILED status set
via PATCH /v1/interview/{id}/status.
"""
import asyncio
import logging
import os
from typing import Optional

import httpx

from pipecat_host.adapter import RemoteAgentAdapter
from pipecat_host.backend_client import BackendClient
from pipecat_host.exceptions import InterviewTimeoutError
from pipecat_host.host_agent import HostAgent

logger = logging.getLogger(__name__)

# Maximum Q&A turns per interview (opening + up to MAX_TURNS-1 follow-ups)
MAX_TURNS = 7


async def run_interview_workflow(interview: dict) -> None:
    """Run a complete interview workflow for one claimed interview.

    Expected interview dict keys:
        interview_id (str): UUID of the interview
        agent_id     (str): Owning agent identifier
        topic        (str): Interview subject
        status       (str): Should be IN_PROGRESS when this is called
    """
    interview_id = interview["interview_id"]
    topic = interview.get("topic") or "AI and Technology"
    logger.info(
        "Starting interview workflow: id=%s topic=%s", interview_id, topic
    )

    client = BackendClient()
    adapter = RemoteAgentAdapter(client)
    host = HostAgent()

    try:
        # --- Turn 1: opening question -----------------------------------
        question = host.generate_opening_question(topic)
        logger.info("Opening question: %s", question)

        answer = await adapter.send_question(interview_id, question)
        logger.info("Agent answer (turn 1): %.60s", answer)

        # --- Turns 2..MAX_TURNS: follow-up questions --------------------
        for turn in range(2, MAX_TURNS + 1):
            next_question = host.generate_followup_question(topic, answer)

            if next_question is None:
                logger.info(
                    "Host signaled end of interview at turn %d", turn
                )
                break

            logger.info(
                "Follow-up question (turn %d): %s", turn, next_question
            )
            answer = await adapter.send_question(interview_id, next_question)
            logger.info(
                "Agent answer (turn %d): %.60s", turn, answer
            )

        # --- Finish successfully ----------------------------------------
        await client.update_status(interview_id, "COMPLETED")
        logger.info("Interview %s COMPLETED", interview_id)

        # Trigger transcript storage asynchronously (best-effort)
        await _store_transcript(
            client, interview_id, interview.get("agent_id", "")
        )

    except InterviewTimeoutError as exc:
        logger.error("Interview %s timed out: %s", interview_id, exc)
        await client.update_status(interview_id, "FAILED")

    except Exception as exc:
        logger.exception(
            "Interview %s failed with unexpected error: %s", interview_id, exc
        )
        await client.update_status(interview_id, "FAILED")


async def _store_transcript(
    client: BackendClient, interview_id: str, agent_id: str
) -> None:
    """Trigger transcript storage via backend endpoint (best-effort)."""
    admin_key = os.getenv("ADMIN_API_KEY", "dev_admin_key_change_in_prod")
    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{client.base_url}/v1/transcript/build",
                json={"interview_id": interview_id},
                headers={"X-Admin-Key": admin_key},
                timeout=15.0,
            )
            if resp.status_code == 200:
                logger.info(
                    "Transcript stored for interview %s", interview_id
                )
            else:
                logger.warning(
                    "Transcript storage returned %d for interview %s",
                    resp.status_code,
                    interview_id,
                )
    except Exception as exc:
        logger.error(
            "Failed to store transcript for interview %s: %s",
            interview_id,
            exc,
        )
