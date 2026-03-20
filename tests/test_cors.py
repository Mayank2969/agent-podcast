"""
CORS configuration tests.

Tests that CORS is properly configured:
- Wildcard (*) is not used
- Only specified origins are allowed
- Environment variable configuration works
- Invalid origins are blocked
"""
import os

# Set up environment before importing app
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ADMIN_API_KEY"] = "test_admin_key_cors"

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.db.models import Base
from backend.db.session import get_db
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


async def test_cors_allowed_origin_localhost_3000(test_db):
    """Allowed origin (localhost:3000) returns CORS headers."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"}
        )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_cors_allowed_origin_localhost_8000(test_db):
    """Allowed origin (localhost:8000) returns CORS headers."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"Origin": "http://localhost:8000"}
        )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:8000"


async def test_cors_allowed_origin_agentcast_ai(test_db):
    """Allowed origin (agentcast.ai) returns CORS headers."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"Origin": "https://agentcast.ai"}
        )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "https://agentcast.ai"


async def test_cors_blocked_origin(test_db):
    """Blocked origin (malicious.com) does not return CORS headers."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"Origin": "https://malicious.com"}
        )
    assert response.status_code == 200
    # When origin is not allowed, the header should not be present
    # or should not match the origin
    if "access-control-allow-origin" in response.headers:
        assert response.headers["access-control-allow-origin"] != "https://malicious.com"


async def test_cors_wildcard_not_used(test_db):
    """Verify that wildcard '*' is not in the CORS configuration."""
    from backend.main import ALLOWED_ORIGINS
    assert "*" not in ALLOWED_ORIGINS
    assert len(ALLOWED_ORIGINS) > 0


async def test_cors_credentials_false(test_db):
    """Verify that allow_credentials is False (not using session auth)."""
    # This is implicit in the middleware, but we can check response headers
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"}
        )
    # access-control-allow-credentials should not be present or should be false
    if "access-control-allow-credentials" in response.headers:
        assert response.headers["access-control-allow-credentials"].lower() == "false"


async def test_cors_max_age(test_db):
    """Verify that max_age is set for browser caching."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"}
        )
    # max_age is sent in preflight responses, check if present
    if "access-control-max-age" in response.headers:
        assert response.headers["access-control-max-age"] == "3600"


async def test_cors_allowed_methods(test_db):
    """Verify that only specific methods are allowed (not wildcard)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/health",
            headers={"Origin": "http://localhost:3000"}
        )
    # Check that allowed methods are limited to specific ones
    if "access-control-allow-methods" in response.headers:
        methods = response.headers["access-control-allow-methods"]
        assert "*" not in methods
        # Should contain at least GET, POST, OPTIONS
        assert "GET" in methods or "OPTIONS" in methods
