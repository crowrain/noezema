"""Question selector (T1.12).

MVP = FIFO with an eligibility filter (ARCHITECTURE §5.3.2): the config
snapshot declares ``curiosity.selector = "fifo"``. Score-based ranking and
epsilon-diversity arrive in M5; the interface is already
config-driven so the switch is a config change, not a rewrite.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository


class FIFOQuestionSelector:
    """Select the next question: highest priority, oldest first."""

    async def select(self, db: AsyncSession) -> ORMQuestion | None:
        candidates = await QuestionRepository.list_candidates(db, limit=1)
        if not candidates:
            return None
        question = candidates[0]
        if not self.is_eligible(question):
            return None
        return question

    @staticmethod
    def is_eligible(question: ORMQuestion) -> bool:
        """MVP eligibility: candidate state + a verifiable path exists.

        In the Sealed profile every seeded/message question has a
        verifiable path (workspace, local corpus, python experiment);
        budget checks happen in the orchestrator before wake.
        """
        if question.state != QuestionState.CANDIDATE.value:
            return False
        return question.origin in (
            QuestionOrigin.SEEDED,
            QuestionOrigin.MESSAGE,
            QuestionOrigin.CONFLICT,
            QuestionOrigin.UNKNOWN_TERM,
            QuestionOrigin.UNVERIFIED_CLAIM,
            QuestionOrigin.PREVIOUS_RESULT,
            QuestionOrigin.LOCAL_CORPUS,
            QuestionOrigin.MODEL_PROPOSAL,
            QuestionOrigin.INVALID_ASSESSMENT,
        )
