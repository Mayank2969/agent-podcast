"""
Test nonce-based replay attack prevention.

Tests that identical signatures cannot be replayed within the 120-second TTL.
"""
import hashlib
import json
import os
import time
from base64 import urlsafe_b64encode

# Override DATABASE_URL before importing backend modules
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ADMIN_API_KEY"] = "test_admin_key_replay"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"

import pytest
import pytest_asyncio
import redis
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.db.models import Base
from backend.db.session import get_db
from backend.interviews.auth import validate_and_store_nonce
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


@pytest_asyncio.fixture(scope="function")
def mock_redis_client(monkeypatch):
    """Create mock Redis client for testing."""
    # Create a simple in-memory mock if Redis is unavailable
    try:
        client = redis.from_url("redis://localhost:6379/0")
        client.ping()
        # Flush test database
        client.flushdb()
        return client
    except redis.ConnectionError:
        pytest.skip("Redis not available for replay prevention tests")


@pytest.fixture
def registered_agent(test_db):
    """Return (agent_id, private_key) for a registered agent."""
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes_raw()
    pub_b64 = urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    agent_id = hashlib.sha256(pub_bytes).hexdigest()
    return agent_id, priv


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


def test_nonce_validation_new_signature_allowed(mock_redis_client):
    """New signature is stored and allowed."""
    signature = "test_signature_1"
    result = validate_and_store_nonce(signature, mock_redis_client)
    assert result is True, "First use of signature should be allowed"

    # Verify nonce was stored
    nonce = hashlib.sha256(signature.encode()).hexdigest()
    assert mock_redis_client.exists(f"nonce:{nonce}") == 1


def test_nonce_validation_replay_rejected(mock_redis_client):
    """Replayed signature (same signature) is rejected."""
    signature = "test_signature_replay"

    # First request
    result1 = validate_and_store_nonce(signature, mock_redis_client)
    assert result1 is True, "First use should be allowed"

    # Immediate replay with same signature
    result2 = validate_and_store_nonce(signature, mock_redis_client)
    assert result2 is False, "Replayed signature should be rejected"


def test_nonce_validation_no_redis():
    """When Redis unavailable, requests are allowed (graceful degradation)."""
    signature = "test_signature"
    result = validate_and_store_nonce(signature, None)
    assert result is True, "Should allow requests when Redis unavailable"


def test_nonce_expires_after_ttl(mock_redis_client):
    """Nonce expires after 120 seconds."""
    signature = "test_signature_ttl"
    nonce = hashlib.sha256(signature.encode()).hexdigest()

    # Store nonce
    result = validate_and_store_nonce(signature, mock_redis_client)
    assert result is True
    assert mock_redis_client.exists(f"nonce:{nonce}") == 1

    # Expire immediately for test (set TTL to 0)
    mock_redis_client.expire(f"nonce:{nonce}", 0)
    assert mock_redis_client.exists(f"nonce:{nonce}") == 0

    # Same signature can be used again after expiry
    result = validate_and_store_nonce(signature, mock_redis_client)
    assert result is True, "Signature should be allowed after nonce expiry"


@pytest.mark.asyncio
async def test_replay_attack_prevented_in_api(test_db, registered_agent, mock_redis_client):
    """Replay attack is prevented at API level."""
    agent_id, priv_key = registered_agent

    # Mock Redis client in app state
    app.state.redis_client = mock_redis_client

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # First register the agent
        headers = {
            "X-Admin-Key": "test_admin_key_replay",
        }
        resp = client.post(
            "/v1/register",
            json={"public_key": hashlib.sha256(priv_key.public_key().public_bytes_raw()).hexdigest()[:44]},
            headers=headers,
        )
        # Registration may succeed or fail, but we focus on signature replay

        # Create signed headers for a respond request
        path = "/v1/interview/respond"
        method = "POST"
        body = json.dumps({
            "interview_id": "test-interview-id",
            "answer": "test answer"
        }).encode()

        headers = auth_headers(priv_key, method, path, body)

        # First request (will fail on missing interview, but headers are valid)
        resp1 = client.post(
            path,
            json={"interview_id": "test-interview-id", "answer": "test answer"},
            headers=headers,
        )
        # Should not be 401 for replay reason
        assert resp1.status_code != 401 or "replay" not in resp1.json().get("detail", "").lower()

        # Immediate replay with same signature and headers
        resp2 = client.post(
            path,
            json={"interview_id": "test-interview-id", "answer": "test answer"},
            headers=headers,
        )
        # Should be 401 with replay message
        assert resp2.status_code == 401
        assert "replay" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_different_signature_allowed(test_db, registered_agent, mock_redis_client):
    """Different signature (new timestamp) is allowed even for same endpoint."""
    agent_id, priv_key = registered_agent

    app.state.redis_client = mock_redis_client

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        path = "/v1/interview/respond"
        method = "POST"
        body1 = json.dumps({
            "interview_id": "test-interview-id-1",
            "answer": "answer 1"
        }).encode()
        body2 = json.dumps({
            "interview_id": "test-interview-id-2",
            "answer": "answer 2"
        }).encode()

        # First request with timestamp T
        headers1 = auth_headers(priv_key, method, path, body1)
        resp1 = client.post(
            path,
            json={"interview_id": "test-interview-id-1", "answer": "answer 1"},
            headers=headers1,
        )

        # Wait 1 second to ensure different timestamp
        time.sleep(1)

        # Second request with timestamp T+1 (different signature)
        headers2 = auth_headers(priv_key, method, path, body2)
        resp2 = client.post(
            path,
            json={"interview_id": "test-interview-id-2", "answer": "answer 2"},
            headers=headers2,
        )

        # Different signatures should not conflict on replay check
        # (They may both fail for other reasons like missing interview, but not replay)
        if resp1.status_code == 401:
            assert "replay" not in resp1.json().get("detail", "").lower()
        if resp2.status_code == 401:
            assert "replay" not in resp2.json().get("detail", "").lower()
