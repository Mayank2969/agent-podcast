"""Pytest configuration for interview tests."""
import asyncio
import pytest
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Use file-based SQLite database for tests to avoid sharing issues in :memory:
DB_PATH = "/tmp/agentcast_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"
os.environ["TESTING"] = "1"  # Disable rate limiting in tests

from backend.db.models import Base
from backend.main import app
from backend.db import get_db


@pytest.fixture(scope="session", autouse=True)
async def init_test_database():
    """Initialize test database with schema before running tests."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def get_test_db():
        async with async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # Override the dependency
    app.dependency_overrides[get_db] = get_test_db

    yield

    await engine.dispose()
