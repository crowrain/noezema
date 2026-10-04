"""Unit: operator question intake rules (T7.59, §5.3/§13.6).

Pure validation only — no database. The DB half (dedup, queue position) is in
tests/scenario/test_operator_question_intake.py; the HTTP contract is in
tests/scenario/test_web_questions.py.
"""

from __future__ import annotations

import pytest

from packages.cognition.question_selector import FIFOQuestionSelector
from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.services.question_intake import (
    MAX_QUESTION_TEXT_CHARS,
    PRIORITY_MAX,
    PRIORITY_MIN,
    QuestionIntakeError,
    intake_error_payload,
    validate_operator_question,
)

pytestmark = pytest.mark.unit


def _text(length: int) -> str:
    return "вопрос " + "x" * max(0, length - len("вопрос "))


def test_plain_text_is_accepted_and_stripped() -> None:
    text, priority = validate_operator_question("  Сколько будет 6*7?  \n", 3)
    assert text == "Сколько будет 6*7?"
    assert priority == 3


def test_default_priority_is_zero() -> None:
    assert validate_operator_question("q")[1] == 0


@pytest.mark.parametrize("raw", ["", "   ", "\n\t \r"])
def test_empty_or_whitespace_text_is_rejected(raw: str) -> None:
    with pytest.raises(QuestionIntakeError, match="empty"):
        validate_operator_question(raw)


def test_length_bound_is_exact() -> None:
    assert validate_operator_question(_text(MAX_QUESTION_TEXT_CHARS))[0]
    with pytest.raises(QuestionIntakeError, match=str(MAX_QUESTION_TEXT_CHARS)):
        validate_operator_question(_text(MAX_QUESTION_TEXT_CHARS + 1))


@pytest.mark.parametrize("raw", [None, 5, 2.5, ["q"], {"t": "q"}])
def test_non_string_text_is_rejected(raw: object) -> None:
    with pytest.raises(QuestionIntakeError, match="string"):
        validate_operator_question(raw)


@pytest.mark.parametrize("raw", [PRIORITY_MIN, 0, PRIORITY_MAX])
def test_priority_bounds_are_inclusive(raw: int) -> None:
    assert validate_operator_question("q", raw)[1] == raw


@pytest.mark.parametrize("raw", [PRIORITY_MIN - 1, PRIORITY_MAX + 1, 10**9])
def test_priority_out_of_range_is_rejected(raw: int) -> None:
    with pytest.raises(QuestionIntakeError, match="out of range"):
        validate_operator_question("q", raw)


@pytest.mark.parametrize("raw", [True, False, 1.0, "5", None])
def test_priority_must_be_a_real_int(raw: object) -> None:
    # bool is rejected although Python counts it as an int: a ranking flag
    # is not a rank, and the API/CLI must not silently read True as 1.
    with pytest.raises(QuestionIntakeError, match="integer"):
        validate_operator_question("q", raw)


def test_operator_questions_are_fifo_eligible_without_touching_the_selector() -> None:
    """§5.3.2 names "seeded/message question → FIFO selection": the operator
    path reuses the EXISTING QuestionOrigin.MESSAGE, so intake needs no new
    origin and no selector change. This guard fails if that eligibility moves."""
    question = ORMQuestion(
        text="q",
        origin=QuestionOrigin.MESSAGE.value,
        state=QuestionState.CANDIDATE.value,
        priority=0,
    )
    assert FIFOQuestionSelector.is_eligible(question) is True


def test_intake_error_payload_is_operator_readable() -> None:
    payload = intake_error_payload(QuestionIntakeError("text is empty"))
    assert payload == {"error": "invalid_question", "detail": "text is empty"}
