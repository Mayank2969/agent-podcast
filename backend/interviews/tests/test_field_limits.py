"""
Field length limit validation tests for interviews router.

Tests that Pydantic validation returns 422 for oversized payloads.
"""
import hashlib
import json
import os
import time
import uuid
from base64 import urlsafe_b64encode

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ADMIN_API_KEY"] = "test_admin_field_limits"

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.db.models import Base
from backend.db.session import get_db
from backend.db import Agent, Interview
from backend.main import app


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def test_db():
    """Create fresh in-memory SQLite DB for each test function."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()
    await engine.dispose()


def generate_keypair():
    """Generate ED25519 keypair for testing."""
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes_raw()
    pub_b64 = urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    agent_id = hashlib.sha256(pub_bytes).hexdigest()
    return priv, pub_b64, agent_id


def auth_headers(priv_key, method: str, path: str, body: bytes = b"") -> dict:
    """Generate X-Agent-ID / X-Timestamp / X-Signature headers."""
    ts = str(int(time.time()))
    body_sha256 = (
        hashlib.sha256(body).hexdigest()
        if body
        else "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    payload = f"{method.upper()}:{path}:{ts}:{body_sha256}".encode()
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
async def test_respond_answer_exceeds_max_length(test_db):
    """Test that answer exceeding 5000 chars returns 422."""
    async with AsyncClient(
        app=app, transport=ASGITransport(app=app)
    ) as client:
        # Register agent
        priv, pub_b64, agent_id = generate_keypair()
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64}
        )
        assert response.status_code == 200

        # Create interview
        response = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "Test"},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 201
        interview_id = response.json()["interview_id"]

        # Claim interview
        response = await client.get(
            "/v1/interview/claim",
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 200

        # Send question
        body = json.dumps({"content": "Test question"}).encode()
        response = await client.post(
            "/v1/interview/message",
            json={"interview_id": interview_id, "sender": "HOST", "content": "Test question", "sequence_num": 1},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 201

        # Try to respond with oversized answer (5001 chars)
        oversized_answer = "x" * 5001
        body = json.dumps({"interview_id": interview_id, "answer": oversized_answer}).encode()
        response = await client.post(
            "/v1/interview/respond",
            json={"interview_id": interview_id, "answer": oversized_answer},
            headers=auth_headers(priv, "POST", "/v1/interview/respond", body)
        )
        assert response.status_code == 422
        assert "max_length" in response.text.lower() or "max" in response.text.lower()


@pytest.mark.asyncio
async def test_respond_answer_at_max_length(test_db):
    """Test that answer at exactly 5000 chars is accepted."""
    async with AsyncClient(
        app=app, transport=ASGITransport(app=app)
    ) as client:
        # Register agent
        priv, pub_b64, agent_id = generate_keypair()
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64}
        )
        assert response.status_code == 200

        # Create interview
        response = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "Test"},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 201
        interview_id = response.json()["interview_id"]

        # Claim interview
        response = await client.get(
            "/v1/interview/claim",
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 200

        # Send question
        response = await client.post(
            "/v1/interview/message",
            json={"interview_id": interview_id, "sender": "HOST", "content": "Test question", "sequence_num": 1},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 201

        # Respond with exactly 5000 chars (should succeed)
        max_answer = "x" * 5000
        body = json.dumps({"interview_id": interview_id, "answer": max_answer}).encode()
        response = await client.post(
            "/v1/interview/respond",
            json={"interview_id": interview_id, "answer": max_answer},
            headers=auth_headers(priv, "POST", "/v1/interview/respond", body)
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_respond_answer_empty_fails(test_db):
    """Test that empty answer returns 422."""
    async with AsyncClient(
        app=app, transport=ASGITransport(app=app)
    ) as client:
        # Register agent
        priv, pub_b64, agent_id = generate_keypair()
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64}
        )
        assert response.status_code == 200

        # Create interview
        response = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "Test"},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 201
        interview_id = response.json()["interview_id"]

        # Claim interview
        response = await client.get(
            "/v1/interview/claim",
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 200

        # Send question
        response = await client.post(
            "/v1/interview/message",
            json={"interview_id": interview_id, "sender": "HOST", "content": "Test question", "sequence_num": 1},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 201

        # Try to respond with empty answer
        body = json.dumps({"interview_id": interview_id, "answer": ""}).encode()
        response = await client.post(
            "/v1/interview/respond",
            json={"interview_id": interview_id, "answer": ""},
            headers=auth_headers(priv, "POST", "/v1/interview/respond", body)
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_interview_github_url_exceeds_limit(test_db):
    """Test that github_repo_url exceeding 500 chars returns 422."""
    async with AsyncClient(
        app=app, transport=ASGITransport(app=app)
    ) as client:
        # Register agent
        priv, pub_b64, agent_id = generate_keypair()
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64}
        )
        assert response.status_code == 200

        # Try to create interview with oversized github_repo_url
        oversized_url = "https://github.com/" + ("a" * 500)
        response = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "Test", "github_repo_url": oversized_url},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_store_message_content_exceeds_limit(test_db):
    """Test that message content exceeding 10000 chars returns 422."""
    async with AsyncClient(
        app=app, transport=ASGITransport(app=app)
    ) as client:
        # Register agent
        priv, pub_b64, agent_id = generate_keypair()
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64}
        )
        assert response.status_code == 200

        # Create interview
        response = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "Test"},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 201
        interview_id = response.json()["interview_id"]

        # Try to store message with oversized content
        oversized_content = "x" * 10001
        response = await client.post(
            "/v1/interview/message",
            json={"interview_id": interview_id, "sender": "HOST", "content": oversized_content, "sequence_num": 1},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_store_message_invalid_sender(test_db):
    """Test that invalid sender value returns 422."""
    async with AsyncClient(
        app=app, transport=ASGITransport(app=app)
    ) as client:
        # Register agent
        priv, pub_b64, agent_id = generate_keypair()
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64}
        )
        assert response.status_code == 200

        # Create interview
        response = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "Test"},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 201
        interview_id = response.json()["interview_id"]

        # Try to store message with invalid sender
        response = await client.post(
            "/v1/interview/message",
            json={"interview_id": interview_id, "sender": "INVALID", "content": "Test", "sequence_num": 1},
            headers={"X-API-Key": "test_admin_field_limits"}
        )
        assert response.status_code == 422
