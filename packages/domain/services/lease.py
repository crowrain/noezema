"""Session lease, heartbeat and progress watchdog (T2.16, §5.2.3).

The lease is a conditional UPDATE on the session row — no separate lease
table. A heartbeat only succeeds while the caller still owns a live
lease; the progress watchdog compares last_progress_at / phase_deadline
so a stuck session is not renewed past its deadline.
"""

from __future__ import annotations

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
        result = await db.execute(
            text(
                """
                UPDATE sessions SET
                    lease_owner = :owner,
                    lease_expires_at = now() + :ttl,
                    last_heartbeat_at = now(),
                    last_progress_at = now(),
                    phase_deadline = now() + :deadline
                WHERE id = :id
                  AND state NOT IN ('succeeded','succeeded_partial','failed','cancelled','reconciling_commit')
                  AND (lease_owner IS NULL OR lease_expires_at < now() OR lease_owner = :owner)
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
        progress_set = ", last_progress_at = now()" if progress else ""
        result = await db.execute(
            text(
                f"""
                UPDATE sessions SET
                    lease_expires_at = now() + :ttl,
                    last_heartbeat_at = now()
                    {progress_set}
                WHERE id = :id
                  AND lease_owner = :owner
                  AND lease_expires_at > now()
                  AND (phase_deadline IS NULL OR phase_deadline > now())
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
