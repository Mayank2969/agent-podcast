"""
HTTP client for communicating with the AgentCast backend.
Used by the Pipecat host to claim interviews, store messages, and update status.
"""
import os
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "dev_admin_key_change_in_prod")


class BackendClient:
    """Async HTTP client for AgentCast backend internal API."""

    def __init__(self, base_url: str = BACKEND_URL, admin_key: str = ADMIN_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Admin-Key": admin_key, "Content-Type": "application/json"}

    async def claim_interview(self) -> Optional[dict]:
        """Claim next QUEUED interview -> IN_PROGRESS. Returns interview dict or None."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/v1/interview/claim",
                headers=self.headers,
                timeout=10.0,
            )
            if resp.status_code == 204:
                return None
            # FastAPI raises 422 when HTTPException(204) is used — treat as no content
            if resp.status_code == 422:
                return None
            resp.raise_for_status()
            return resp.json()

    async def store_message(
        self, interview_id: str, sender: str, content: str, sequence_num: int
    ) -> None:
        """Store a HOST or AGENT message in the interview_messages table."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/v1/interview/message",
                json={
                    "interview_id": interview_id,
                    "sender": sender,
                    "content": content,
                    "sequence_num": sequence_num,
                },
                headers=self.headers,
                timeout=10.0,
            )
            resp.raise_for_status()

    async def fetch_latest_agent_message(self, interview_id: str) -> Optional[dict]:
        """Poll for the latest AGENT message in an interview. Returns None if none yet."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/v1/interview/messages/{interview_id}",
                headers=self.headers,
                timeout=10.0,
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            return resp.json()

    async def update_status(self, interview_id: str, status: str) -> None:
        """Set interview status to COMPLETED or FAILED."""
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{self.base_url}/v1/interview/{interview_id}/status",
                json={"status": status},
                headers=self.headers,
                timeout=10.0,
            )
            resp.raise_for_status()
