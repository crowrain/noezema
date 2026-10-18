"""Unit: итог карточки «Ответ по уже записанным знаниям» (T7.121 часть 5, случай с4).

Стендовый случай с4: сессия ответила, опираясь на уже записанное утверждение (блок
карточки «Использовано из знаний»), и не записала ни одного нового. Прежде витрина
сообщала «Ответ не записан» — неправда того же рода, какую закрыл T7.74 для
перепроверенных/принятых повторно утверждений. Теперь у построителя есть параметр
`relied_claim_count` (карточка передаёт размер relied-блока; список вопросов — нет,
relied-строк там нет по дизайну RELATION_KEYS), и новый kind `answered_from_relied`.

Приоритеты прежних исходов не тронуты: свои записи, работа в процессе, отказ сессии
и отсутствие сессий решают раньше нового исхода.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.web import answer as ans
from apps.web import labels as ui_labels

pytestmark = pytest.mark.unit


def _session(state: str, termination_reason: str | None = "goal_reached") -> dict[str, Any]:
    return {"id": "s1", "state": state, "termination_reason": termination_reason}


SUCCEEDED = [_session("succeeded")]


def test_relied_answer_without_own_claims_gets_new_kind() -> None:
    """Было (на 6372138): no_answer («Ответ не записан»). Стало: честный исход
    «по уже записанным знаниям», подписи — из словаря."""
    result = ans.build_answer_result(
        question_state="candidate",
        sessions=SUCCEEDED,
        answer_claim_count=0,
        relied_claim_count=2,
    )
    assert result["kind"] == "answered_from_relied"
    entry = ui_labels.describe("answer_result", "answered_from_relied")
    assert result["label"] == entry["label"]
    assert result["action"] == entry["action"]
    assert "Новых утверждений сессия не записала" in result["hint"]
    assert "Последняя сессия:" in result["hint"]  # прежняя строка о сессии не исчезла
    assert result["active"] is False


def test_without_relied_the_old_no_answer_stands() -> None:
    """Замок неизменности: `relied_claim_count` по умолчанию 0 — прежний путь остаётся прежним."""
    result = ans.build_answer_result(
        question_state="candidate",
        sessions=SUCCEEDED,
        answer_claim_count=0,
    )
    assert result["kind"] == "no_answer"


def test_own_claims_beat_reliance() -> None:
    """Свои действующие записи решают: reliance не подменяет «Ответ есть»."""
    result = ans.build_answer_result(
        question_state="verified",
        sessions=SUCCEEDED,
        answer_claim_count=1,
        relations=["created"],
        relied_claim_count=3,
    )
    assert result["kind"] == "answered"


def test_failed_session_beats_reliance() -> None:
    result = ans.build_answer_result(
        question_state="rejected",
        sessions=[_session("failed", "attempt_aborted")],
        answer_claim_count=0,
        relied_claim_count=1,
    )
    assert result["kind"] == "failed"


def test_working_session_beats_reliance() -> None:
    result = ans.build_answer_result(
        question_state="researching",
        sessions=[_session("researching", None)],
        answer_claim_count=0,
        relied_claim_count=1,
    )
    assert result["kind"] == "in_progress"


def test_reliance_without_sessions_is_still_waiting() -> None:
    """Без сессий relying показать нечем — карточка не выдумывает опору из воздуха."""
    result = ans.build_answer_result(
        question_state="candidate",
        sessions=[],
        answer_claim_count=0,
        relied_claim_count=1,
    )
    assert result["kind"] == "waiting"


def test_kind_is_registered_in_vocabulary_and_listed_in_builders() -> None:
    """Закрытие замка test_web_labels: kind в RESULT_KINDS ⇒ подпись обязательна."""
    assert "answered_from_relied" in ans.RESULT_KINDS
    entry = ui_labels.describe("answer_result", "answered_from_relied")
    assert entry["label"] == "Ответ по уже записанным знаниям"
    assert entry["hint"].startswith("Новых утверждений сессия не записала")
    assert entry["action"].startswith("Посмотрите блок «Использовано из знаний»")
