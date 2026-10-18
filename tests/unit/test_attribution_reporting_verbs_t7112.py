"""T7.112 (а3): закрытый словарь глаголов отчётности первоисточника и pairing caliber.az.

Случай приёмки (сессия 4e7ba8c2): caliber.az пересказывает Росстат — «Росстат зафиксировал
уровень безработицы в России в сентябре 2026 года на отметке 2,1–2,2%». Прежде ни один шаблон
не покрывал эти глаголы, и значение оставалось без атрибуции. Добавлены четыре глагола
совершённого вида; правило пары не ослаблено: те же измеренное число и алиас словаря в окне
одного фрагмента. Разночтение строгого режима во фрагменте, называющем двух словарных
первоисточников, разрешается прежним правилом владения числом T7.109 (б2) (STATUS T7.112).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.research_proxy.source_attribution import (
    STATUS_DERIVATIVE as PAGE_DERIVATIVE,
)
from apps.research_proxy.source_attribution import (
    STATUS_NO_VALUE_ATTRIBUTION,
    detect_source_attribution,
)
from apps.research_proxy.value_attribution import (
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_VALUE_NOT_ATTRIBUTED,
    attribute_value_in_fragment,
)

pytestmark = pytest.mark.unit

FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "t7112" / "caliber-az-rosstat-unemployment.normalized.txt"
)
CALIBER_URI = "https://caliber.az/ru"

CLAIM_SEPT = "Уровень безработицы в России в сентябре 2026 года составил 2,1–2,2%."
CLAIM_AUG = "Уровень безработицы в России в августе 2026 года составил 2,2%."


def _decide(text: str, claim: str) -> object:
    return attribute_value_in_fragment(canonical_uri=CALIBER_URI, text=text, claim_statement=claim)


# ─── целевой случай приёмки: страница и значение через новый шаблон ──────────────────────


def test_caliber_page_is_attributed_to_rosstat_via_reporting_verb() -> None:
    decision = detect_source_attribution(canonical_uri=CALIBER_URI, text=FIXTURE.read_text(encoding="utf-8"))
    assert decision.status == PAGE_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "rosstat"
    assert decision.basis_fragment is not None
    lowered = decision.basis_fragment.lower()
    assert "зафиксировал" in lowered or "обнародовал" in lowered


def test_composite_unemployment_value_pairs_strictly_with_rosstat() -> None:
    decision = _decide(FIXTURE.read_text(encoding="utf-8"), CLAIM_SEPT)
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat"
    assert decision.pairing == "value_and_primary_in_fragment"


def test_plain_month_value_still_refuses_the_composite_range() -> None:
    # фильтр T7.109a цел: составной диапазон «2,1–2,2» не равен голому «2,2» — честный отказ
    decision = _decide(FIXTURE.read_text(encoding="utf-8"), CLAIM_AUG)
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision


# ─── синтетические формы новых шаблонов ──────────────────────────────────────────────────


def test_reported_composite_range_pairs_with_named_primary() -> None:
    text = "Уровень безработицы в России в сентябре 2026 года составил 2,1–2,2% по данным Росстата."
    decision = _decide(text, CLAIM_SEPT)
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat"


def test_published_data_fragment_pairs_with_rosstat() -> None:
    text = "Росстат опубликовал данные: годовая инфляция в России в августе 2026 года составила 2,2%."
    decision = _decide(text, "Годовая инфляция в России в августе 2026 года составила 2,2%.")
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat"


def test_reporting_verb_without_a_dictionary_primary_stays_honest_refusal() -> None:
    # «аналитики опубликовали прогноз» — несловарный актор: алиаса нет, пары нет
    text = "Аналитики опубликовали прогноз годовой инфляции в России 2,2% на конец 2027 года."
    page = detect_source_attribution(canonical_uri=CALIBER_URI, text=text)
    assert page.status == STATUS_NO_VALUE_ATTRIBUTION, page.basis_fragment
    decision = _decide(text, "Годовая инфляция в России составила 2,2%.")
    assert decision.status != STATUS_DERIVATIVE, decision


def test_bare_publication_mention_without_value_stays_refusal() -> None:
    # голое упоминание публикационной деятельности (без измеренного значения — «16 января»
    # датой не становится парой) — прежний честный отказ страницы, значение наследует его
    text = "Росстат опубликовал релиз 16 января о графике публикации данных."
    decision = _decide(text, CLAIM_AUG)
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision


# ─── сужение разночтения прежним правилом владения числом (T7.109 б2) ─────────────────────


def test_two_named_primaries_resolve_to_the_number_owner() -> None:
    # новый глагол привёл строгую пару во фрагмент с двумя словарными алиасами; значение 7,5%
    # — собственное число Росстата (ближайшее число после алиаса), у Минэкономразвития своего
    # числа после упоминания нет: разночтение разрешается владением, pairing строгий
    text = (
        "Росстат опубликовал оценку годовой инфляции 7,5%. "
        "Минэкономразвития прогнозирует ускорение роста цен во втором квартале."
    )
    decision = _decide(text, "Годовая инфляция в России в сентябре 2026 года составила 7,5%.")
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat"
    assert decision.pairing == "value_and_primary_in_fragment"


def test_two_primaries_owning_the_same_value_stay_ambiguous() -> None:
    # зеркало: оба алиаса несут своё ближайшее число равное значению утверждения — разночтение
    # остаётся прежним честным отказом (правило владения не выбирает победителя силой)
    text = "Росстат опубликовал оценку 7,5% на июль и Минэкономразвития опубликовал прогноз 7,5% на декабрь."
    decision = _decide(text, "Годовая инфляция в России в сентябре 2026 года составила 7,5%.")
    assert decision.status == STATUS_AMBIGUOUS, decision
