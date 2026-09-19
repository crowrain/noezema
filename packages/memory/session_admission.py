"""T7.20 (§8.7.2, ADR-0009): the committed session admission record.

The online-activation quiesce check ("no active sessions") is only sound
if an in-flight session is VISIBLE to it. A session's phase-1 transaction
is long-lived: the ``sessions`` row (and its lease) is uncommitted until
the session reaches COMMITTING, so a session admitted before the flip is
invisible to the check at the moment of the flip — the EVAL-3d quiesce
race (docs/eval/EVAL-3-freeze.md §10.5, claim 8bbbb06a).

The barrier: before the phase-1 transaction opens, the host commits a
record in ``session_admissions`` (migration 0022). The record lives until
the session's terminal transaction (a DB trigger on ``sessions`` deletes
it on every terminal state change — no code path can miss the release)
or its lease expires. A crashed session cannot commit knowledge once its
own (shorter, heartbeat-renewed) lease is dead, so an expired admission
record is swept by the activation at the quiesce check.

The admission lease is NOT heartbeat-renewed: the session itself cannot
outlive its phase deadline (the watchdog refuses renewals past it), so
``lease = created_at + phase_deadline + margin`` covers the whole
possible lifetime of the session, including the prepare + final window.

The commit-time backstop for the residual window (admission record lost
or expired while the session can still commit) lives in
``packages/domain/services/commit.py`` — see ADR-0009.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: margin over the phase deadline for the admission lease: the prepare +
#: final transactions run right after the phase-1 commit and are short,
#: but the window is bounded conservatively (a crashed session blocks the
#: flip at most for phase_deadline + this margin)
ADMISSION_LEASE_MARGIN_SECONDS = 600


async def register_session_admission(
    db: AsyncSession,
    session_id: UUID,
    *,
    node_owner: str,
    config_snapshot_id: UUID,
    phase_deadline_seconds: int,
) -> None:
    """Insert the admission record (the caller commits this ``db``).

    ``config_snapshot_id`` is the effective snapshot at admission —
    informational for operators; the session's own pin is the
    ``sessions.config_snapshot_id`` written in the phase-1 transaction,
    and the commit-time backstop compares that pin against the pointer.
    """
    await db.execute(
        text(
            """
            INSERT INTO session_admissions
                (session_id, node_owner, config_snapshot_id, lease_expires_at)
            VALUES (
                :id, :owner, :snap,
                clock_timestamp() + make_interval(secs => :lease)
            )
            """
        ),
        {
            "id": session_id,
            "owner": node_owner,
            "snap": config_snapshot_id,
            "lease": phase_deadline_seconds + ADMISSION_LEASE_MARGIN_SECONDS,
        },
    )


async def count_live_admissions(db: AsyncSession) -> int:
    """Live admission records = in-flight sessions the phase-1 visibility
    gap hides from the ``sessions`` count (T7.20)."""
    return int(
        (
            await db.execute(
                text("SELECT count(*) FROM session_admissions WHERE lease_expires_at > now()")
            )
        ).scalar_one()
    )


async def sweep_expired_admissions(db: AsyncSession) -> int:
    """Delete expired admission records (crashed/stalled sessions that
    cannot commit knowledge anymore — their own lease is dead). Returns
    the number of rows swept. The caller commits this ``db``; the sweep
    is inside the activation's quiesce transaction and rolls back with
    it, so it is safely re-runnable."""
    result = await db.execute(
        text("DELETE FROM session_admissions WHERE lease_expires_at <= now()")
    )
    return int(getattr(result, "rowcount", 0) or 0)
