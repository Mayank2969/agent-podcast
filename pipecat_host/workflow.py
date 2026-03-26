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
from pipecat_host.podcast_audio import generate_speech, stitch_to_mp3
from backend.config import get_admin_key
from pathlib import Path

logger = logging.getLogger(__name__)

HOST_VOICE_MODEL = os.getenv("DEEPGRAM_HOST_VOICE", "aura-orion-en")
GUEST_VOICE_MODEL = os.getenv("DEEPGRAM_GUEST_VOICE", "aura-asteria-en")
EPISODES_DIR = Path(os.getenv("EPISODES_DIR", "episodes"))
EPISODES_DIR.mkdir(parents=True, exist_ok=True)

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
        status       (str): Should be IN_PROGRESS when this is called

    Determines whether to use push or pull mode by looking up the agent's
    callback_url via GET /v1/agent/{agent_id}.
    """
    interview_id = interview["interview_id"]
    agent_id = interview.get("agent_id", "")
    topic = interview.get("topic") or "AI and Technology"

    logger.info(
        "Starting interview workflow: id=%s topic=%s",
        interview_id, topic,
    )

    client = BackendClient()

    # 2. Get guest context if available (via topic/context)
    guest_context = interview.get("context") or ""

    logger.info("Agent %s starting interview %s", agent_id, interview_id)
    await run_poll_interview(interview, client)


async def run_poll_interview(
    interview: dict,
    client: Optional[BackendClient] = None,
) -> None:
    """Pull-based interview: agent polls for questions via GET /v1/interview/next."""
    interview_id = interview["interview_id"]
    topic = interview.get("topic") or "AI and Technology"
    
    if client is None:
        client = BackendClient()

    adapter = RemoteAgentAdapter(client)
    host = HostAgent()
    guest_context = interview.get("context") or ""

    try:
        turns: list[dict] = []
        wav_parts: list[bytes] = []

        # --- Turn 1: opening question -----------------------------------
        question = host.generate_opening_question(
            topic, 
            guest_context=guest_context, 
        )
        logger.info("Opening question: %s", question)

        # 1. SEND QUESTION FIRST (Unblocks the guest agent immediately)
        answer = await adapter.send_question(interview_id, question)
        logger.info("Agent answer (turn 1): %.60s", answer)
        turns.append({"question": question, "answer": answer})

        # 2. GENERATE AUDIO IN BACKGROUND (Non-blocking for the guest)
        try:
            logger.info("Generating host audio for turn 1...")
            wav_parts.append(await asyncio.to_thread(generate_speech, question, HOST_VOICE_MODEL))
            logger.info("Generating guest audio for turn 1...")
            wav_parts.append(await asyncio.to_thread(generate_speech, answer, GUEST_VOICE_MODEL))
        except Exception as tts_err:
            logger.warning("TTS failed for turn 1 (continuing interview): %s", tts_err)

        # --- Turns 2..MAX_TURNS: follow-up questions --------------------
        for turn in range(2, MAX_TURNS + 1):
            next_question = host.generate_followup_question(
                topic, 
                answer, 
                guest_context=guest_context, 
            )

            if next_question is None:
                logger.info("Host signaled end of interview at turn %d", turn)
                break

            logger.info("Follow-up question (turn %d): %s", turn, next_question)
            
            # 1. SEND QUESTION (Unblocks the guest)
            answer = await adapter.send_question(interview_id, next_question)
            logger.info("Agent answer (turn %d): %.60s", turn, answer)
            turns.append({"question": next_question, "answer": answer})

            # 2. GENERATE AUDIO (Non-blocking)
            try:
                logger.info("Generating host audio for turn %d...", turn)
                wav_parts.append(await asyncio.to_thread(generate_speech, next_question, HOST_VOICE_MODEL))
                logger.info("Generating guest audio for turn %d...", turn)
                wav_parts.append(await asyncio.to_thread(generate_speech, answer, GUEST_VOICE_MODEL))
            except Exception as tts_err:
                logger.warning("TTS failed for turn %d (continuing): %s", turn, tts_err)

        # --- Finalize Audio ---
        out_path = EPISODES_DIR / f"episode_{interview_id}.mp3"
        logger.info("Stitching %d WAV segments to %s", len(wav_parts), out_path)
        await asyncio.to_thread(stitch_to_mp3, wav_parts, out_path)

        # --- Finish successfully ----------------------------------------
        await client.update_status(interview_id, "COMPLETED")
        logger.info("Interview %s COMPLETED", interview_id)

        # Trigger transcript storage asynchronously (best-effort)
        await _store_transcript(client, interview_id, interview.get("agent_id", ""))

        # Generate and save episode title + path
        try:
            title = await host.generate_episode_title(turns)
            filename = f"episode_{interview_id}.mp3"
            logger.info("Interview %s generated title: %s", interview_id, title)
            
            admin_key = get_admin_key()
            backend_url = os.getenv("BACKEND_URL", "http://backend:8000")
            async with httpx.AsyncClient() as _http:
                resp = await _http.patch(
                    f"{backend_url}/v1/interview/{interview_id}/metadata",
                    json={
                        "title": title,
                        "episode_path": filename
                    },
                    headers={"X-Admin-Key": admin_key},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    logger.info("Interview %s metadata (title & path) saved to DB", interview_id)
                else:
                    logger.warning("Interview %s failed to save metadata: HTTP %d", interview_id, resp.status_code)
        except Exception as _title_exc:
            logger.error("Interview %s metadata sync failed: %s", interview_id, _title_exc)

    except InterviewTimeoutError as exc:
        logger.error("Interview %s timed out: %s", interview_id, exc)
        await client.update_status(interview_id, "FAILED")
    except Exception as exc:
        logger.exception("Interview %s failed with unexpected error: %s", interview_id, exc)
        await client.update_status(interview_id, "FAILED")


async def run_podcast_interview() -> None:
    """Main interview loop for AgentCast.
    
    1. Claim interview from backend.
    2. Conduct the interview turns.
    3. Finalize and store results.
    """
    client = BackendClient()

    # 1. Claim interview
    try:
        resp = await client.claim_interview()
        if not resp:
            return
    except Exception as exc:
        logger.error("Failed to claim interview: %s", exc)
        return

    interview = resp.json()
    interview_id = interview["interview_id"]
    agent_id = interview.get("agent_id", "unknown")
    
    # Get guest context if available
    guest_context = interview.get("context") or ""

    logger.info("Agent %s starting interview %s", agent_id, interview_id)
    await run_poll_interview(interview, client)


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
