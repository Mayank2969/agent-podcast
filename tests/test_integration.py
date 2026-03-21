"""
Integration test for AgentCast P0 platform.

Tests the full flow:
1. Register anonymous agent
2. Admin creates interview (QUEUED)
3. Pipecat claims interview (IN_PROGRESS)
4. Agent polls -> gets question (HOST message)
5. Agent responds -> answer stored
6. Multiple turns
7. Interview marked COMPLETED
8. Transcript stored and retrievable
9. Guardrail: sensitive answer gets redacted in transcript

Uses in-memory SQLite for isolation (no live DB needed).
"""
import hashlib
import json
import os
import time
import uuid
from base64 import urlsafe_b64encode

# Override DATABASE_URL before importing backend modules so session.py
# picks up SQLite instead of PostgreSQL.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ADMIN_API_KEY"] = "test_admin_key"

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.db.models import Base
from backend.db.session import get_db
from backend.main import app

# ── Test DB setup ─────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

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


ADMIN_HEADERS = {"X-Admin-Key": "test_admin_key"}

# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_health_check(test_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_register_agent(test_db):
    """P0 criterion 1: Register anonymous agent."""
    priv, pub_b64, expected_agent_id = generate_keypair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/register", json={"public_key": pub_b64})
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == expected_agent_id


