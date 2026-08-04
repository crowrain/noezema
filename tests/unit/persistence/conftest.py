"""SQLite-backed transaction fixtures for persistence unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    BOOTSTRAP_PAYLOAD_SHA256,
    BOOTSTRAP_REVISION_SHA256,
    Base,
    bootstrap_payload,
)
from packages.persistence.models import ConfigSnapshotRecord


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(sqlite_engine: Engine) -> sessionmaker[Session]:
    factory = sessionmaker(bind=sqlite_engine, class_=Session, expire_on_commit=False)
    with factory.begin() as db:
        db.add(
            ConfigSnapshotRecord(
                id=BOOTSTRAP_CONFIG_SNAPSHOT_ID,
                base_snapshot_id=None,
                payload=bootstrap_payload(),
                payload_sha256=BOOTSTRAP_PAYLOAD_SHA256,
                sha=BOOTSTRAP_REVISION_SHA256,
                activation_mode="bootstrap",
                activation_state="active",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    return factory
