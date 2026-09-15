"""Curiosity ranking (T5.1, §5.3.1).

The score-based question selector that replaces the MVP FIFO
(§5.3.2) for ranking only — the research cycle itself is unchanged.

```text
novelty(q)          = 1 - max similarity(q, prior_questions ∪ claims)
coverage_gap(q)     = мера незакрытых зависимостей и противоречий
evidenceability(q)  = доступность независимого источника или эксперимента
score(q) = w1*novelty + w2*coverage_gap + w3*evidenceability
           + w4*feasibility - w5*cost - w6*risk - w7*topic_recency
```

All inputs are normalized to [0, 1] and stored on the selected
question (``score_components``) together with the embedding/similarity
fingerprint. Weights, thresholds, ε, M, δ and the fingerprint live in
the config snapshot's ``curiosity`` section — switching the selector
is a config change (§5.3.1).

v1 determinism decisions (documented in STATUS, M5):

- similarity is token Jaccard over normalized word sets (embeddings
  are off in the bootstrap config; pgvector is ADR-gated) — the
  "embedding fingerprint" is the fingerprint of the similarity
  algorithm actually in use (``token-jaccard-v1``);
- ``coverage_gap``: an origin pointing at a known weakness in already
  used knowledge (conflict / unverified claim / invalid assessment /
  unknown term) scores 1.0 — the system does not run away from its
  weak spots into new topics only; any other origin scores the
  system's open debt ratio;
- ``evidenceability``: an origin with a concrete local source or
  memory locus scores 1.0, a general verifiable path 0.5 (eligibility
  already guarantees at least one verifiable path);
- ``feasibility``: how well the question formulation fits the context
  window (word limit from the config);
- ``cost``: the expected exploration cost grows with the formulation
  length (word threshold from the config);
- ``risk``: 0.0 in the sealed local profile (a risk model is a
  separate ADR);
- ``topic_recency``: the fraction of the last R sessions whose
  question topic overlaps the candidate's topic (the "difference from
  the last topics" factor);
- the ε-exploration is deterministic: the RNG is seeded from
  (session_id + candidate ids) and the seed is recorded in the
  selection audit, so a selection is reproducible from the DB.
"""

from __future__ import annotations

import hashlib
import random
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.cognition.question_selector import FIFOQuestionSelector
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository

WORD_RE = re.compile(r"[a-zа-яё0-9]+")

#: origins that point at a known weakness in already used knowledge
WEAKNESS_ORIGINS = frozenset(
    {"conflict", "unverified_claim", "invalid_assessment", "unknown_term"}
)
#: origins with a concrete local source / memory locus
CONCRETE_SOURCE_ORIGINS = WEAKNESS_ORIGINS | frozenset({"local_corpus", "previous_result"})

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "novelty": 0.3,
    "coverage_gap": 0.2,
    "evidenceability": 0.2,
    "feasibility": 0.1,
    "cost": 0.1,
    "risk": 0.0,
    "topic_recency": 0.1,
}
DEFAULT_NORMALIZATION: Mapping[str, float] = {
    "gap_debt_threshold": 4.0,
    "feasibility_word_limit": 200.0,
    "cost_word_threshold": 400.0,
    "topic_overlap_threshold": 0.34,
    "min_word_len": 3.0,
}
DEFAULT_SIMILARITY_FINGERPRINT = "token-jaccard-v1"


class CuriosityConfigError(ValueError):
    """The config snapshot's curiosity section is malformed (fail-closed)."""


def _num(mapping: Mapping[str, Any], key: str, default: float, *, positive: bool = False) -> float:
    raw = mapping.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise CuriosityConfigError(f"curiosity.{key} must be a number, got {raw!r}")
    value = float(raw)
    if positive and value <= 0:
        raise CuriosityConfigError(f"curiosity.{key} must be > 0, got {value}")
    return value


