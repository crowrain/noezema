"""T7.131 (часть 4б): предикат трёх состояний ответа — чистая функция и сверка с карточкой.

`packages/memory/honesty_report.py` не импортирует `apps/` (AGENTS §7), поэтому предикат
«Ответ по уже записанным знаниям» в отчёте повторён как чистая функция. Здесь закреплено:

* множества рабочих состояний вопроса и провалившихся состояний сессии — те же, что у
  карточки `apps/web/answer.py` (сверка импортами: расхождение = падение теста);
* терминальность читается из перечисления домена, а не из списка текстов;
* таблица переходов совпадает с порядком веток `build_answer_result`.
"""

from __future__ import annotations

import pytest

from apps.web import answer as web_answer
from packages.domain.models.enums import SessionState
from packages.memory.honesty_report import (
    ANSWER_STATE_LABELS,
    FAILED_SESSION_STATES,
    WORKING_QUESTION_STATES,
    answer_state,
    is_terminal_session_state,
)

pytestmark = pytest.mark.unit


def test_mirror_sets_equal_the_vitrine_sets() -> None:
    assert set(WORKING_QUESTION_STATES) == set(web_answer.WORKING_QUESTION_STATES)
    assert set(FAILED_SESSION_STATES) == set(web_answer.FAILED_SESSION_STATES)


def test_terminal_predicate_comes_from_domain_enum() -> None:
    terminal = {state.value for state in SessionState if state.is_terminal}
    for state in sorted(terminal):
        assert is_terminal_session_state(state), state
    assert not is_terminal_session_state("searching")
    assert not is_terminal_session_state(None)
    assert not is_terminal_session_state("нет такого состояния")


@pytest.mark.parametrize(
    ("own", "relied", "question_state", "session_states", "expected"),
    [
        # своя запись — ответ, даже если опора тоже есть
        (True, 3, "verified", ["succeeded"], "answered"),
        (False, 1, "verified", ["succeeded"], "relied_only"),
        (False, 2, "partially_answered", ["succeeded", "succeeded_partial"], "relied_only"),
        # работа ещё идёт: нетерминальная сессия или рабочее состояние вопроса
        (False, 1, "verified", ["searching"], "none"),
        (False, 1, "researching", ["succeeded"], "none"),
        (False, 1, "selected", [], "none"),
        # последняя попытка провалилась — показывать опор нечем (карточка: `failed`)
        (False, 1, "verified", ["succeeded", "failed"], "none"),
        (False, 1, "verified", ["cancelled"], "none"),
        # опор нет — прежний «Ответ не записан»
        (False, 0, "verified", ["succeeded"], "none"),
        # без сессий опору показать нечем (карточка: `waiting`)
        (False, 4, "verified", [], "none"),
    ],
)
def test_answer_state_table(
    own: bool, relied: int, question_state: str, session_states: list[str], expected: str
) -> None:
    got = answer_state(
        has_own_answer_claims=own,
        relied_claim_count=relied,
        question_state=question_state,
        session_states=session_states,
    )
    assert got == expected
    assert got in ANSWER_STATE_LABELS


def test_labels_are_the_three_honest_states() -> None:
    assert ANSWER_STATE_LABELS == {
        "answered": "Ответ есть",
        "relied_only": "Ответ по уже записанным знаниям",
        "none": "Ответ не записан",
    }
