"""T7.123 (часть 3, ADR-0038 Дополнение): объявленное в сводке намерение при пустом claims.

Дыра к3 (стенд b7e93ff6): куратор написал в сводке «предлагается создать новый claim …»,
«мы фиксируем новое состояние» и вернул `claims: []` без опоры. Хост отличает такой дефект
ответа от честного пустого итога правила 16: чистая функция
`apps/orchestrator/curator_declared_claims.py::summary_declares_new_claim`, причина отказа и
kind — именованные константы этого же модуля.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.curator_declared_claims import (
    CURATOR_REJECT_KIND_DECLARED_EMPTY,
    DECLARED_EMPTY_REASON,
    summary_declares_new_claim,
)

pytestmark = pytest.mark.unit

# дословные фрагменты сводки куратора стенда b7e93ff6 (событие claim_created с claims: [])
STAND_SUMMARY_PART = (
    "Существующий claim [c:c263e93c-2e02-4a37-80bd-6d5ce091362e] (5-6%) противоречит новым "
    "данным, поэтому предлагается создать новый claim с актуальным значением и датой "
    "прогноза, а старый оставить как исторический. Однако, согласно правилам, если факт "
    "изменился, мы фиксируем новое состояние."
)


def test_stand_summary_declares_new_claim() -> None:
    assert summary_declares_new_claim(STAND_SUMMARY_PART)


def test_declared_bases_are_folded_across_case_and_line_breaks() -> None:
    assert summary_declares_new_claim("ПРЕДЛАГАЕТСЯ  СОЗДАТЬ\nновый claim")
    assert summary_declares_new_claim("фиксируем\nновое\u00a0состояние")


def test_honest_outcomes_are_not_declarations() -> None:
    # честный итог правила 16 (curator-v16): пустые claims с опорой — не объявление нового
    assert not summary_declares_new_claim(
        "Новых утверждений нет; вывод опирается на уже записанные claims [c:c263e93c…]."
    )
    assert not summary_declares_new_claim(
        "Состояние знания не меняется: значение подтверждено прежним источником."
    )
    assert not summary_declares_new_claim("")


def test_declared_empty_reason_and_kind_are_named() -> None:
    assert DECLARED_EMPTY_REASON == "в сводке объявлено новое утверждение, но список claims пуст"
    assert CURATOR_REJECT_KIND_DECLARED_EMPTY == "claims_empty_declared_in_summary"
