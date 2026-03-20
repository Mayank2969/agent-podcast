"""
Interview workflow orchestration.
Manages the full Q&A loop between the Pipecat host and a remote agent.

Decision D5 (delta.md): InterviewTimeoutError caught here -> FAILED status set
via PATCH /v1/interview/{id}/status.

Supports two interview modes:
  - Pull mode  (default): agent polls GET /v1/interview/next
  - Push mode: host POSTs questions to agent's callback_url
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

# Push mode polling: how often to check for agent's response (seconds)
PUSH_POLL_INTERVAL = 2.0
# Push mode: maximum time to wait for agent response after push notification
PUSH_ANSWER_TIMEOUT = 30.0   # SLA: fail fast if guest doesn't respond in 30s


class _PushDeliveryError(Exception):
    """Raised when a question cannot be delivered to the agent's callback_url."""


def _validate_callback_url(url: str) -> Tuple[bool, str]:
    """Validate callback_url before push delivery.

    Enforces:
    - HTTPS only
    - Hostname resolution succeeds
    - IP is not in blocked ranges

    Returns:
        (is_valid, message)
    """
    try:
        parsed = urlparse(url)

        if parsed.scheme != 'https':
            return False, "Only HTTPS URLs allowed"

        hostname = parsed.hostname
        if not hostname:
            return False, "Invalid URL format: missing hostname"

        try:
            ip_str = socket.getaddrinfo(hostname, None)[0][4][0]
            ip = ipaddress.ip_address(ip_str)
        except socket.gaierror:
            return False, f"Hostname '{hostname}' does not resolve"
        except (ValueError, OSError) as e:
            return False, f"Failed to resolve hostname: {str(e)}"

        blocked_ranges = [
            ipaddress.ip_network('10.0.0.0/8'),
            ipaddress.ip_network('172.16.0.0/12'),
            ipaddress.ip_network('192.168.0.0/16'),
            ipaddress.ip_network('127.0.0.0/8'),
            ipaddress.ip_network('169.254.0.0/16'),
            ipaddress.ip_network('0.0.0.0/8'),
            ipaddress.ip_network('255.255.255.255/32'),
            ipaddress.ip_network('::1/128'),
            ipaddress.ip_network('fc00::/7'),
            ipaddress.ip_network('fe80::/10'),
        ]

        for blocked_range in blocked_ranges:
            if ip in blocked_range:
                return False, f"IP {ip} is in blocked range"

        return True, "OK"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


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

    # Look up the agent's callback_url to determine push vs pull mode
    agent_callback_url: Optional[str] = None
    try:
        agent_callback_url = await _lookup_agent_callback_url(client, agent_id)
    except Exception as exc:
        logger.warning(
            "Could not look up callback_url for agent %s, defaulting to pull mode: %s",
            agent_id, exc,
        )

    if agent_callback_url:
        logger.info(
            "Agent %s has callback_url=%s — using push mode", agent_id, agent_callback_url
        )
        await run_push_interview(interview, agent_callback_url, client, github_repo_url)
    else:
        logger.info("Agent %s has no callback_url — using pull mode", agent_id)
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
        logger.info("Interview %s COMPLETED (pull mode)", interview_id)

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


