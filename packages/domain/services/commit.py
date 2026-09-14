"""Commit boundary: prepare + fenced final transaction (T2.18, T2.19, §5.2.2).

The flow is:

1. ``prepare`` writes a durable ``commit_attempts(status='prepared')`` row
   (host-generated id, bound to the staging hash and the frozen workspace
   manifest) in its OWN transaction, BEFORE the final one. This is what
   makes "did the commit boundary exist?" a durable question.

2. ``finalize`` runs the short fenced transaction: locks in canonical
   order, verifies the fencing predicate (lease owner + both revision
   components + attempt=prepared), applies staging, updates the workspace
   pointer and the knowledge revision, sets the attempt to committed, and
   writes the terminal session state + audit + outbox — one transaction.

The outcome is decided by the EXISTENCE of the durable record, not by
network timing: no prepared row -> the failure before the boundary gives
``failed``; a prepared row exists -> ``reconciling_commit`` (the
reconciler, not a guessed rollback, decides the outcome).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.artifacts import ORMWorkspaceManifest
from packages.domain.models.commit import ORMCommitAttempt
from packages.domain.models.enums import AuditEventType, SessionState
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.domain.services.locks import lock_commit_set
from packages.domain.services.staging import StagingService

FinalizeOutcome = Literal["committed", "fencing_conflict", "lease_lost", "attempt_missing"]


@dataclass(frozen=True)
class FinalizeResult:
    outcome: FinalizeOutcome
    attempt_id: UUID
    knowledge_revision: int | None = None
    applied_claims: int = 0
    applied_questions: int = 0


async def _staging_hash(db: AsyncSession, session_id: UUID) -> str:
    """Canonical hash over the recorded staging rows (immutable manifest)."""
    from packages.domain.canonical import canonical_sha256

    rows = (
        (
            await db.execute(
                text(
                    "SELECT op, payload_hash, schema_version FROM session_staging "
                    "WHERE session_id = :s AND state = 'recorded' ORDER BY created_at, id"
                ),
                {"s": session_id},
            )
        )
        .mappings()
        .all()
    )
    return canonical_sha256([dict(r) for r in rows])


async def prepare(
    db: AsyncSession,
    audit: AuditService,
    session: ORMSession,
    manifest: ORMWorkspaceManifest | None,
    owner: str,
) -> ORMCommitAttempt:
    """Write the durable prepared attempt (own transaction, caller commits
    this ``db``). The session moves to ``committing`` and records the
    writer intent (commit_intent_at)."""
    from datetime import datetime

    attempt = ORMCommitAttempt(
        session_id=session.id,
        status="prepared",
        staging_hash=await _staging_hash(db, session.id),
        workspace_manifest_id=manifest.id if manifest is not None else None,
        base_knowledge_revision=await _revision(db, "knowledge"),
        base_dependency_graph_revision=await _revision(db, "dependency_graph"),
    )
    db.add(attempt)
    await db.flush()

    session.commit_attempt_id = attempt.id
    session.commit_intent_at = datetime.now(UTC)
    await db.flush()

    await audit.record(
        AuditEventType.COMMIT_ATTEMPT_PREPARED,
        session_id=session.id,
        payload={
            "attempt_id": str(attempt.id),
            "staging_hash": attempt.staging_hash,
            "workspace_manifest_id": str(manifest.id) if manifest is not None else None,
            "base_knowledge_revision": attempt.base_knowledge_revision,
            "base_dependency_graph_revision": attempt.base_dependency_graph_revision,
        },
    )
    return attempt


async def _revision(db: AsyncSession, scope: str) -> int:
    row = (
        await db.execute(
            text("SELECT revision FROM domain_revisions WHERE scope = :s"), {"s": scope}
        )
    ).scalar_one()
    return int(row)


async def finalize(
    db: AsyncSession,
    audit: AuditService,
    session: ORMSession,
    attempt: ORMCommitAttempt,
    owner: str,
    staging: StagingService,
    *,
    terminal: SessionState,
    steps: int,
    evidence_count: int,
    claims: int,
    questions_created: int,
    termination_reason: str | None,
    question_id: UUID | None = None,
    question_terminal: str | None = None,
) -> FinalizeResult:
    """The short fenced final transaction (caller's ``db``; the caller
    commits/rolls back). Locks in canonical order, checks the fencing
    predicate, applies staging, bumps the knowledge revision, and writes
    the terminal state + audit + outbox atomically."""
    # 1. locks in canonical order: session -> knowledge -> attempt
    await lock_commit_set(db, session.id, attempt.id, touches_dependency_graph=False)

    # 2. fencing predicate (§5.2.2)
    fence = (
        await db.execute(
            text(
                """
                SELECT s.state, s.lease_owner, s.lease_expires_at,
                       r.revision AS knowledge_revision,
                       a.status AS attempt_status
                FROM sessions s
                JOIN domain_revisions r ON r.scope = 'knowledge'
                JOIN commit_attempts a ON a.id = s.commit_attempt_id
                WHERE s.id = :id FOR UPDATE
                """
            ),
            {"id": session.id},
        )
    ).mappings().first()
    if fence is None:
        return FinalizeResult("attempt_missing", attempt.id)

    if fence["state"] != "committing":
        return FinalizeResult("fencing_conflict", attempt.id)
    # live lease check (now() server-side)
    live = (
        await db.execute(
            text(
                "SELECT 1 FROM sessions WHERE id = :id "
                "AND lease_owner = :owner AND lease_expires_at > now()"
            ),
            {"id": session.id, "owner": owner},
        )
    ).scalar_one_or_none()
    if live is None:
        return FinalizeResult("lease_lost", attempt.id)
    if int(fence["knowledge_revision"]) != attempt.base_knowledge_revision:
        return FinalizeResult("fencing_conflict", attempt.id)
    if fence["attempt_status"] != "prepared":
        return FinalizeResult("fencing_conflict", attempt.id)

    # 3. apply staging (recorded -> applied) inside the same transaction
    applied_claims, applied_questions = await staging.apply_recorded(db, audit, session)

    # 4. workspace pointer + knowledge revision bump
    if attempt.workspace_manifest_id is not None:
        session.committed_workspace_manifest_id = attempt.workspace_manifest_id
    new_rev = attempt.base_knowledge_revision + 1
    await db.execute(
        text(
            "UPDATE domain_revisions SET revision = :r, updated_at = now() "
            "WHERE scope = 'knowledge' AND revision = :base"
        ),
        {"r": new_rev, "base": attempt.base_knowledge_revision},
    )

    # 5. attempt -> committed + question terminal state
    attempt.status = "committed"
    from datetime import datetime

    attempt.finished_at = datetime.now(UTC)
    if question_id is not None and question_terminal is not None:
        await db.execute(
            text("UPDATE questions SET state = :st WHERE id = :id"),
            {"id": question_id, "st": question_terminal},
        )
    await db.flush()

    # 6. terminal session state + audit (+ outbox twin)
    session.state = terminal.value
    session.termination_reason = termination_reason
    session.finished_at = datetime.now(UTC)
    session.lease_owner = None
    session.lease_expires_at = None
    await db.flush()

    event = (
        AuditEventType.SESSION_COMMITTED
        if terminal is SessionState.SUCCEEDED
        else AuditEventType.SESSION_STATE_CHANGED
    )
    await audit.record(
        event,
        session_id=session.id,
        payload={
            "attempt_id": str(attempt.id),
            "terminal": terminal.value,
            "steps": steps,
            "evidence": evidence_count,
            "claims": claims,
            "questions_created": questions_created,
        },
        public_summary=f"session {terminal.value} (fenced commit)",
    )
    await audit.record(
        AuditEventType.COMMIT_ATTEMPT_COMMITTED,
        session_id=session.id,
        payload={"attempt_id": str(attempt.id), "knowledge_revision": new_rev},
    )
    return FinalizeResult(
        "committed",
        attempt.id,
        knowledge_revision=new_rev,
        applied_claims=applied_claims,
        applied_questions=applied_questions,
    )
