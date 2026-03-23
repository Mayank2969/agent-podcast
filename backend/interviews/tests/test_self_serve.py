"""Functional tests for the self-serve interview flow."""
import hashlib
import time
import os
import uuid
from base64 import urlsafe_b64encode

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
async def test_self_serve_flow():
    """Test full flow: register -> self-request -> claim."""
    os.environ["ADMIN_API_KEY"] = "test_admin"
    priv, pub_b64, agent_id = generate_keypair()
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Register
        reg_resp = await client.post("/v1/register", json={"public_key": pub_b64, "display_name": "TestBot"})
        assert reg_resp.status_code == 200
        
        # 2. Request Interview (Self-Serve)
        body = b'{"context": "I am a test agent."}'
        headers = sign_request(priv, "POST", "/v1/interview/request", body)
        headers["Content-Type"] = "application/json"
        
        req_resp = await client.post("/v1/interview/request", content=body, headers=headers)
        assert req_resp.status_code == 201
        data = req_resp.json()
        assert data["status"] == "QUEUED"
        assert not data["already_queued"]
        interview_id = data["interview_id"]
        
        # 3. Idempotency check
        req_resp_2 = await client.post("/v1/interview/request", content=body, headers=headers)
        assert req_resp_2.status_code == 200
        assert req_resp_2.json()["already_queued"]
        assert req_resp_2.json()["interview_id"] == interview_id
        
        # 4. Claim (Admin/Host)
        claim_resp = await client.get("/v1/interview/claim", headers={"X-Admin-Key": "test_admin"})
        assert claim_resp.status_code == 200
        claim_data = claim_resp.json()
        assert claim_data["interview_id"] == interview_id
        assert claim_data["agent_id"] == agent_id
        assert claim_data["status"] == "IN_PROGRESS"
        assert claim_data["context"] == "I am a test agent."

        # 5. Verify no more queued
        claim_resp_empty = await client.get("/v1/interview/claim", headers={"X-Admin-Key": "test_admin"})
        assert claim_resp_empty.status_code == 204
