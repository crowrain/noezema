"""DB setup for Web API — uses Database singleton from domain layer."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.base import Base
from packages.domain.db_config import Database, get_db_settings


async def init_db() -> None:
    """Initialize Database singleton. Call once at app startup.
    Skips if already initialized (for tests with in-memory DB)."""
    if Database._engine is not None:
        return  # Already initialized (e.g. in tests)
    settings = get_db_settings()
    Database.init(settings)


async def create_tables() -> None:
    """Auto-create tables (dev only — use Alembic in prod)."""
    await init_db()
    engine = Database.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a DB session."""
    await init_db()
    factory = Database.get_session_factory()
    async with factory() as session:
        yield session


async def shutdown_db() -> None:
    """Dispose engine at app shutdown."""
    await Database.close()
