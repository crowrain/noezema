"""Session lease, writer-intent and revision-vector invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from packages.domain import ConfigSnapshotId, RevisionScope, RevisionVector, SessionId
from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    FenceMismatchError,
    LeaseBusyError,
    LeaseExpiredError,
    RevisionVectorConflictError,
    acquire_session_lease,
    acquire_writer_intents,
    advance_revision_vector,
    create_session_with_audit,
    load_revision_vector,
    release_session_lease,
    release_writer_intents,
    renew_session_lease,
    validate_session_lease,
    validate_writer_intents,
)
from packages.persistence.models import WriterIntentRecord

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _session(session_factory: sessionmaker[Session]) -> SessionId:
    session_id = SessionId.new()
    with session_factory.begin() as db:
        create_session_with_audit(
            db,
            session_id=session_id,
            config_snapshot_id=ConfigSnapshotId(root=BOOTSTRAP_CONFIG_SNAPSHOT_ID),
            occurred_at=NOW,
        )
    return session_id


def test_session_lease_is_idempotent_but_reacquisition_increments_fence(
    session_factory: sessionmaker[Session],
) -> None:
    session_id = _session(session_factory)
    with session_factory.begin() as db:
        first = acquire_session_lease(
            db,
            session_id=session_id,
            owner="orchestrator-a",
            ttl_seconds=30,
            occurred_at=NOW,
        )
        renewed = renew_session_lease(
            db,
            lease=first,
            ttl_seconds=60,
            occurred_at=NOW + timedelta(seconds=1),
        )
        repeated = acquire_session_lease(
            db,
            session_id=session_id,
            owner="orchestrator-a",
            ttl_seconds=30,
            occurred_at=NOW + timedelta(seconds=2),
        )
        assert first.fence == renewed.fence == repeated.fence == 1
        assert repeated.expires_at == renewed.expires_at
        release_session_lease(
            db,
            lease=repeated,
            occurred_at=NOW + timedelta(seconds=3),
        )
        reacquired = acquire_session_lease(
            db,
            session_id=session_id,
            owner="orchestrator-a",
            ttl_seconds=30,
            occurred_at=NOW + timedelta(seconds=4),
        )
        assert reacquired.fence == 2
        with pytest.raises(FenceMismatchError):
            validate_session_lease(
                db,
                lease=first,
                occurred_at=NOW + timedelta(seconds=5),
            )


def test_expired_lease_takeover_rejects_old_owner_and_prevents_aba(
    session_factory: sessionmaker[Session],
) -> None:
    session_id = _session(session_factory)
    with session_factory.begin() as db:
        old = acquire_session_lease(
            db,
            session_id=session_id,
            owner="worker-a",
            ttl_seconds=5,
            occurred_at=NOW,
        )
        with pytest.raises(LeaseBusyError):
            acquire_session_lease(
                db,
                session_id=session_id,
                owner="worker-b",
                ttl_seconds=5,
                occurred_at=NOW + timedelta(seconds=1),
            )
        takeover = acquire_session_lease(
            db,
            session_id=session_id,
            owner="worker-b",
            ttl_seconds=5,
            occurred_at=NOW + timedelta(seconds=6),
        )
        assert takeover.fence == old.fence + 1
        with pytest.raises(FenceMismatchError):
            validate_session_lease(
                db,
                lease=old,
                occurred_at=NOW + timedelta(seconds=6),
            )


def test_multi_scope_writer_acquisition_is_all_or_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    first_session = _session(session_factory)
    second_session = _session(session_factory)
    with session_factory.begin() as db:
        first_lease = acquire_session_lease(
            db,
            session_id=first_session,
            owner="worker-a",
            ttl_seconds=30,
            occurred_at=NOW,
        )
        second_lease = acquire_session_lease(
            db,
            session_id=second_session,
            owner="worker-b",
            ttl_seconds=30,
            occurred_at=NOW,
        )
        acquire_writer_intents(
            db,
            session_lease=first_lease,
            operation_id=uuid4(),
            scopes=(RevisionScope.WORKSPACE,),
            ttl_seconds=30,
            occurred_at=NOW,
        )
        with pytest.raises(LeaseBusyError):
            acquire_writer_intents(
                db,
                session_lease=second_lease,
                operation_id=uuid4(),
                scopes=(RevisionScope.ARTIFACT_STORE, RevisionScope.WORKSPACE),
                ttl_seconds=30,
                occurred_at=NOW,
            )
        artifact_intent = db.get(WriterIntentRecord, RevisionScope.ARTIFACT_STORE.value)
        assert artifact_intent is not None
        assert artifact_intent.holder_operation_id is None
        assert artifact_intent.fence == 0


def test_writer_fence_validates_base_vector_advances_and_releases(
    session_factory: sessionmaker[Session],
) -> None:
    session_id = _session(session_factory)
    with session_factory.begin() as db:
        session_lease = acquire_session_lease(
            db,
            session_id=session_id,
            owner="worker-a",
            ttl_seconds=30,
            occurred_at=NOW,
        )
        leases = acquire_writer_intents(
            db,
            session_lease=session_lease,
            operation_id=uuid4(),
            scopes=(RevisionScope.ARTIFACT_STORE, RevisionScope.WORKSPACE),
            ttl_seconds=30,
            expected_revisions=RevisionVector(),
            occurred_at=NOW,
        )
        vector = advance_revision_vector(
            db,
            leases=leases,
            mutated_scopes=(RevisionScope.ARTIFACT_STORE, RevisionScope.WORKSPACE),
            occurred_at=NOW + timedelta(seconds=1),
        )
        assert vector.workspace == 1
        assert vector.artifact_store == 1
        release_writer_intents(
            db,
            leases=leases,
            occurred_at=NOW + timedelta(seconds=2),
        )
        for scope in (RevisionScope.ARTIFACT_STORE, RevisionScope.WORKSPACE):
            record = db.get(WriterIntentRecord, scope.value)
            assert record is not None
            assert record.holder_operation_id is None
            assert record.fence == 1


def test_stale_revision_and_expired_writer_are_rejected_before_publish(
    session_factory: sessionmaker[Session],
) -> None:
    session_id = _session(session_factory)
    with session_factory.begin() as db:
        session_lease = acquire_session_lease(
            db,
            session_id=session_id,
            owner="worker-a",
            ttl_seconds=30,
            occurred_at=NOW,
        )
        vector = load_revision_vector(db)
        leases = acquire_writer_intents(
            db,
            session_lease=session_lease,
            operation_id=uuid4(),
            scopes=(RevisionScope.KNOWLEDGE,),
            ttl_seconds=5,
            expected_revisions=vector,
            occurred_at=NOW,
        )
        with pytest.raises(LeaseExpiredError):
            validate_writer_intents(
                db,
                leases=leases,
                occurred_at=NOW + timedelta(seconds=6),
            )
        with pytest.raises(RevisionVectorConflictError):
            acquire_writer_intents(
                db,
                session_lease=session_lease,
                operation_id=uuid4(),
                scopes=(RevisionScope.DEPENDENCY_GRAPH,),
                ttl_seconds=5,
                expected_revisions=RevisionVector(dependency_graph=1),
                occurred_at=NOW + timedelta(seconds=1),
            )
