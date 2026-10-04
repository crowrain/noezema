"""Operator question intake (T7.59, §5.3, §5.3.2, §13.6).

One service layer behind every operator entrance that puts a question into
the FIFO queue — `noezemactl ask`, `POST /api/v1/questions` and the web form.
Validation, exact-text dedup (idempotent replay) and the queue position live
HERE, so the three entrances cannot drift apart (AGENTS §4: one logical
block, invariants in the service, the entrances stay thin).

Provenance (§3.4): an operator question is stored with ``origin='message'`` —
the existing ``QuestionOrigin.MESSAGE`` value, already allowed by the
questions CHECK constraint (migration 0002 derives the list from the enum)
and already eligible in ``FIFOQuestionSelector.is_eligible`` (§5.3.2 names
"seeded/message question → FIFO selection"). No enum value, no migration:
this is the first producer of that origin, not a new kind of row.

Dedup is EXACT text after strip — the same rule the eval-run seed uses
(hostctl/cli.py::_seed_questions). Semantic near-duplicates are NOT merged
here: §9 (packages/cognition/repetition.py) handles rephrases at selection
time against already-investigated questions, which is a different question
("is this worth another session?") than intake ("did the operator already
put exactly this text?").

Audit: no new AuditEventType. The closed audit registry (§14.4) has no
question-intake type (``question_selected`` is a session event), inventing one
would widen a closed enum; the durable record of the intake is the questions
row itself, and the queue endpoint reads it back.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.models.sessions import ORMSession
from packages.domain.repositories.questions import QuestionRepository

# ── validation bounds (single source for CLI + API) ───────────────────────
# The text ceiling mirrors the operator-message contract (MessageIn.body is
# 2000 chars) so every operator-supplied free text has one limit. It bounds
# OPERATOR intake only: model-proposed questions go through staging, which
# truncates to its own 2000 (§5.2.2) and is not this contract's subject.
MAX_QUESTION_TEXT_CHARS = 2000
# The priority band mirrors the operator command/message contract (-100..100).
# questions.priority has no CHECK constraint (it is a ranking input, §5.3.1),
# so the bound is enforced here rather than in the schema.
PRIORITY_MIN = -100
PRIORITY_MAX = 100

#: How deep the queue view / `ask` looks for a position: the FIFO selector
#: only ever reads the head of the candidate list, so a deeper position is
#: reported as None (out of window) instead of costing an unbounded scan.
QUEUE_WINDOW = 500


class QuestionIntakeError(ValueError):
    """The operator question is rejected before it reaches the queue."""


def validate_operator_question(raw_text: Any, raw_priority: Any = 0) -> tuple[str, int]:
    """Pure validation of one intake request: returns ``(text, priority)``.

    - text: stripped, non-empty (whitespace-only is empty), ≤ MAX chars;
    - priority: a real int (bools are rejected — they are not a ranking),
      inside [PRIORITY_MIN, PRIORITY_MAX].

    Raises QuestionIntakeError with the operator-readable reason. Pure
    function on purpose (AGENTS §4): entrances stay thin and this rule is
    testable without a database.
    """
    if not isinstance(raw_text, str):
        raise QuestionIntakeError("text must be a string")
    text = raw_text.strip()
    if not text:
        raise QuestionIntakeError("text is empty: a question needs a formulation")
    if len(text) > MAX_QUESTION_TEXT_CHARS:
        raise QuestionIntakeError(
            f"text is too long: {len(text)} > {MAX_QUESTION_TEXT_CHARS} characters"
        )
    if isinstance(raw_priority, bool) or not isinstance(raw_priority, int):
        raise QuestionIntakeError("priority must be an integer")
    if raw_priority < PRIORITY_MIN or raw_priority > PRIORITY_MAX:
        raise QuestionIntakeError(
            f"priority out of range: {raw_priority} not in [{PRIORITY_MIN}, {PRIORITY_MAX}]"
        )
    return text, raw_priority


async def find_question_by_text(db: AsyncSession, text: str) -> ORMQuestion | None:
    """Exact-text lookup over ALL questions (any state, any origin).

    The seed rule (`SELECT 1 FROM questions WHERE text = :t`) widened to
    return the row: an operator re-sending a text that was already asked —
    by the corpus seed or by the model — gets THAT question back instead of
    a second candidate with the same formulation. Deterministic when
    several rows share the text (oldest first, id as tiebreaker).
    """
    stmt = (
        select(ORMQuestion)
        .where(ORMQuestion.text == text)
        .order_by(ORMQuestion.created_at.asc(), ORMQuestion.id.asc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def put_operator_question(
    db: AsyncSession,
    *,
    raw_text: Any,
    raw_priority: Any = 0,
) -> tuple[ORMQuestion, bool]:
    """Put one operator question into the queue. Returns ``(question, created)``.

    Idempotent by exact text: a repeat returns the existing row unchanged
    (``created=False``) — no duplicate candidate, no priority mutation
    (changing an existing question's rank is not part of this contract).

    The caller owns the transaction (same discipline as every other service:
    `transaction(db)` in the CLI, `transaction(db)` in the API).
    """
    text, priority = validate_operator_question(raw_text, raw_priority)
    existing = await find_question_by_text(db, text)
    if existing is not None:
        return existing, False
    question = await QuestionRepository.create(
        db,
        ORMQuestion(
            text=text,
            origin=QuestionOrigin.MESSAGE.value,
            state=QuestionState.CANDIDATE.value,
            priority=priority,
        ),
    )
    await db.refresh(question)
    return question, True


async def queue_position(db: AsyncSession, question: ORMQuestion) -> int | None:
    """1-based FIFO position of a CANDIDATE question (None if not queued).

    Uses the repository's own FIFO order (priority DESC, created_at ASC, id) —
    the same query the selector reads, so the reported position cannot drift
    from the order sessions are actually served in. Beyond QUEUE_WINDOW the
    position is None: the selector only ever consumes the head of the queue.
    """
    if question.state != QuestionState.CANDIDATE.value:
        return None
    candidates = await QuestionRepository.list_candidates(db, limit=QUEUE_WINDOW)
    for index, candidate in enumerate(candidates, start=1):
        if candidate.id == question.id:
            return index
    return None


def _session_ref(session_row: ORMSession | None) -> dict[str, Any] | None:
    if session_row is None:
        return None
    return {"id": str(session_row.id), "state": session_row.state}


async def question_queue(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """The operator-visible queue view (§13.3): candidates in FIFO order with
    their position first, then the already-worked questions by recency, each
    row annotated with the newest session that took it (if any).

    The leading candidate block is exactly the selector's order, so positions
    are assigned by index — one query for the whole view.
    """
    stmt = (
        select(ORMQuestion)
        .order_by(
            (ORMQuestion.state == QuestionState.CANDIDATE.value).desc(),
            ORMQuestion.priority.desc(),
            ORMQuestion.created_at.asc(),
            ORMQuestion.id,
        )
        .limit(limit)
    )
    questions = list((await db.execute(stmt)).scalars().all())
    if not questions:
        return []

    ids = tuple(q.id for q in questions)
    session_stmt = (
        select(ORMSession)
        .where(ORMSession.question_id.in_(ids))
        .order_by(ORMSession.created_at.desc(), ORMSession.id.desc())
    )
    newest: dict[uuid.UUID, ORMSession] = {}
    for session_row in (await db.execute(session_stmt)).scalars().all():
        if session_row.question_id is not None and session_row.question_id not in newest:
            newest[session_row.question_id] = session_row

    rows: list[dict[str, Any]] = []
    position = 0
    for question in questions:
        if question.state == QuestionState.CANDIDATE.value:
            position += 1
            queued_position: int | None = position
        else:
            queued_position = None
        rows.append(
            {
                "id": str(question.id),
                "text": question.text,
                "origin": question.origin,
                "state": question.state,
                "priority": question.priority,
                "created_at": question.created_at.isoformat(),
                "position": queued_position,
                "session": _session_ref(newest.get(question.id)),
            }
        )
    return rows


def intake_error_payload(exc: QuestionIntakeError) -> Mapping[str, str]:
    """The operator-readable rejection body (shared by CLI and API)."""
    return {"error": "invalid_question", "detail": str(exc)}
