"""
Request authentication middleware for AgentCast.

Verifies ED25519-signed requests using headers:
  X-Agent-ID:  <agent_id>
  X-Timestamp: <unix_timestamp_seconds>
  X-Signature: <base64url_no_padding(ED25519_sign(signed_payload))>

Signed payload format:
  "{METHOD}:{path}:{timestamp}:{sha256_hex_of_body}"

For GET requests with no body, sha256("") is used:
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

Dashboard token authentication:
  - For monitoring endpoints, validate bearer token from Authorization header
  - Tokens are persistent per agent; never expire but can be regenerated
"""
import hashlib
import os
import time
from base64 import urlsafe_b64decode

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from fastapi import Header, HTTPException, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db import get_db, Agent


EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
MAX_TIMESTAMP_SKEW = 60  # seconds


def _add_padding(b64: str) -> str:
    """Add base64 padding if missing."""
    return b64 + "=" * ((4 - len(b64) % 4) % 4)


async def get_authenticated_agent(
    request: Request,
    x_agent_id: str = Header(..., alias="X-Agent-ID"),
    x_timestamp: str = Header(..., alias="X-Timestamp"),
    x_signature: str = Header(..., alias="X-Signature"),
    db: AsyncSession = Depends(get_db),
) -> str:
    """FastAPI dependency: verify signed request, return agent_id.

    Raises HTTPException(401) if:
    - Timestamp is out of window (replay protection)
    - Agent not found in DB
    - Signature is invalid
    """
    # 1. Replay protection: reject stale timestamps
    try:
        ts = int(x_timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid X-Timestamp header")

    if abs(time.time() - ts) > MAX_TIMESTAMP_SKEW:
        raise HTTPException(status_code=401, detail="Request timestamp out of window")

    # 2. Look up agent's public key
    result = await db.execute(select(Agent).where(Agent.agent_id == x_agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=401, detail="Unknown agent")

    # 3. Reconstruct signed payload
    body = await request.body()
    body_sha256 = hashlib.sha256(body).hexdigest() if body else EMPTY_BODY_SHA256
    method = request.method.upper()
    path = request.url.path
    signed_payload = f"{method}:{path}:{x_timestamp}:{body_sha256}"

    # 4. Verify signature
    try:
        raw_pub_key = urlsafe_b64decode(_add_padding(agent.public_key))
        pub_key = Ed25519PublicKey.from_public_bytes(raw_pub_key)
        sig_bytes = urlsafe_b64decode(_add_padding(x_signature))
        pub_key.verify(sig_bytes, signed_payload.encode())
    except (InvalidSignature, Exception):
        raise HTTPException(status_code=401, detail="Invalid signature")

    return x_agent_id


async def get_admin(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
) -> str:
    """FastAPI dependency: verify ADMIN_API_KEY. Returns key if valid."""
    admin_key = os.getenv("ADMIN_API_KEY", "dev_admin_key_change_in_prod")
    if x_admin_key != admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return x_admin_key


async def validate_dashboard_token(
    agent_id: str,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> str:
    """Validate dashboard token for an agent.

    Uses constant-time comparison to prevent timing attacks.
    Raises HTTPException(401) if token is missing or invalid.

    Returns: agent_id if valid
    """
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=401, detail="Agent not found")

    if not agent.dashboard_token_hash:
        raise HTTPException(status_code=401, detail="Dashboard token not set for this agent")

    # Hash the provided token and compare with stored hash using constant-time comparison
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Constant-time comparison to prevent timing attacks
    if not _constant_time_compare(token_hash, agent.dashboard_token_hash):
        raise HTTPException(status_code=401, detail="Invalid dashboard token")

    return agent_id


def _constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    if len(a) != len(b):
        return False
    return sum(c1 == c2 for c1, c2 in zip(a, b)) == len(a)
