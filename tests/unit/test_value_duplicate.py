"""T7.83 + T7.83a: чистый детектор «дубля по значению» (хостовый гейт кураторского
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

T7.83a (замечание приёмки, ADR-0033 §7) добавляет два условия СКЛЕЙКИ:

* кандидат обязан быть объявлен куратором в `relied_claim_ids` (иначе хост решает
  за модель, к какому из видимых утверждений относится его вывод); подсчёт
  «ровно один кандидат» — после этого фильтра;
* это обязан быть ТОТ ЖЕ ПОКАЗАТЕЛЬ: либо совпадают объявленные метки scope
  (`scope.metric` и синонимы), либо формулировки разделяют словарь показателя
  не слабее `INDICATOR_OVERLAP_MIN`, и они не называют разные территории.

Ровно один кандидат → дубль; ноль или несколько → операция не трогается,
причина записывается честно. Пары фикстуры: e189c80d↔9266248e — дубль;
391c4388↔6d9a12ff — НЕ дубль (и по типу, и по значению).
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from apps.orchestrator.value_duplicate import (
    INDICATOR_OVERLAP_MIN,
    IndicatorCheck,
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    declared_scope_metric,
    find_value_duplicates,
    indicator_check,
    statement_indicator_words,
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
    metric: str | None = None,
) -> ValueDuplicateClaim:
    return ValueDuplicateClaim(
        index=index,
        statement=statement,
        claim_type=claim_type,
        as_of_year=as_of_year,
        has_support_link=supports,
        has_counter_link=counters,
        metric=metric,
    )


def _candidate(
    claim_id: str,
    statement: str = CANDIDATE_OFFICIAL,
    claim_type: str = "temporal_fact",
    as_of_year: int | None = 2026,
    relied: bool = False,
    metric: str | None = None,
) -> ValueDuplicateCandidate:
    return ValueDuplicateCandidate(
        claim_id=claim_id,
        statement=statement,
        claim_type=claim_type,
        as_of_year=as_of_year,
        relied=relied,
        metric=metric,
    )


def _overlap(claim_text: str, candidate_text: str) -> Fraction:
    """Пересечение словарей показателя как ТОЧНОЕ рациональное число (тот же
    знаменатель, что считает гейт: объединение значимых основ)."""
    claim_words = statement_indicator_words(claim_text)
    candidate_words = statement_indicator_words(candidate_text)
    return Fraction(len(claim_words & candidate_words), len(claim_words | candidate_words))


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


# ── T7.83a: опора (relied_claim_ids) и сверка показателя ─────────────────────

KEYRATE_CANDIDATE = "Средняя ключевая ставка Банка России за 2025 год составила 13,7%."
EXPECTATIONS_CLAIM = "Инфляционные ожидания населения в декабре 2025 года составили 13,7%."
UNEMPLOYMENT_CLAIM = "Безработица в России в 2025 году — 2,2%."
GDP_CANDIDATE = "Рост ВВП России в 2025 году составил 2,2%."
RUSSIA_INFLATION = "Инфляция в России за 2025 год составила 5,59%."
KAZAKHSTAN_INFLATION = "Инфляция в Казахстане за 2025 год составила 5,59%."
OBSERVED_RUSSIA = "Наблюдаемая населением инфляция в России в декабре 2025 года составила 14,5%."
OBSERVED_BELARUS = "Наблюдаемая населением инфляция в Беларуси в декабре 2025 года составила 14,5%."


def test_perfect_value_match_outside_relied_claim_ids_is_left_as_proposed() -> None:
    """Т7.83a: тип, значения и период совпали идеально — этого мало. Куратор не объявил
    кандидата опорой своего ответа, значит хост не имеет права решать за модель, к какому
    из видимых утверждений относится её вывод."""
    decisions = find_value_duplicates([_claim(DUP_CLAIM)], [_candidate("c-1", relied=False)])
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert decisions[0]["target"] is None
    assert "relied_claim_ids" in decisions[0]["reason"]


def test_ambiguity_is_counted_after_the_relied_filter() -> None:
    """«Ровно один кандидат» считается ПОСЛЕ фильтра по опорe: второй совпадающий
    кандидат вне `relied_claim_ids` не создаёт ложной неоднозначности."""
    decisions = find_value_duplicates(
        [_claim(DUP_CLAIM)],
        [
            _candidate("c-1", relied=True),
            _candidate(
                "c-2",
                "Инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) — 5,59% (обзор).",
                relied=False,
            ),
        ],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "reverified"
    assert decisions[0]["target"] == "c-1"


def test_two_relied_candidates_stay_ambiguous() -> None:
    """Опора на два подходящих кандидата — это выбор модели, а не хоста: склейки нет."""
    decisions = find_value_duplicates(
        [_claim(DUP_CLAIM)],
        [
            _candidate("c-1", CANDIDATE_OFFICIAL, relied=True),
            _candidate(
                "c-2",
                "Инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) — 5,59% (Росстат).",
                relied=True,
            ),
        ],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert "ambiguous" in decisions[0]["reason"]


@pytest.mark.parametrize("relied", [True, False])
def test_inflation_expectations_are_not_the_key_rate(relied: bool) -> None:
    """Дефект приёмки T7.83 ровно в той формулировке, в которой он был найден:
    13,7% ожиданий населения ≠ 13,7% средней ключевой ставки."""
    decisions = find_value_duplicates(
        [_claim(EXPECTATIONS_CLAIM)],
        [_candidate("aaaa-keyrate", KEYRATE_CANDIDATE, as_of_year=2025, relied=relied)],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert decisions[0]["target"] is None
    reason = decisions[0]["reason"]
    if relied:
        assert "indicator does not match" in reason
    else:
        assert "relied_claim_ids" in reason


def test_unemployment_and_gdp_with_the_same_number_are_different_indicators() -> None:
    decisions = find_value_duplicates(
        [_claim(UNEMPLOYMENT_CLAIM)],
        [_candidate("c-gdp", GDP_CANDIDATE, as_of_year=2025, relied=True)],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert "indicator does not match" in decisions[0]["reason"]


def test_inflation_of_two_countries_is_not_one_indicator() -> None:
    """Показатель привязан к территории: словарь значимых слов это видит, отдельного
    словаря стран для этой пары не требуется."""
    decisions = find_value_duplicates(
        [_claim(RUSSIA_INFLATION)],
        [_candidate("c-kz", KAZAKHSTAN_INFLATION, as_of_year=2025, relied=True)],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert "territories" in decisions[0]["reason"]


def test_same_indicator_in_two_countries_is_refused_even_at_the_overlap_threshold() -> None:
    """Формулировки различаются ОДНИМ словом и пересекаются ровно на пороге (3/5):
    без словаря территорий склейка была бы «законной» — её отказывает территория."""
    assert _overlap(OBSERVED_RUSSIA, OBSERVED_BELARUS) == Fraction(3, 5)
    assert not indicator_check(OBSERVED_RUSSIA, OBSERVED_BELARUS).matched
    decisions = find_value_duplicates(
        [_claim(OBSERVED_RUSSIA)],
        [_candidate("c-by", OBSERVED_BELARUS, as_of_year=2025, relied=True)],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert "territories" in decisions[0]["reason"]


def test_same_territory_written_differently_is_not_a_conflict() -> None:
    """«в России» и «в Российской Федерации» — одна территория: конфликта нет."""
    assert indicator_check(RUSSIA_INFLATION, KAZAKHSTAN_INFLATION).matched is False
    assert (
        indicator_check(
            RUSSIA_INFLATION, "Инфляция в Российской Федерации за 2025 год — 5,59%."
        ).matched
        is True
    )


def test_paraphrase_of_the_same_indicator_is_still_merged() -> None:
    """Сверка показателя не должна убить то, ради чего делался гейт T7.83."""
    decisions = find_value_duplicates(
        [
            _claim(
                "Средняя ключевая ставка Банка России по итогам 2025 года составила 13,7%.",
            )
        ],
        [_candidate("c-1", KEYRATE_CANDIDATE, as_of_year=2026, relied=True)],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "reverified"
    assert decisions[0]["target"] == "c-1"


def test_observed_inflation_paraphrase_is_merged() -> None:
    decisions = find_value_duplicates(
        [_claim(OBSERVED_RUSSIA)],
        [
            _candidate(
                "c-1",
                "Опрос: наблюдаемая населением инфляция в России (декабрь 2025) — 14,5%.",
                as_of_year=2026,
                relied=True,
            )
        ],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "reverified"


def test_counterevidence_and_empty_support_are_refused_even_with_reliance() -> None:
    """Прежние ветки T7.83 не ослаблены: с объявленной опорой отказ всё равно по уликам."""
    countered = find_value_duplicates(
        [_claim(DUP_CLAIM, supports=False, counters=True)], [_candidate("c-1", relied=True)]
    )
    assert len(countered) == 1 and countered[0]["action"] == "kept"
    assert "counterevidence" in countered[0]["reason"]
    unsupported = find_value_duplicates(
        [_claim(DUP_CLAIM, supports=False)], [_candidate("c-1", relied=True)]
    )
    assert len(unsupported) == 1 and unsupported[0]["action"] == "kept"
    assert "no supporting evidence" in unsupported[0]["reason"]


# ── словарь показателя:.constants и пороги (калибровка ADR-0033 §7) ──────────

CALIBRATION: tuple[tuple[str, str, Fraction, bool], ...] = (
    # реальная пара фикстуры .92 (e189c80d ↔ 9266248e) — склейка ожидается
    (DUP_CLAIM, CANDIDATE_OFFICIAL, Fraction(3, 5), True),
    ("Опрос: наблюдаемая населением инфляция в России (декабрь 2025) — 14,5%.", OBSERVED_RUSSIA,
     Fraction(4, 5), True),
    (KEYRATE_CANDIDATE, "Средняя ключевая ставка Банка России по итогам 2025 года составила 13,7%.",
     Fraction(1), True),
    # отрицательные примеры приёмки — склейки быть не должно
    (EXPECTATIONS_CLAIM, KEYRATE_CANDIDATE, Fraction(0), False),
    # обе формулировки называют одну территорию — одного этого не достаточно
    (UNEMPLOYMENT_CLAIM, GDP_CANDIDATE, Fraction(1, 4), False),
    (RUSSIA_INFLATION, KAZAKHSTAN_INFLATION, Fraction(1, 3), False),
    ("Средняя ставка по вкладам в России за 2025 год составила 13,7%.", KEYRATE_CANDIDATE,
     Fraction(1, 2), False),
    (EXPECTATIONS_CLAIM, "Инфляция в России за декабрь 2025 года составила 13,7%.",
     Fraction(1, 4), False),
    ("Глобальная инфляция по итогам 2025 года составила 5,59%.", RUSSIA_INFLATION,
     Fraction(1, 3), False),
    # ровно на пороге: решает словарь территорий
    (OBSERVED_RUSSIA, OBSERVED_BELARUS, Fraction(3, 5), False),
)


@pytest.mark.parametrize(("claim_text", "candidate_text", "overlap", "matched"), CALIBRATION)
def test_indicator_vocabulary_calibration_table(
    claim_text: str, candidate_text: str, overlap: Fraction, matched: bool
) -> None:
    """Таблица калибровки порога `INDICATOR_OVERLAP_MIN` (ADR-0033 §7): слияние —
    1/1 … 3/5, отказ — 1/2 и ниже; 3/5 — наименьшее значение, отделяющее пару
    фикстуры от ближайшего ошибочного кандидата (1/2)."""
    assert Fraction(3, 5) == INDICATOR_OVERLAP_MIN  # порог — точное рациональное число
    assert _overlap(claim_text, candidate_text) == overlap
    check = indicator_check(claim_text, candidate_text)
    assert isinstance(check, IndicatorCheck)
    assert check.matched is matched


def test_indicator_words_drop_numbers_months_dates_and_service_words() -> None:
    """Значимые слова — только буквы, без значений, дат и связок; территории значимы."""
    assert statement_indicator_words(RUSSIA_INFLATION) == frozenset({"инфля", "росси"})
    assert statement_indicator_words(CANDIDATE_OFFICIAL) == frozenset(
        {"инфля", "росси", "банк", "росст"}
    )
    assert statement_indicator_words("Инфляция в России по итогам 2025 года составила 13,7%.") == (
        frozenset({"инфля", "росси"})
    )


def test_declared_scope_metric_is_read_only_from_indicator_keys() -> None:
    """Метка показателя берётся из свободного scope (`metric`/`indicator`/`показатель`/
    `объект`) и нормализуется; host-scope-v1 (as_of/date_anchor/source_domains) метки
    не несёт → None, и тогда решает словарь формулировок."""
    assert declared_scope_metric({"metric": "  Годовая   инфляция "}) == "годовая инфляция"
    assert (
        declared_scope_metric({"объект": "независимая оценка годовой инфляции"})
        == "независимая оценка годовой инфляции"
    )
    assert declared_scope_metric({"metric": 5, "indicator": "ключевая ставка"}) == "ключевая ставка"
    assert declared_scope_metric({"metric": ""}) is None
    assert (
        declared_scope_metric(
            {"as_of": "2026-01-21", "scope_schema": "host-scope-v1", "source_domains": ["rosstat.gov.ru"]}
        )
        is None
    )
    assert declared_scope_metric({}) is None
    assert declared_scope_metric(None) is None


def test_declared_metrics_must_agree_even_when_wordings_agree() -> None:
    """Метки сильнее формулировок: одинаковый текст с разными метками — разные показатели."""
    decisions = find_value_duplicates(
        [_claim("Средняя ключевая ставка Банка России за 2025 год составила 13,7%.", metric="ставка по вкладам")],
        [_candidate("c-1", KEYRATE_CANDIDATE, as_of_year=2025, relied=True, metric="ключевая ставка")],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "kept"
    assert "scope metric" in decisions[0]["reason"]


def test_agreeing_declared_metrics_allow_thin_wording() -> None:
    """Совпавшие метки разрешают то, что словарь слов не пустил бы (пересечение 1/6)."""
    claim_text = "Потребительские цены в России за 2025 год выросли на 5,59%."
    assert _overlap(claim_text, CANDIDATE_OFFICIAL) == Fraction(1, 6)
    decisions = find_value_duplicates(
        [_claim(claim_text, metric="годовая инфляция")],
        [_candidate("c-1", CANDIDATE_OFFICIAL, relied=True, metric="годовая инфляция")],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "reverified"


def test_missing_metric_on_one_side_leaves_the_vocabulary_to_decide() -> None:
    """У кандидата метки нет (host-scope-v1) — решает словарь формулировок, как и раньше."""
    decisions = find_value_duplicates(
        [_claim(DUP_CLAIM, metric="годовая инфляция")],
        [_candidate("c-1", CANDIDATE_OFFICIAL, relied=True)],
    )
    assert len(decisions) == 1
    assert decisions[0]["action"] == "reverified"
