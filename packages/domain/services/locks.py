"""Canonical partial lock order (T2.15, §5.2.2).

The system-wide canonical order is:

    runtime_config_heads(scope)
      -> sessions
      -> domain_revisions(scope='knowledge')
      -> domain_revisions(scope='dependency_graph')
      -> commit_attempts

A transaction may SKIP rows it does not need, but every lock it actually
takes must form a SUBSEQUENCE of the canonical order. If it turns out
late that a skipped row was needed, the transaction rolls back and
restarts — it never takes an out-of-order lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# canonical order as a list of (kind, scope) tokens
CANONICAL_ORDER: tuple[tuple[str, str], ...] = (
    ("runtime_config_heads", "scope"),
    ("sessions", ""),
    ("domain_revisions", "knowledge"),
    ("domain_revisions", "dependency_graph"),
    ("commit_attempts", ""),
)


class LockOrderViolation(RuntimeError):
    """The requested lock sequence is not a subsequence of the canonical
    order — taking it would risk deadlock."""


def validate_lock_order(locks: list[tuple[str, str]]) -> None:
    """Raise LockOrderViolation unless ``locks`` is a subsequence of
    CANONICAL_ORDER (order-preserving). Re-locking an already-locked row
    is a no-op, so duplicate tokens are allowed: the position sequence
    must only be non-decreasing."""
    position = {tok: i for i, tok in enumerate(CANONICAL_ORDER)}
    last = -1
    for want in locks:
        if want not in position:
            raise LockOrderViolation(f"lock {want} is not in the canonical order")
        if position[want] < last:
            raise LockOrderViolation(f"lock {want} is out of canonical order")
        last = position[want]


@dataclass(frozen=True)
class CommitLocks:
    """The lock set actually taken for a session commit."""

    session_id: UUID
    attempt_id: UUID
    touches_dependency_graph: bool


async def lock_commit_set(
    db: AsyncSession,
    session_id: UUID,
    attempt_id: UUID,
    *,
    touches_dependency_graph: bool = False,
) -> CommitLocks:
    """Take the commit lock set in canonical order inside the caller's
    transaction:

        sessions -> knowledge -> [dependency_graph] -> commit_attempts

    Each ``SELECT ... FOR UPDATE`` runs in order; if any row is missing
    the transaction must roll back and restart (it is an error, not a
    skip, because the row is REQUIRED for the fencing predicate).
    """
    if touches_dependency_graph:
        plan = [
            ("sessions", ""),
            ("domain_revisions", "knowledge"),
            ("domain_revisions", "dependency_graph"),
            ("commit_attempts", ""),
        ]
    else:
        plan = [
            ("sessions", ""),
            ("domain_revisions", "knowledge"),
            ("commit_attempts", ""),
        ]
    validate_lock_order(plan)
    taken: list[tuple[str, str]] = []

    # 1. sessions
    row = (
        await db.execute(
            text("SELECT id FROM sessions WHERE id = :id FOR UPDATE"), {"id": session_id}
        )
    ).first()
    if row is None:
        raise LookupError(f"session {session_id} vanished under lock")
    taken.append(("sessions", ""))

    # 2. domain_revisions(knowledge)
    row = (
        await db.execute(
            text("SELECT scope FROM domain_revisions WHERE scope = 'knowledge' FOR UPDATE")
        )
    ).first()
    if row is None:
        raise LookupError("knowledge revision row missing")
    taken.append(("domain_revisions", "knowledge"))

    # 3. domain_revisions(dependency_graph) — only when touched
    if touches_dependency_graph:
        row = (
            await db.execute(
                text("SELECT scope FROM domain_revisions WHERE scope = 'dependency_graph' FOR UPDATE")
            )
        ).first()
        if row is None:
            raise LookupError("dependency_graph revision row missing")
        taken.append(("domain_revisions", "dependency_graph"))

    # 4. commit_attempts
    row = (
        await db.execute(
            text("SELECT id FROM commit_attempts WHERE id = :id FOR UPDATE"),
            {"id": attempt_id},
        )
    ).first()
    if row is None:
        raise LookupError(f"commit attempt {attempt_id} vanished under lock")
    taken.append(("commit_attempts", ""))

    return CommitLocks(
        session_id=session_id,
        attempt_id=attempt_id,
        touches_dependency_graph=touches_dependency_graph,
    )
