"""T7.83: чистый детектор «дубля по значению» (хостовый гейт кураторского
предложения).

Определение (ADR-0033, разбор фикстуры стенда .92 f452a978): новая операция
claim без `existing_claim_id` и кандидат из контекст-пака с:

* тем же `claim_type`;
* одинаковым НЕПУСТЫМ множеством значимых чисел формулировок (десятичные
  числа с границей числа, как в assertion_window: «5,59» ≠ «5,6» и ≠ «105,59»;
  годы и даты значениями не считаются);
* совместным периодом: годы новой формулировки (плюс год её `as_of`) —
  подмножество годов кандидата (плюс год его `as_of`; у кандидата могут быть
  дополнительные годы — напр. «опубликовано … 2026»).

Ровно один кандидат → дубль; ноль или несколько → операция не трогается,
причина записывается честно. Пары фикстуры: e189c80d↔9266248e — дубль;
391c4388↔6d9a12ff — НЕ дубль (и по типу, и по значению).
"""

from __future__ import annotations

import pytest

from apps.orchestrator.value_duplicate import (
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    find_value_duplicates,
    statement_values,
    statement_years,
)

pytestmark = pytest.mark.unit

# ── фикстурные тексты (стенд .92, SELECT из noezema-dev) ────────────────────
CANDIDATE_OFFICIAL = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
    "составила 5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)
DUP_CLAIM = (
    "Официальная годовая инфляция в России по итогам 2025 года "
    "(декабрь 2025 к декабрю 2024) составила 5,59% (по данным Росстата)."
)
CANDIDATE_OBSERVED = (
    "Медианная оценка годовой наблюдаемой инфляции населением России в декабре 2025 года "
    "составила 13,1% для граждан с накоплениями и 15,6% для граждан без накоплений "
    "(по данным опроса «инФОМ»)."
)
NEW_OBSERVED_CLAIM = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года "
    "составила 14,5% (по данным опроса инФОМ для Банка России)."
)


def _claim(
    statement: str,
    claim_type: str = "temporal_fact",
    as_of_year: int | None = 2025,
    index: int = 0,
    supports: bool = True,
    counters: bool = False,
) -> ValueDuplicateClaim:
    return ValueDuplicateClaim(
        index=index,
        statement=statement,
        claim_type=claim_type,
        as_of_year=as_of_year,
        has_support_link=supports,
        has_counter_link=counters,
    )


def _candidate(
    claim_id: str,
    statement: str = CANDIDATE_OFFICIAL,
    claim_type: str = "temporal_fact",
    as_of_year: int | None = 2026,
    relied: bool = False,
) -> ValueDuplicateCandidate:
    return ValueDuplicateCandidate(
        claim_id=claim_id,
        statement=statement,
        claim_type=claim_type,
        as_of_year=as_of_year,
        relied=relied,
    )


# ── разбор значимых чисел и годов ───────────────────────────────────────────

def test_values_are_decimals_with_digit_boundary() -> None:
    assert statement_values(CANDIDATE_OFFICIAL) == {"5.59"}
    assert statement_values(DUP_CLAIM) == {"5.59"}
    # «105,59» — одно число, а не вхождение «5,59» (та же граница, что у assertion_window)
    assert statement_values("Индекс потребительских цен составил 105,59%") == {"105.59"}
    # 5,6 ≠ 5,59 — разные значения
    assert statement_values("...составила 5,6%") == {"5.6"}
    # годы и даты — не значения; «16–21» — не десятичные числа
    assert statement_values("опубликовано 16–21 января 2026 г.") == set()


def test_years_are_four_digit_tokens_not_decimals() -> None:
    assert statement_years(CANDIDATE_OFFICIAL) == {2025, 2024, 2026}
    assert statement_years(DUP_CLAIM) == {2025, 2024}
    # десятичный год-как-значение не превращается в год периода
    assert statement_years("значение 12,2026 зафиксировано") == set()


# ── пары фикстуры ────────────────────────────────────────────────────────────

def test_fixture_pair_official_is_a_value_duplicate() -> None:
    decisions = find_value_duplicates(
        [_claim(DUP_CLAIM)], [_candidate("c-1", relied=True)]
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["claim_index"] == 0
    assert entry["target"] == "c-1"
    assert entry["action"] == "reverified"
    assert entry["reason"]


def test_fixture_pair_observed_is_not_a_duplicate() -> None:
    # разные И тип (external_fact), и значения ({13.1,15.6} vs {14.5}) — тишина
    decisions = find_value_duplicates(
        [_claim(NEW_OBSERVED_CLAIM)],
        [
            _candidate("c-obs", CANDIDATE_OBSERVED, claim_type="external_fact", as_of_year=2025),
            _candidate("c-off"),
        ],
    )
    assert decisions == []


# ── контрольные грани определения ────────────────────────────────────────────

def test_close_number_is_not_the_same_value() -> None:
    assert find_value_duplicates([_claim("Инфляция по итогам 2025 года составила 5,6%.")], [_candidate("c-1")]) == []


def test_longer_number_is_not_a_substring_match() -> None:
    assert find_value_duplicates(
        [_claim("Индекс потребительских цен по итогам 2025 года (к декабрю 2024) составил 105,59%.")],
        [_candidate("c-1")],
    ) == []


def test_different_period_is_left_alone() -> None:
    decisions = find_value_duplicates(
        [_claim("Официальная инфляция по итогам 2023 года составила 5,59% (Росстат).", as_of_year=2023)],
        [_candidate("c-1")],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert decisions[0]["target"] is None
    assert "period" in decisions[0]["reason"]


def test_different_claim_type_is_not_a_candidate() -> None:
    # та же формулировка, другой тип — гейт молчит (T7.9 и rules engine отвечают сами)
    assert find_value_duplicates([_claim(DUP_CLAIM, claim_type="external_fact")], [_candidate("c-1")]) == []


def test_integer_only_statements_are_conservatively_silent() -> None:
    # годы/целые без разделителя — не значения; консервативно: дубль не объявляем
    assert (
        find_value_duplicates(
            [_claim("6*7 равно 42", claim_type="computed_result", as_of_year=None)],
            [_candidate("c-1", "6*7 равно 42", claim_type="computed_result", as_of_year=None)],
        )
        == []
    )


def test_two_matching_candidates_are_ambiguous_and_recorded() -> None:
    decisions = find_value_duplicates(
        [_claim(DUP_CLAIM)],
        [
            _candidate("c-1"),
            _candidate(
                "c-2",
                "Инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) — 5,59% (обзор).",
            ),
        ],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert decisions[0]["target"] is None
    assert "ambiguous" in decisions[0]["reason"]


def test_counterevidence_operation_is_not_merged() -> None:
    # гейт соединяет только поддерживающие операции; спор остаётся честным спорам
    decisions = find_value_duplicates(
        [_claim(DUP_CLAIM, supports=False, counters=True)], [_candidate("c-1")]
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert decisions[0]["target"] is None


def test_no_candidates_no_decisions() -> None:
    assert find_value_duplicates([_claim(DUP_CLAIM)], []) == []
