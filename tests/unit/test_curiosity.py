"""Unit: curiosity ranking score + config (T5.1, §5.3.1).

Pure functions only — the DB-backed selector is covered in
tests/scenario/test_curiosity_selector.py.
"""

from __future__ import annotations

import uuid

import pytest

from packages.cognition.curiosity import (
    CuriosityConfig,
    CuriosityConfigError,
    CuriosityQuestionSelector,
    compute_score,
    jaccard,
    selection_rng_seed,
    word_set,
)
from packages.domain.models.questions import ORMQuestion

pytestmark = [pytest.mark.unit]


def _question(text: str, origin: str = "seeded") -> ORMQuestion:
    return ORMQuestion(id=uuid.uuid4(), text=text, origin=origin)


def test_word_set_normalizes_cyrillic_and_filters_short() -> None:
    ws = word_set("Что ТАКОЕ энтропия в термодинамике? abc", min_word_len=3)
    assert ws == frozenset({"что", "такое", "энтропия", "термодинамике", "abc"})
    assert word_set("аб в", min_word_len=3) == frozenset()


def test_jaccard_values() -> None:
    a = word_set("энтропия термодинамики")
    b = word_set("энтропия статистика")
    assert jaccard(a, a) == 1.0
    assert jaccard(a, b) == pytest.approx(1 / 3)
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_score_formula_exact() -> None:
    cfg = CuriosityConfig.from_section(None)
    q = _question("Что такое энтропия в термодинамике?")  # 5 words
    score = compute_score(
        q, other_texts=[], open_debt=2, session_topic_texts=[], cfg=cfg
    )
    c = score.components
    assert c["novelty"] == 1.0  # no prior texts
    assert c["coverage_gap"] == pytest.approx(2 / 4)  # seeded origin: debt ratio
    assert c["evidenceability"] == 0.5  # seeded: general verifiable path
    assert c["feasibility"] == 1.0  # 5 words << 200 limit
    assert c["cost"] == pytest.approx(5 / 400)
    assert c["risk"] == 0.0  # sealed local profile (v1)
    assert c["topic_recency"] == 0.0
    expected = 0.3 * 1.0 + 0.2 * 0.5 + 0.2 * 0.5 + 0.1 * 1.0 - 0.1 * (5 / 400)
    assert score.score == pytest.approx(expected)


def test_rephrase_reduces_novelty() -> None:
    cfg = CuriosityConfig.from_section(None)
    q = _question("Почему небо голубое из-за рaleigh рассеяния света")
    fresh = compute_score(q, other_texts=[], open_debt=0, session_topic_texts=[], cfg=cfg)
    rephrased = compute_score(
        q, other_texts=["небо голубое рaleigh рассеяние"], open_debt=0, session_topic_texts=[], cfg=cfg
    )
    assert fresh.components["novelty"] == 1.0
    assert rephrased.components["novelty"] < 1.0
    assert rephrased.score < fresh.score


def test_weakness_origin_gets_full_coverage_gap() -> None:
    cfg = CuriosityConfig.from_section(None)
    for origin in ("conflict", "unverified_claim", "invalid_assessment", "unknown_term"):
        q = _question("Почему это так", origin=origin)
        score = compute_score(q, other_texts=[], open_debt=0, session_topic_texts=[], cfg=cfg)
        assert score.components["coverage_gap"] == 1.0, origin
        assert score.components["evidenceability"] == 1.0, origin


def test_concrete_source_origin_gets_full_evidenceability() -> None:
    cfg = CuriosityConfig.from_section(None)
    for origin in ("local_corpus", "previous_result"):
        q = _question("Что в корпусе", origin=origin)
        score = compute_score(q, other_texts=[], open_debt=9, session_topic_texts=[], cfg=cfg)
        assert score.components["evidenceability"] == 1.0, origin
        # debt ratio capped at 1.0 for non-weakness origins
        assert score.components["coverage_gap"] == 1.0, origin


def test_topic_recency_is_fraction_of_recent_sessions() -> None:
    cfg = CuriosityConfig.from_section(None)
    q = _question("энтропия термодинамики статистика")
    topics = [
        "энтропия термодинамики",
        "совершенно другая тема про музыку",
        "энтропия статистика",
    ]
    score = compute_score(
        q, other_texts=[], open_debt=0, session_topic_texts=topics, cfg=cfg
    )
    # 2 of 3 recent sessions share the topic (jaccard >= 0.34)
    assert score.components["topic_recency"] == pytest.approx(2 / 3)
    penalized = compute_score(
        q, other_texts=[], open_debt=0, session_topic_texts=["энтропия термодинамики"], cfg=cfg
    )
    assert penalized.score < score.score  # the recency factor subtracts


def test_config_defaults_and_custom() -> None:
    cfg = CuriosityConfig.from_section(None)
    assert cfg.epsilon == 0.0
    assert cfg.weights["novelty"] == 0.3
    assert cfg.similarity_fingerprint == "token-jaccard-v1"
    custom = CuriosityConfig.from_section(
        {
            "selector": "curiosity",
            "epsilon": 0.25,
            "top_m": 5,
            "delta": 0.05,
            "recency_sessions": 10,
            "weights": {"novelty": 0.9, "coverage_gap": 0.1},
            "similarity": {"fingerprint": "token-jaccard-v2"},
        }
    )
    assert custom.epsilon == 0.25
    assert custom.top_m == 5
    assert custom.weights["novelty"] == 0.9
    # missing weights fall back to the defaults
    assert custom.weights["feasibility"] == 0.1
    assert custom.similarity_fingerprint == "token-jaccard-v2"


@pytest.mark.parametrize(
    "section",
    [
        {"epsilon": 1.5},
        {"weights": {"novelty": "high"}},
        {"weights": {"novelty": -0.1}},
        {"normalization": "nope"},
        {"similarity": {"fingerprint": ""}},
    ],
)
def test_config_fail_closed(section: dict) -> None:
    with pytest.raises(CuriosityConfigError):
        CuriosityConfig.from_section(section)


def test_selection_rng_seed_is_deterministic_and_sensitive() -> None:
    session = uuid.uuid4()
    ids = [uuid.uuid4(), uuid.uuid4()]
    assert selection_rng_seed(session, ids) == selection_rng_seed(session, list(reversed(ids)))
    assert selection_rng_seed(session, ids) != selection_rng_seed(uuid.uuid4(), ids)
    assert selection_rng_seed(session, ids) != selection_rng_seed(session, [ids[0]])


def test_selector_rejects_bad_section() -> None:
    with pytest.raises(CuriosityConfigError):
        CuriosityQuestionSelector({"epsilon": "lots"})
