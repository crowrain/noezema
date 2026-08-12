"""Caller-owned transactional lease, fencing and revision operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain import (
    RevisionScope,
    RevisionVector,
    SessionId,
    SessionLease,
    SessionState,
    WriterIntentLease,
    WriterIntentSet,
)
from packages.persistence.models import (
    DomainRevisionRecord,
    SessionRecord,
    WriterIntentRecord,
)


class ConcurrencyControlError(RuntimeError):
    """Base class for trusted-host concurrency rejections."""


class LeaseBusyError(ConcurrencyControlError):
    """A live lease or intent belongs to another operation."""


class LeaseExpiredError(ConcurrencyControlError):
    """A caller presented a lease after its expiry boundary."""


class FenceMismatchError(ConcurrencyControlError):
    """A stale owner presented an obsolete monotonic fence."""


class RevisionVectorConflictError(ConcurrencyControlError):
    """A protected revision changed after the caller observed it."""


class IncompleteConcurrencyStateError(ConcurrencyControlError):
    """Required stable revision or writer-intent rows are missing."""


def load_revision_vector(db: Session, *, lock: bool = False) -> RevisionVector:
    records = _revision_records(db, tuple(RevisionScope), lock=lock)
    return RevisionVector(
        **{RevisionScope(record.scope).value: record.revision for record in records}
    )


def acquire_session_lease(
    db: Session,
    *,
    session_id: SessionId,
    owner: str,
    ttl_seconds: int,
    occurred_at: datetime | None = None,
) -> SessionLease:
    """Acquire or idempotently renew one session lease under its row lock."""

    timestamp = _aware(occurred_at or datetime.now(UTC))
    _validate_ttl(ttl_seconds)
    record = _locked_session(db, session_id)
    if record.state in {state.value for state in SessionState if state.is_terminal}:
        raise LeaseExpiredError("terminal session cannot acquire a new lease")
    requested_expiry = timestamp + timedelta(seconds=ttl_seconds)
    current_expiry = _optional_aware(record.lease_expires_at)
    if record.lease_owner is not None and current_expiry is not None and current_expiry > timestamp:
        if record.lease_owner != owner:
            raise LeaseBusyError("session has a live lease owned by another worker")
        expiry = max(current_expiry, requested_expiry)
    else:
        record.fence += 1
        record.lease_owner = owner
        expiry = requested_expiry
    record.lease_expires_at = expiry
    record.updated_at = timestamp
    db.flush()
    return SessionLease(
        session_id=session_id,
        owner=owner,
        fence=record.fence,
        expires_at=expiry,
    )


def renew_session_lease(
    db: Session,
    *,
    lease: SessionLease,
    ttl_seconds: int,
    occurred_at: datetime | None = None,
) -> SessionLease:
    timestamp = _aware(occurred_at or datetime.now(UTC))
    _validate_ttl(ttl_seconds)
    record = _locked_session(db, lease.session_id)
    _validate_session_record(record, lease=lease, occurred_at=timestamp)
    expiry = max(_aware(lease.expires_at), timestamp + timedelta(seconds=ttl_seconds))
    record.lease_expires_at = expiry
    record.updated_at = timestamp
    db.flush()
    return lease.model_copy(update={"expires_at": expiry})


def validate_session_lease(
    db: Session,
    *,
    lease: SessionLease,
    occurred_at: datetime | None = None,
) -> SessionRecord:
    timestamp = _aware(occurred_at or datetime.now(UTC))
    record = _locked_session(db, lease.session_id)
    _validate_session_record(record, lease=lease, occurred_at=timestamp)
    return record


def release_session_lease(
    db: Session,
    *,
    lease: SessionLease,
    occurred_at: datetime | None = None,
) -> None:
    timestamp = _aware(occurred_at or datetime.now(UTC))
    record = _locked_session(db, lease.session_id)
    _validate_session_record(record, lease=lease, occurred_at=timestamp)
    record.lease_owner = None
    record.lease_expires_at = None
    record.updated_at = timestamp
    db.flush()


def acquire_writer_intents(
    db: Session,
    *,
    session_lease: SessionLease,
    operation_id: UUID,
    scopes: tuple[RevisionScope, ...],
    ttl_seconds: int,
    expected_revisions: RevisionVector | None = None,
    occurred_at: datetime | None = None,
) -> WriterIntentSet:
    """Atomically acquire all requested scopes in one canonical lock order."""

    timestamp = _aware(occurred_at or datetime.now(UTC))
    _validate_ttl(ttl_seconds)
    validate_session_lease(db, lease=session_lease, occurred_at=timestamp)
    canonical_scopes = _canonical_scopes(scopes)
    intent_records = _intent_records(db, canonical_scopes, lock=True)
    revisions = _revision_records(db, canonical_scopes, lock=True)
    revisions_by_scope = {RevisionScope(item.scope): item for item in revisions}
    if expected_revisions is not None:
        for scope in canonical_scopes:
            if revisions_by_scope[scope].revision != expected_revisions.revision_for(scope):
                raise RevisionVectorConflictError(
                    f"revision changed before writer intent acquisition: {scope.value}"
                )

    expiry = min(
        _aware(session_lease.expires_at),
        timestamp + timedelta(seconds=ttl_seconds),
    )
    prepared: list[tuple[WriterIntentRecord, bool]] = []
    for record in intent_records:
        live = (
            record.holder_operation_id is not None
            and record.lease_expires_at is not None
            and _aware(record.lease_expires_at) > timestamp
        )
        same_operation = (
            live
            and record.holder_operation_id == operation_id
            and record.holder_session_id == session_lease.session_id.root
            and record.holder_owner == session_lease.owner
            and record.holder_session_fence == session_lease.fence
        )
        if live and not same_operation:
            raise LeaseBusyError(f"writer intent is busy: {record.scope}")
        prepared.append((record, bool(same_operation)))

    leases: list[WriterIntentLease] = []
    for record, same_operation in prepared:
        scope = RevisionScope(record.scope)
        if same_operation:
            record.lease_expires_at = max(_aware(record.lease_expires_at), expiry)
        else:
            record.fence += 1
            record.holder_session_id = session_lease.session_id.root
            record.holder_operation_id = operation_id
            record.holder_owner = session_lease.owner
            record.holder_session_fence = session_lease.fence
            record.lease_expires_at = expiry
            record.base_revision = revisions_by_scope[scope].revision
        record.updated_at = timestamp
        leases.append(_writer_lease(record, session_lease=session_lease))
    db.flush()
    return WriterIntentSet(
        session_lease=session_lease,
        operation_id=operation_id,
        intents=tuple(leases),
    )


def validate_writer_intents(
    db: Session,
    *,
    leases: WriterIntentSet,
    occurred_at: datetime | None = None,
) -> tuple[WriterIntentRecord, ...]:
    timestamp = _aware(occurred_at or datetime.now(UTC))
    validate_session_lease(db, lease=leases.session_lease, occurred_at=timestamp)
    scopes = tuple(item.scope for item in leases.intents)
    records = _intent_records(db, scopes, lock=True)
    presented = {item.scope: item for item in leases.intents}
    for record in records:
        lease = presented[RevisionScope(record.scope)]
        if (
            record.fence != lease.writer_fence
            or record.holder_session_id != lease.session_id.root
            or record.holder_operation_id != lease.operation_id
            or record.holder_owner != lease.owner
            or record.holder_session_fence != lease.session_fence
        ):
            raise FenceMismatchError(f"writer fence is stale: {record.scope}")
        record_expiry = _optional_aware(record.lease_expires_at)
        if record_expiry is None or record_expiry <= timestamp or lease.expires_at <= timestamp:
            raise LeaseExpiredError(f"writer intent expired: {record.scope}")
        if record.base_revision != lease.base_revision:
            raise FenceMismatchError(f"writer base revision changed: {record.scope}")
    return tuple(records)


def advance_revision_vector(
    db: Session,
    *,
    leases: WriterIntentSet,
    mutated_scopes: tuple[RevisionScope, ...],
    occurred_at: datetime | None = None,
) -> RevisionVector:
    """Fence a publication, validate every base revision and advance selected heads."""

    timestamp = _aware(occurred_at or datetime.now(UTC))
    validate_writer_intents(db, leases=leases, occurred_at=timestamp)
    held_scopes = tuple(item.scope for item in leases.intents)
    canonical_mutations = _canonical_scopes(mutated_scopes)
    if not set(canonical_mutations).issubset(held_scopes):
        raise FenceMismatchError("cannot advance a revision without its writer intent")
    revisions = _revision_records(db, held_scopes, lock=True)
    presented = {item.scope: item for item in leases.intents}
    for record in revisions:
        scope = RevisionScope(record.scope)
        if record.revision != presented[scope].base_revision:
            raise RevisionVectorConflictError(
                f"revision changed behind a writer fence: {record.scope}"
            )
        if scope in canonical_mutations:
            record.revision += 1
            record.updated_at = timestamp
    db.flush()
    return load_revision_vector(db, lock=True)


def release_writer_intents(
    db: Session,
    *,
    leases: WriterIntentSet,
    occurred_at: datetime | None = None,
) -> None:
    timestamp = _aware(occurred_at or datetime.now(UTC))
    records = validate_writer_intents(db, leases=leases, occurred_at=timestamp)
    for record in records:
        record.holder_session_id = None
        record.holder_operation_id = None
        record.holder_owner = None
        record.holder_session_fence = None
        record.lease_expires_at = None
        record.base_revision = None
        record.updated_at = timestamp
    db.flush()


def _locked_session(db: Session, session_id: SessionId) -> SessionRecord:
    record = db.scalar(
        select(SessionRecord).where(SessionRecord.id == session_id.root).with_for_update()
    )
    if record is None:
        raise IncompleteConcurrencyStateError(f"session does not exist: {session_id}")
    return record


def _validate_session_record(
    record: SessionRecord,
    *,
    lease: SessionLease,
    occurred_at: datetime,
) -> None:
    if record.lease_owner != lease.owner or record.fence != lease.fence:
        raise FenceMismatchError("session lease owner or fence is stale")
    expiry = _optional_aware(record.lease_expires_at)
    if expiry is None or expiry <= occurred_at or lease.expires_at <= occurred_at:
        raise LeaseExpiredError("session lease expired")


def _intent_records(
    db: Session,
    scopes: tuple[RevisionScope, ...],
    *,
    lock: bool,
) -> tuple[WriterIntentRecord, ...]:
    statement = (
        select(WriterIntentRecord)
        .where(WriterIntentRecord.scope.in_(scope.value for scope in scopes))
        .order_by(WriterIntentRecord.scope)
    )
    if lock:
        statement = statement.with_for_update()
    records = tuple(db.scalars(statement))
    if len(records) != len(scopes):
        raise IncompleteConcurrencyStateError("writer-intent registry is incomplete")
    return records


def _revision_records(
    db: Session,
    scopes: tuple[RevisionScope, ...],
    *,
    lock: bool,
) -> tuple[DomainRevisionRecord, ...]:
    statement = (
        select(DomainRevisionRecord)
        .where(DomainRevisionRecord.scope.in_(scope.value for scope in scopes))
        .order_by(DomainRevisionRecord.scope)
    )
    if lock:
        statement = statement.with_for_update()
    records = tuple(db.scalars(statement))
    if len(records) != len(scopes):
        raise IncompleteConcurrencyStateError("domain revision vector is incomplete")
    return records


def _writer_lease(
    record: WriterIntentRecord,
    *,
    session_lease: SessionLease,
) -> WriterIntentLease:
    if (
        record.holder_operation_id is None
        or record.lease_expires_at is None
        or record.base_revision is None
    ):
        raise IncompleteConcurrencyStateError("writer intent holder tuple is incomplete")
    return WriterIntentLease(
        scope=RevisionScope(record.scope),
        session_id=session_lease.session_id,
        operation_id=record.holder_operation_id,
        owner=session_lease.owner,
        session_fence=session_lease.fence,
        writer_fence=record.fence,
        base_revision=record.base_revision,
        expires_at=_aware(record.lease_expires_at),
    )


def _canonical_scopes(scopes: tuple[RevisionScope, ...]) -> tuple[RevisionScope, ...]:
    if not scopes or len(scopes) != len(set(scopes)):
        raise ValueError("writer intent scopes must be non-empty and unique")
    return tuple(sorted(scopes, key=lambda item: item.value))


def _validate_ttl(ttl_seconds: int) -> None:
    if not 1 <= ttl_seconds <= 3_600:
        raise ValueError("lease TTL must be between 1 and 3600 seconds")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None
