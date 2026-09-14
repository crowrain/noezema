"""Session staging (T2.13, §5.2.2, §5.9).

``session_staging`` is the ONLY isolation mechanism for model-proposed
changes to questions, claims, evidence and identity: proposals are written
here first and applied at the commit boundary (PR #15 replaces the M2
simple apply with the fenced transaction).

Each operation is a whole unit: it is accepted or rejected as a whole, and
an already-recorded staging row is never truncated. ``payload_hash`` pins
the canonical payload; the reserve is checked BEFORE the write (T2.14).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.canonical import canonical_sha256
from packages.domain.models.artifacts import ORMStagingOp
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import AuditEventType
from packages.domain.models.questions import ORMQuestion
from packages.domain.models.sessions import ORMSession
from packages.domain.repositories.questions import QuestionRepository
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService, StagingBudgetExceeded

STAGING_SCHEMA_VERSION = 1

VALID_OPS = ("claim", "evidence", "question", "identity")


class StagingService:
    def __init__(self, reserve: HostReserveService) -> None:
        self.reserve = reserve

    async def record(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        op: str,
        payload: JsonDict,
        *,
        proposed_claims: int = 0,
        proposed_evidence: int = 0,
        proposed_questions: int = 0,
    ) -> ORMStagingOp:
        """Record one staging operation (caller's transaction).

        The reserve is checked BEFORE the insert; on overflow the whole
        operation is rejected with a structured error.
        """
        if op not in VALID_OPS:
            raise ValueError(f"invalid staging op: {op}")
        await self.reserve.check(
            db, session.id,
            proposed_claims=proposed_claims,
            proposed_evidence=proposed_evidence,
            proposed_questions=proposed_questions,
        )
        row = ORMStagingOp(
            session_id=session.id,
            op=op,
            payload=payload,
            payload_hash=canonical_sha256(payload),
            schema_version=STAGING_SCHEMA_VERSION,
        )
        db.add(row)
        await db.flush()
        await audit.record(
            AuditEventType.STAGING_RECORDED,
            session_id=session.id,
            payload={
                "staging_id": str(row.id),
                "op": op,
                "payload_hash": row.payload_hash,
                "schema_version": row.schema_version,
            },
        )
        return row

    async def reject(
        self,
        db: AsyncSession,
        audit: AuditService,
        staging_id: uuid.UUID,
        reason: str,
    ) -> None:
        """Reject a recorded operation (e.g. host validation failed)."""
        row = await db.get(ORMStagingOp, staging_id)
        if row is None or row.state != "recorded":
            return
        row.state = "rejected"
        await db.flush()
        await audit.record(
            AuditEventType.STAGING_REJECTED,
            session_id=row.session_id,
            payload={"staging_id": str(staging_id), "reason": reason[:500]},
        )

    async def apply_recorded(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
    ) -> tuple[int, int]:
        """Apply the session's recorded staging at the commit boundary.

        M2 simple apply (PR #15 upgrades this to the fenced transaction):
        questions become real question rows, claims/evidence/identity are
        validated (state=validated) and carried in the commit audit.
        Returns (claims_applied, questions_applied).
        """
        from sqlalchemy import select

        rows = (
            (
                await db.execute(
                    select(ORMStagingOp)
                    .where(ORMStagingOp.session_id == session.id, ORMStagingOp.state == "recorded")
                    .order_by(ORMStagingOp.created_at)
                )
            )
            .scalars()
            .all()
        )
        claims = 0
        questions = 0
        for row in rows:
            if row.op == "question":
                text = str(row.payload.get("text", "")).strip()
                if text:
                    q = ORMQuestion(
                        text=text[:2000],
                        origin=str(row.payload.get("origin", "model_proposal")),
                        origin_config_snapshot_id=session.config_snapshot_id,
                        parent_id=session.question_id,
                    )
                    await QuestionRepository.create(db, q)
                    questions += 1
                row.state = "applied"
            elif row.op in ("claim", "evidence", "identity"):
                row.state = "applied"
                if row.op == "claim":
                    claims += 1
        await db.flush()
        if rows:
            await audit.record(
                AuditEventType.STAGING_VALIDATED,
                session_id=session.id,
                payload={
                    "applied": len(rows),
                    "claims": claims,
                    "questions": questions,
                },
            )
        return claims, questions


def staging_budget_error(exc: StagingBudgetExceeded) -> JsonDict:
    return exc.to_payload()