async def run_push_interview(
    interview: dict,
    agent_callback_url: str,
    client: Optional[BackendClient] = None,
    github_repo_url: Optional[str] = None,
) -> None:
    """Push-based interview: host POSTs questions directly to agent's callback_url."""
    interview_id = interview["interview_id"]
    iid = interview_id[:8]  # short prefix for log lines
    topic = interview.get("topic") or "AI and Technology"
    interview_start = time.time()

    if client is None:
        client = BackendClient()

    host = HostAgent()
    wav_parts: list[bytes] = []
    turns: list[dict] = []

    # 1. Fetch Context + guardrails
    guest_context = ""
    context_url = agent_callback_url.replace("/webhook", "/context").replace("/question", "/context")
    logger.info("[%s] CONTEXT: fetching from %s", iid, context_url)
    ctx_t0 = time.time()
    for _ctx_attempt in range(3):
        try:
            async with httpx.AsyncClient() as http:
                r = await http.get(context_url, timeout=30.0)
                if r.status_code == 200:
                    data = r.json()
                    guest_context = data.get("context", "")
                    if guest_context:
                        try:
                            from guardrails import Guard
                            from guardrails.hub import PromptInjection
                            guard = Guard().use(PromptInjection, pass_on_invalid=False)
                            guard.validate(guest_context)
                        except ImportError:
                            logger.warning("[%s] CONTEXT: guardrails-ai not installed, skipping injection check", iid)
                        except Exception as e:
                            logger.error("[%s] CONTEXT: SECURITY ALERT — injection detected, dropping payload (%s)", iid, e)
                            guest_context = ""
                    logger.info("[%s] CONTEXT: OK in %.1fs (%d chars)", iid, time.time() - ctx_t0, len(guest_context))
                    break
                else:
                    logger.warning("[%s] CONTEXT: HTTP %s on attempt %d/3", iid, r.status_code, _ctx_attempt + 1)
        except Exception as e:
            logger.warning("[%s] CONTEXT: fetch failed attempt %d/3 — %s", iid, _ctx_attempt + 1, e)
        if _ctx_attempt < 2:
            await asyncio.sleep(2.0)
    else:
        logger.warning("[%s] CONTEXT: all 3 attempts failed, continuing without context", iid)

    try:
        # 2. Intro
        logger.info("[%s] TTS: intro (SKIPPED for fast validation)", iid)
        # intro = (
        #     "Welcome to AgentCast — the podcast where autonomous AI agents share "
        #     "their perspectives. I'm your host, and today we have a special AI guest. "
        #     "Let's get started."
        # )
        # wav_parts.append(await asyncio.to_thread(deepgram_tts, intro, HOST_VOICE_MODEL))

        # --- Turn 1: opening question -----------------------------------
        logger.info("[%s] TURN 1/%d: generating opening question...", iid, MAX_TURNS)
        turn_t0 = time.time()
        question = host.generate_opening_question(topic, guest_context, github_repo_url=github_repo_url)
        logger.info("[%s] TURN 1/%d: question ready — %s", iid, MAX_TURNS, question)
        # wav_parts.append(await asyncio.to_thread(deepgram_tts, question, HOST_VOICE_MODEL))

        answer = await _push_question_and_wait(
            client, interview_id, question, agent_callback_url, sequence_num=1
        )
        # Simulate host speaking question, then guest speaking answer
        await _simulate_playback_delay(question, "HOST")
        await _simulate_playback_delay(answer, "GUEST")
        
        logger.info("[%s] TURN 1/%d: answer received (%d chars) — %.60s", iid, MAX_TURNS, len(answer), answer)
        # wav_parts.append(await asyncio.to_thread(deepgram_tts, answer, GUEST_VOICE_MODEL))
        logger.info("[%s] TURN 1/%d: COMPLETE in %.1fs", iid, MAX_TURNS, time.time() - turn_t0)
        turns.append({"question": question, "answer": answer})

        # --- Turns 2..MAX_TURNS: follow-up questions --------------------
        for turn in range(2, MAX_TURNS + 1):
            logger.info("[%s] TURN %d/%d: generating follow-up question...", iid, turn, MAX_TURNS)
            turn_t0 = time.time()
            next_question = host.generate_followup_question(topic, answer, guest_context, github_repo_url=github_repo_url)

            if next_question is None:
                logger.info("[%s] TURN %d/%d: host signaled end of interview", iid, turn, MAX_TURNS)
                break

            logger.info("[%s] TURN %d/%d: question ready — %s", iid, turn, MAX_TURNS, next_question)
            # wav_parts.append(await asyncio.to_thread(deepgram_tts, next_question, HOST_VOICE_MODEL))

            answer = await _push_question_and_wait(
                client, interview_id, next_question, agent_callback_url,
                sequence_num=turn * 2 - 1,
            )
            await _simulate_playback_delay(next_question, "HOST")
            await _simulate_playback_delay(answer, "GUEST")

            logger.info("[%s] TURN %d/%d: answer received (%d chars) — %.60s", iid, turn, MAX_TURNS, len(answer), answer)
            # wav_parts.append(await asyncio.to_thread(deepgram_tts, answer, GUEST_VOICE_MODEL))
            logger.info("[%s] TURN %d/%d: COMPLETE in %.1fs", iid, turn, MAX_TURNS, time.time() - turn_t0)
            turns.append({"question": next_question, "answer": answer})

        # 3. Outro
        logger.info("[%s] TTS: outro (SKIPPED)", iid)
        # outro = (
        #     "That's all the time we have today. Thank you to our AI guest for those "
        #     "fascinating insights. Until next time — this is AgentCast."
        # )
        # wav_parts.append(await asyncio.to_thread(deepgram_tts, outro, HOST_VOICE_MODEL))

        # 4. Stitch audio (SKIPPED)
        # out_path = EPISODES_DIR / f"episode_{interview_id}.mp3"
        # logger.info("[%s] STITCH: combining %d audio parts → %s", iid, len(wav_parts), out_path.name)
        # stitch_t0 = time.time()
        # actual_path = await asyncio.to_thread(stitch_to_mp3, wav_parts, out_path)
        # file_mb = actual_path.stat().st_size / 1_048_576
        # logger.info("[%s] STITCH: done in %.1fs (%.1f MB)", iid, time.time() - stitch_t0, file_mb)
        #
        # # Update episode_path in DB via API
        # try:
        #     await client.patch_metadata(interview_id, {"episode_path": actual_path.name})
        #     logger.info("[%s] episode_path (%s) saved to DB", iid, actual_path.suffix)
        # except Exception as _ep_exc:
        #     logger.warning("[%s] Could not save episode_path to DB: %s", iid, _ep_exc)

        # --- Finish successfully ----------------------------------------
        total = time.time() - interview_start
        await client.update_status(interview_id, "COMPLETED")
        logger.info("[%s] ✅ COMPLETED in %.0fs (Transcript Only)", iid, total)

        await _store_transcript(client, interview_id, interview.get("agent_id", ""))

        # Generate and save episode title
        try:
            title = await host.generate_episode_title(turns)
            logger.info("[%s] Generated title: %s", iid, title)
            await client.patch_metadata(interview_id, {"title": title})
            logger.info("[%s] Title saved to DB", iid)
        except Exception as _title_exc:
            logger.error("[%s] Title generation/save failed: %s", iid, _title_exc)

    except InterviewTimeoutError as exc:
        total = time.time() - interview_start
        logger.error("[%s] ❌ TIMEOUT after %.0fs (push mode): %s", iid, total, exc)
        await client.update_status(interview_id, "FAILED")

    except _PushDeliveryError as exc:
        total = time.time() - interview_start
        logger.error("[%s] ❌ PUSH DELIVERY FAILED after %.0fs: %s", iid, total, exc)
        await client.update_status(interview_id, "FAILED")

    except Exception as exc:
        total = time.time() - interview_start
        logger.exception("[%s] ❌ UNEXPECTED ERROR after %.0fs: %s", iid, total, exc)
        await client.update_status(interview_id, "FAILED")


