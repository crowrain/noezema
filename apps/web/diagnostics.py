"""Diagnostics queries for the M7 web (T7.1, §22.1).

Read-only operational views:
  - reconciliation: sessions in committing/reconciling_commit + their
    commit attempts (an unresolved attempt blocks wake and GC — the view
    makes that state visible, the block itself is enforced in the
    scheduler/finalizer);
  - invalidation: dependency-invalidation barriers with closure progress
    (next_offset / member_count);
  - reassessment jobs: blocked / retry / queued views with error class,
    attempt count and next-attempt time;
  - activation slot + writer gate + domain revisions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.base import JsonDict

TERMINAL_STATES = ("succeeded", "succeeded_partial", "failed", "cancelled")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _uuid(value: Any) -> str | None:
    return None if value is None else str(value)


async def _rows(db: AsyncSession, sql: str, params: dict[str, Any]) -> list[Any]:
    return list((await db.execute(text(sql), params)).mappings().all())


async def summary(db: AsyncSession) -> JsonDict:
    """One aggregated diagnostics view (the /diagnostics page)."""
    session_rows = await _rows(
        db,
        "SELECT state, count(*)::int AS n FROM sessions "
        f"WHERE state NOT IN {TERMINAL_STATES!r} GROUP BY state",
        {},
    )
    attempt_rows = await _rows(
        db, "SELECT status, count(*)::int AS n FROM commit_attempts GROUP BY status", {}
    )
    barrier_rows = await _rows(
        db,
        "SELECT status, count(*)::int AS n, "
        "coalesce(sum(member_count), 0)::int AS members, "
        "coalesce(sum(next_offset), 0)::int AS closed "
        "FROM dependency_invalidation_barriers WHERE status <> 'resolved' "
        "GROUP BY status",
        {},
    )
    job_rows = await _rows(
        db, "SELECT status, count(*)::int AS n FROM reassessment_jobs GROUP BY status", {}
    )
    blocked_jobs = await _rows(
        db,
        "SELECT j.id, j.error_class, j.attempts, j.blocked_at, c.statement "
        "FROM reassessment_jobs j "
        "LEFT JOIN claims c ON c.id = j.claim_id "
        "WHERE j.status = 'blocked' ORDER BY j.blocked_at DESC LIMIT 10",
        {},
    )
    head = await _rows(
        db,
        "SELECT active_config_snapshot_id, activating_config_snapshot_id, "
        "activation_fence, activation_lease_owner, activation_lease_expires_at "
        "FROM runtime_config_heads WHERE scope = 'global'",
        {},
    )
    gate = await _rows(
        db,
        "SELECT owner_kind, owner_id, priority, acquired_at, lease_expires_at "
        "FROM knowledge_write_gate WHERE scope = 'global'",
        {},
    )
    revisions = await _rows(db, "SELECT scope, revision::int FROM domain_revisions ORDER BY scope", {})

    head_row = head[0] if head else None
    gate_row = gate[0] if gate else None
    return {
        "sessions": {r["state"]: r["n"] for r in session_rows},
        "commit_attempts": {r["status"]: r["n"] for r in attempt_rows},
        "unresolved_attempts": sum(
            r["n"] for r in attempt_rows if r["status"] in ("prepared", "reconciling")
        ),
        "open_barriers": {r["status"]: dict(r) for r in barrier_rows},
        "reassessment_jobs": {r["status"]: r["n"] for r in job_rows},
        "blocked_jobs": [
            {
                "id": _uuid(r["id"]),
                "error_class": r["error_class"],
                "attempts": r["attempts"],
                "blocked_at": _iso(r["blocked_at"]),
                "claim_statement": r["statement"],
            }
            for r in blocked_jobs
        ],
        "activation": (
            {
                "active_snapshot_id": _uuid(head_row["active_config_snapshot_id"]),
                "activating_snapshot_id": _uuid(head_row["activating_config_snapshot_id"]),
                "fence": int(head_row["activation_fence"] or 0),
                "lease_owner": head_row["activation_lease_owner"],
                "lease_expires_at": _iso(head_row["activation_lease_expires_at"]),
            }
            if head_row is not None
            else None
        ),
        "writer_gate": (
            {
                "owner_kind": gate_row["owner_kind"],
                "owner_id": gate_row["owner_id"],
                "priority": gate_row["priority"],
                "acquired_at": _iso(gate_row["acquired_at"]),
                "lease_expires_at": _iso(gate_row["lease_expires_at"]),
            }
            if gate_row is not None
            else None
        ),
        "revisions": {r["scope"]: r["revision"] for r in revisions},
    }


async def reconciliation(db: AsyncSession) -> JsonDict:
    """Sessions in the commit boundary + their attempts and checkpoints.

    A ``prepared``/``reconciling`` attempt without a terminal session is
    the unresolved state: it blocks wake admission and GC (§5.2.2, §15.3)."""
    rows = await _rows(
        db,
        """
        SELECT s.id AS session_id, s.state AS session_state, s.commit_intent_at,
               s.termination_reason, s.finished_at,
               ca.id AS attempt_id, ca.status AS attempt_status,
               ca.staging_hash, ca.base_knowledge_revision::int AS base_knowledge_revision,
               ca.base_dependency_graph_revision::int AS base_dependency_graph_revision,
               ca.prepared_at, ca.finished_at AS attempt_finished_at,
               ck.knowledge_revision::int AS checkpoint_knowledge_revision,
               ck.dependency_graph_revision::int AS checkpoint_dependency_graph_revision,
               ck.workspace_manifest_id
        FROM sessions s
        LEFT JOIN commit_attempts ca ON ca.session_id = s.id
        LEFT JOIN checkpoints ck ON ck.session_id = s.id
        WHERE s.state IN ('committing', 'reconciling_commit')
           OR ca.status IN ('prepared', 'reconciling')
        ORDER BY s.created_at DESC
        """,
        {},
    )
    return {
        "entries": [
            {
                "session_id": _uuid(r["session_id"]),
                "session_state": r["session_state"],
                "commit_intent_at": _iso(r["commit_intent_at"]),
                "termination_reason": r["termination_reason"],
                "finished_at": _iso(r["finished_at"]),
                "attempt": (
                    {
                        "id": _uuid(r["attempt_id"]),
                        "status": r["attempt_status"],
                        "staging_hash": r["staging_hash"],
                        "base_knowledge_revision": r["base_knowledge_revision"],
                        "base_dependency_graph_revision": r["base_dependency_graph_revision"],
                        "prepared_at": _iso(r["prepared_at"]),
                        "finished_at": _iso(r["attempt_finished_at"]),
                    }
                    if r["attempt_id"] is not None
                    else None
                ),
                "checkpoint": (
                    {
                        "knowledge_revision": r["checkpoint_knowledge_revision"],
                        "dependency_graph_revision": r["checkpoint_dependency_graph_revision"],
                        "workspace_manifest_id": _uuid(r["workspace_manifest_id"]),
                    }
                    if r["checkpoint_knowledge_revision"] is not None
                    else None
                ),
            }
            for r in rows
        ],
        "unresolved": any(
            r["attempt_status"] in ("prepared", "reconciling") for r in rows
        ),
    }


async def jobs(
    db: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> JsonDict:
    """Reassessment jobs (the retry/blocked views): error class, attempt
    budget, next-attempt scheduling, and the claim they target."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    # a dynamic WHERE clause: SQLAlchemy's text() does not convert a
    # nullable bind parameter (asyncpg cannot infer its type from None)
    where = ""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status is not None:
        where = " AND j.status = :status"
        params["status"] = status
    rows = await _rows(
        db,
        f"""
        SELECT j.id, j.status, j.reason, j.priority, j.attempts, j.max_attempts,
               j.error_class, j.last_error, j.lease_owner, j.lease_expires_at,
               j.next_attempt_at, j.blocked_at, j.completed_at, j.enqueued_at,
               c.statement AS claim_statement
        FROM reassessment_jobs j
        LEFT JOIN claims c ON c.id = j.claim_id
        WHERE 1 = 1{where}
        ORDER BY j.priority DESC, j.enqueued_at ASC, j.id
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    return {
        "jobs": [
            {
                "id": _uuid(r["id"]),
                "status": r["status"],
                "reason": r["reason"],
                "priority": r["priority"],
                "attempts": r["attempts"],
                "max_attempts": r["max_attempts"],
                "error_class": r["error_class"],
                "last_error": r["last_error"],
                "lease_owner": r["lease_owner"],
                "lease_expires_at": _iso(r["lease_expires_at"]),
                "next_attempt_at": _iso(r["next_attempt_at"]),
                "blocked_at": _iso(r["blocked_at"]),
                "completed_at": _iso(r["completed_at"]),
                "enqueued_at": _iso(r["enqueued_at"]),
                "claim_statement": r["claim_statement"],
            }
            for r in rows
        ]
    }


async def barriers(
    db: AsyncSession, *, include_resolved: bool = False, limit: int = 100
) -> JsonDict:
    """Dependency-invalidation barriers with closure progress
    (next_offset / member_count) and the immutable closure manifest."""
    limit = max(1, min(limit, 500))
    where = "" if include_resolved else " AND b.status <> 'resolved'"
    rows = await _rows(
        db,
        f"""
        SELECT b.id, b.root_claim_id, b.status, b.generation,
               b.graph_revision::int AS graph_revision,
               b.member_count, b.next_offset, b.last_error,
               b.created_at, b.updated_at, b.resolved_at,
               c.statement AS root_statement,
               cm.sha256 AS closure_sha256, cm.count AS closure_count
        FROM dependency_invalidation_barriers b
        JOIN claims c ON c.id = b.root_claim_id
        LEFT JOIN closure_manifests cm ON cm.id = b.closure_manifest_id
        WHERE 1 = 1{where}
        ORDER BY b.created_at DESC, b.generation DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return {
        "barriers": [
            {
                "id": _uuid(r["id"]),
                "root_claim_id": _uuid(r["root_claim_id"]),
                "status": r["status"],
                "generation": r["generation"],
                "graph_revision": r["graph_revision"],
                "member_count": r["member_count"],
                "next_offset": r["next_offset"],
                "closure_progress": (
                    f"{r['next_offset']}/{r['member_count']}" if r["member_count"] else "0/0"
                ),
                "last_error": r["last_error"],
                "created_at": _iso(r["created_at"]),
                "updated_at": _iso(r["updated_at"]),
                "resolved_at": _iso(r["resolved_at"]),
                "root_statement": r["root_statement"],
                "closure_manifest": (
                    {"sha256": r["closure_sha256"], "count": r["closure_count"]}
                    if r["closure_sha256"] is not None
                    else None
                ),
            }
            for r in rows
        ]
    }
