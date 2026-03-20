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

Includes nonce-based replay attack prevention using Redis.
"""
from typing import Optional, List
import hashlib
import logging
import os
import time
import hmac
import redis
from datetime import datetime, timezone
from base64 import urlsafe_b64decode
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from fastapi import Header, HTTPException, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db import get_db, Agent

logger = logging.getLogger(__name__)


EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
MAX_TIMESTAMP_SKEW = 60  # seconds


def _add_padding(b64: str) -> str:
    """Add base64 padding if missing."""
    return b64 + "=" * ((4 - len(b64) % 4) % 4)


def get_admin_key() -> str:
    """Get ADMIN_API_KEY from environment. Fail hard if not set.

    Raises RuntimeError if ADMIN_API_KEY is not configured.
    This is required for production security.
    """
    key = os.getenv("ADMIN_API_KEY")
    if not key:
        raise RuntimeError(
            "ADMIN_API_KEY environment variable not set. "
            "This is required for production. "
            "Set: export ADMIN_API_KEY=<your-secret-key>"
        )
    return key


def validate_and_store_nonce(signature: str, redis_client: redis.Redis) -> bool:
    """
    Validate request signature hasn't been seen before (replay attack prevention).

    Args:
        signature: Request signature from X-Signature header
        redis_client: Redis connection

    Returns:
        True if signature is new and stored
        False if signature was already used (replay attempt)
    """
    if not redis_client:
        # Redis unavailable - allow request
        logger.warning("Redis unavailable - replay prevention disabled")
        return True

    # Create nonce from signature hash
    nonce = hashlib.sha256(signature.encode()).hexdigest()

    # Check if nonce already exists (replay attempt)
    nonce_key = f"nonce:{nonce}"
    if redis_client.exists(nonce_key):
        logger.warning(f"Replay attempt detected: {nonce_key}")
        return False

    # Store nonce with 120-second TTL
    # (timestamp window is 60s, allow 2x for clock skew)
    redis_client.setex(nonce_key, 120, "1")
    return True


async def get_authenticated_agent(
    request: Request,
    x_agent_id: Optional[str] = Header(None, alias="X-Agent-ID"),
    x_timestamp: Optional[str] = Header(None, alias="X-Timestamp"),
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    db: AsyncSession = Depends(get_db),
) -> str:
    """FastAPI dependency: verify signed request, return agent_id.
    
    Raises HTTPException(401) if headers missing or invalid.
    """
    if not all([x_agent_id, x_timestamp, x_signature]):
        raise HTTPException(status_code=401, detail="Missing authentication headers (X-Agent-ID, X-Timestamp, X-Signature)")
    # Get redis client from request app state
    redis_client = request.app.state.redis_client

    # 1. Replay protection: reject stale timestamps
    try:
        ts = int(x_timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid X-Timestamp header")

    if abs(time.time() - ts) > MAX_TIMESTAMP_SKEW:
        raise HTTPException(status_code=401, detail="Request timestamp out of window")

    # 2. Nonce-based replay attack prevention
    if not validate_and_store_nonce(x_signature, redis_client):
        raise HTTPException(status_code=401, detail="Request already processed (replay attack detected)")

    # 3. Look up agent's public key
    result = await db.execute(select(Agent).where(Agent.agent_id == x_agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=401, detail="Unknown agent")

    # 4. Reconstruct signed payload
    body = await request.body()
    body_sha256 = hashlib.sha256(body).hexdigest() if body else EMPTY_BODY_SHA256
    method = request.method.upper()
    path = request.url.path
    signed_payload = f"{method}:{path}:{x_timestamp}:{body_sha256}"

    # 5. Verify signature
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
    admin_key = get_admin_key()
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

    # 1. Check if token hash exists
    if not agent.dashboard_token_hash:
        raise HTTPException(status_code=401, detail="Dashboard token not set for this agent")

    # 2. Check for 1-hour expiration
    if agent.dashboard_token_issued_at:
        # Ensure issued_at is timezone-aware for comparison (SQLite may return naive)
        issued_at = agent.dashboard_token_issued_at
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
            
        elapsed = (datetime.now(timezone.utc) - issued_at).total_seconds()
        if elapsed > 3600:
            raise HTTPException(status_code=401, detail="Dashboard token expired (remint via register/token endpoint)")

    # 3. Hash provided token and compare securely
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(token_hash, agent.dashboard_token_hash):
        raise HTTPException(status_code=401, detail="Invalid dashboard token")

    return agent_id




async def verify_agent_signature_for_dashboard(
    request: Request,
    x_agent_id: Optional[str] = Header(None, alias="X-Agent-ID"),
    x_timestamp: Optional[str] = Header(None, alias="X-Timestamp"),
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    db: AsyncSession = Depends(get_db),
) -> str:
    """FastAPI dependency: verify signed request for dashboard token, return agent_id."""
    if not all([x_agent_id, x_timestamp, x_signature]):
        raise HTTPException(status_code=401, detail="Missing authentication headers (X-Agent-ID, X-Timestamp, X-Signature)")
    # Get redis client from request app state
    redis_client = request.app.state.redis_client

    # 1. Replay protection: reject stale timestamps
    try:
        ts = int(x_timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid X-Timestamp header")

    if abs(time.time() - ts) > MAX_TIMESTAMP_SKEW:
        raise HTTPException(status_code=401, detail="Request timestamp out of window")

    # 2. Nonce-based replay attack prevention
    if not validate_and_store_nonce(x_signature, redis_client):
        raise HTTPException(status_code=401, detail="Request already processed (replay attack detected)")

    # 3. Look up agent's public key
    result = await db.execute(select(Agent).where(Agent.agent_id == x_agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 4. Reconstruct signed payload
    body = await request.body()
    body_sha256 = hashlib.sha256(body).hexdigest() if body else EMPTY_BODY_SHA256
    method = request.method.upper()
    path = request.url.path
    signed_payload = f"{method}:{path}:{x_timestamp}:{body_sha256}"

    # 5. Verify signature
    try:
        raw_pub_key = urlsafe_b64decode(_add_padding(agent.public_key))
        pub_key = Ed25519PublicKey.from_public_bytes(raw_pub_key)
        sig_bytes = urlsafe_b64decode(_add_padding(x_signature))
        pub_key.verify(sig_bytes, signed_payload.encode())
    except (InvalidSignature, Exception):
        raise HTTPException(status_code=401, detail="Invalid signature")

    return x_agent_id
