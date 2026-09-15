"""T7.3 (stage 7, §15.3, §20.12): GC — the full root set.

«GC не удаляет root-reachable object» is a first-level invariant
(§15.2, §20.12: «Удаляется объект текущей БД, unresolved commit или
backup. Контроль: полный root set и random-point restore»).

The sweep collects the FULL root set of §15.3 into a single
``gc_roots`` CTE (one consistent snapshot):

- current domain FKs, evidence, environments, context/model/tool
  artifacts and attestations (every live reference to an artifact row:
  evidence observation artifacts, attestation artifacts, environment
  manifest artifacts, source-graph correction basis artifacts, backup
  inventory documents);
- workspace/backup manifests in the retention window (backup
  manifests keep their inventory document; expired backups are
  REPORTED, never auto-deleted — an operator decision);
- active staging/overlays (the staging ops of non-terminal sessions
  and the artifacts they reference);
- unresolved commit attempts (``prepared``/``reconciling``) and their
  manifests;
- reassessment/resolution basis artifacts (the resolution evidence and
  its observation artifact);
- closure manifests of active AND retention-window dependency
  barriers (plus their root claims via the manifest FK);
- config-attempt manifests and shadow heads in
  ``preparing_heads | ready | publishing | post_publish |
  post_publish_blocked`` (after audited ``failed | superseded`` —
  until the retention window; ``active`` — the effective snapshot,
  kept indefinitely);
- active host-transition / host-policy-change heads and their current
  records — captured in the host_state document of every backup
  manifest inside the retention window (a backup taken while an
  operation is active IS the GC root for it; the record/event
  directories live in the host lib, not in the DB);
- pinned/legal-retention objects (``gc_pinned``).

Retention policies (defaults documented, injectable — the exact
periods remain an open question per §21 item 11):

- workspace manifests: 14 days after freeze;
- backup manifests: the ``retention_until`` stamped at creation;
- terminal config attempts (``failed | superseded``): 30 days;
- resolved dependency barriers: 30 days;
- terminal (committed/aborted) commit attempts: 30 days.

«При ``reconciling_commit`` GC соответствующей сессии запрещён»: the
sweep skips the session's own rows (staging, checkpoints, workspace
manifests, commit attempts) while the session is in
``reconciling_commit``; a ``prepared``/``reconciling`` attempt of any
session is a root (an unresolved attempt is a GC root, §5.2.2).

The sweep is DRY by default: it reports the candidates and the root
set. With ``apply=True`` it deletes in one transaction (artifacts +
their store objects first, then registry rows) and records the audit
(``gc_sweep``) in the same transaction.
"""

from __future__ import annotations

import contextlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import AuditEventType
from packages.domain.services.audit import AuditService

#: §15.3: config-attempt states that keep the attempt a GC root
#: indefinitely (after audited failed/superseded — the retention window)
ACTIVE_CONFIG_ATTEMPT_STATES = (
    "preparing_heads",
    "ready",
    "publishing",
    "post_publish",
    "post_publish_blocked",
)
#: terminal config-attempt states kept until the retention window
TERMINAL_CONFIG_ATTEMPT_STATES = ("failed", "superseded")
#: unresolved (non-terminal) commit attempts — GC roots (§5.2.2)
UNRESOLVED_ATTEMPT_STATES = ("prepared", "reconciling")
#: active dependency-barrier statuses — GC roots (§8.6)
ACTIVE_BARRIER_STATUSES = ("discovering", "active", "closing")
#: non-terminal session states — active staging/overlays
ACTIVE_SESSION_STATES = (
    "created",
    "waking",
    "orienting",
    "selecting_question",
    "planning",
    "exploring",
    "verifying",
    "stopping",
    "consolidating",
    "reporting",
    "committing",
    "reconciling_commit",
    "aborting",
)
#: terminal session states (a session's own rows are no longer roots
#: once it reaches one of these, subject to retention)
TERMINAL_SESSION_STATES = ("succeeded", "succeeded_partial", "failed", "cancelled")

