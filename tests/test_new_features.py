import hashlib
import json
import os
import time
import uuid
from base64 import urlsafe_b64encode

# Setup for SQLite in-memory
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ADMIN_API_KEY"] = "test_admin_key"
os.environ["TESTING"] = "1"

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.db.models import Base
from backend.db.session import get_db
from backend.main import app

# ── Setup ─────────────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture(scope="function")
async def test_db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    app.dependency_overrides[get_db] = override_get_db
    app.state.redis_client = None
    
    yield
    app.dependency_overrides.clear()
    await engine.dispose()

def generate_keypair():
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes_raw()
    pub_b64 = urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    agent_id = hashlib.sha256(pub_bytes).hexdigest()
    return priv, pub_b64, agent_id

def auth_headers(priv_key, method: str, path: str, body: bytes = b""):
    ts = str(int(time.time()))
    body_sha256 = hashlib.sha256(body).hexdigest() if body else "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    payload = f"{method.upper()}:{path}:{ts}:{body_sha256}".encode()
    sig = priv_key.sign(payload)
    sig_b64 = urlsafe_b64encode(sig).rstrip(b"=").decode()
    pub_bytes = priv_key.public_key().public_bytes_raw()
    agent_id = hashlib.sha256(pub_bytes).hexdigest()
    return {"X-Agent-ID": agent_id, "X-Timestamp": ts, "X-Signature": sig_b64}

# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_dashboard_token_and_request(test_db):
    """Test dashboard token generation and interview request via dashboard."""
    priv, pub_b64, agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Register
        r = await client.post("/v1/register", json={"public_key": pub_b64})
        assert r.status_code == 200
        
        # 2. Get dashboard token via signed request
        path = "/v1/dashboard-token"
        headers = auth_headers(priv, "POST", path)
        r = await client.post(path, headers=headers)
        assert r.status_code == 201
        token = r.json()["dashboard_token"]
        
        # 3. Request interview via dashboard
        payload = {"agent_id": agent_id, "token": token, "topic": "Dashboard Test"}
        r = await client.post("/v1/dashboard/request-interview", json=payload)
        assert r.status_code == 200
        assert r.json()["status"] == "QUEUED"
        interview_id = r.json()["interview_id"]
        assert interview_id is not None

async def test_interview_history(test_db):
    """Test fetching interview history with correct/wrong agent ownership."""
    priv1, pub_b64_1, agent_id1 = generate_keypair()
    priv2, pub_b64_2, agent_id2 = generate_keypair()
    
    admin_headers = {"X-Admin-Key": "test_admin_key"}
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Setup agents
        await client.post("/v1/register", json={"public_key": pub_b64_1})
        await client.post("/v1/register", json={"public_key": pub_b64_2})
        
        # 2. Get dashboard token for agent 1
        headers_token = auth_headers(priv1, "POST", "/v1/dashboard-token")
        r = await client.post("/v1/dashboard-token", headers=headers_token)
        token1 = r.json()["dashboard_token"]
        
        # 3. Create interview for agent 1 via dashboard (allowed for pull-mode)
        r = await client.post("/v1/dashboard/request-interview", json={
            "agent_id": agent_id1,
            "token": token1,
            "topic": "History Test"
        })
        assert r.status_code == 200
        interview_id = r.json()["interview_id"]
        
        # 4. Add a message via Admin API
        r = await client.post("/v1/interview/message", json={
            "interview_id": interview_id,
            "sender": "HOST",
            "content": "Hello",
            "sequence_num": 1
        }, headers=admin_headers)
        assert r.status_code == 201
        
        # 5. Agent 1 fetches history - SUCCESS
        path = f"/v1/interview/{interview_id}/history"
        headers1 = auth_headers(priv1, "GET", path)
        r = await client.get(path, headers=headers1)
        assert r.status_code == 200, f"Fetch history failed: {r.text}"
        messages = r.json()
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello"
        
        # 6. Agent 2 fetches history - 403 (Not your interview)
        headers2 = auth_headers(priv2, "GET", path)
        r = await client.get(path, headers=headers2)
        assert r.status_code == 403