async def test_full_interview_flow(test_db):
    """
    P0 criterion 2: Full interview flow:
    register -> create -> claim -> Q&A x 2 -> complete -> transcript.
    """
    priv, pub_b64, agent_id = generate_keypair()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        # 1. Register agent
        r = await client.post("/v1/register", json={"public_key": pub_b64})
        assert r.status_code == 200, f"Register failed: {r.text}"
        dashboard_token = r.json().get("dashboard_token")

        # 2. Admin creates interview -> QUEUED
        r = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "AI Safety & Alignment"},
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 201, f"Create failed: {r.text}"
        interview_id = r.json()["interview_id"]
        assert r.json()["status"] == "QUEUED"

        # 3. Agent polls -- should get 204 (no IN_PROGRESS interview yet)
        r = await client.get(
            "/v1/interview/next",
            headers=auth_headers(priv, "GET", "/v1/interview/next"),
        )
        assert r.status_code == 204, f"Expected 204 before claim, got {r.status_code}"

        # 4. Pipecat claims interview -> IN_PROGRESS
        r = await client.get("/v1/interview/claim", headers=ADMIN_HEADERS)
        assert r.status_code == 200, f"Claim failed: {r.text}"
        claimed = r.json()
        assert claimed["interview_id"] == interview_id
        assert claimed["status"] == "IN_PROGRESS"

        # 5. Pipecat stores first HOST question
        r = await client.post(
            "/v1/interview/message",
            json={
                "interview_id": interview_id,
                "sender": "HOST",
                "content": "What is your approach to AI safety?",
                "sequence_num": 1,
            },
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 201, f"Store message failed: {r.text}"

        # 6. Agent polls -> gets question
        r = await client.get(
            "/v1/interview/next",
            headers=auth_headers(priv, "GET", "/v1/interview/next"),
        )
        assert r.status_code == 200, f"Expected 200 with question, got {r.status_code}: {r.text}"
        question_data = r.json()
        assert question_data["interview_id"] == interview_id
        assert "AI safety" in question_data["question"]

        # 7. Agent responds
        answer1 = "I prioritize alignment through constitutional AI and RLHF techniques."
        body = json.dumps({"interview_id": interview_id, "answer": answer1}).encode()
        r = await client.post(
            "/v1/interview/respond",
            content=body,
            headers={
                **auth_headers(priv, "POST", "/v1/interview/respond", body),
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200, f"Respond failed: {r.text}"

        # 8. Second turn: Pipecat stores follow-up question
        r = await client.post(
            "/v1/interview/message",
            json={
                "interview_id": interview_id,
                "sender": "HOST",
                "content": "How do you handle emergent capabilities?",
                "sequence_num": 3,
            },
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 201

        # 9. Agent answers second question
        answer2 = "I monitor for emergent behaviors using interpretability tools."
        body2 = json.dumps({"interview_id": interview_id, "answer": answer2}).encode()
        r = await client.post(
            "/v1/interview/respond",
            content=body2,
            headers={
                **auth_headers(priv, "POST", "/v1/interview/respond", body2),
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200

        # 10. Mark interview COMPLETED
        r = await client.patch(
            f"/v1/interview/{interview_id}/status",
            json={"status": "COMPLETED"},
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 200

        # 11. Build transcript
        r = await client.post(
            "/v1/transcript/build",
            json={"interview_id": interview_id},
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 200
        assert r.json()["turn_count"] == 4  # 2 HOST + 2 AGENT messages

        r = await client.get(
            f"/v1/transcript/{interview_id}"
        )
        assert r.status_code == 200, f"Transcript fetch failed: {r.text} | token: {dashboard_token}"
        transcript = r.json()
        assert transcript["interview_id"] == interview_id
        assert transcript["topic"] == "AI Safety & Alignment"
        assert len(transcript["turns"]) == 4
        assert transcript["turns"][0]["sender"] == "HOST"
        assert transcript["turns"][1]["sender"] == "AGENT"
        assert transcript["turns"][2]["sender"] == "HOST"
        assert transcript["turns"][3]["sender"] == "AGENT"


async def test_guardrail_redacts_sensitive_answer(test_db):
    """P0 criterion 4: Guardrails redact sensitive data in agent answers."""
    priv, pub_b64, agent_id = generate_keypair()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Setup: register, create interview, claim, store HOST question
        r = await client.post("/v1/register", json={"public_key": pub_b64})
        dashboard_token = r.json().get("dashboard_token")
        r = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id, "topic": "Security"},
            headers=ADMIN_HEADERS,
        )
        interview_id = r.json()["interview_id"]

        await client.get("/v1/interview/claim", headers=ADMIN_HEADERS)
        await client.post(
            "/v1/interview/message",
            json={
                "interview_id": interview_id,
                "sender": "HOST",
                "content": "Tell me about your security approach.",
                "sequence_num": 1,
            },
            headers=ADMIN_HEADERS,
        )

        # Agent submits answer containing an API key phrase (should be redacted).
        # The guardrail uses word-boundary regex: it redacts the matched keyword
        # phrase "api key" in-place, replacing it with [REDACTED]. Any value
        # that follows the keyword is not part of the match and remains in the
        # text. The test verifies the keyword phrase is gone and the redaction
        # sentinel is present.
        sensitive_answer = "I use my api key for authentication, never sharing it"
        body = json.dumps({"interview_id": interview_id, "answer": sensitive_answer}).encode()
        r = await client.post(
            "/v1/interview/respond",
            content=body,
            headers={
                **auth_headers(priv, "POST", "/v1/interview/respond", body),
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200

        # Mark completed and build transcript
        await client.patch(
            f"/v1/interview/{interview_id}/status",
            json={"status": "COMPLETED"},
            headers=ADMIN_HEADERS,
        )
        await client.post(
            "/v1/transcript/build",
            json={"interview_id": interview_id},
            headers=ADMIN_HEADERS,
        )

        r = await client.get(
            f"/v1/transcript/{interview_id}"
        )
        assert r.status_code == 200, f"Transcript fetch failed: {r.text} | token: {dashboard_token}"
        transcript = r.json()
        agent_turn = next(t for t in transcript["turns"] if t["sender"] == "AGENT")
        # The keyword phrase must be gone
        assert "api key" not in agent_turn["content"].lower()
        # The redaction sentinel must be present
        assert "[REDACTED]" in agent_turn["content"]


async def test_respond_to_wrong_interview_returns_403(test_db):
    """Agent cannot respond to another agent's interview."""
    priv1, pub_b641, agent_id1 = generate_keypair()
    priv2, pub_b642, agent_id2 = generate_keypair()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register both agents
        await client.post("/v1/register", json={"public_key": pub_b641})
        await client.post("/v1/register", json={"public_key": pub_b642})

        # Create interview for agent 1
        r = await client.post(
            "/v1/interview/create",
            json={"agent_id": agent_id1, "topic": "Test"},
            headers=ADMIN_HEADERS,
        )
        interview_id = r.json()["interview_id"]
        await client.get("/v1/interview/claim", headers=ADMIN_HEADERS)
        await client.post(
            "/v1/interview/message",
            json={
                "interview_id": interview_id,
                "sender": "HOST",
                "content": "Question for agent 1",
                "sequence_num": 1,
            },
            headers=ADMIN_HEADERS,
        )

        # Agent 2 tries to respond to agent 1's interview -> 403
        body = json.dumps({"interview_id": interview_id, "answer": "hijack attempt"}).encode()
        r = await client.post(
            "/v1/interview/respond",
            content=body,
            headers={
                **auth_headers(priv2, "POST", "/v1/interview/respond", body),
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 403


async def test_transcript_not_found_returns_404(test_db):
    """Retrieving a non-existent transcript returns 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/v1/transcript/{uuid.uuid4()}")
    assert r.status_code == 404
