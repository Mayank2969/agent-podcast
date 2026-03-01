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
