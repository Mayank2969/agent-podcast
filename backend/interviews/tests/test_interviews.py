"""Tests for interview routes and auth middleware."""
import hashlib
import time
from base64 import urlsafe_b64encode
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, ASGITransport

from backend.main import app


def generate_keypair():
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
async def test_get_next_interview_returns_204_when_empty():
    """Unregistered agent or no interview -> 401 or 204."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register first
        await client.post("/v1/register", json={"public_key": pub_b64})
        # Poll -- no interview queued
        headers = sign_request(priv, "GET", "/v1/interview/next")
        resp = await client.get("/v1/interview/next", headers=headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_get_next_interview_without_auth_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/interview/next")
    assert resp.status_code == 422  # Missing required headers


@pytest.mark.asyncio
async def test_create_interview_requires_admin_key():
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/register", json={"public_key": pub_b64})
        resp = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "AI Safety"},
            headers={"X-Admin-Key": "wrong_key"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_interview_with_valid_admin_key():
    os.environ["ADMIN_API_KEY"] = "test_admin_key"
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/register", json={"public_key": pub_b64})
        resp = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "AI Safety"},
            headers={"X-Admin-Key": "test_admin_key"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "QUEUED"
    assert "interview_id" in data
