"""Session lease, heartbeat and progress watchdog (T2.16, T3.30, §5.2.3).

The lease is a conditional UPDATE on the session row — no separate lease
table. A heartbeat only succeeds while the caller still owns a live
lease; the progress watchdog compares last_progress_at / phase_deadline
so a stuck session is not renewed past its deadline.

T3.30 (first real MVP session): the lease timestamps use
``clock_timestamp()`` (real time), never ``now()`` — the latter is the
transaction start and is constant inside the long phase-1 session
transaction, which would pin ``lease_expires_at`` at
``txn_start + ttl`` and make every session longer than the TTL lose the
fenced commit. Long operations (LLM calls) are additionally covered by
:class:`LeaseHeartbeatGuard`.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_LEASE_TTL = timedelta(seconds=30)
DEFAULT_PHASE_DEADLINE = timedelta(seconds=600)


class LeaseLost(RuntimeError):
    """The caller no longer owns a live lease (fenced or expired)."""


@dataclass(frozen=True)
class LeaseState:
    owner: str | None
    expires_at: datetime | None
    last_heartbeat_at: datetime | None
    last_progress_at: datetime | None
    phase_deadline: datetime | None


class LeaseService:
    def __init__(self, ttl: timedelta = DEFAULT_LEASE_TTL) -> None:
        self.ttl = ttl

    async def acquire(
        self,
        db: AsyncSession,
        session_id: UUID,
        owner: str,
        *,
        phase_deadline: timedelta = DEFAULT_PHASE_DEADLINE,
    ) -> None:
        """Take the lease (session must be nonterminal) and start the
        phase deadline. Conditional: fails if another owner holds a live
        lease."""
        # T3.30: clock_timestamp() (real time), not now() (transaction
        # start): the phase-1 transaction is long-lived, and now() would
        # pin lease_expires_at at txn_start + ttl — a session longer than
        # the TTL would lose the fenced commit regardless of heartbeats
        # (first real MVP session, qwen36-35b-a3b-q6-mtp, 2026-09-14).
        result = await db.execute(
            text(
                """
                UPDATE sessions SET
                    lease_owner = :owner,
                    lease_expires_at = clock_timestamp() + :ttl,
                    last_heartbeat_at = clock_timestamp(),
                    last_progress_at = clock_timestamp(),
                    phase_deadline = clock_timestamp() + :deadline
                WHERE id = :id
                  AND state NOT IN ('succeeded','succeeded_partial','failed','cancelled','reconciling_commit')
                  AND (lease_owner IS NULL OR lease_expires_at < clock_timestamp() OR lease_owner = :owner)
                RETURNING id
                """
            ),
            {"owner": owner, "ttl": self.ttl, "deadline": phase_deadline, "id": session_id},
        )
        if result.first() is None:
            raise LeaseLost(f"cannot acquire lease for session {session_id}")

    async def heartbeat(
        self, db: AsyncSession, session_id: UUID, owner: str, *, progress: bool = True
    ) -> None:
        """Conditional renewal. ``progress=True`` also advances
        last_progress_at; the renewal is refused once the phase deadline
        has passed (the watchdog refuses to keep a stuck session alive)."""
        # T3.30: clock_timestamp() — see acquire(): now() is the
        # transaction start and would make mid-transaction renewals no-ops.
        progress_set = ", last_progress_at = clock_timestamp()" if progress else ""
        result = await db.execute(
            text(
                f"""
                UPDATE sessions SET
                    lease_expires_at = clock_timestamp() + :ttl,
                    last_heartbeat_at = clock_timestamp()
                    {progress_set}
                WHERE id = :id
                  AND lease_owner = :owner
                  AND lease_expires_at > clock_timestamp()
                  AND (phase_deadline IS NULL OR phase_deadline > clock_timestamp())
                RETURNING id
                """
            ),
            {"ttl": self.ttl, "id": session_id, "owner": owner},
        )
        if result.first() is None:
            raise LeaseLost(f"heartbeat refused for session {session_id}")

    async def release(self, db: AsyncSession, session_id: UUID, owner: str) -> None:
        await db.execute(
            text(
                "UPDATE sessions SET lease_owner = NULL, lease_expires_at = NULL "
                "WHERE id = :id AND lease_owner = :owner"
            ),
            {"id": session_id, "owner": owner},
        )

    async def is_live(self, db: AsyncSession, session_id: UUID, owner: str) -> bool:
        row = await db.execute(
            text(
                "SELECT 1 FROM sessions "
                "WHERE id = :id AND lease_owner = :owner AND lease_expires_at > now()"
            ),
            {"id": session_id, "owner": owner},
        )
        return row.scalar_one_or_none() is not None

    async def state(self, db: AsyncSession, session_id: UUID) -> LeaseState | None:
        row = (
            await db.execute(
                text(
                    "SELECT lease_owner, lease_expires_at, last_heartbeat_at, "
                    "last_progress_at, phase_deadline FROM sessions WHERE id = :id"
                ),
                {"id": session_id},
            )
        ).mappings().first()
        if row is None:
            return None
        return LeaseState(
            owner=row["lease_owner"],
            expires_at=row["lease_expires_at"],
            last_heartbeat_at=row["last_heartbeat_at"],
            last_progress_at=row["last_progress_at"],
            phase_deadline=row["phase_deadline"],
        )


class LeaseHeartbeatGuard:
    """Background lease renewal while a long operation (an LLM call) runs
    (T3.30, §5.2.3).

    The step-boundary heartbeat alone cannot keep the lease alive across a
    model call: a real local model answers in 15–90 s against a 30 s TTL
    (first real MVP session, qwen36-35b-a3b-q6-mtp — the fenced commit
    observed an expired lease and the reconciler aborted the session).
    §5.2.3: «TTL равен нескольким heartbeat intervals с запасом на
    scheduler jitter» — the renewal interval is therefore derived from the
    TTL (ttl / 3), not from the step boundary.

    Renewals run on the CALLER's session/transaction (no commit here): a
    separate connection would block on the session-row lock the caller's
    long transaction already holds, and the extension becomes durable with
    the caller's commit. Crash semantics stay clean — a mid-call crash
    rolls the whole phase back, exactly like today. Renewals use
    ``progress=False``: a mid-step renewal is a health confirmation, not a
    step completion, so ``last_progress_at`` (the progress watchdog) only
    advances at the step boundary. If a renewal is refused (phase deadline
    passed), ``LeaseLost`` is raised at guard exit and the caller aborts;
    the fenced commit / reconciler remain the final gate.

    T7.47b (flake ``test_slow_llm_does_not_lose_commit_lease`` under xdist,
    measured): the renewal runs in a CHILD task, and ``__aexit__`` DRAINS
    an in-flight renewal to completion instead of cancelling it mid-
    execute. A cancel landing inside the heartbeat's ``db.execute`` on the
    caller's shared session leaves it needing a rollback the guard cannot
    perform (the caller's transaction is in flight) — the caller then
    trips ``PendingRollbackError`` on its next statement and the phase-1
    transaction dies (reproduced deterministically: a mid-execute task
    cancel on a shared ``AsyncSession`` always poisons it; CPU contention
    under xdist only widens the ~ms execute window). Semantics unchanged:
    same renewals, same ``LeaseLost`` at exit, fenced commit still the
    gate; the exit may let one in-flight renewal finish (a lease
    extension a few ms later — exactly what the next scheduled renewal
    would have done).
    """

    def __init__(
        self,
        lease: LeaseService,
        db: AsyncSession,
        session_id: UUID,
        owner: str,
    ) -> None:
        self._lease = lease
        self._db = db
        self._session_id = session_id
        self._owner = owner
        self._interval = lease.ttl.total_seconds() / 3
        self._task: asyncio.Task[None] | None = None
        self._renewal: asyncio.Task[None] | None = None
        self._lost = False

    async def __aenter__(self) -> LeaseHeartbeatGuard:
        self._task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        assert self._task is not None
        # T7.47b: canceling the LOOP task is safe (it only ever sleeps or
        # waits on the detached renewal child); the in-flight renewal is
        # then drained to completion on the shared session — never
        # cancelled mid-execute (see class docstring).
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        renewal = self._renewal
        self._renewal = None
        if renewal is not None and not renewal.done():
            try:
                await renewal
            except LeaseLost:
                self._lost = True
            except Exception:  # infra error: the fenced commit is the gate
                pass
        self._task = None
        if self._lost:
            raise LeaseLost(f"lease lost during long operation for session {self._session_id}")
        return False

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            # T7.47b: the heartbeat runs in a CHILD task (nobody cancels
            # it): a cancel of the loop task must not land inside the
            # heartbeat's execute on the caller's shared session — that
            # would poison the session (PendingRollbackError for the
            # caller). __aexit__ drains the child to completion.
            renewal = asyncio.create_task(self._heartbeat_once())
            self._renewal = renewal
            try:
                await asyncio.shield(renewal)
            except asyncio.CancelledError:
                # the guard is stopping; __aexit__ drains the renewal
                return
            except LeaseLost:
                self._lost = True
                return
            except Exception:  # infra error: keep trying; the fenced commit is the gate
                continue

    async def _heartbeat_once(self) -> None:
        # the guard task only runs while the main coroutine awaits the
        # long operation — the session is not used concurrently
        await self._lease.heartbeat(self._db, self._session_id, self._owner, progress=False)
