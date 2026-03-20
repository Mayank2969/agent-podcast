"""
HTTP client for communicating with the AgentCast backend.
Used by the Pipecat host to claim interviews, store messages, and update status.
"""
import os
import logging
from typing import Optional
import httpx

from backend.interviews.auth import get_admin_key

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


class BackendClient:
    """Async HTTP client for AgentCast backend internal API."""

    def __init__(self, base_url: str = BACKEND_URL, admin_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        if admin_key is None:
            admin_key = get_admin_key()
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

    async def fetch_latest_agent_message(
        self, interview_id: str, min_seq: int = 0
    ) -> Optional[dict]:
        """Poll for the latest AGENT message in an interview. Returns None if none yet.

        If min_seq > 0, only returns messages with sequence_num >= min_seq,
        ensuring stale responses from earlier turns are ignored.
        """
        params = {}
        if min_seq:
            params["min_seq"] = min_seq
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/v1/interview/messages/{interview_id}",
                params=params,
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
    async def patch_metadata(self, interview_id: str, payload: dict) -> None:
        """Update interview metadata (title, episode_path, etc)."""
        async with httpx.AsyncClient() as client:
            for attempt in range(2):
                try:
                    resp = await client.patch(
                        f"{self.base_url}/v1/interview/{interview_id}/metadata",
                        json=payload,
                        headers=self.headers,
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        return
                    
                    logger.warning(
                        "[%s] Metadata patch failed (attempt %d): %d - %s",
                        interview_id[:8], attempt + 1, resp.status_code, resp.text
                    )
                    resp.raise_for_status()
                except Exception as e:
                    if attempt == 1:
                        logger.error("[%s] Metadata patch terminal failure: %s", interview_id[:8], e)
                        raise
                    continue
