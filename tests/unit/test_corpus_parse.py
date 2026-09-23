"""Corpus parsing for hostctl eval-run (T7.7, EVAL-3).

The corpus may carry an optional integer ``priority`` per question; the
FIFO selector consumes (priority DESC, created_at ASC, id), so the
corpus controls the consumption order through priorities (§5.3.2).
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from hostctl.cli import parse_corpus_questions
from packages.cognition.curiosity import jaccard, word_set
from packages.memory.scope import (
    parse_question_date,
    question_uses_relative_date,
)


@pytest.mark.unit
def test_parses_text_and_priority() -> None:
    raw = '\n# comment line\n{"text": "question one"}\n{"text": "question two", "priority": 10}\n'
    assert parse_corpus_questions(raw) == [("question one", 0), ("question two", 10)]


@pytest.mark.unit
def test_comments_and_blanks_only() -> None:
    assert parse_corpus_questions("# only a comment\n\n") == []


@pytest.mark.unit
def test_malformed_line_reports_number() -> None:
    with pytest.raises(ValueError, match="corpus line 2"):
        parse_corpus_questions('{"text": "ok"}\nnot-json\n')


@pytest.mark.unit
def test_bad_priority_rejected() -> None:
    bad_lines = (
        '{"text": "q", "priority": "ten"}',
        '{"text": "q", "priority": true}',
        '{"text": "q", "priority": 1.5}',
        '{"text": "q", "priority": null}',
    )
    for line in bad_lines:
        with pytest.raises(ValueError):
            parse_corpus_questions(line)


@pytest.mark.unit
def test_empty_text_rejected() -> None:
    with pytest.raises(ValueError):
        parse_corpus_questions('{"text": ""}')


@pytest.mark.unit
def test_non_object_line_rejected() -> None:
    for line in ("42", '"just a string"', "null", "[1, 2]"):
        with pytest.raises(ValueError):
            parse_corpus_questions(line)


@pytest.mark.unit
def test_question_set_v2_corpus_shape() -> None:
    """The frozen EVAL-3 corpus (docs/eval/question-set-v2.jsonl):
    50 unique questions — 3 old-as_of (priority 100), 11 pack anchors
    (90), 22 pack follow-ups (80), 14 standalone URL questions (0)."""
    raw = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "eval"
        / "question-set-v2.jsonl"
    ).read_text(encoding="utf-8")
    questions = parse_corpus_questions(raw)
    assert len(questions) == 50
    priorities = [p for _, p in questions]
    assert priorities.count(100) == 3
    assert priorities.count(90) == 11
    assert priorities.count(80) == 22
    assert priorities.count(0) == 14
    texts = [t for t, _ in questions]
    assert len(set(texts)) == 50


def _registrable_domain(url: str) -> str:
    """Last two host labels: ru.wikipedia.org and en.wikipedia.org are the
    SAME domain for the two-independent-sources rule (§2.2 corpus design)."""
    return ".".join(urlparse(url).hostname.split(".")[-2:])


_URL_RE = re.compile(r"https?://[^\s]+")


def _urls(text: str) -> list[str]:
    return _URL_RE.findall(text)


def _corpus_path(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "eval" / name


@pytest.mark.unit
def test_question_set_v3_corpus_shape() -> None:
    """The frozen EVAL-4 corpus (docs/eval/question-set-v3.jsonl):
    69 unique questions = 50 v2 questions VERBATIM (byte-identical block)
    + 19 new (3 old-as_of priority 100, 16 standalone priority 0).

    Hard constraints (EVAL-4 corpus design):
    - every question naming sources names exactly two, on different
      registrable domains;
    - pairwise token-Jaccard (curiosity.word_set/jaccard, the repetition-
      gate mechanism) strictly < 0.6 for every new-new and new-v2 pair
      (new = the 19 questions appended after the v2 block).
    """
    raw_v3 = _corpus_path("question-set-v3.jsonl").read_text(encoding="utf-8")
    raw_v2 = _corpus_path("question-set-v2.jsonl").read_text(encoding="utf-8")

    json_v3 = [ln for ln in raw_v3.splitlines() if ln.strip() and not ln.startswith("#")]
    json_v2 = [ln for ln in raw_v2.splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(json_v2) == 50
    assert len(json_v3) == 69
    # v2 block is verbatim (byte-identical), in the same order:
    assert json_v3[:50] == json_v2

    questions = parse_corpus_questions(raw_v3)
    texts = [t for t, _ in questions]
    assert len(set(texts)) == 69
    priorities = [p for _, p in questions]
    # v2: 3×100, 11×90, 22×80, 14×0; new: 3×100 (T-old), 16×0:
    assert priorities.count(100) == 6
    assert priorities.count(90) == 11
    assert priorities.count(80) == 22
    assert priorities.count(0) == 30

    # two independent sources (different registrable domains) per question
    # that names sources; the new 19 all name sources.
    for t in texts:
        urls = _urls(t)
        if urls:
            assert len(urls) == 2, t
            assert _registrable_domain(urls[0]) != _registrable_domain(urls[1]), t
    for t in texts[50:]:
        assert len(_urls(t)) == 2, t

    # pairwise Jaccard < 0.6: new-new and new-v2 (strict).
    new_sets = [word_set(t) for t in texts[50:]]
    v2_sets = [word_set(t) for t in texts[:50]]
    for i in range(len(new_sets)):
        for j in range(i + 1, len(new_sets)):
            assert jaccard(new_sets[i], new_sets[j]) < 0.6
    for ns in new_sets:
        for vs in v2_sets:
            assert jaccard(ns, vs) < 0.6


@pytest.mark.unit
def test_question_set_v4_corpus_shape() -> None:
    """The frozen EVAL-5 corpus (docs/eval/question-set-v4.jsonl, T7.31):
    55 unique questions = 34 v2 + 13 v3 lines VERBATIM (each inherited
    line byte-identical to a frozen original; T7.31 drops all dateless
    questions and the Rublyovo pack, whose sources now conflict) + 8 new
    questions (all relative «на текущую дату»).

    Hard constraints (EVAL-5 corpus design):
    - priorities 2×100 (old-as_of — the g6 designed numerator, K = 2),
      10×90 (pack anchors), 14×80 (pack follow-ups), 29×0 (standalone);
    - every question naming sources names exactly two, on different
      registrable domains;
    - NO dateless URL question: every URL-bearing question carries an
      explicit date (parse_question_date) or a relative form
      (question_uses_relative_date) — under T7.30/ADR-0016 this bounds
      the due-by-construction numerator to the 2 old-as_of questions;
    - pairwise token-Jaccard (curiosity.word_set/jaccard) strictly < 0.6
      on all three slices: new-new, new-vs-v2 (all 50), new-vs-v3 (all 69).
    """
    import json

    raw_v4 = _corpus_path("question-set-v4.jsonl").read_text(encoding="utf-8")
    raw_v2 = _corpus_path("question-set-v2.jsonl").read_text(encoding="utf-8")
    raw_v3 = _corpus_path("question-set-v3.jsonl").read_text(encoding="utf-8")

    json_v4 = [ln for ln in raw_v4.splitlines() if ln.strip() and not ln.startswith("#")]
    json_v2 = [ln for ln in raw_v2.splitlines() if ln.strip() and not ln.startswith("#")]
    json_v3 = [ln for ln in raw_v3.splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(json_v2) == 50
    assert len(json_v3) == 69
    assert len(json_v4) == 55

    questions = parse_corpus_questions(raw_v4)
    texts = [t for t, _ in questions]
    assert len(set(texts)) == 55
    priorities = [p for _, p in questions]
    assert priorities.count(100) == 2
    assert priorities.count(90) == 10
    assert priorities.count(80) == 14
    assert priorities.count(0) == 29

    # inherited lines are byte-identical to the frozen v2/v3 originals
    # (a curated subset, not a contiguous block):
    orig = set(json_v2) | set(json_v3)
    new_lines = [ln for ln in json_v4 if ln not in orig]
    assert len(new_lines) == 8
    assert all(ln in orig for ln in json_v4 if ln not in new_lines)

    # two independent sources (different registrable domains) per
    # question that names sources; the new 8 all name sources.
    for t in texts:
        urls = _urls(t)
        if urls:
            assert len(urls) == 2, t
            assert _registrable_domain(urls[0]) != _registrable_domain(urls[1]), t
    for ln in new_lines:
        t = json.loads(ln)["text"]
        assert len(_urls(t)) == 2, t

    # no dateless URL question: under T7.30/ADR-0016 the claim as_of of
    # every URL question is host-derived (explicit date or the session
    # date for relative forms) — zero model-as_of exposure:
    for t in texts:
        if _urls(t):
            assert parse_question_date(t) is not None or question_uses_relative_date(t), t

    # the 2 old-as_of questions carry explicit PAST dates (g6 K = 2):
    old = [t for t, p in questions if p == 100]
    assert len(old) == 2
    for t in old:
        d = parse_question_date(t)
        assert d is not None and d.year <= 2026, t

    # pairwise Jaccard < 0.6 on all three slices (strict).
    new_sets = [word_set(json.loads(ln)["text"]) for ln in new_lines]
    v2_sets = [word_set(t) for t in [json.loads(ln)["text"] for ln in json_v2]]
    v3_sets = [word_set(t) for t in [json.loads(ln)["text"] for ln in json_v3]]
    for i in range(len(new_sets)):
        for j in range(i + 1, len(new_sets)):
            assert jaccard(new_sets[i], new_sets[j]) < 0.6
    for ns in new_sets:
        for vs in v2_sets:
            assert jaccard(ns, vs) < 0.6
        for vs in v3_sets:
            assert jaccard(ns, vs) < 0.6
