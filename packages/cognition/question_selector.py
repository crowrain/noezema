"""FIFO Question Selector — MVP (§5.3.2)."""

import uuid

from packages.domain.models.enums import QuestionSource


class FIFOQuestionSelector:
    """In-memory FIFO question queue for MVP.

    Later replaced by DB-backed selector with priority.
    """

    def __init__(self):
        self._queue: list[dict] = []
        self._resolved: set[uuid.UUID] = set()

    async def select_next(self) -> dict | None:
        for q in self._queue:
            if q["id"] not in self._resolved:
                return q
        return None

    async def create_seeded(self, statement: str) -> uuid.UUID:
        qid = uuid.uuid4()
        self._queue.append({
            "id": qid,
            "statement": statement,
            "source": QuestionSource.SEEDED.value,
        })
        return qid

    async def mark_resolved(self, question_id: uuid.UUID):
        self._resolved.add(question_id)

    @property
    def pending_count(self) -> int:
        return len([q for q in self._queue if q["id"] not in self._resolved])
