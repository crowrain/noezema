"""Domain database engine and settings (T0.3).

PostgreSQL 15+ via asyncpg. The domain DB is the single source of truth
(ARCHITECTURE §3.3); there is no Redis/RQ in v1 (ADR-0003) — background
work is durable in PostgreSQL tables.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:5432/noezema"


class DatabaseSettings(BaseSettings):
    """Runtime configuration, read from the environment (NOEZEMA_ prefix)."""

    model_config = SettingsConfigDict(env_prefix="NOEZEMA_", extra="ignore")

    database_url: str = Field(default=DEFAULT_DATABASE_URL)
    echo_sql: bool = False


class Database:
    """Owns the async engine and session factory for the domain database."""

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings = settings if settings is not None else DatabaseSettings()
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(self.settings.database_url, echo=self.settings.echo_sql)
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        return self._session_factory

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