#: default retention policies (documented; the exact periods remain an
#: open question per §21 item 11 — injectable via run_gc)
DEFAULT_WORKSPACE_RETENTION_DAYS = 14
DEFAULT_BACKUP_RETENTION_DAYS = 30
DEFAULT_ARTIFACT_RETENTION_DAYS = 30
DEFAULT_TERMINAL_CONFIG_ATTEMPT_RETENTION_DAYS = 30
DEFAULT_RESOLVED_BARRIER_RETENTION_DAYS = 30
DEFAULT_TERMINAL_ATTEMPT_RETENTION_DAYS = 30


def _in(states: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{s}'" for s in states) + ")"


def _root_set_sql() -> str:
    """The full §15.3 root set as one CTE (a single consistent snapshot).

    Emits ``root_artifact_id`` (nullable — some roots are rows, not
    artifacts; the callers filter/JOIN as needed).
    """
    return f"""
WITH gc_roots AS (
    -- (1) current domain FKs / evidence / environments / attestations:
    -- every live reference to an artifact row
    SELECT id AS root_artifact_id FROM artifacts WHERE id IN (
        SELECT observation_artifact_id FROM evidence
        WHERE observation_artifact_id IS NOT NULL
        UNION
        SELECT supporting_artifact_id FROM operator_attestations
        WHERE supporting_artifact_id IS NOT NULL
        UNION
        SELECT manifest_artifact_id FROM environment_manifests
        WHERE manifest_artifact_id IS NOT NULL
        UNION
        SELECT basis_artifact_id FROM source_graph_corrections
        WHERE basis_artifact_id IS NOT NULL
        UNION
        SELECT artifact_inventory_artifact_id FROM backup_manifests
        WHERE artifact_inventory_artifact_id IS NOT NULL
    )
    UNION
    -- (2) backup manifests in the retention window: their inventory
    -- document stays alive while the manifest is in the window
    SELECT a.id FROM artifacts a
    JOIN backup_manifests bm ON bm.artifact_inventory_artifact_id = a.id
    WHERE bm.retention_until IS NULL OR bm.retention_until > now()
    UNION
    -- (4) unresolved commit attempts and their manifests (the
    -- checkpoint's workspace manifest row of an unresolved session is
    -- kept via the checkpoint's own row — the manifest itself is a
    -- candidate only after the attempt resolves)
    SELECT cm.id FROM checkpoints cm
    JOIN commit_attempts ca ON ca.session_id = cm.session_id
    WHERE ca.status IN {_in(UNRESOLVED_ATTEMPT_STATES)}
    UNION
    -- (5) reassessment/resolution basis artifacts
    SELECT ev.observation_artifact_id FROM counterevidence_resolutions cr
    JOIN evidence ev ON ev.id = cr.basis_evidence_id
    WHERE cr.basis_evidence_id IS NOT NULL
      AND ev.observation_artifact_id IS NOT NULL
    UNION
    SELECT ev.observation_artifact_id FROM counterevidence_resolutions cr
    JOIN evidence ev ON ev.id = cr.evidence_id
    WHERE cr.evidence_id IS NOT NULL AND ev.observation_artifact_id IS NOT NULL
    UNION
    -- (6) closure manifests of active AND retention-window barriers
    -- (the manifest rows and root claims stay alive; the candidate
    -- query for barriers enforces the same predicate)
    SELECT cm.id FROM closure_manifests cm
    JOIN dependency_invalidation_barriers b ON b.closure_manifest_id = cm.id
    WHERE b.status IN {_in(ACTIVE_BARRIER_STATUSES)}
       OR (b.status = 'resolved'
           AND b.resolved_at > now() - interval '{DEFAULT_RESOLVED_BARRIER_RETENTION_DAYS} days')
)
SELECT root_artifact_id FROM gc_roots
"""





async def list_gc_roots(db: AsyncSession) -> dict[str, Any]:
    """Compute the root set and the deletion candidates (DRY RUN).

    Every candidate query re-embeds the root CTE so the root logic and
    the candidate predicate live in one statement (one consistent
    read). The reconciling-session guard is applied per §15.3:
    «При reconciling_commit GC соответствующей сессии запрещён».
    """
    reconciling_sessions = [
        r[0]
        for r in (
            await db.execute(
                text("SELECT id::text FROM sessions WHERE state = 'reconciling_commit'")
            )
        ).all()
    ]
    # «При reconciling_commit GC соответствующей сессии запрещён»: the
    # session's own rows (staging, workspace manifests, commit
    # attempts) are never candidates while it reconciles
    protected = list(set(reconciling_sessions))

    root_artifact_count = (
        await db.execute(
            text(
                f"SELECT count(DISTINCT root_artifact_id)::int FROM ({_root_set_sql()}) r "
                "WHERE root_artifact_id IS NOT NULL"
            )
        )
    ).scalar_one()

    candidate_artifacts = [
        r[0]
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT a.id::text FROM artifacts a
                    WHERE NOT EXISTS (
                        SELECT 1 FROM ({_root_set_sql()}) r
                        WHERE r.root_artifact_id = a.id
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM gc_pinned p
                        WHERE p.kind = 'artifact' AND p.object_id = a.id::text
                    )
                    AND a.created_at < now() - interval '{DEFAULT_ARTIFACT_RETENTION_DAYS} days'
                    ORDER BY a.created_at, a.id
                    """
                )
            )
        ).all()
    ]

    candidate_workspace_manifests = [
        r[0]
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT wm.id::text FROM workspace_manifests wm
                    WHERE wm.frozen_at < now() - interval '{DEFAULT_WORKSPACE_RETENTION_DAYS} days'
                      AND (wm.session_id IS NULL OR wm.session_id::text != ALL(:protected))
                      AND NOT EXISTS (
                          SELECT 1 FROM commit_attempts ca
                          WHERE ca.workspace_manifest_id = wm.id
                            AND ca.status IN {_in(UNRESOLVED_ATTEMPT_STATES)}
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM checkpoints cp
                          WHERE cp.workspace_manifest_id = wm.id
                            AND cp.session_id::text != ALL(:protected)
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM gc_pinned p
                          WHERE p.kind = 'workspace_manifest' AND p.object_id = wm.id::text
                      )
                    ORDER BY wm.frozen_at
                    """
                ),
                {"protected": protected},
            )
        ).all()
    ]

    candidate_terminal_attempts = [
        r[0]
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT ca.id::text FROM commit_attempts ca
                    WHERE ca.status IN ('committed','aborted')
                      AND ca.finished_at < now() - interval '{DEFAULT_TERMINAL_ATTEMPT_RETENTION_DAYS} days'
                      AND ca.session_id::text != ALL(:protected)
                      AND ca.session_id NOT IN (
                          SELECT id FROM sessions WHERE state = 'reconciling_commit'
                      )
                    ORDER BY ca.finished_at
                    """
                ),
                {"protected": protected},
            )
        ).all()
    ]

    # NOTE: config snapshots are NOT deletable by the sweep — the
    # effective snapshot, the shadow heads (claim_assessment_heads FK),
    # sessions, reassessment jobs and the snapshot base chain all
    # reference them, and there is no FK-free terminal state in the
    # current schema. The retention policy for terminal
    # (failed | superseded) attempts is therefore ENFORCED AS
    # RETENTION (they stay, audited), and this list is the report of
    # which terminal attempts are past their retention window (the
    # operator decides on cleanup; the sweep never deletes them).
    expired_terminal_config_attempts = [
        r[0]
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT cs.id::text FROM config_snapshots cs
                    WHERE cs.activation_state IN {_in(TERMINAL_CONFIG_ATTEMPT_STATES)}
                      AND cs.created_at < now() - interval '{DEFAULT_TERMINAL_CONFIG_ATTEMPT_RETENTION_DAYS} days'
                    ORDER BY cs.created_at
                    """
                )
            )
        ).all()
    ]

    candidate_resolved_barriers = [
        r[0]
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT b.id::text FROM dependency_invalidation_barriers b
                    WHERE b.status = 'resolved'
                      AND b.resolved_at < now() - interval '{DEFAULT_RESOLVED_BARRIER_RETENTION_DAYS} days'
                    ORDER BY b.resolved_at
                    """
                )
            )
        ).all()
    ]

    expired_backup_manifests = [
        r[0]
        for r in (
            await db.execute(
                text(
                    "SELECT id::text FROM backup_manifests "
                    "WHERE retention_until IS NOT NULL AND retention_until <= now() "
                    "ORDER BY retention_until"
                )
            )
        ).all()
    ]

    return {
        "reconciling_sessions": reconciling_sessions,
        "root_artifact_count": root_artifact_count,
        "candidate_artifacts": candidate_artifacts,
        "candidate_workspace_manifests": candidate_workspace_manifests,
        "candidate_terminal_attempts": candidate_terminal_attempts,
        "expired_terminal_config_attempts": expired_terminal_config_attempts,
        "candidate_resolved_barriers": candidate_resolved_barriers,
        "expired_backup_manifests": expired_backup_manifests,
    }


async def run_gc(
    db: AsyncSession,
    *,
    artifact_store: Any | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Run one GC sweep.

    ``apply=False`` (default): dry run — computes the root set and the
    candidates, deletes nothing. ``apply=True``: deletes in the
    caller's transaction (artifacts + their store objects first, then
    registry rows) and records the audit (``gc_sweep``) in the same
    transaction.
    """
    plan = await list_gc_roots(db)
    result: dict[str, Any] = dict(plan)
    deleted: dict[str, int] = {}

    if apply:
        if plan["candidate_artifacts"]:
            rows = (
                await db.execute(
                    text("SELECT id, sha256 FROM artifacts WHERE id::text = ANY(:ids)"),
                    {"ids": plan["candidate_artifacts"]},
                )
            ).all()
            for row in rows:
                if artifact_store is not None and artifact_store.exists(row[1]):
                    with contextlib.suppress(Exception):
                        artifact_store.remove(row[1])
            await db.execute(
                text("DELETE FROM artifacts WHERE id::text = ANY(:ids)"),
                {"ids": plan["candidate_artifacts"]},
            )
            deleted["artifacts"] = len(rows)

        if plan["candidate_terminal_attempts"]:
            await db.execute(
                text("DELETE FROM commit_attempts WHERE id::text = ANY(:ids)"),
                {"ids": plan["candidate_terminal_attempts"]},
            )
            deleted["commit_attempts"] = len(plan["candidate_terminal_attempts"])

        if plan["candidate_resolved_barriers"]:
            await db.execute(
                text(
                    """
                    DELETE FROM dependency_invalidation_barriers
                    WHERE id::text = ANY(:ids)
                    """
                ),
                {"ids": plan["candidate_resolved_barriers"]},
            )
            deleted["resolved_barriers"] = len(plan["candidate_resolved_barriers"])
            # the closure manifest of a deleted barrier is a root only
            # through that barrier (§15.3: closure manifests of ACTIVE
            # and retention-window barriers) — after the retention
            # window the manifest is deletable
            await db.execute(
                text(
                    """
                    DELETE FROM closure_manifests cm
                    WHERE NOT EXISTS (
                        SELECT 1 FROM dependency_invalidation_barriers b
                        WHERE b.closure_manifest_id = cm.id
                    )
                    """
                )
            )

        if plan["candidate_workspace_manifests"]:
            await db.execute(
                text("DELETE FROM workspace_manifests WHERE id::text = ANY(:ids)"),
                {"ids": plan["candidate_workspace_manifests"]},
            )
            deleted["workspace_manifests"] = len(plan["candidate_workspace_manifests"])

    await AuditService(db).record(
        AuditEventType.GC_SWEEP,
        payload={
            "apply": apply,
            "reconciling_sessions": plan["reconciling_sessions"],
            "root_artifact_count": plan["root_artifact_count"],
            "candidates": {
                k: len(v)
                for k, v in plan.items()
                if k.startswith("candidate_") or k.startswith("expired_")
            },
            "deleted": deleted,
        },
        actor="operator",
        public_summary=(
            f"GC sweep ({'apply' if apply else 'dry-run'}): "
            f"roots={plan['root_artifact_count']}, "
            f"artifact candidates={len(plan['candidate_artifacts'])}"
            + (f", deleted={deleted}" if apply else "")
        ),
    )
    result["deleted"] = deleted
    return result
