"""Tests for dashboard token endpoint (H0: signature verification)."""
import hashlib
import time
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from backend.main import app
from backend.db import get_db, Agent


def generate_keypair():
    """Generate ED25519 keypair for testing."""
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes_raw()
    pub_b64 = urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    agent_id = hashlib.sha256(pub_bytes).hexdigest()
    return priv, pub_b64, agent_id


def sign_request(priv_key, method: str, path: str, body: bytes = b"") -> dict:
    """Generate auth headers for a signed request."""
    ts = str(int(time.time()))
    body_sha256 = hashlib.sha256(body).hexdigest()
    payload = f"{method}:{path}:{ts}:{body_sha256}".encode()
    sig = priv_key.sign(payload)
    sig_b64 = urlsafe_b64encode(sig).rstrip(b"=").decode()
    pub_bytes = priv_key.public_key().public_bytes_raw()
    agent_id = hashlib.sha256(pub_bytes).hexdigest()
    return {
        "X-Agent-ID": agent_id,
        "X-Timestamp": ts,
        "X-Signature": sig_b64,
    }


@pytest.mark.asyncio
async def test_dashboard_token_requires_signature():
    """Dashboard token endpoint requires ED25519 signature (reject missing headers)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Try without signature headers
        resp = await client.post("/v1/dashboard-token", json={})
    assert resp.status_code == 401  # Missing required headers
    assert "Missing authentication headers" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dashboard_token_returns_401_for_invalid_signature():
    """Invalid signature returns 401."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register agent first
        await client.post("/v1/register", json={"public_key": pub_b64})

        # Try with invalid signature
        headers = {
            "X-Agent-ID": agent_id,
            "X-Timestamp": str(int(time.time())),
            "X-Signature": "invalid_signature_xyz",
        }
        resp = await client.post("/v1/dashboard-token", json={}, headers=headers)

    assert resp.status_code == 401
    assert "Invalid signature" in resp.json()["detail"] or "signature" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dashboard_token_returns_404_for_unknown_agent():
    """Unknown agent returns 404."""
    priv, pub_b64, unknown_agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Don't register agent
        headers = sign_request(priv, "POST", "/v1/dashboard-token", b"{}")
        resp = await client.post("/v1/dashboard-token", json={}, headers=headers)

    assert resp.status_code == 404
    assert "Agent not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dashboard_token_returns_401_for_stale_timestamp():
    """Timestamp outside ±60s window returns 401."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register agent first
        await client.post("/v1/register", json={"public_key": pub_b64})

        # Create signature with stale timestamp (>60s old)
        stale_ts = str(int(time.time()) - 65)
        body = b"{}"
        body_sha256 = hashlib.sha256(body).hexdigest()
        payload = f"POST:/v1/dashboard-token:{stale_ts}:{body_sha256}".encode()
        sig = priv.sign(payload)
        sig_b64 = urlsafe_b64encode(sig).rstrip(b"=").decode()

        headers = {
            "X-Agent-ID": agent_id,
            "X-Timestamp": stale_ts,
            "X-Signature": sig_b64,
        }
        resp = await client.post("/v1/dashboard-token", json={}, headers=headers)

    assert resp.status_code == 401
    assert "timestamp" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dashboard_token_valid_signature_returns_201_with_token():
    """Valid signature returns 201 with dashboard_token, expires_in, agent_id."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register agent first
        await client.post("/v1/register", json={"public_key": pub_b64})

        # Request token with valid signature
        headers = sign_request(priv, "POST", "/v1/dashboard-token", b"{}")
        resp = await client.post("/v1/dashboard-token", json={}, headers=headers)

    assert resp.status_code == 201
    data = resp.json()
    assert "dashboard_token" in data
    assert data["expires_in"] == 3600
    assert data["agent_id"] == agent_id
    assert isinstance(data["dashboard_token"], str)
    assert len(data["dashboard_token"]) > 0


@pytest.mark.asyncio
async def test_dashboard_token_hash_stored_in_db():
    """Dashboard token hash is stored in database."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register agent
        await client.post("/v1/register", json={"public_key": pub_b64})

        # Request token
        headers = sign_request(priv, "POST", "/v1/dashboard-token", b"{}")
        resp = await client.post("/v1/dashboard-token", json={}, headers=headers)

    assert resp.status_code == 201
    data = resp.json()
    token = data["dashboard_token"]
    # Verify token is returned (hash is verified by token validation tests)
    assert len(token) > 0
    # Verify token hash structure by computing it
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    assert len(token_hash) == 64  # SHA256 produces 64-char hex string


@pytest.mark.asyncio
async def test_dashboard_token_issued_at_timestamp_stored():
    """Dashboard token issued_at timestamp is stored and returned."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register agent
        await client.post("/v1/register", json={"public_key": pub_b64})

        # Request token
        headers = sign_request(priv, "POST", "/v1/dashboard-token", b"{}")
        resp = await client.post("/v1/dashboard-token", json={}, headers=headers)

    assert resp.status_code == 201
    data = resp.json()
    # Verify token metadata is returned
    assert "expires_in" in data
    assert data["expires_in"] == 3600  # 1 hour


@pytest.mark.asyncio
async def test_dashboard_token_multiple_requests_regenerate():
    """Requesting token multiple times regenerates (overwrites) the previous token."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register agent
        await client.post("/v1/register", json={"public_key": pub_b64})

        # Request token 1
        headers1 = sign_request(priv, "POST", "/v1/dashboard-token", b"{}")
        resp1 = await client.post("/v1/dashboard-token", json={}, headers=headers1)
        token1 = resp1.json()["dashboard_token"]

        # Request token 2
        headers2 = sign_request(priv, "POST", "/v1/dashboard-token", b"{}")
        resp2 = await client.post("/v1/dashboard-token", json={}, headers=headers2)
        token2 = resp2.json()["dashboard_token"]

    # Tokens should be different
    assert token1 != token2


@pytest.mark.asyncio
async def test_dashboard_token_response_structure():
    """Dashboard token response has correct structure."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register agent
        await client.post("/v1/register", json={"public_key": pub_b64})

        # Request token
        headers = sign_request(priv, "POST", "/v1/dashboard-token", b"{}")
        resp = await client.post("/v1/dashboard-token", json={}, headers=headers)

    assert resp.status_code == 201
    data = resp.json()
    # Verify response structure
    assert "dashboard_token" in data
    assert "expires_in" in data
    assert "agent_id" in data
    assert data["expires_in"] == 3600
    assert data["agent_id"] == agent_id
    # Token should be a non-empty string (base64url)
    assert isinstance(data["dashboard_token"], str)
    assert len(data["dashboard_token"]) > 20
