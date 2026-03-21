"""
Interview workflow orchestration.
Manages the full Q&A loop between the Pipecat host and a remote agent.

Decision D5 (delta.md): InterviewTimeoutError caught here -> FAILED status set
via PATCH /v1/interview/{id}/status.

Supports one interview mode:
  - Pull mode: agent polls GET /v1/interview/next
"""
import asyncio
import ipaddress
import logging
import os
import socket
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx

from pipecat_host.adapter import RemoteAgentAdapter
from pipecat_host.backend_client import BackendClient
from pipecat_host.exceptions import InterviewTimeoutError
from pipecat_host.host_agent import HostAgent
from pipecat_host.podcast_audio import deepgram_tts, stitch_to_mp3
from backend.config import get_admin_key
from pathlib import Path

logger = logging.getLogger(__name__)

HOST_VOICE_MODEL = os.getenv("DEEPGRAM_HOST_VOICE", "aura-orion-en")
GUEST_VOICE_MODEL = os.getenv("DEEPGRAM_GUEST_VOICE", "aura-asteria-en")
EPISODES_DIR = Path("/app/episodes")

# Maximum Q&A turns per interview (opening + up to MAX_TURNS-1 follow-ups)
MAX_TURNS = 6


async def _simulate_playback_delay(text: str, role: str) -> None:
    """Simulate the time it would take to speak the given text.
    
    Average speaking rate is ~150 words per minute (2.5 words per second).
    We add a 1.5s base "thinking/breathing" buffer.
    """
    if not text:
        return
    word_count = len(text.split())
    duration = (word_count / 2.5) + 1.5
    logger.info("[SIM] Simulating %s playback: %.1fs (%d words)", role, duration, word_count)
    await asyncio.sleep(duration)


async def run_interview_workflow(interview: dict) -> None:
    """Run a complete interview workflow for one claimed interview.

    Expected interview dict keys:
        interview_id (str): UUID of the interview
        agent_id     (str): Owning agent identifier
        topic        (str): Interview subject
        github_repo_url (str|None): Optional GitHub repo URL for context
        status       (str): Should be IN_PROGRESS when this is called

    Determines whether to use push or pull mode by looking up the agent's
    callback_url via GET /v1/agent/{agent_id}.
    """
    interview_id = interview["interview_id"]
    agent_id = interview.get("agent_id", "")
    topic = interview.get("topic") or "AI and Technology"
    github_repo_url = interview.get("github_repo_url")

    logger.info(
        "Starting interview workflow: id=%s topic=%s github_repo_url=%s",
        interview_id, topic, github_repo_url,
    )

    client = BackendClient()

    logger.info("Agent %s starting interview %s", agent_id, interview_id)
    await run_poll_interview(interview, client, github_repo_url)


async def run_poll_interview(
    interview: dict,
    client: Optional[BackendClient] = None,
    github_repo_url: Optional[str] = None,
) -> None:
    """Pull-based interview: agent polls for questions via GET /v1/interview/next.

    This is the original implementation, unchanged in behaviour.
    """
    interview_id = interview["interview_id"]
    topic = interview.get("topic") or "AI and Technology"
    # Allow github_repo_url to be passed directly or from interview dict
    if github_repo_url is None:
        github_repo_url = interview.get("github_repo_url")

    if client is None:
        client = BackendClient()

    adapter = RemoteAgentAdapter(client)
    host = HostAgent()

    try:
        turns: list[dict] = []

        # --- Turn 1: opening question -----------------------------------
        guest_context = interview.get("context") or ""
        question = host.generate_opening_question(topic, guest_context, github_repo_url=github_repo_url)
        logger.info("Opening question: %s", question)

        answer = await adapter.send_question(interview_id, question)
        # Simulate host speaking question, then guest speaking answer
        await _simulate_playback_delay(question, "HOST")
        await _simulate_playback_delay(answer, "GUEST")
        
        logger.info("Agent answer (turn 1): %.60s", answer)
        turns.append({"question": question, "answer": answer})

        # --- Turns 2..MAX_TURNS: follow-up questions --------------------
        for turn in range(2, MAX_TURNS + 1):
            next_question = host.generate_followup_question(
                topic, answer, guest_context, github_repo_url=github_repo_url
            )

            if next_question is None:
                logger.info(
                    "Host signaled end of interview at turn %d", turn
                )
                break

            logger.info(
                "Follow-up question (turn %d): %s", turn, next_question
            )
            answer = await adapter.send_question(interview_id, next_question)
            await _simulate_playback_delay(next_question, "HOST")
            await _simulate_playback_delay(answer, "GUEST")
            
            logger.info("Agent answer (turn %d): %.60s", turn, answer)
            turns.append({"question": next_question, "answer": answer})

        # --- Finish successfully ----------------------------------------
        await client.update_status(interview_id, "COMPLETED")
        logger.info("Interview %s COMPLETED", interview_id)

        # Trigger transcript storage asynchronously (best-effort)
        await _store_transcript(
            client, interview_id, interview.get("agent_id", "")
        )

        # Generate and save episode title
        try:
            title = await host.generate_episode_title(turns)
            logger.info("Interview %s generated title: %s", interview_id, title)
            admin_key = get_admin_key()
            backend_url = os.getenv("BACKEND_URL", "http://backend:8000")
            async with httpx.AsyncClient() as _http:
                resp = await _http.patch(
                    f"{backend_url}/v1/interview/{interview_id}/metadata",
                    json={"title": title},
                    headers={"X-Admin-Key": admin_key},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    logger.info("Interview %s title saved to DB", interview_id)
                else:
                    logger.warning(
                        "Interview %s failed to save title: HTTP %d",
                        interview_id, resp.status_code,
                    )
        except Exception as _title_exc:
            logger.error("Interview %s title generation/save failed: %s", interview_id, _title_exc)

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
    admin_key = get_admin_key()
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
