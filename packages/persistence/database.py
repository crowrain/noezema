"""Engine and unit-of-work construction for the operational database."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(
    database_url: str,
    *,
    echo: bool = False,
) -> tuple[Engine, Callable[[], Session]]:
    """Create a synchronous SQLAlchemy engine and non-expiring sessions."""

    engine = create_engine(database_url, echo=echo, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return engine, factory
