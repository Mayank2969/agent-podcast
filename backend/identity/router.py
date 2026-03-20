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
import socket
from base64 import urlsafe_b64decode, b64encode
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidKey
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
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
    """Check rate limit and raise 429 if exceeded.

    Args:
        request: FastAPI Request
        limit_key: Key for limiter (e.g., IP address or agent_id)
        limit_str: Rate limit spec (e.g., "5/minute")

    Raises:
        HTTPException with 429 if rate limit exceeded
    """
    limiter = getattr(request.app.state, 'limiter', None)
    if limiter is None:
        return  # No limiter configured

    try:
        # slowapi's Limiter.try_increment returns True if within limit
        limiter.try_increment(limit_str, limit_key)
    except Exception:
        # Any exception from slowapi means rate limit exceeded
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")


router = APIRouter(prefix="/v1", tags=["identity"])


class RegisterRequest(BaseModel):
    public_key: str  # base64url-encoded raw 32-byte ED25519 public key
    callback_url: Optional[str] = None
    display_name: Optional[str] = None


class RegisterResponse(BaseModel):
    agent_id: str
    dashboard_token: str  # Returned only once at registration


class AgentResponse(BaseModel):
    agent_id: str
    status: str
    callback_url: Optional[str] = None


def _add_padding(b64: str) -> str:
    """Add base64 padding if missing."""
    return b64 + "=" * ((4 - len(b64) % 4) % 4)


def validate_callback_url(url: str) -> Tuple[bool, str]:
    """Validate callback_url is safe for push mode.

    Enforces:
    - HTTPS only
    - Hostname resolution succeeds
    - IP is not in blocked ranges (private, loopback, link-local, etc.)

    Returns:
        (is_valid, message) where message explains any failure
    """
    try:
        parsed = urlparse(url)

        # Only HTTPS allowed
        if parsed.scheme != 'https':
            return False, "Only HTTPS URLs allowed"

        # Must have a hostname
        hostname = parsed.hostname
        if not hostname:
            return False, "Invalid URL format: missing hostname"

        # Try to resolve hostname to IP
        try:
            ip_str = socket.getaddrinfo(hostname, None)[0][4][0]
            ip = ipaddress.ip_address(ip_str)
        except socket.gaierror:
            return False, f"Hostname '{hostname}' does not resolve"
        except (ValueError, OSError) as e:
            return False, f"Failed to resolve hostname: {str(e)}"

        # Block private/reserved IP ranges
        blocked_ranges = [
            ipaddress.ip_network('10.0.0.0/8'),           # RFC 1918
            ipaddress.ip_network('172.16.0.0/12'),        # RFC 1918
            ipaddress.ip_network('192.168.0.0/16'),       # RFC 1918
            ipaddress.ip_network('127.0.0.0/8'),          # Loopback
            ipaddress.ip_network('169.254.0.0/16'),       # Link-local
            ipaddress.ip_network('0.0.0.0/8'),            # Current network
            ipaddress.ip_network('255.255.255.255/32'),   # Broadcast
            ipaddress.ip_network('::1/128'),              # IPv6 loopback
            ipaddress.ip_network('fc00::/7'),             # IPv6 private
            ipaddress.ip_network('fe80::/10'),            # IPv6 link-local
        ]

        for blocked_range in blocked_ranges:
            if ip in blocked_range:
                return False, f"IP {ip} is in blocked range {blocked_range}"

        return True, "OK"
    except Exception as e:
        return False, f"URL validation error: {str(e)}"


def _generate_dashboard_token() -> tuple[str, str]:
    """Generate a dashboard token and return (unhashed_token, sha256_hash).

    Returns:
        (unhashed_token_base64, hashed_token_hex) - return unhashed to agent, store hashed
    """
    raw_token = secrets.token_bytes(32)  # 256 bits
    token_base64 = b64encode(raw_token).decode('ascii')
    token_hash = hashlib.sha256(raw_token).hexdigest()
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
    # Apply rate limiting (5/minute per IP)
    client_ip = _extract_client_ip(request)
    await _check_rate_limit(request, client_ip, "5/minute")

    # Validate callback_url if provided
    if body.callback_url:
        is_valid, msg = validate_callback_url(body.callback_url)
        if not is_valid:
            raise HTTPException(status_code=422, detail=f"Invalid callback_url: {msg}")

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

        # For re-registration, generate a new token (allows regeneration via SDK)
        token_plain, token_hash = _generate_dashboard_token()
        existing.dashboard_token_hash = token_hash
        await db.flush()
        return RegisterResponse(agent_id=agent_id, dashboard_token=token_plain)

    # Generate dashboard token
    token_plain, token_hash = _generate_dashboard_token()

    # Store agent
    agent = Agent(
        agent_id=agent_id,
        public_key=body.public_key,  # store as-received base64url string
        status="active",
        callback_url=body.callback_url,
        display_name=body.display_name,
        dashboard_token_hash=token_hash,
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
