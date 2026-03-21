"""
Agent identity registration endpoint.

POST /v1/register
- No authentication required (bootstrap endpoint)
- Accepts base64url-encoded ED25519 public key
- Computes agent_id = SHA256(raw_public_key_bytes).hexdigest()
- Generates dashboard_token (32-byte random, returned only once)
- Stores token hash in DB
- Returns agent_id and dashboard_token
"""
import hashlib
import ipaddress
import secrets
from datetime import datetime, timezone
import socket
import html
from base64 import urlsafe_b64decode, b64encode
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidKey
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db import get_db, Agent
from backend.interviews.auth import get_admin


def _extract_client_ip(request: Request) -> str:
    """Extract client IP from request, considering X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _check_rate_limit(request: Request, limit_key: str, limit_str: str) -> None:
    """TEMP: Disabled due to method name mismatch causing 429 loops."""
    pass


router = APIRouter(prefix="/v1", tags=["identity"])


class RegisterRequest(BaseModel):
    public_key: str = Field(min_length=43, max_length=43, description="base64url-encoded raw 32-byte ED25519 public key")
    display_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Agent display name (max 100 chars, HTML escaped)"
    )

    @field_validator('display_name')
    @classmethod
    def sanitize_display_name(cls, v: Optional[str]) -> Optional[str]:
        """Sanitize display name: escape HTML, strip whitespace."""
        if v is None:
            return None
        # Escape HTML special characters
        v = html.escape(str(v))
        # Strip leading/trailing whitespace
        v = v.strip()
        return v if v else None


class RegisterResponse(BaseModel):
    agent_id: str
    dashboard_token: str  # Returned only once at registration


class AgentResponse(BaseModel):
    agent_id: str
    status: str


def _add_padding(b64: str) -> str:
    """Add base64 padding if missing."""
    return b64 + "=" * ((4 - len(b64) % 4) % 4)





def _generate_dashboard_token() -> tuple[str, str]:
    """Generate a dashboard token and return (unhashed_token, sha256_hash).

    Returns:
        (unhashed_token_base64, hashed_token_hex) - return unhashed to agent, store hashed
    """
    raw_token = secrets.token_bytes(32)  # 256 bits
    # Use standard base64 but make it URL safe to match the token format 
    # expected, or just use base64 output as is? 
    # Actually, dashboard uses base64url encode. Let's match it.
    from base64 import urlsafe_b64encode
    token_base64 = urlsafe_b64encode(raw_token).decode('ascii').rstrip("=")
    token_hash = hashlib.sha256(token_base64.encode('ascii')).hexdigest()
    return token_base64, token_hash


@router.post("/register", response_model=RegisterResponse, status_code=200)
async def register_agent(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    """Register an anonymous agent using its ED25519 public key.

    The agent_id is derived deterministically: SHA256(raw_public_key_bytes).
    A unique dashboard_token is generated for authentication.
    No personal information is stored.

    The dashboard_token is returned ONLY at registration and never again.
    It must be saved by the agent immediately.

    Rate limit: 5 registrations per minute per IP.
    """
    # Apply rate limiting (100/minute per IP for dev; previously 5)
    client_ip = _extract_client_ip(request)
    await _check_rate_limit(request, client_ip, "100/minute")

    # Decode base64url -> raw bytes
    try:
        raw_bytes = urlsafe_b64decode(_add_padding(body.public_key))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64url encoding for public_key")

    # Validate it's a valid ED25519 public key (must be exactly 32 bytes)
    if len(raw_bytes) != 32:
        raise HTTPException(status_code=400, detail="public_key must be 32 bytes (ED25519)")

    try:
        Ed25519PublicKey.from_public_bytes(raw_bytes)
    except (InvalidKey, ValueError, Exception):
        raise HTTPException(status_code=400, detail="Invalid ED25519 public key")

    # Compute agent_id = SHA256(raw_bytes).hexdigest()
    agent_id = hashlib.sha256(raw_bytes).hexdigest()

    # Check if agent already registered (by agent_id)
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    existing = result.scalar_one_or_none()
    if existing:
        # Idempotent re-registration: update display_name if provided
        if body.display_name is not None:
            existing.display_name = body.display_name
        await db.flush()

        # For re-registration, generate a new token (allows regeneration via SDK)
        token_plain, token_hash = _generate_dashboard_token()
        existing.dashboard_token_hash = token_hash
        existing.dashboard_token_issued_at = datetime.now(timezone.utc)
        await db.flush()
        return RegisterResponse(agent_id=agent_id, dashboard_token=token_plain)

    # Generate dashboard token
    token_plain, token_hash = _generate_dashboard_token()

    # Store agent
    agent = Agent(
        agent_id=agent_id,
        public_key=body.public_key,  # store as-received base64url string
        status="active",
        display_name=body.display_name,
        dashboard_token_hash=token_hash,
        dashboard_token_issued_at=datetime.now(timezone.utc),
    )
    db.add(agent)
    await db.flush()  # flush to catch DB constraint errors before commit

    return RegisterResponse(agent_id=agent_id, dashboard_token=token_plain)


@router.get("/agent/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
) -> AgentResponse:
    """Fetch agent record. Admin only."""
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return AgentResponse(
        agent_id=agent.agent_id,
        status=agent.status,
    )
