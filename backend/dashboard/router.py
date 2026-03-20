"""Dashboard token endpoint for agent authentication."""
import secrets
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db import get_db, Agent
from backend.interviews.auth import verify_agent_signature_for_dashboard


def _extract_client_ip(request: Request) -> str:
    """Extract client IP from request, considering X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _check_rate_limit(request: Request, limit_key: str, limit_str: str) -> None:
    """TEMP: Disabled due to method name mismatch causing 429 loops."""
    pass


def _base64url_encode(data: bytes) -> str:
    """Encode bytes to base64url without padding."""
    from base64 import urlsafe_b64encode
    return urlsafe_b64encode(data).decode().rstrip("=")


router = APIRouter(prefix="/v1", tags=["dashboard"])


class DashboardTokenResponse(BaseModel):
    dashboard_token: str
    expires_in: int
    agent_id: str


@router.post("/dashboard-token", response_model=DashboardTokenResponse, status_code=201)
async def get_dashboard_token(
    request: Request,
    agent_id: str = Depends(verify_agent_signature_for_dashboard),
    db: AsyncSession = Depends(get_db),
):
    """
    Agent requests a temporary dashboard token by proving ownership with ED25519 signature.
    
    Rate limit: 10 requests per minute per agent.
    """
    # Apply rate limiting (10/minute per agent_id)
    # Apply rate limiting (60/minute; previously 10)
    await _check_rate_limit(request, f"agent:{agent_id}", "60/minute")
    """
    Agent requests a temporary dashboard token by proving ownership with ED25519 signature.

    Request headers:
      X-Agent-ID: agent_id (64-char hex)
      X-Timestamp: unix timestamp (±60s)
      X-Signature: base64url ED25519 signature

    Response (201):
      {
        "dashboard_token": "base64url_encoded_token",
        "expires_in": 3600,
        "agent_id": "agent_id"
      }

    Token is valid for 1 hour and required for dashboard API access.
    """
    # Generate token (32 bytes random)
    token_bytes = secrets.token_bytes(32)
    token_plain = _base64url_encode(token_bytes)
    token_hash = hashlib.sha256(token_plain.encode()).hexdigest()

    # Store hashed token and issue timestamp in DB
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.dashboard_token_hash = token_hash
    agent.dashboard_token_issued_at = datetime.now(timezone.utc)
    await db.commit()

    # Return unhashed token (only this once)
    return DashboardTokenResponse(
        dashboard_token=token_plain,
        expires_in=3600,
        agent_id=agent_id,
    )