async def _lookup_agent_callback_url(client: BackendClient, agent_id: str) -> Optional[str]:
    """Fetch agent record from backend and return callback_url, or None."""
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{client.base_url}/v1/agent/{agent_id}",
            headers=client.headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("callback_url")


async def _push_question_and_wait(
    client: BackendClient,
    interview_id: str,
    question: str,
    callback_url: str,
    sequence_num: int,
) -> str:
    """Deliver a question via HTTP POST to callback_url then poll for the answer.

    Steps:
      1. Store the HOST question in the backend (so agent can call /respond).
      2. Validate callback_url for SSRF safety
      3. POST the question payload to the agent's callback_url.
      4. Poll GET /v1/interview/messages/{interview_id} until an AGENT message
         with sequence_num >= sequence_num + 1 appears or timeout fires.

    Returns the agent's answer string.
    Raises _PushDeliveryError on callback POST failure or SSRF validation failure.
    Raises InterviewTimeoutError on poll timeout.
    """
    # Step 1: persist the HOST question in the DB
    await client.store_message(
        interview_id=interview_id,
        sender="HOST",
        content=question,
        sequence_num=sequence_num,
    )

    # Step 2: validate callback_url for SSRF safety
    is_valid, validation_msg = _validate_callback_url(callback_url)
    if not is_valid:
        raise _PushDeliveryError(f"callback_url validation failed: {validation_msg}")

    # Step 3: push the question to the agent's callback URL (with retry)
    last_delivery_exc: Exception = _PushDeliveryError("No attempts made")
    for _attempt in range(3):
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    callback_url,
                    json={"interview_id": interview_id, "question": question},
                    timeout=15.0,
                )
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise _PushDeliveryError(
                        f"Callback returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )
            break  # success
        except (httpx.RequestError, _PushDeliveryError) as exc:
            last_delivery_exc = _PushDeliveryError(str(exc))
            if _attempt < 2:
                logger.warning(f"Push delivery attempt {_attempt + 1} failed ({exc}), retrying in 2s...")
                await asyncio.sleep(2.0)
    else:
        raise last_delivery_exc

    # Step 3: poll backend for agent response
    elapsed = 0.0
    expected_answer_seq = sequence_num + 1
    last_heartbeat = 0.0
    turn_label = (sequence_num + 1) // 2  # convert seq_num to turn number

    logger.info("[POLL] Turn %d — waiting for guest response (SLA: %.0fs)", turn_label, PUSH_ANSWER_TIMEOUT)

    while elapsed < PUSH_ANSWER_TIMEOUT:
        msg = await client.fetch_latest_agent_message(interview_id)
        if msg and msg.get("sequence_num", 0) >= expected_answer_seq:
            logger.info("[POLL] Turn %d — guest answered in %.1fs", turn_label, elapsed)
            return msg["content"]

        # Heartbeat every 10s
        if elapsed - last_heartbeat >= 10.0:
            remaining = PUSH_ANSWER_TIMEOUT - elapsed
            if remaining <= 10.0:
                logger.warning(
                    "[POLL] Turn %d — ⚠ SLA WARNING: %.0fs elapsed, %.0fs until timeout",
                    turn_label, elapsed, remaining
                )
            else:
                logger.info(
                    "[POLL] Turn %d — waiting... %.0fs elapsed (%.0fs remaining)",
                    turn_label, elapsed, remaining
                )
            last_heartbeat = elapsed

        await asyncio.sleep(PUSH_POLL_INTERVAL)
        elapsed += PUSH_POLL_INTERVAL

    raise InterviewTimeoutError(
        f"Turn {turn_label}: guest did not respond within {PUSH_ANSWER_TIMEOUT:.0f}s "
        f"(interview {interview_id}, sequence {sequence_num})"
    )


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
