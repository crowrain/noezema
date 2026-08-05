"""Database configuration and async session management."""

from pydantic import BaseModel, Field

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseConfig(BaseModel):
    """Database connection settings."""
    url: str = Field(
        default="postgresql+asyncpg://noezema:noezema_dev@localhost:5432/noezema",
        description="Async SQLAlchemy URL",
    )
    pool_size: int = 5
    max_overflow: int = 10
    echo: bool = False

    def get_database_url(self) -> str:
        return self.url


def get_db_settings() -> DatabaseConfig:
    """Get database config from environment or defaults."""
    import os
    return DatabaseConfig(
        url=os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://noezema:noezema_dev@localhost:5432/noezema"
        ),
        echo=os.environ.get("DB_ECHO", "0") == "1",
    )


class Database:
    """Async database singleton with session factory."""

    _engine: AsyncEngine | None = None
    _session_factory: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    def init(cls, config: DatabaseConfig) -> AsyncEngine:
        """Initialize engine and session factory."""
        cls._engine = create_async_engine(
            config.url,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            echo=config.echo,
        )
        cls._session_factory = async_sessionmaker(
            cls._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        return cls._engine

    @classmethod
    def get_engine(cls) -> AsyncEngine:
        if cls._engine is None:
            raise RuntimeError("Database not initialized. Call Database.init() first.")
        return cls._engine

    @classmethod
    def get_session_factory(cls) -> async_sessionmaker[AsyncSession]:
        if cls._session_factory is None:
            raise RuntimeError("Database not initialized. Call Database.init() first.")
        return cls._session_factory

    @classmethod
    async def close(cls) -> None:
        """Dispose engine and close connections."""
        if cls._engine is not None:
            await cls._engine.dispose()
            cls._engine = None
            cls._session_factory = None