@dataclass(frozen=True)
class CuriosityConfig:
    """Parsed + validated curiosity section of the config snapshot."""

    weights: Mapping[str, float]
    epsilon: float
    top_m: int
    delta: float
    recency_sessions: int
    gap_debt_threshold: float
    feasibility_word_limit: float
    cost_word_threshold: float
    topic_overlap_threshold: float
    min_word_len: int
    similarity_fingerprint: str

    @classmethod
    def from_section(cls, section: Mapping[str, Any] | None) -> CuriosityConfig:
        section = section or {}
        weights_raw = section.get("weights", dict(DEFAULT_WEIGHTS))
        if not isinstance(weights_raw, Mapping):
            raise CuriosityConfigError("curiosity.weights must be an object")
        weights: dict[str, float] = {}
        for key, default in DEFAULT_WEIGHTS.items():
            raw = weights_raw.get(key, default)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise CuriosityConfigError(f"curiosity.weights.{key} must be a number")
            value = float(raw)
            if value < 0:
                raise CuriosityConfigError(f"curiosity.weights.{key} must be >= 0")
            weights[key] = value
        normalization = section.get("normalization", dict(DEFAULT_NORMALIZATION))
        if not isinstance(normalization, Mapping):
            raise CuriosityConfigError("curiosity.normalization must be an object")
        similarity = section.get("similarity", {})
        if not isinstance(similarity, Mapping):
            raise CuriosityConfigError("curiosity.similarity must be an object")
        fingerprint = similarity.get("fingerprint", DEFAULT_SIMILARITY_FINGERPRINT)
        if not isinstance(fingerprint, str) or not fingerprint:
            raise CuriosityConfigError("curiosity.similarity.fingerprint must be a non-empty string")
        epsilon = _num(section, "epsilon", 0.0)
        if epsilon > 1:
            raise CuriosityConfigError(f"curiosity.epsilon must be <= 1, got {epsilon}")
        return cls(
            weights=weights,
            epsilon=epsilon,
            top_m=int(_num(section, "top_m", 1, positive=True)),
            delta=float(_num(section, "delta", 0.0)),
            recency_sessions=int(_num(section, "recency_sessions", 5, positive=True)),
            gap_debt_threshold=_num(
                normalization,
                "gap_debt_threshold",
                DEFAULT_NORMALIZATION["gap_debt_threshold"],
                positive=True,
            ),
            feasibility_word_limit=_num(
                normalization,
                "feasibility_word_limit",
                DEFAULT_NORMALIZATION["feasibility_word_limit"],
                positive=True,
            ),
            cost_word_threshold=_num(
                normalization,
                "cost_word_threshold",
                DEFAULT_NORMALIZATION["cost_word_threshold"],
                positive=True,
            ),
            topic_overlap_threshold=_num(
                normalization, "topic_overlap_threshold", DEFAULT_NORMALIZATION["topic_overlap_threshold"]
            ),
            min_word_len=int(
                _num(normalization, "min_word_len", DEFAULT_NORMALIZATION["min_word_len"], positive=True)
            ),
            similarity_fingerprint=fingerprint,
        )


def word_set(text: str, min_word_len: int = 3) -> frozenset[str]:
    """Normalized word tokens (lowercased, cyrillic/latin/digits)."""
    return frozenset(token for token in WORD_RE.findall(text.lower()) if len(token) >= min_word_len)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


@dataclass(frozen=True)
class QuestionScore:
    question_id: uuid.UUID
    score: float
    components: Mapping[str, float]


def compute_score(
    question: ORMQuestion,
    *,
    other_texts: Sequence[str],
    open_debt: int,
    session_topic_texts: Sequence[str],
    cfg: CuriosityConfig,
) -> QuestionScore:
    """The §5.3.1 score: all components in [0, 1], the signed sum."""
    own = word_set(question.text, cfg.min_word_len)
    sims = [jaccard(own, word_set(t, cfg.min_word_len)) for t in other_texts if t]
    novelty = 1.0 - (max(sims) if sims else 0.0)

    coverage_gap = (
        1.0
        if question.origin in WEAKNESS_ORIGINS
        else min(1.0, open_debt / cfg.gap_debt_threshold)
    )

    evidenceability = 1.0 if question.origin in CONCRETE_SOURCE_ORIGINS else 0.5

    word_count = max(1, len(question.text.split()))
    feasibility = min(1.0, cfg.feasibility_word_limit / word_count)
    cost = min(1.0, word_count / cfg.cost_word_threshold)
    risk = 0.0

    if session_topic_texts:
        hits = sum(
            1
            for topic in session_topic_texts
            if jaccard(own, word_set(topic, cfg.min_word_len)) >= cfg.topic_overlap_threshold
        )
        topic_recency = hits / len(session_topic_texts)
    else:
        topic_recency = 0.0

    w = cfg.weights
    score = (
        w["novelty"] * novelty
        + w["coverage_gap"] * coverage_gap
        + w["evidenceability"] * evidenceability
        + w["feasibility"] * feasibility
        - w["cost"] * cost
        - w["risk"] * risk
        - w["topic_recency"] * topic_recency
    )
    return QuestionScore(
        question_id=question.id,
        score=score,
        components={
            "novelty": novelty,
            "coverage_gap": coverage_gap,
            "evidenceability": evidenceability,
            "feasibility": feasibility,
            "cost": cost,
            "risk": risk,
            "topic_recency": topic_recency,
        },
    )


