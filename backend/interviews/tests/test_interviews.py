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
async def test_get_next_interview_without_auth_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/interview/next")
    # Now returns 401 due to explicit header check in auth.py
    assert resp.status_code == 401


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
        # Register with callback_url (push-mode) so interview can be created
        await client.post("/v1/register", json={
            "public_key": pub_b64,
            "callback_url": "https://agent.example.com/callback"
        })
        resp = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "AI Safety"},
            headers={"X-Admin-Key": "test_admin_key"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "QUEUED"
    assert "interview_id" in data


@pytest.mark.asyncio
async def test_update_interview_status_invalid_uuid_returns_400():
    """Invalid interview_id format should return 400, not 500."""
    os.environ["ADMIN_API_KEY"] = "test_admin_key"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/v1/interview/not-a-uuid/status",
            json={"status": "COMPLETED"},
            headers={"X-Admin-Key": "test_admin_key"},
        )
    assert resp.status_code == 400
    data = resp.json()
    assert "Invalid interview_id format" in data["detail"]


@pytest.mark.asyncio
async def test_abandon_interview_invalid_uuid_returns_400():
    """Invalid interview_id format in abandon should return 400."""
    priv, pub_b64, _ = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/register", json={"public_key": pub_b64})
        headers = sign_request(priv, "DELETE", "/v1/interview/invalid-id/abandon")
        resp = await client.delete("/v1/interview/invalid-id/abandon", headers=headers)
    assert resp.status_code == 400
    data = resp.json()
    assert "Invalid interview_id format" in data["detail"]


@pytest.mark.asyncio
async def test_store_message_invalid_uuid_returns_400():
    """Invalid interview_id format in store message should return 400."""
    os.environ["ADMIN_API_KEY"] = "test_admin_key"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/interview/message",
            json={
                "interview_id": "not-a-valid-uuid",
                "sender": "HOST",
                "content": "test question",
                "sequence_num": 1,
            },
            headers={"X-Admin-Key": "test_admin_key"},
        )
    assert resp.status_code == 400
    data = resp.json()
    assert "Invalid interview_id format" in data["detail"]


@pytest.mark.asyncio
async def test_get_latest_agent_message_invalid_uuid_returns_400():
    """Invalid interview_id format in get messages should return 400."""
    os.environ["ADMIN_API_KEY"] = "test_admin_key"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/v1/interview/messages/bad-uuid",
            headers={"X-Admin-Key": "test_admin_key"},
        )
    assert resp.status_code == 400
    data = resp.json()
    assert "Invalid interview_id format" in data["detail"]


@pytest.mark.asyncio
async def test_update_interview_metadata_invalid_uuid_returns_400():
    """Invalid interview_id format in metadata update should return 400."""
    os.environ["ADMIN_API_KEY"] = "test_admin_key"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/v1/interview/invalid-uuid/metadata",
            json={"title": "Test Episode"},
            headers={"X-Admin-Key": "test_admin_key"},
        )
    assert resp.status_code == 400
    data = resp.json()
    assert "Invalid interview_id format" in data["detail"]
