"""
Agent identity registration endpoint.

POST /v1/register
- No authentication required (bootstrap endpoint)
- Accepts base64url-encoded ED25519 public key
- Computes agent_id = SHA256(raw_public_key_bytes).hexdigest()
- Stores agent in DB
- Returns agent_id
"""
import hashlib
from base64 import urlsafe_b64decode

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidKey
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db import get_db, Agent
from backend.interviews.auth import get_admin

router = APIRouter(prefix="/v1", tags=["identity"])


class RegisterRequest(BaseModel):
    public_key: str  # base64url-encoded raw 32-byte ED25519 public key
    callback_url: Optional[str] = None
    display_name: Optional[str] = None


class RegisterResponse(BaseModel):
    agent_id: str


class AgentResponse(BaseModel):
    agent_id: str
    status: str
    callback_url: Optional[str] = None


def _add_padding(b64: str) -> str:
    """Add base64 padding if missing."""
    return b64 + "=" * ((4 - len(b64) % 4) % 4)


@router.post("/register", response_model=RegisterResponse, status_code=200)
async def register_agent(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    """Register an anonymous agent using its ED25519 public key.

    The agent_id is derived deterministically: SHA256(raw_public_key_bytes).
    No personal information is stored.
    """
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
        # Idempotent re-registration: update callback_url and display_name if provided
        if body.callback_url is not None:
            existing.callback_url = body.callback_url
        if body.display_name is not None:
            existing.display_name = body.display_name
        await db.flush()
        return RegisterResponse(agent_id=agent_id)

    # Store agent
    agent = Agent(
        agent_id=agent_id,
        public_key=body.public_key,  # store as-received base64url string
        status="active",
        callback_url=body.callback_url,
        display_name=body.display_name,
    )
    db.add(agent)
    await db.flush()  # flush to catch DB constraint errors before commit

    return RegisterResponse(agent_id=agent_id)


@router.get("/agent/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
) -> AgentResponse:
    """Fetch agent record including callback_url. Admin only.

    Used by the Pipecat host to determine whether to use push or pull mode
    before starting an interview.
    """
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return AgentResponse(
        agent_id=agent.agent_id,
        status=agent.status,
        callback_url=agent.callback_url,
    )
