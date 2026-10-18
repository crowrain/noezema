"""Commit reconciliation: the fenced row-lock protocol (T2.20, §5.2.2).

The outcome of a lost COMMIT answer is decided by a LOCKING protocol, not
by a plain SELECT (which under MVCC could see a stale ``prepared`` row
while the final transaction is still running).

    BEGIN
      SELECT session ... FOR UPDATE
      verify: original owner fenced AND no live lease
      SELECT attempt ... FOR UPDATE

      attempt=committed AND session terminal AND checkpoint exists
          -> accept the committed terminal state

      attempt IN (prepared, reconciling)
      AND session terminal records absent
      AND original owner fenced
          -> atomically attempt=aborted, session=failed, discard staging

      lock timeout / finalizer still in progress
          -> roll back; transient retry (finalizer_in_progress)

      records inconsistent
          -> remain reconciling_commit, critical alert, no GC, no wake
    COMMIT

``database_unavailable`` and ``finalizer_in_progress`` are TRANSIENT and
must resolve automatically; ``records_inconsistent`` means an invariant
was broken and requires a human.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import AuditEventType
from packages.domain.services.audit import AuditService

ReconcileOutcome = Literal[
    "committed_accepted",
    "aborted",
    "finalizer_in_progress",
    "records_inconsistent",
    "no_attempt",
    "database_unavailable",
]


@dataclass(frozen=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    detail: str = ""


class LockTimeoutError(RuntimeError):
    """The row lock could not be acquired in time — the finalizer is
    probably still in progress (a TRANSIENT state, not a rollback)."""


TERMINAL = ("succeeded", "succeeded_partial", "failed", "cancelled")


async def _lock_or_timeout(
    db: AsyncSession, sql: str, params: dict[str, Any], timeout_ms: int = 2000
) -> Any:
    """Run a SELECT ... FOR UPDATE with a lock_timeout so a running
    finalizer yields ``finalizer_in_progress`` instead of a false
    ``aborted``. The timeout is restored afterwards."""
    await db.execute(text(f"SET LOCAL lock_timeout = {int(timeout_ms)}"))
    try:
        result = await db.execute(text(sql), params)
        return result
    except Exception as exc:  # asyncpg QueryCanceledError on lock timeout
        msg = str(exc)
        if "lock_timeout" in msg or "canceling statement due to lock timeout" in msg:
            raise LockTimeoutError("row lock not acquired in time") from exc
        raise


async def reconcile_commit(
    db: AsyncSession,
    audit: AuditService,
    session_id: UUID,
    *,
    original_owner: str | None = None,
    lock_timeout_ms: int = 2000,
) -> ReconcileResult:
    """Run the fenced reconciliation protocol inside the caller's
    transaction (the caller commits/rolls back)."""
    # 1. lock the session row (canonical order: session -> attempt)
    sess = (
        await _lock_or_timeout(
            db,
            "SELECT state, lease_owner, lease_expires_at, commit_attempt_id, question_id "
            "FROM sessions WHERE id = :id FOR UPDATE",
            {"id": session_id},
            lock_timeout_ms,
        )
    ).mappings().first()
    if sess is None:
        return ReconcileResult("no_attempt", "session missing")
    if sess["state"] in TERMINAL and sess["state"] != "failed":
        # already resolved to a success terminal — accept
        return ReconcileResult("committed_accepted", f"terminal={sess['state']}")
    if sess["state"] == "failed":
        return ReconcileResult("no_attempt", "session already failed")

    attempt_id = sess["commit_attempt_id"]
    if attempt_id is None:
        # no durable prepared row: the failure was BEFORE the boundary
        await _mark_failed(db, audit, session_id, "no_prepared_attempt")
        return ReconcileResult("aborted", "no prepared attempt (pre-boundary failure)")

    # 2. verify the original owner is fenced (no live lease)
    live = (
        await db.execute(
            text(
                "SELECT 1 FROM sessions WHERE id = :id "
                "AND lease_owner IS NOT NULL AND lease_expires_at > now()"
            ),
            {"id": session_id},
        )
    ).scalar_one_or_none()
    if live is not None:
        # a live lease means the finalizer may still be in progress
        return ReconcileResult("finalizer_in_progress", "live lease present")

    # 3. lock the attempt row
    attempt = (
        await _lock_or_timeout(
            db,
            "SELECT status FROM commit_attempts WHERE id = :id FOR UPDATE",
            {"id": attempt_id},
            lock_timeout_ms,
        )
    ).mappings().first()
    if attempt is None:
        return ReconcileResult("records_inconsistent", "attempt row vanished under lock")
    status = attempt["status"]

    # 4. decide
    if status == "committed":
        if sess["state"] in TERMINAL:
            return ReconcileResult("committed_accepted", "attempt committed + terminal present")
        # committed but no terminal record -> inconsistent (the final
        # transaction writes both in ONE transaction, so this is a bug)
        await audit.record(
            AuditEventType.ALERT_RAISED,
            session_id=session_id,
            payload={"kind": "records_inconsistent", "detail": "attempt committed but session not terminal"},
            public_summary="SECURITY: committed attempt without terminal record",
        )
        return ReconcileResult("records_inconsistent", "committed attempt without terminal record")

    if status in ("prepared", "reconciling"):
        # unresolved, owner fenced, no terminal record -> abort
        await db.execute(
            text(
                "UPDATE commit_attempts SET status = 'aborted', finished_at = now() "
                "WHERE id = :id AND status IN ('prepared','reconciling')"
            ),
            {"id": attempt_id},
        )
        await _mark_failed(db, audit, session_id, "reconciled_abort")
        return ReconcileResult("aborted", "unresolved attempt fenced -> aborted")

    if status == "aborted":
        if sess["state"] != "failed":
            await _mark_failed(db, audit, session_id, "attempt already aborted")
        return ReconcileResult("aborted", "attempt already aborted")

    return ReconcileResult("records_inconsistent", f"unknown attempt status {status}")


async def _mark_failed(
    db: AsyncSession, audit: AuditService, session_id: UUID, reason: str
) -> None:
    # T4.4 (§5.9.1 rule 5): recovery clears a stale writer intent only
    # AFTER the lease/commit attempt fencing above (no live lease here,
    # and the attempt is fenced terminal).
    await db.execute(
        text(
            "UPDATE sessions SET state = 'failed', finished_at = now(), "
            "lease_owner = NULL, lease_expires_at = NULL, termination_reason = :r, "
            "commit_intent_at = NULL "
            "WHERE id = :id AND state NOT IN ('succeeded','succeeded_partial','failed','cancelled')"
        ),
        {"id": session_id, "r": reason},
    )
    await audit.record(
        AuditEventType.SESSION_FAILED,
        session_id=session_id,
        payload={"reconciliation": reason},
        public_summary=f"session failed by reconciliation ({reason})",
    )
    await audit.record(
        AuditEventType.COMMIT_RECONCILED,
        session_id=session_id,
        payload={"reason": reason, "outcome": "aborted"},
    )


TRANSIENT_OUTCOMES = ("finalizer_in_progress", "database_unavailable")

# ── reconcile tick (T7.24, hostctl reconcile-tick, §5.2.2) ─────────────────


@dataclass(frozen=True)
class ReconcileTickOutcome:
    session_id: UUID
    outcome: ReconcileOutcome
    detail: str = ""


@dataclass(frozen=True)
class ReconcileTickResult:
    """One tick: every stuck session with its final classification.

    ``transient`` (finalizer_in_progress / database_unavailable after all
    probes) is retried by the NEXT tick; ``inconsistent`` (records
    inconsistent — a broken invariant) is NOT retried: a human is
    required (the critical alert was recorded by the protocol)."""

    resolved: tuple[ReconcileTickOutcome, ...] = ()
    transient: tuple[ReconcileTickOutcome, ...] = ()
    inconsistent: tuple[ReconcileTickOutcome, ...] = ()

    @property
    def all_resolved(self) -> bool:
        return not self.transient and not self.inconsistent


#: Session states that are stuck at the commit boundary: the session row
#: reached COMMITTING (phase 1 committed) and the commit outcome is
#: unresolved. Only the final transaction and the reconciler leave these
#: states.
STUCK_STATES = ("committing", "reconciling_commit")

#: Probe outcomes that END the session (the loop stops, the next tick
#: sees nothing for this session).
TERMINAL_TICK_OUTCOMES = ("committed_accepted", "aborted", "no_attempt")


async def find_unresolved_sessions(db: AsyncSession) -> list[UUID]:
    """The sessions stuck at the commit boundary (T7.24).

    A session in one of these states with a LIVE lease is a running
    finalizer — the probe loop classifies it as
    ``finalizer_in_progress`` (transient, retry next tick), never as
    ``aborted`` (live finalizer ≠ rollback, M2). A session WITHOUT a
    prepared attempt (a crash between the phase-1 commit and the
    prepare transaction) is resolved as a pre-boundary failure by the
    protocol (``no_prepared_attempt``)."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id FROM sessions "
                    "WHERE state IN ('committing','reconciling_commit') "
                    "ORDER BY created_at"
                )
            )
        )
        .scalars()
        .all()
    )
    # asyncpg returns its own UUID type: guard before uuid.UUID(...)
    return [r if isinstance(r, UUID) else UUID(str(r)) for r in rows]


