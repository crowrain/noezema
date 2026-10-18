"""Unit: нумерация/порядок/краткий итог списка вопросов (T7.67a) — чистая часть.

Сценарные проверки с БД (`tests/scenario/test_web_questions_view_api.py`) подтверждают
оконную нумерацию и пакетность запросов; здесь — построители без базы:

* `summary_statement` — одна human-строка из утверждения: сворачивание переносов,
  маскировка NUL, потолок длины со многоточием внутри потолка;
* `build_answer_summary` — тот же выбор итога, что у карточки (`build_answer_result`):
  pending/invalid (пустой список действующих claims) никогда не рождают «ответ» с
  утверждением или бейджем; бейдж есть только при действующем утверждении;
* порядок выдачи закрыт списком `ORDERS`, неизвестный порядок отвергается, а не
  подставляется.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from apps.web import labels as ui_labels
from apps.web import questions_view as qview

pytestmark = pytest.mark.unit


def _rules() -> dict[str, Any]:
    """Пороги «как из снимка»: пустой набор — модуль надёжности использует свой сверяемый запасной путь."""
    return {"claim_type_rules": {}}


# ─── строка утверждения для колонки «Ответ» ────────────────────────────────


def test_summary_statement_flattens_and_trims_to_the_cap() -> None:
    assert qview.summary_statement("  первая   строка\n вторая\tтретья ") == "первая строка вторая третья"

    long = "а" * 300
    short = qview.summary_statement(long)
    assert len(short) == qview.SUMMARY_STATEMENT_CHARS
    assert short.endswith("…") and short.startswith("а" * (qview.SUMMARY_STATEMENT_CHARS - 1))

    exact = "б" * qview.SUMMARY_STATEMENT_CHARS
    assert qview.summary_statement(exact) == exact  # ровно по потолку — без многоточия


def test_summary_statement_masks_nul_and_never_invents_text() -> None:
    masked = qview.summary_statement("а\x00б")
    assert "\x00" not in masked and "а" in masked and "б" in masked
    assert qview.summary_statement(None) == "" and qview.summary_statement("") == ""
    assert qview.summary_statement("   \n\t ") == ""


# ─── итог ответа: те же ветки, что у карточки ──────────────────────────────


_CURRENT_CLAIM: dict[str, Any] = {
    "id": "11111111-1111-4111-8111-111111111111",
    "statement": "первое действующее утверждение",
    "claim_type": "computed_result",
    "freshness_status": "fresh",
    "epistemic_status": "supported",
    "effective_grade": "E2",
}


def test_answer_summary_shows_first_current_claim_with_reliability() -> None:
    summary = qview.build_answer_summary(
        question_state="answered",
        sessions=[{"state": "succeeded", "termination_reason": None}],
        claims=[_CURRENT_CLAIM, dict(_CURRENT_CLAIM, statement="второе позже")],
        rules=_rules(),
    )
    expected = ui_labels.describe("answer_result", "answered")
    assert summary["kind"] == "answered" and summary["label"] == expected["label"]
    # показывается первое по (created_at, id) — ровно как список claims карточки
    assert summary["statement"] == "первое действующее утверждение"
    assert {"level", "label", "hint", "color"} <= set(summary["reliability"] or {})


def test_answer_summary_is_truncated_statement_not_the_whole_claim() -> None:
    long = "в" * 400
    summary = qview.build_answer_summary(
        question_state="answered",
        sessions=[{"state": "succeeded", "termination_reason": None}],
        claims=[dict(_CURRENT_CLAIM, statement=long)],
        rules=_rules(),
    )
    assert len(summary["statement"]) == qview.SUMMARY_STATEMENT_CHARS and summary["statement"].endswith("…")


def test_answer_summary_without_current_claim_never_shows_text_or_badge() -> None:
    """Нет действующего утверждения (pending/invalid головы отсечены запросом) — нет и «ответа»."""
    for question_state, sessions, expected_kind in (
        ("candidate", [], "waiting"),
        ("answered", [{"state": "succeeded", "termination_reason": None}], "no_answer"),
        ("failed", [{"state": "failed", "termination_reason": "budget_exceeded"}], "failed"),
        ("researching", [{"state": "exploring", "termination_reason": None}], "in_progress"),
    ):
        summary = qview.build_answer_summary(
            question_state=question_state, sessions=sessions, claims=[], rules=_rules()
        )
        expected = ui_labels.describe("answer_result", expected_kind)
        assert summary["kind"] == expected_kind and summary["label"] == expected["label"]
        assert summary["statement"] is None and summary["reliability"] is None


def test_waiting_summary_label_is_the_human_queue_status() -> None:
    summary = qview.build_answer_summary(question_state="candidate", sessions=[], claims=[], rules=_rules())
    assert summary["label"] == ui_labels.describe("answer_result", "waiting")["label"]


# ─── порядок выдачи ─────────────────────────────────────────────────────────


def test_only_known_orders_are_accepted() -> None:
    assert {qview.QUEUE_ORDER, qview.RECENT_ORDER} == qview.ORDERS

    with pytest.raises(ValueError):
        asyncio.run(qview.list_question_rows(None, limit=5, order="sideways"))  # type: ignore[arg-type]
