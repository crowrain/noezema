"""Host reserve (T2.14, §6.6).

The reserve is computed from the config-snapshot limits (not assumed
infinite) and is checked BEFORE each staging operation is recorded: a
staging command is accepted whole or rejected whole, and already-recorded
staging is never truncated. The final validation repeats the check against
the immutable staging manifest, but this is the first place an overflow is
detected.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.artifacts import ORMStagingOp
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot

STAGING_BUDGET_EXCEEDED = "staging_budget_exceeded"


class StagingBudgetExceeded(RuntimeError):
    """Structured error the curator can see in the protocol context."""

    def __init__(self, counter: str, current: int, proposed: int, limit: int) -> None:
        self.counter = counter
        self.current = current
        self.proposed = proposed
        self.limit = limit
        super().__init__(
            f"{STAGING_BUDGET_EXCEEDED}: {counter} current={current} proposed={proposed} limit={limit}"
        )

    def to_payload(self) -> JsonDict:
        return {
            "code": STAGING_BUDGET_EXCEEDED,
            "counter": self.counter,
            "current": self.current,
            "proposed": self.proposed,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class ReserveLimits:
    max_claims_assessed_per_session: int
    max_new_claims_per_session: int
    max_evidence_items_per_session: int
    max_new_questions_per_session: int

    @classmethod
    def from_snapshot(cls, snapshot: ORMConfigSnapshot) -> ReserveLimits:
        lim = snapshot.session_limits
        return cls(
            max_claims_assessed_per_session=int(lim.get("max_claims_assessed_per_session", 32)),
            max_new_claims_per_session=int(lim.get("max_new_claims_per_session", 16)),
            max_evidence_items_per_session=int(lim.get("max_evidence_items_per_session", 64)),
            max_new_questions_per_session=int(lim.get("max_new_questions_per_session", 4)),
        )


class HostReserveService:
    """Counts recorded staging rows against the snapshot limits."""

    def __init__(self, limits: ReserveLimits) -> None:
        self.limits = limits

    @classmethod
    def for_snapshot(cls, snapshot: ORMConfigSnapshot) -> HostReserveService:
        return cls(ReserveLimits.from_snapshot(snapshot))

    async def _count(self, db: AsyncSession, session_id: uuid.UUID, op: str) -> int:
        stmt = select(func.count()).select_from(ORMStagingOp).where(
            ORMStagingOp.session_id == session_id,
            ORMStagingOp.op == op,
            ORMStagingOp.state.in_(["recorded", "validated"]),
        )
        return int((await db.execute(stmt)).scalar_one())

    async def check(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        proposed_claims: int = 0,
        proposed_evidence: int = 0,
        proposed_questions: int = 0,
    ) -> None:
        """Raise StagingBudgetExceeded before the write happens."""
        claims = await self._count(db, session_id, "claim")
        if claims + proposed_claims > self.limits.max_new_claims_per_session:
            raise StagingBudgetExceeded(
                "max_new_claims_per_session", claims, proposed_claims,
                self.limits.max_new_claims_per_session,
            )
        evidence = await self._count(db, session_id, "evidence")
        if evidence + proposed_evidence > self.limits.max_evidence_items_per_session:
            raise StagingBudgetExceeded(
                "max_evidence_items_per_session", evidence, proposed_evidence,
                self.limits.max_evidence_items_per_session,
            )
        questions = await self._count(db, session_id, "question")
        if questions + proposed_questions > self.limits.max_new_questions_per_session:
            raise StagingBudgetExceeded(
                "max_new_questions_per_session", questions, proposed_questions,
                self.limits.max_new_questions_per_session,
            )