async def reconcile_tick(
    make_db: Callable[[], Any],
    audit_factory: Callable[[Any], AuditService],
    *,
    max_probes: int = 5,
    base_delay: float = 0.5,
    lock_timeout_ms: int = 2000,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> ReconcileTickResult:
    """One tick of the reconciliation worker (T7.24, §5.2.2).

    Finds every session stuck at the commit boundary and drives the M2
    probe loop (``reconcile_with_retries``) over each: a FRESH
    connection per probe (a dead connection must never decide the
    outcome), fenced row-locks, ``finalizer_in_progress`` /
    ``database_unavailable`` retried with exponential backoff + jitter.
    The find probe uses a fresh connection too.

    Raises when the database is unreachable at the find probe — the
    caller (the tick command) reports ``database_unavailable`` and the
    next tick retries."""
    async with make_db() as db:
        stuck = await find_unresolved_sessions(db)

    resolved: list[ReconcileTickOutcome] = []
    transient: list[ReconcileTickOutcome] = []
    inconsistent: list[ReconcileTickOutcome] = []
    for session_id in stuck:
        probe = await reconcile_with_retries(
            make_db,
            audit_factory,
            session_id,
            max_probes=max_probes,
            base_delay=base_delay,
            lock_timeout_ms=lock_timeout_ms,
            sleep=sleep,
        )
        outcome = ReconcileTickOutcome(session_id, probe.outcome, probe.detail)
        if probe.outcome in TERMINAL_TICK_OUTCOMES:
            resolved.append(outcome)
        elif probe.outcome == "records_inconsistent":
            inconsistent.append(outcome)
        else:
            transient.append(outcome)
    return ReconcileTickResult(
        resolved=tuple(resolved), transient=tuple(transient), inconsistent=tuple(inconsistent)
    )


async def reconcile_with_retries(
    make_db: Callable[[], Any],
    audit_factory: Callable[[Any], AuditService],
    session_id: UUID,
    *,
    original_owner: str | None = None,
    lock_timeout_ms: int = 2000,
    max_probes: int = 5,
    base_delay: float = 0.5,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> ReconcileResult:
    """Probe loop for the reconciliation worker (T2.20): every probe uses
    a FRESH connection (a dead connection must never decide the outcome);
    transient outcomes (``finalizer_in_progress``,
    ``database_unavailable``) retry with exponential backoff + jitter;
    terminal outcomes stop the loop."""
    result = ReconcileResult("finalizer_in_progress", "no probe run")
    for probe in range(max_probes):
        try:
            async with make_db() as db, db.begin():
                result = await reconcile_commit(
                    db,
                    audit_factory(db),
                    session_id,
                    original_owner=original_owner,
                    lock_timeout_ms=lock_timeout_ms,
                )
        except LockTimeoutError:
            result = ReconcileResult("finalizer_in_progress", "row lock timeout")
        except Exception as exc:  # database_unavailable etc.
            result = ReconcileResult("database_unavailable", str(exc)[:200])
        if result.outcome not in TRANSIENT_OUTCOMES:
            return result
        if probe + 1 < max_probes:
            delay = base_delay * (2**probe) + random.uniform(0, 0.25)
            await sleep(delay)
    return result
