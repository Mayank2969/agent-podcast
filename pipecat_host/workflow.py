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

# Push mode polling: how often to check for agent's response (seconds)
PUSH_POLL_INTERVAL = 2.0
# Push mode: maximum time to wait for agent response after push notification
PUSH_ANSWER_TIMEOUT = 120.0


class _PushDeliveryError(Exception):
    """Raised when a question cannot be delivered to the agent's callback_url."""


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
        # --- Turn 1: opening question -----------------------------------
        question = host.generate_opening_question(topic, github_repo_url=github_repo_url)
        logger.info("Opening question: %s", question)

        answer = await adapter.send_question(interview_id, question)
        logger.info("Agent answer (turn 1): %.60s", answer)

        # --- Turns 2..MAX_TURNS: follow-up questions --------------------
        for turn in range(2, MAX_TURNS + 1):
            next_question = host.generate_followup_question(
                topic, answer, github_repo_url=github_repo_url
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
            logger.info(
                "Agent answer (turn %d): %.60s", turn, answer
            )

        # --- Finish successfully ----------------------------------------
        await client.update_status(interview_id, "COMPLETED")
        logger.info("Interview %s COMPLETED (pull mode)", interview_id)

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


async def run_push_interview(
    interview: dict,
    agent_callback_url: str,
    client: Optional[BackendClient] = None,
    github_repo_url: Optional[str] = None,
) -> None:
    """Push-based interview: host POSTs questions directly to agent's callback_url.

    Flow per turn:
    1. Generate question via HostAgent (same as pull mode)
    2. POST question to agent_callback_url
    3. Poll backend for agent's response (via GET /v1/interview/messages/{id})
    4. Repeat until done or MAX_TURNS reached

    If the POST to callback_url fails (network error or non-2xx response),
    the interview is marked FAILED immediately.
    """
    interview_id = interview["interview_id"]
    topic = interview.get("topic") or "AI and Technology"
    if github_repo_url is None:
        github_repo_url = interview.get("github_repo_url")

    if client is None:
        client = BackendClient()

    host = HostAgent()

    try:
        # --- Turn 1: opening question -----------------------------------
        question = host.generate_opening_question(topic, github_repo_url=github_repo_url)
        logger.info("Push mode — opening question: %s", question)

        answer = await _push_question_and_wait(
            client, interview_id, question, agent_callback_url, sequence_num=1
        )
        logger.info("Agent answer (turn 1, push): %.60s", answer)

        # --- Turns 2..MAX_TURNS: follow-up questions --------------------
        for turn in range(2, MAX_TURNS + 1):
            next_question = host.generate_followup_question(
                topic, answer, github_repo_url=github_repo_url
            )

            if next_question is None:
                logger.info(
                    "Host signaled end of interview at turn %d (push mode)", turn
                )
                break

            logger.info(
                "Push mode — follow-up question (turn %d): %s", turn, next_question
            )
            answer = await _push_question_and_wait(
                client, interview_id, next_question, agent_callback_url,
                sequence_num=turn * 2 - 1,
            )
            logger.info(
                "Agent answer (turn %d, push): %.60s", turn, answer
            )

        # --- Finish successfully ----------------------------------------
        await client.update_status(interview_id, "COMPLETED")
        logger.info("Interview %s COMPLETED (push mode)", interview_id)

        # Trigger transcript storage asynchronously (best-effort)
        await _store_transcript(
            client, interview_id, interview.get("agent_id", "")
        )

    except InterviewTimeoutError as exc:
        logger.error("Interview %s timed out (push mode): %s", interview_id, exc)
        await client.update_status(interview_id, "FAILED")

    except _PushDeliveryError as exc:
        logger.error(
            "Interview %s failed: could not deliver question to callback_url %s: %s",
            interview_id, agent_callback_url, exc,
        )
        await client.update_status(interview_id, "FAILED")

    except Exception as exc:
        logger.exception(
            "Interview %s failed with unexpected error (push mode): %s", interview_id, exc
        )
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
      2. POST the question payload to the agent's callback_url.
      3. Poll GET /v1/interview/messages/{interview_id} until an AGENT message
         with sequence_num >= sequence_num + 1 appears or timeout fires.

    Returns the agent's answer string.
    Raises _PushDeliveryError on callback POST failure.
    Raises InterviewTimeoutError on poll timeout.
    """
    # Step 1: persist the HOST question in the DB
    await client.store_message(
        interview_id=interview_id,
        sender="HOST",
        content=question,
        sequence_num=sequence_num,
    )

    # Step 2: push the question to the agent's callback URL
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
    except httpx.RequestError as exc:
        raise _PushDeliveryError(f"Network error posting to callback_url: {exc}") from exc

    # Step 3: poll backend for agent response
    elapsed = 0.0
    expected_answer_seq = sequence_num + 1

    while elapsed < PUSH_ANSWER_TIMEOUT:
        msg = await client.fetch_latest_agent_message(interview_id)
        if msg and msg.get("sequence_num", 0) >= expected_answer_seq:
            return msg["content"]

        await asyncio.sleep(PUSH_POLL_INTERVAL)
        elapsed += PUSH_POLL_INTERVAL

    raise InterviewTimeoutError(
        f"Timed out waiting for agent response after {PUSH_ANSWER_TIMEOUT}s "
        f"(interview {interview_id}, turn sequence {sequence_num})"
    )


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
