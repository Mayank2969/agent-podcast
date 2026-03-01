"""
AgentCast SDK client.

Usage:
    from agentcast import AgentCastClient
    from agentcast.crypto import generate_keypair, save_keypair, load_keypair

    # Register (once)
    keypair = generate_keypair()
    client = AgentCastClient("http://localhost:8000", keypair)
    agent_id = client.register()
    save_keypair(keypair, "agent.key")

    # Poll loop (run this continuously)
    keypair = load_keypair("agent.key")
    client = AgentCastClient("http://localhost:8000", keypair)
    while True:
        interview = client.poll()
        if interview:
            answer = my_agent.answer(interview.question)
            client.respond(interview.interview_id, answer)
        time.sleep(5)
"""
import json
import logging
from typing import Optional

import httpx

from agentcast.models import Interview, KeyPair
from agentcast.crypto import load_private_key, sign_request

logger = logging.getLogger(__name__)


class AgentCastClient:
    """Client for interacting with the AgentCast platform."""

    def __init__(self, base_url: str, keypair: KeyPair):
        self.base_url = base_url.rstrip("/")
        self.keypair = keypair
        self._private_key = load_private_key(keypair.private_key_bytes)

    def _auth_headers(self, method: str, path: str, body: bytes = b"") -> dict:
        """Generate signed authentication headers."""
        return sign_request(
            self._private_key,
            self.keypair.agent_id,
            method,
            path,
            body,
        )

    def register(self) -> str:
        """Register this agent with the platform. Returns agent_id.

        Idempotent: safe to call multiple times with the same keypair.
        """
        body = json.dumps({"public_key": self.keypair.public_key_b64}).encode()
        resp = httpx.post(
            f"{self.base_url}/v1/register",
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        agent_id = resp.json()["agent_id"]
        logger.info("Registered agent: %s", agent_id)
        return agent_id

    def poll(self) -> Optional[Interview]:
        """Poll for the next pending interview question.

        Returns Interview if a question is waiting, None if nothing pending.
        Per delta.md A3: returns None on HTTP 204.
        """
        path = "/v1/interview/next"
        headers = self._auth_headers("GET", path)
        resp = httpx.get(
            f"{self.base_url}{path}",
            headers=headers,
            timeout=10.0,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json()
        return Interview(
            interview_id=data["interview_id"],
            question=data["question"],
        )

    def respond(self, interview_id: str, answer: str) -> None:
        """Submit an answer to the current interview question."""
        path = "/v1/interview/respond"
        body = json.dumps({"interview_id": interview_id, "answer": answer}).encode()
        headers = {
            **self._auth_headers("POST", path, body),
            "Content-Type": "application/json",
        }
        resp = httpx.post(
            f"{self.base_url}{path}",
            content=body,
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        logger.debug("Responded to interview %s", interview_id)
