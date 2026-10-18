"""T7.98 (ADR-0033 дополнение): гейт дублей по значению читает целочисленные диапазоны.

Стендовый дефект (ca2484b2): утверждение 136a0ca0 «…повысил прогноз инфляции на 2026 год
до 6–7%» не слилось с опорным c6729d4c «Прогноз Банка России … составляет 6,0–7,0%» —
прежний `statement_values` видел только десятичные числа с единицей и не читал форму
«6–7%» (оба конца диапазона). Правка: `_RANGE_TOKEN_RE` (целые концы, единица после правого
конца, защита от десятильного разделителя с обеих сторон) + стоп-основы прогнозной лексики
в словаре показателя (`повыс/пониз/пониж`), чтобы глагол пересмотра не входил в overlap.

Тест закрепляет границу правила и ЗАМЕРЫ по живым утверждениям стенда (claims_live.tsv):
единственное изменение множеств значений — 136a0ca0 (∅ → {6.0, 7.0}); 7cc2b578 остаётся
{5.59, 6.5, 6.6, 6.8} («6,5–7%» с десятильным концом — нечитаем, сознательная граница).
"""

from __future__ import annotations

import pytest

from apps.orchestrator.value_duplicate import (
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    find_value_duplicates,
    indicator_check,
    statement_values,
)

pytestmark = pytest.mark.unit

# утверждения стенда .92 (claims_live.tsv, дословно)
STAND_136A0CA0 = "Банк России в июле 2026 года повысил прогноз инфляции на 2026 год до 6–7%."
STAND_C6729D4C = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."
STAND_7CC2B578 = (
    "Фактическая годовая инфляция в России по итогам 2025 года (5,59%) оказалась ниже всех "
    "прогнозов аналитиков: прогноз Банка России — 6,5–7%, Минэкономразвития — 6,8%, "
    "консенсус-прогноз ЦБ — 6,6%"
)
STAND_9266248E = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила "
    "5,59% (Банк России и Росстат, опубликовано 15 января 2026)."
)
STAND_2F456E85 = (
    "Консенсус-прогноз аналитиков по годовой инфляции в России на 2026 год составляет 6,6% "
    "(по данным макроэкономического опроса Банка России за сентябрь 2026 года)."
)
C6729D4C_ID = "c6729d4c-72a7-4f74-a432-e9f41de6f13c"


# ─── правило чтения диапазона ───────────────────────────────────────────────


def test_integer_ranges_with_unit_are_read_as_both_endpoints() -> None:
    assert statement_values("повысил прогноз инфляции до 6–7%.") == frozenset({"6.0", "7.0"})
    assert statement_values("прогноз составляет 6,0–7,0%.") == frozenset({"6.0", "7.0"})
    assert statement_values("диапазон 6-7%.") == frozenset({"6.0", "7.0"})  # дефис
    assert statement_values("диапазон 6—7%.") == frozenset({"6.0", "7.0"})  # тире
    assert statement_values("рост на 12–13 п.п.") == frozenset({"12.0", "13.0"})


def test_mixed_decimal_ranges_stay_unreadable_by_design() -> None:
    """Сознательная граница: смешанная форма «6,5–7%» читается только как явное десятильное
    число 6,5 (левый конец), правый конец «7» — это хвост диапазона, не самостоятельное
    значение. Прежние множества значений от этого не меняются (замер — ниже)."""
    assert statement_values("прогноз Банка России — 6,5–7%, консенсус — 6,6%") == frozenset(
        {"6.5", "6.6"}
    )
    assert statement_values("оценка 6–7,5%") == frozenset({"7.5"})


def test_date_and_duration_ranges_are_not_values() -> None:
    assert statement_values("публикация данных 16–21 января 2026 года.") == frozenset()
    assert statement_values("горизонт 5-6 месяцев.") == frozenset()
    assert statement_values("сопоставление 2024–2025 годов.") == frozenset()


# ─── замер по живым утверждениям стенда (было/стало множеств) ───────────────


def test_live_statement_value_sets_measured() -> None:
    # стало: дефектное утверждение получило множество значений (было — пустое)
    assert statement_values(STAND_136A0CA0) == frozenset({"6.0", "7.0"})
    assert statement_values(STAND_C6729D4C) == frozenset({"6.0", "7.0"})
    # неизменное: смешанные диапазоны 7cc2b578 — ровно прежние четыре значения
    assert statement_values(STAND_7CC2B578) == frozenset({"5.59", "6.5", "6.6", "6.8"})
    assert statement_values(STAND_9266248E) == frozenset({"5.59"})
    assert statement_values(STAND_2F456E85) == frozenset({"6.6"})


# ─── стоп-основы прогнозной лексики ─────────────────────────────────────────


def test_forecast_verbs_do_not_count_into_indicator_overlap() -> None:
    check = indicator_check(STAND_136A0CA0, STAND_C6729D4C)
    assert check.matched is True, check.detail
    assert "повыс" not in check.detail  # глагол пересмотра — стоп-основа, не показатель


# ─── гейт: дефектная пара склеивается, остальные — нет ──────────────────────


def _claim(statement: str, year: int | None) -> ValueDuplicateClaim:
    return ValueDuplicateClaim(
        index=0,
        statement=statement,
        claim_type="external_fact",
        as_of_year=year,
        has_support_link=True,
        has_counter_link=False,
    )


def _candidate(claim_id: str, statement: str, year: int | None) -> ValueDuplicateCandidate:
    return ValueDuplicateCandidate(
        claim_id=claim_id,
        statement=statement,
        claim_type="external_fact",
        as_of_year=year,
        relied=True,
    )


def test_gate_merges_the_stand_defect_pair() -> None:
    decisions = find_value_duplicates(
        [_claim(STAND_136A0CA0, 2026)],
        [_candidate(C6729D4C_ID, STAND_C6729D4C, 2026)],
    )
    assert len(decisions) == 1, decisions
    entry = decisions[0]
    assert entry["action"] == "reverified"
    assert entry["target"] == C6729D4C_ID
    assert "same value set ['6.0', '7.0']" in entry["reason"]
    assert "period [2026] covered" in entry["reason"]


def test_gate_refuses_different_value_sets() -> None:
    # 7cc2b578 (факт 5,59 + чужие прогнозы 6,5/6,8/6,6) против прогноза ЦБ 6,0–7,0:
    # множества не равны — гейт молчит (нет подозреваемых), склейки нет
    assert find_value_duplicates(
        [_claim(STAND_7CC2B578, 2025)], [_candidate(C6729D4C_ID, STAND_C6729D4C, 2026)]
    ) == []
    # консенсус 6,6% против 6,0–7,0% — тоже разные множества
    assert find_value_duplicates(
        [_claim(STAND_2F456E85, 2026)], [_candidate(C6729D4C_ID, STAND_C6729D4C, 2026)]
    ) == []


def test_gate_stays_silent_on_date_only_overlap() -> None:
    decisions = find_value_duplicates(
        [_claim("Инфляция ускорилась 16–21 января 2026 года.", 2026)],
        [_candidate("c-date", "ЦБ публиковал данные 16–21 января 2026 года.", 2026)],
    )
    assert decisions == []
