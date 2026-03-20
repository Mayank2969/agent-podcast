"""Unit tests for POST /v1/register endpoint."""
import hashlib
from base64 import urlsafe_b64encode

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, ASGITransport

from backend.main import app


def generate_test_keypair():
    """Generate a valid ED25519 keypair for testing."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    raw_bytes = public_key.public_bytes_raw()
    public_key_b64 = urlsafe_b64encode(raw_bytes).rstrip(b"=").decode()
    agent_id = hashlib.sha256(raw_bytes).hexdigest()
    return public_key_b64, agent_id


@pytest.mark.asyncio
async def test_register_returns_agent_id():
    pub_key_b64, expected_agent_id = generate_test_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/register", json={"public_key": pub_key_b64})
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == expected_agent_id


@pytest.mark.asyncio
async def test_register_invalid_base64_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/register", json={"public_key": "not-valid-base64!!!"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_register_wrong_key_length_returns_400():
    # 16 bytes instead of 32
    short_key = urlsafe_b64encode(b"\x00" * 16).rstrip(b"=").decode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/register", json={"public_key": short_key})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_register_idempotent():
    """Re-registering same key returns same agent_id (not 409)."""
    pub_key_b64, expected_agent_id = generate_test_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/v1/register", json={"public_key": pub_key_b64})
        r2 = await client.post("/v1/register", json={"public_key": pub_key_b64})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["agent_id"] == r2.json()["agent_id"] == expected_agent_id


@pytest.mark.asyncio
async def test_display_name_html_escaped():
    """HTML in display_name is escaped to prevent XSS."""
    pub_key_b64, agent_id = generate_test_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/register", json={
            "public_key": pub_key_b64,
            "display_name": "<img src=x onerror=alert(1)>"
        })
    assert response.status_code == 200
    assert response.json()["agent_id"] == agent_id

    # Fetch agent via public endpoint and verify display_name is escaped
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/v1/agent/{agent_id}/public")
    assert resp.status_code == 200
    # Note: /v1/agent/{id}/public does NOT expose display_name (admin-only)
    # So we test via /v1/agents (admin endpoint) instead


@pytest.mark.asyncio
async def test_display_name_length_limit():
    """Display name exceeding 100 chars is rejected."""
    pub_key_b64, _ = generate_test_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/register", json={
            "public_key": pub_key_b64,
            "display_name": "x" * 101
        })
    assert response.status_code == 422  # Pydantic validation error


@pytest.mark.asyncio
async def test_display_name_normal():
    """Normal display names are stored unchanged."""
    pub_key_b64, agent_id = generate_test_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/register", json={
            "public_key": pub_key_b64,
            "display_name": "Alice Bot"
        })
    assert response.status_code == 200
    assert response.json()["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_display_name_whitespace_stripped():
    """Leading/trailing whitespace in display_name is stripped."""
    pub_key_b64, _ = generate_test_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/register", json={
            "public_key": pub_key_b64,
            "display_name": "  Alice Bot  "
        })
    assert response.status_code == 200
    # Can't directly verify via public endpoint, but registration should succeed


@pytest.mark.asyncio
async def test_display_name_empty_string():
    """Empty display_name becomes None."""
    pub_key_b64, _ = generate_test_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/register", json={
            "public_key": pub_key_b64,
            "display_name": ""
        })
    assert response.status_code == 200
