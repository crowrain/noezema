"""The knowledge writer gate + session commit intent (§5.9.1, §14.1, T4.4).

One-directional session rules 1–6: the session ALWAYS wins, the worker
yields. The protocol in code:

- the gate (``knowledge_write_gate``, exactly one scope='global' row) is
  acquired with a SINGLE CAS UPDATE — NOWAIT semantics: a held, unexpired
  gate owned by someone else is a conflict (0 rows), an expired lease is
  taken over. No long DB transaction, no blocking wait: the holder's
  lease is the crash-recovery window.
- the session registers the PRIORITY writer intent
  (``sessions.commit_intent_at``) under a live lease BEFORE the heavy
  validation (§5.2.2 step 4, §5.9.1 rule 1). The intent is cleared in the
  session's terminal transaction (committed or aborted); recovery clears
  a stale intent only after fencing the lease/commit attempt
  (``reconciler._mark_failed``).
- the worker (reassessment) and the activation acquisition (T4.5) take the
  gate NOWAIT; a session intent present at admission or appearing during
  validation makes the worker return its jobs without committing
  (rule 4). The session itself does NOT take the gate: its declaration is
  the intent, and the fenced revision check is the last line of defence
  (rule 6).

Owner kinds and priorities (higher wins; the gate row records the current
holder's priority for operator visibility):

- ``session`` — reserved; sessions declare via intent, not the gate;
- ``activation`` — activation acquisition takes the gate exclusively
  before installing the activating pointer (T4.5);
- ``worker`` — the reassessment worker (low priority, defers on conflict
  with jitter, rule 3);
- ``cascade`` — cascade invalidation start/barrier (trusted writer, same
  writer class as the worker).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

GATE_SCOPE = "global"

GATE_PRIORITY_WORKER = 0
GATE_PRIORITY_CASCADE = 1
GATE_PRIORITY_ACTIVATION = 2
GATE_PRIORITY_SESSION = 3

OWNER_WORKER = "worker"
OWNER_CASCADE = "cascade"
OWNER_ACTIVATION = "activation"
OWNER_SESSION = "session"


@dataclass(frozen=True)
class GateHolder:
    """The current gate holder (None-safe access via :func:`writer_gate_holder`)."""

    owner_kind: str
    owner_id: str
    priority: int
    acquired_at: datetime
    lease_expires_at: datetime


async def acquire_writer_gate(
    db: AsyncSession,
    *,
    owner_kind: str,
    owner_id: str,
    priority: int,
    lease_seconds: int,
) -> bool:
    """Acquire the gate NOWAIT (single CAS UPDATE).

    Returns True when the caller now holds the gate. A live foreign holder
    is a conflict (False); an expired lease or the caller's own previous
    lease is taken over. The lease bounds the crash window: a crashed
    holder is overwritten after ``lease_seconds``.
    """
    result = await db.execute(
        text(
            """
            UPDATE knowledge_write_gate
            SET owner_kind = :k, owner_id = :o, priority = :p,
                acquired_at = now(),
                lease_expires_at = now() + make_interval(secs => :l)
            WHERE scope = :s
              AND (owner_kind IS NULL
                   OR lease_expires_at < now()
                   OR (owner_kind = :k AND owner_id = :o))
            """
        ),
        {"k": owner_kind, "o": owner_id, "p": priority, "l": lease_seconds, "s": GATE_SCOPE},
    )
    return int(getattr(result, "rowcount", 0) or 0) == 1


async def release_writer_gate(db: AsyncSession, *, owner_kind: str, owner_id: str) -> None:
    """Release the gate if (and only if) the caller still holds it."""
    await db.execute(
        text(
            """
            UPDATE knowledge_write_gate
            SET owner_kind = NULL, owner_id = NULL, priority = NULL,
                acquired_at = NULL, lease_expires_at = NULL
            WHERE scope = :s AND owner_kind = :k AND owner_id = :o
            """
        ),
        {"s": GATE_SCOPE, "k": owner_kind, "o": owner_id},
    )


async def writer_gate_holder(db: AsyncSession) -> GateHolder | None:
    """Read the current holder (unexpired leases only)."""
    row = (
        (
            await db.execute(
                text(
                    """
                    SELECT owner_kind, owner_id, priority, acquired_at, lease_expires_at
                    FROM knowledge_write_gate
                    WHERE scope = :s
                      AND owner_kind IS NOT NULL
                      AND lease_expires_at > now()
                    """
                ),
                {"s": GATE_SCOPE},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return GateHolder(
        owner_kind=row["owner_kind"],
        owner_id=row["owner_id"],
        priority=int(row["priority"]),
        acquired_at=row["acquired_at"],
        lease_expires_at=row["lease_expires_at"],
    )


# ── session commit intent (§5.9.1 rule 1, §5.2.2 step 4) ──────────────────


async def register_session_intent(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Register the priority writer intent under a LIVE lease (rule 1:
    «атомарно устанавливает commit_intent_at под живым lease до тяжёлой
    валидации»). The session must already be in ``committing``-bound
    state; without a live lease the intent is refused (False) — a dead
    session must not declare."""
    result = await db.execute(
        text(
            """
            UPDATE sessions SET commit_intent_at = now()
            WHERE id = :id AND state = 'committing'
              AND lease_owner IS NOT NULL AND lease_expires_at > now()
              AND commit_intent_at IS NULL
            """
        ),
        {"id": session_id},
    )
    return int(getattr(result, "rowcount", 0) or 0) == 1


async def clear_session_intent(db: AsyncSession, session_id: uuid.UUID) -> None:
    """Clear the intent (the session's terminal transaction, rule 5)."""
    await db.execute(
        text("UPDATE sessions SET commit_intent_at = NULL WHERE id = :id"),
        {"id": session_id},
    )


async def active_session_intent(db: AsyncSession) -> bool:
    """True while any session declares a live writer intent (state
    ``committing`` + live lease + intent set). This is the signal the
    worker yields on (rule 4)."""
    n = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM sessions
                WHERE state = 'committing'
                  AND commit_intent_at IS NOT NULL
                  AND lease_owner IS NOT NULL AND lease_expires_at > now()
                """
            )
        )
    ).scalar_one()
    return int(n) > 0
