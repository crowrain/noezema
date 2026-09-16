"""Corpus parsing for hostctl eval-run (T7.7, EVAL-3).

The corpus may carry an optional integer ``priority`` per question; the
FIFO selector consumes (priority DESC, created_at ASC, id), so the
corpus controls the consumption order through priorities (§5.3.2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hostctl.cli import parse_corpus_questions


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
