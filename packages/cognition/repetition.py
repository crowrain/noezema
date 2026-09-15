"""Repetition protection (T5.4, stage 4, §9).

Detects the semantic repetition patterns of §9:

- rephrasing: the candidate question is semantically the same as a
  question already investigated (word-set Jaccard similarity — the
  same deterministic fingerprint family as the curiosity ranking,
  §5.3.1: no LLM call, no embeddings in v1);
- no progress: consecutive sessions on that (similar) question
  without a single new claim — the count of sessions without new
  verifiable results (§9);

A cycle is detected when a candidate is a rephrase of an already
investigated question AND the no-progress count reached the
configured limit. On a cycle the host chooses a strategy from the
closed list of §9 (deterministic rotation by the no-progress count —
not a random textual nudge):

- ``compare_previous_session`` — explicitly compare with the previous
  session (its question/plan/observations go into the context);
- ``opposite_hypothesis`` — check the opposite hypothesis;
- ``change_source_type`` — switch the type of source;
- ``experiment`` — move from reading to an experiment;
- ``defer_question`` — defer the question (→ the next candidate);
- ``choose_different_area`` — choose a different area (→ the next
  candidate).

The skip strategies make the orchestrator re-select with the
offending question excluded; the note strategies inject a
host-generated context section. Everything is audited
(``repeat_cycle_detected``) and fingerprinted so a later run can
explain the decision.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.cognition.curiosity import jaccard, word_set
from packages.domain.models.enums import QuestionState
from packages.domain.models.memory import ORMClaim
from packages.domain.models.questions import ORMQuestion
from packages.domain.models.sessions import ORMSession

#: closed list of cycle strategies (§9, in order — the rotation order)
REPEAT_STRATEGIES: tuple[str, ...] = (
    "compare_previous_session",
    "opposite_hypothesis",
    "change_source_type",
    "experiment",
    "defer_question",
    "choose_different_area",
)

#: states of an already-investigated question (terminal for the
#: rephrase check; a candidate is not "previously investigated")
_INVESTIGATED_STATES: tuple[str, ...] = (
    QuestionState.VERIFIED.value,
    QuestionState.PARTIALLY_ANSWERED.value,
    QuestionState.REJECTED.value,
    QuestionState.DEFERRED.value,
    QuestionState.RESEARCHING.value,
    QuestionState.SELECTED.value,
)


class RepetitionConfigError(ValueError):
    """The repetition section is structurally invalid (fail-closed)."""


@dataclass(frozen=True)
class RepetitionConfig:
    """Defaults reproduce the bootstrap section exactly (fail-closed
    semantics: an unknown/NULL section = disabled, the MVP behavior)."""

    enabled: bool = False
    rephrase_threshold: float = 0.6
    plan_cycle_threshold: float = 0.5
    no_progress_limit: int = 2

    @classmethod
    def from_section(cls, section: Mapping[str, Any] | None) -> RepetitionConfig:
        if section is None:
            return cls()
        if not isinstance(section, Mapping):
            raise RepetitionConfigError(f"repetition section must be a mapping, got {type(section).__name__}")
        enabled = section.get("enabled", False)
        rephrase = section.get("rephrase_threshold", 0.6)
        plan_cycle = section.get("plan_cycle_threshold", 0.5)
        limit = section.get("no_progress_limit", 2)
        if not isinstance(enabled, bool):
            raise RepetitionConfigError("enabled must be a bool")
        for name, value in (("rephrase_threshold", rephrase), ("plan_cycle_threshold", plan_cycle)):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
                raise RepetitionConfigError(f"{name} must be a number in [0, 1]")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise RepetitionConfigError("no_progress_limit must be an int >= 1")
        return cls(
            enabled=enabled,
            rephrase_threshold=float(rephrase),
            plan_cycle_threshold=float(plan_cycle),
            no_progress_limit=limit,
        )


@dataclass(frozen=True)
class RepetitionReport:
    """The host-side cycle detection result for one selected question."""

    question_id: uuid.UUID
    similar_question_id: uuid.UUID
    similarity: float
    no_progress_sessions: int
    cycle_count: int
    strategy: str
    fingerprint: str = field(repr=False, default="repeat-jaccard-v1")


def choose_strategy(no_progress_sessions: int, no_progress_limit: int) -> str:
    """Deterministic rotation over the closed §9 list. The more
    consecutive no-progress sessions, the further along the strategy
    list (mod len) — never a random textual nudge (§9)."""
    if no_progress_sessions < no_progress_limit:
        return REPEAT_STRATEGIES[0]
    index = (no_progress_sessions - no_progress_limit) % len(REPEAT_STRATEGIES)
    return REPEAT_STRATEGIES[index]


def is_skip_strategy(strategy: str) -> bool:
    """Strategies that abandon the selected question for this session."""
    return strategy in ("defer_question", "choose_different_area")


def strategy_context_note(strategy: str, similar_question_text: str) -> str:
    """The host-generated context section for the note strategies.
    Host data, not model text: it only restates the strategy and the
    previous question."""
    descriptions: dict[str, str] = {
        "compare_previous_session": (
            f"Цикл: вопрос повторяет ранее исследованный («{similar_question_text}»), "
            "прогресса не было. Явно сравни текущую сессию с предыдущей: "
            "что нового по методу и источникам, где именно застрял результат."
        ),
        "opposite_hypothesis": (
            f"Цикл: вопрос повторяет ранее исследованный («{similar_question_text}»). "
            "Проверь противоположную гипотезу: сформулируй отрицание ожидаемого "
            "результата и ищи контрпримеры."
        ),
        "change_source_type": (
            f"Цикл: вопрос повторяет ранее исследованный («{similar_question_text}»). "
            "Смени тип источника: если читали текст — ищи численные данные или код; "
            "если пересчитывали — ищи независимый корпус."
        ),
        "experiment": (
            f"Цикл: вопрос повторяет ранее исследованный («{similar_question_text}»). "
            "Перейди от чтения к эксперименту: спроектируй python-эксперимент, "
            "который напрямую проверяет утверждение."
        ),
    }
    return descriptions.get(strategy, "")


async def _investigated_questions(db: AsyncSession, question: ORMQuestion) -> list[ORMQuestion]:
    stmt = (
        select(ORMQuestion)
        .where(ORMQuestion.state.in_(_INVESTIGATED_STATES))
        .where(ORMQuestion.id != question.id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _no_progress_sessions(db: AsyncSession, question_id: uuid.UUID) -> int:
    """Consecutive sessions on the question (newest first) without a
    single new claim — the count of sessions without new verifiable
    results (§9). Stops at the first session that produced a claim."""
    sessions_stmt = (
        select(ORMSession)
        .where(ORMSession.question_id == question_id)
        .order_by(ORMSession.created_at.desc(), ORMSession.id.desc())
        .limit(50)
    )
    result = await db.execute(sessions_stmt)
    sessions = [s for s in result.scalars().all() if s.id is not None]
    count = 0
    for session in sessions:
        claim_stmt = select(ORMClaim.id).where(ORMClaim.created_in_session == session.id).limit(1)
        row = (await db.execute(claim_stmt)).first()
        if row is not None:
            break  # a session with new verifiable results resets the count
        count += 1
    return count


async def detect_repetition(
    db: AsyncSession,
    question: ORMQuestion,
    cfg: RepetitionConfig,
) -> RepetitionReport | None:
    """Detect a §9 cycle for the selected question. Returns None when
    the question is not a rephrase of an already-investigated one or
    the no-progress count is below the limit. Deterministic: same DB
    state → same report (Jaccard over the host word set, no LLM)."""
    if not cfg.enabled:
        return None
    current_tokens = word_set(question.text)
    if not current_tokens:
        return None
    best: ORMQuestion | None = None
    best_similarity = 0.0
    for other in await _investigated_questions(db, question):
        similarity = jaccard(current_tokens, word_set(other.text))
        if similarity > best_similarity:
            best = other
            best_similarity = similarity
    if best is None or best_similarity < cfg.rephrase_threshold:
        return None
    no_progress = await _no_progress_sessions(db, best.id)
    if no_progress < cfg.no_progress_limit:
        return None
    strategy = choose_strategy(no_progress, cfg.no_progress_limit)
    return RepetitionReport(
        question_id=question.id,
        similar_question_id=best.id,
        similarity=round(best_similarity, 6),
        no_progress_sessions=no_progress,
        cycle_count=no_progress,
        strategy=strategy,
    )
