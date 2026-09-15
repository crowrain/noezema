"""T7.4 (stage 7): §16 metrics — the operational reports.

The security-and-interaction report (§16.3) is the gate metric for the
security regression (этап 7: «security regression»; «Evaluation
thresholds фиксируются до серии» — the thresholds are fixed per the
frozen config, the report is the raw measurement):

- policy deny/require_operator (actions.policy_decision);
- forbidden path/address и provenance gaps (research egress rejections
  — SSRF/egress rejections + sealed-mode denies; the provenance gap is
  a source with no origin, counted via the source graph);
- idempotency mismatch (audit ``alert_raised`` kind
  ``idempotency_key_conflict``);
- source-graph corrections;
- stop/abort outcomes (operator commands + their terminal state);
- command-like messages без исполнения (inbox messages whose body
  matches the operator-command vocabulary — reported, never executed:
  the web has no message→command bridge);
- auth/CSRF/rate-limit failures (the local admin-token 401s are
  counted by the caller; the egress upstream rate-limit rejections are
  in the audit).

The technical report (§16.1) covers the operational subset that the
current schema can measure (commit attempts by status, reconciliation
age, barriers, jobs, backup/PITR age, orphan bytes/root scan via the
GC sweep audit, GC activity). The cognitive report (§16.2) covers the
knowledge-side counts (claims/assessments by state/grade,
reassessment depth, counterevidence found/resolved).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _rows(db: AsyncSession, sql: str) -> list[Any]:
    """Execute a SELECT and return the mapping rows (mypy-clean)."""
    return list((await db.execute(text(sql))).mappings().all())

#: the operator-command vocabulary: an inbox message whose body
#: contains one of these is «command-like» — it is REPORTED (the
#: security metric), never executed (the web has no message→command
#: bridge; the Command API is the only execution path)
_COMMAND_LIKE_TERMS = (
    "wake_now",
    "pause",
    "resume",
    "stop_gracefully",
    "abort_session",
)


async def security_metrics(db: AsyncSession) -> dict[str, Any]:
    """§16.3: the security-and-interaction report."""
    policy = await _rows(
        db,
        "SELECT policy_decision, count(*)::int AS n FROM actions "
        "WHERE policy_decision IS NOT NULL "
        "AND policy_decision IN ('deny','require_operator') "
        "GROUP BY policy_decision",
    )
    egress = await _rows(
        db,
        "SELECT payload->>'reason' AS reason, count(*)::int AS n "
        "FROM audit_events WHERE type = 'research_fetch_rejected' "
        "GROUP BY payload->>'reason'",
    )
    idem = (
        await db.execute(
            text(
                "SELECT count(*)::int FROM audit_events "
                "WHERE type = 'alert_raised' "
                "AND payload->>'kind' = 'idempotency_key_conflict'"
            )
        )
    ).scalar_one()
    corrections = (
        await db.execute(
            text(
                "SELECT count(*)::int FROM audit_events "
                "WHERE type = 'source_graph_changed'"
            )
        )
    ).scalar_one()
    stop_commands = await _rows(
        db,
        "SELECT state, count(*)::int AS n FROM operator_commands "
        "WHERE type IN ('stop_gracefully','abort_session') "
        "GROUP BY state",
    )
    command_like = (
        await db.execute(
            text(
                "SELECT count(*)::int FROM messages WHERE "
                "(body ILIKE '%wake_now%' OR body ILIKE '%pause%' "
                "OR body ILIKE '%resume%' OR body ILIKE '%stop_gracefully%' "
                "OR body ILIKE '%abort_session%')"
            )
        )
    ).scalar_one()
    rate_limited = (
        await db.execute(
            text(
                "SELECT count(*)::int FROM audit_events "
                "WHERE type = 'research_fetch_rejected' "
                "AND payload->>'reason' = 'upstream_rate_limit_exceeded'"
            )
        )
    ).scalar_one()
    return {
        "policy": {r["policy_decision"]: r["n"] for r in policy},
        "egress_rejections": {r["reason"]: r["n"] for r in egress},
        "idempotency_mismatches": idem,
        "source_graph_corrections": corrections,
        "stop_abort_commands": {r["state"]: r["n"] for r in stop_commands},
        "command_like_messages": command_like,
        "egress_rate_limited": rate_limited,
    }


async def technical_metrics(db: AsyncSession) -> dict[str, Any]:
    """§16.1: the technical report (the operational subset)."""
    attempts = await _rows(
        db, "SELECT status, count(*)::int AS n FROM commit_attempts GROUP BY status"
    )
    oldest_row: Any = (
        await db.execute(
            text(
                "SELECT min(prepared_at) AS oldest_unresolved FROM commit_attempts "
                "WHERE status IN ('prepared','reconciling')"
            )
        )
    ).first()
    oldest_row = oldest_row._mapping if oldest_row is not None else None
    barriers = await _rows(
        db,
        "SELECT status, count(*)::int AS n, "
        "coalesce(sum(member_count), 0)::int AS members "
        "FROM dependency_invalidation_barriers WHERE status <> 'resolved' "
        "GROUP BY status",
    )
    jobs = await _rows(
        db, "SELECT status, count(*)::int AS n FROM reassessment_jobs GROUP BY status"
    )
    backup_row: Any = (
        await db.execute(
            text(
                "SELECT count(*)::int AS total, "
                "count(*) FILTER (WHERE retention_until IS NULL OR retention_until > now())::int "
                "AS in_retention, "
                "min(created_at) AS oldest_backup_at, "
                "max(verified_at) AS last_verified_at "
                "FROM backup_manifests"
            )
        )
    ).first()
    backup_row = backup_row._mapping if backup_row is not None else None
    gc_row: Any = (
        await db.execute(
            text(
                "SELECT count(*)::int AS total, "
                "max(occurred_at) AS last_sweep, "
                "coalesce(sum((payload->'deleted'->>'artifacts')::int), 0)::int "
                "AS artifacts_deleted "
                "FROM audit_events WHERE type = 'gc_sweep' AND payload->>'apply' = 'true'"
            )
        )
    ).first()
    gc_row = gc_row._mapping if gc_row is not None else None
    latency_row: Any = (
        await db.execute(
            text(
                "SELECT count(*)::int AS n, "
                "avg(extract(epoch FROM (finished_at - started_at))) AS avg_seconds, "
                "max(extract(epoch FROM (finished_at - started_at))) AS max_seconds "
                "FROM sessions WHERE started_at IS NOT NULL AND finished_at IS NOT NULL"
            )
        )
    ).first()
    latency_row = latency_row._mapping if latency_row is not None else None
    return {
        "commit_attempts": {r["status"]: r["n"] for r in attempts},
        "oldest_unresolved_attempt_at": (
            oldest_row["oldest_unresolved"].isoformat()
            if oldest_row and oldest_row["oldest_unresolved"]
            else None
        ),
        "open_barriers": {
            r["status"]: {"n": r["n"], "members": r["members"]} for r in barriers
        },
        "reassessment_jobs": {r["status"]: r["n"] for r in jobs},
        "backups": (
            {
                "total": backup_row["total"],
                "in_retention": backup_row["in_retention"],
                "oldest_backup_at": backup_row["oldest_backup_at"].isoformat()
                if backup_row and backup_row["oldest_backup_at"]
                else None,
                "last_verified_at": backup_row["last_verified_at"].isoformat()
                if backup_row and backup_row["last_verified_at"]
                else None,
            }
            if backup_row is not None
            else None
        ),
        "gc": (
            {
                "applied_sweeps": gc_row["total"],
                "last_sweep_at": gc_row["last_sweep"].isoformat()
                if gc_row and gc_row["last_sweep"]
                else None,
                "artifacts_deleted": gc_row["artifacts_deleted"],
            }
            if gc_row is not None
            else None
        ),
        "session_latency": (
            {
                "n": latency_row["n"],
                "avg_seconds": (
                    float(latency_row["avg_seconds"])
                    if latency_row and latency_row["avg_seconds"] is not None
                    else None
                ),
                "max_seconds": (
                    float(latency_row["max_seconds"])
                    if latency_row and latency_row["max_seconds"] is not None
                    else None
                ),
            }
            if latency_row is not None
            else None
        ),
    }


async def cognitive_metrics(db: AsyncSession) -> dict[str, Any]:
    """§16.2: the cognitive report (the knowledge-side counts)."""
    claims = await _rows(
        db,
        "SELECT ca.epistemic_status, count(*)::int AS n "
        "FROM claims c JOIN claim_assessments ca "
        "  ON ca.claim_id = c.id AND ca.valid = true "
        "GROUP BY ca.epistemic_status",
    )
    assessments = await _rows(
        db,
        "SELECT effective_grade, count(*)::int AS n FROM claim_assessments "
        "WHERE valid = true GROUP BY effective_grade",
    )
    jobs = await _rows(
        db, "SELECT status, count(*)::int AS n FROM reassessment_jobs GROUP BY status"
    )
    counters = (
        await db.execute(
            text(
                "SELECT count(*) FILTER (WHERE relation = 'counters')::int AS found, "
                "count(*) FILTER (WHERE relation = 'counters' AND id IN ("
                "  SELECT evidence_id FROM counterevidence_resolutions"
                "))::int AS resolved "
                "FROM evidence"
            )
        )
    ).first()
    counter_row: Any = counters
    counter_row = counter_row._mapping if counter_row is not None else None
    return {
        "claims_by_epistemic_status": {
            (r["epistemic_status"] or "unknown"): r["n"] for r in claims
        },
        "assessments_by_grade": {r["effective_grade"]: r["n"] for r in assessments},
        "reassessment_jobs": {r["status"]: r["n"] for r in jobs},
        "counterevidence": (
            {"found": counter_row["found"], "resolved": counter_row["resolved"]}
            if counter_row is not None
            else None
        ),
    }


async def all_metrics(db: AsyncSession) -> dict[str, Any]:
    """The full §16 report: technical + cognitive + security."""
    return {
        "technical": await technical_metrics(db),
        "cognitive": await cognitive_metrics(db),
        "security": await security_metrics(db),
    }