def selection_rng_seed(session_id: uuid.UUID, candidate_ids: Sequence[uuid.UUID]) -> int:
    """Deterministic RNG seed: the selection is reproducible from the DB
    (the seed is recorded in the selection audit)."""
    digest = hashlib.sha256(
        (str(session_id) + ":" + ",".join(str(c) for c in sorted(candidate_ids))).encode()
    ).digest()
    return int.from_bytes(digest[:8], "big")


class CuriosityQuestionSelector:
    """Score-based selection with an eligibility filter and
    ε-diversity: with probability 1-ε the argmax wins; with ε the
    pick is uniform among the top-M candidates / those within δ of the
    maximum (never among the whole registry)."""

    def __init__(self, section: Mapping[str, Any] | None = None) -> None:
        self.config = CuriosityConfig.from_section(section)

    async def select(
        self, db: AsyncSession, *, session_id: uuid.UUID
    ) -> tuple[ORMQuestion | None, Mapping[str, Any] | None]:
        """Selects the next question. Returns (question, audit record);
        the caller persists the record on the question row
        (``score_components`` + ``embedding_fingerprint``)."""
        cfg = self.config
        candidates = await QuestionRepository.list_candidates(db, limit=1000)
        eligible = [q for q in candidates if FIFOQuestionSelector.is_eligible(q)]
        if not eligible:
            return None, None

        candidate_ids = [q.id for q in eligible]
        all_questions = (
            (await db.execute(text("SELECT id, text FROM questions"))).all()
        )
        other_texts = [row.text for row in all_questions if row.id not in set(candidate_ids)]
        claims = (await db.execute(text("SELECT statement FROM claims"))).scalars().all()
        other_texts.extend(claims)
        open_debt = (
            await db.execute(
                text(
                    "SELECT count(*) FROM claim_assessment_heads "
                    "WHERE assessment_state IN ('pending', 'invalid') "
                    "AND config_snapshot_id = "
                    "(SELECT active_config_snapshot_id FROM runtime_config_heads "
                    "WHERE scope = 'global')"
                )
            )
        ).scalar_one()
        topics = (
            (
                await db.execute(
                    text(
                        "SELECT q.text FROM sessions s "
                        "JOIN questions q ON q.id = s.question_id "
                        "WHERE s.question_id IS NOT NULL "
                        "ORDER BY s.created_at DESC, s.id DESC LIMIT :r"
                    ),
                    {"r": cfg.recency_sessions},
                )
            )
            .scalars()
            .all()
        )

        scores = {
            q.id: compute_score(
                q,
                other_texts=other_texts,
                open_debt=int(open_debt),
                session_topic_texts=topics,
                cfg=cfg,
            )
            for q in eligible
        }
        ranked = sorted(eligible, key=lambda q: (-scores[q.id].score, q.created_at, q.id))
        best = ranked[0]
        best_score = scores[best.id].score

        seed = selection_rng_seed(session_id, candidate_ids)
        rng = random.Random(seed)
        explore = cfg.epsilon > 0.0 and rng.random() < cfg.epsilon
        chosen = best
        if explore:
            pool = [
                q
                for position, q in enumerate(ranked)
                if position < cfg.top_m or scores[q.id].score >= best_score - cfg.delta
            ]
            chosen = rng.choice(pool)

        record: dict[str, Any] = {
            "components": {qid: dict(s.components) for qid, s in scores.items()},
            "score": {qid: float(s.score) for qid, s in scores.items()},
            "selected": "explore" if chosen.id != best.id else "argmax",
            "candidates_considered": [str(q.id) for q in eligible],
            "fingerprint": cfg.similarity_fingerprint,
            "epsilon": cfg.epsilon,
            "top_m": cfg.top_m,
            "delta": cfg.delta,
            "rng_seed": seed,
        }
        return chosen, record

    @staticmethod
    def persist(
        db: AsyncSession, question: ORMQuestion, record: Mapping[str, Any], fingerprint: str
    ) -> None:
        """Writes the normalized score inputs + the fingerprint on the
        selected question (the caller commits)."""
        question.score_components = {
            "components": dict(record["components"][question.id]),
            "score": float(record["score"][question.id]),
            "selected": record["selected"],
            "candidates_considered": list(record["candidates_considered"]),
            "fingerprint": fingerprint,
            "epsilon": record["epsilon"],
            "top_m": record["top_m"],
            "delta": record["delta"],
            "rng_seed": record["rng_seed"],
        }
        question.embedding_fingerprint = fingerprint
