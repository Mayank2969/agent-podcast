"""
Async SQLAlchemy session factory.
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://agentcast:agentcast_dev@localhost:5432/agentcast")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """FastAPI dependency for database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Create all tables. Used for development/testing."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_admin_key() -> str:
    """Get ADMIN_API_KEY from environment. Fail hard if not set.

    Raises RuntimeError if ADMIN_API_KEY is not configured.
    This is required for production security.
    """
    key = os.getenv("ADMIN_API_KEY")
    if not key:
        raise RuntimeError(
            "ADMIN_API_KEY environment variable not set. "
            "This is required for production. "
            "Set: export ADMIN_API_KEY=<your-secret-key>"
        )
    return key
