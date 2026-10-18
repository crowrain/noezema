"""T7.84 (часть B): гейт дублей по значению обязан корректно работать с датless-операцией.

Правка части B переносит проверку «temporal_fact требует as_of» ЗА гейт дублей, поэтому
гейт теперь обязательно видит операцию, у которой `as_of` нет (`as_of_year=None`), и обязан
на ней не сломаться и не проявить лишнюю смелость: период такой операции строится ТОЛЬКО из
годов формулировки, пустой период — отказ склеивать (консервативно, как и раньше).

Это же закрепляет причину, по которой перенос безопасен: склейка превращает операцию в
перепроверку, а дату перепроверке даёт якорь (`packages/memory/reverify.py`, T7.73), так что
требовать дату ДО определения вида операции было ошибкой порядка, а не политикой.
"""

from __future__ import annotations

from apps.orchestrator.value_duplicate import (
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    _period,
    find_value_duplicates,
)
from tests.unit.test_value_duplicate import CANDIDATE_OFFICIAL, NEW_OBSERVED_CLAIM

DATELESS_RESTATEMENT = (
    "Официальная годовая инфляция в России по итогам 2025 года (декабрь к декабрю) составила "
    "5,59% (по данным Росстата)."
)
DATELESS_WITHOUT_PERIOD = "Официальная годовая инфляция в России по данным Росстата составила 5,59%."
#: значение то же, а период шире: кандидату (as_of=None, один 2025 год) он не покрыт
DATELESS_TWO_PERIODS = "Инфляция в России: 2024 и 2025 годы — 5,59% (по данным Росстата)."
CANDIDATE_ONE_YEAR = "Годовая инфляция в России по итогам 2025 года составила 5,59% (Росстат)."

OFFICIAL_ID = "9266248e-0000-4000-8000-000000000000"


def _claim(statement: str, *, as_of_year: int | None = None) -> ValueDuplicateClaim:
    return ValueDuplicateClaim(
        index=0,
        statement=statement,
        claim_type="temporal_fact",
        as_of_year=as_of_year,
        has_support_link=True,
        has_counter_link=False,
        metric=None,
    )


def _candidate(statement: str = CANDIDATE_OFFICIAL, *, as_of_year: int | None = 2026) -> ValueDuplicateCandidate:
    return ValueDuplicateCandidate(
        claim_id=OFFICIAL_ID,
        statement=statement,
        claim_type="temporal_fact",
        as_of_year=as_of_year,
        relied=True,
        metric=None,
    )


def test_period_of_a_dateless_op_comes_only_from_its_statement() -> None:
    assert _period(DATELESS_RESTATEMENT, None) == frozenset({2025})
    assert _period(DATELESS_WITHOUT_PERIOD, None) == frozenset()  # пустой период — не ошибка
    assert _period(DATELESS_WITHOUT_PERIOD, 2025) == frozenset({2025})


def test_dateless_restatement_merges_into_the_relied_candidate() -> None:
    """Ровно то, ради чего перенесена проверка даты: датless-пересказ склеивается с якорем,
    а дату позже сохранит якорь — гейту дата не нужна."""
    decisions = find_value_duplicates(
        [_claim(DATELESS_RESTATEMENT)], [_candidate(CANDIDATE_OFFICIAL, as_of_year=2026)]
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "reverified"
    assert entry["target"] == OFFICIAL_ID
    assert entry["claim_index"] == 0


def test_dateless_op_without_any_year_is_left_as_proposed() -> None:
    """Пустой период не должен ни ронять гейт, ни давать склейку: он назван в причине."""
    decisions = find_value_duplicates(
        [_claim(DATELESS_WITHOUT_PERIOD)], [_candidate(CANDIDATE_OFFICIAL, as_of_year=2026)]
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "kept"
    assert entry["target"] is None
    assert "the period [] is not covered" in entry["reason"]


def test_dateless_op_with_a_period_the_candidate_does_not_cover_is_refused() -> None:
    decisions = find_value_duplicates(
        [_claim(DATELESS_TWO_PERIODS)], [_candidate(CANDIDATE_ONE_YEAR, as_of_year=None)]
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert "[2024, 2025] is not covered" in decisions[0]["reason"]


def test_candidate_without_its_own_date_still_matches_on_statement_years() -> None:
    decisions = find_value_duplicates(
        [_claim(DATELESS_RESTATEMENT)], [_candidate(CANDIDATE_OFFICIAL, as_of_year=None)]
    )
    assert [entry["action"] for entry in decisions] == ["reverified"]


def test_dateless_op_for_an_unrelated_value_stays_silent() -> None:
    """Гейт молчит там, где кандидатов нет: отсутствие записи — не отказ операции."""
    decisions = find_value_duplicates(
        [_claim("Наблюдаемая населением инфляция в декабре 2025 года — 14,5%.")], [_candidate()]
    )
    assert decisions == []
    # и форма с известным текстом кандидата тоже не склеивается без опоры
    silent = find_value_duplicates([_claim(NEW_OBSERVED_CLAIM)], [_candidate()])
    assert silent == []
