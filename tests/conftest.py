"""Shared SQLite transaction fixtures for local persistence and scenario tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from packages.domain import NodeState, RevisionScope
from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    BOOTSTRAP_PAYLOAD_SHA256,
    BOOTSTRAP_REVISION_SHA256,
    Base,
    bootstrap_payload,
)
from packages.persistence.models import (
    ConfigSnapshotRecord,
    DomainRevisionRecord,
    RuntimeConfigHeadRecord,
    RuntimeControlRecord,
    WriterIntentRecord,
)


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
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
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
                created_at=created_at,
            )
        )
        db.flush()
        db.add(
            RuntimeConfigHeadRecord(
                scope="global",
                active_config_snapshot_id=BOOTSTRAP_CONFIG_SNAPSHOT_ID,
                activating_config_snapshot_id=None,
                activation_fence=0,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=created_at,
            )
        )
        db.add(
            RuntimeControlRecord(
                scope="global",
                node_state=NodeState.SLEEPING.value,
                wake_generation=0,
                next_global_audit_sequence=1,
                next_outbox_sequence=1,
                updated_at=created_at,
            )
        )
        db.add_all(
            DomainRevisionRecord(scope=scope.value, revision=0, updated_at=created_at)
            for scope in RevisionScope
        )
        db.flush()
        db.add_all(
            WriterIntentRecord(
                scope=scope.value,
                fence=0,
                holder_session_id=None,
                holder_operation_id=None,
                holder_owner=None,
                holder_session_fence=None,
                lease_expires_at=None,
                base_revision=None,
                updated_at=created_at,
            )
            for scope in RevisionScope
        )
    return factory
