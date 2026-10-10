"""Unit: подбор «Независимых оценок» в карточке ответа (T7.88, ADR-0035 вариант D).

Проверяется ЧИСТАЯ функция `apps.web.estimates.estimate_rows_for_source`: без базы и
без LLM — только записанные факты (формулировки, метки assessed_scope, снимок и группы
независимости, имена производителей). Тексты строк закреплены дословно: строку собирает
сервер из словаря подписей, витрина печатает её как есть.

Опорные формулировки — настоящие утверждения стенда .92 (7ceb8047 / 22d1748e):
`9266248e` (официальная годовая инфляция 5,59%), `391c4388` (наблюдаемая населением
инфляция 14,5%, опрос инФОМ), `2d010d53` (прогноз аналитиков 6,3%). Гипотетические
кейсы повторяют ADR-0035 §6.3 (вторая группа, значение совпадает) и §6.4 (независимая
оценка расходится).
"""

from __future__ import annotations

import pytest

from apps.web import estimates

pytestmark = pytest.mark.unit

# ─── опорные данные: подлинные формулировки стенда .92 ──────────────────────

CPI_9266248E = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
    "составила 5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)
OBSERVED_391C4388 = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила "
    "14,5% (по данным опроса инФОМ для Банка России)."
)
FORECAST_2D010D53 = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
    "декабрьского макроэкономического опроса Банка России составил 6,3%."
)
MEDIAN_6D9A12FF = (
    "Медианная оценка годовой наблюдаемой инфляции населением России в декабре 2025 года "
    "составила 13,1% для граждан с накоплениями и 15,6% для граждан без накоплений "
    "(по данным опроса «инФОМ»)."
)
OFFICIAL_E189C80D = (
    "Официальная годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
    "составила 5,59% (по данным Росстата)."
)

METRIC = "годовая инфляция"


def _source(statement: str = CPI_9266248E, **overrides: object) -> estimates.EstimateSource:
    fields: dict[str, object] = {
        "claim_id": "src-0001",
        "statement": statement,
        "metric": None,
        "as_of_year": None,
        "producers": frozenset(),
        "snapshot_id": None,
        "groups": frozenset(),
    }
    fields.update(overrides)
    return estimates.EstimateSource(**fields)  # type: ignore[arg-type]


def _candidate(claim_id: str, statement: str, **overrides: object) -> estimates.EstimateCandidate:
    fields: dict[str, object] = {
        "claim_id": claim_id,
        "statement": statement,
        "metric": None,
        "as_of_year": None,
        "producers": frozenset(),
        "snapshot_id": None,
        "groups": frozenset(),
    }
    fields.update(overrides)
    return estimates.EstimateCandidate(**fields)  # type: ignore[arg-type]


def _rows(source: estimates.EstimateSource, *candidates: estimates.EstimateCandidate) -> list[tuple[str, str]]:
    return [(row.kind, row.text) for row in estimates.estimate_rows_for_source(source, list(candidates))]


NO_INDEPENDENT = "В знаниях системы нет независимых измерений этого показателя за этот период."

# ─── тот же показатель и период + независимость выводится → независимая оценка ──

def test_same_indicator_other_group_matching_value_is_independent() -> None:
    """ADR-0035 §6.3: вторая группа независимости, то же значение — «совпадает с»."""
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 5,59%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    assert _rows(source, candidate) == [
        (
            "independent",
            "Независимая оценка того же показателя: Независимая оценка: годовая инфляция в России "
            "по итогам 2025 года (декабрь 2025…, значение 5,59% — совпадает с 5,59%",
        )
    ]


def test_same_indicator_other_group_diverging_value_names_the_gap_in_pp() -> None:
    """ADR-0035 §6.4: независимая оценка 7,9% против 5,59% — расхождение названо открыто."""
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 7,9%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    assert _rows(source, candidate) == [
        (
            "independent",
            "Независимая оценка того же показателя: Независимая оценка: годовая инфляция в России "
            "по итогам 2025 года (декабрь 2025…, значение 7,9% — расходится на 2,31 п.п.",
        )
    ]


def test_disjoint_named_producers_without_groups_are_independent() -> None:
    """Независимость выводится и из записанных производителей: разные имена — не один кластер."""
    source = _source(metric=METRIC, producers=frozenset({"Росстат"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 7,9%.",
        metric=METRIC,
        producers=frozenset({"Инфомир"}),
    )
    rows = _rows(source, candidate)
    assert len(rows) == 1
    assert rows[0][0] == "independent"


# ─── тот же показатель, но независимость не выводится → «другая запись» без слова «независимая» ──

def test_same_group_record_is_other_record_not_independent() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 7,9%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g0"}),
    )
    assert _rows(source, candidate) == [
        (
            "other_record",
            "Другая запись того же показателя: Независимая оценка: годовая инфляция в России "
            "по итогам 2025 года (декабрь 2025…, значение 7,9% — расходится на 2,31 п.п.",
        )
    ]


def test_same_named_producer_is_other_record() -> None:
    """Кластер вокруг одного производителя (случай T7.87) не даёт независимой оценки."""
    source = _source(metric=METRIC, producers=frozenset({"Росстат"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 7,9%.",
        metric=METRIC,
        producers=frozenset({"Росстат"}),
    )
    rows = _rows(source, candidate)
    assert [kind for kind, _text in rows] == ["other_record"]


def test_stand_pair_official_duplicate_is_other_record_matching_value() -> None:
    """Стендовая пара `e189c80d`/`9266248e` (карта 22d1748e): тот же показатель и значение,
    но записанных групп/производителей нет → независимость не выводится → «другая запись»."""
    rows = _rows(_source(OFFICIAL_E189C80D), _candidate("c-926", CPI_9266248E))
    assert rows == [
        (
            "other_record",
            "Другая запись того же показателя: Годовая инфляция в России по итогам 2025 года "
            "(декабрь 2025 к декабрю 2024)…, значение 5,59% — совпадает с 5,59%",
        )
    ]


def test_different_snapshot_groups_are_not_compared() -> None:
    """Группы разных снимков независимости несопоставимы: сравнение групп молчит честно."""
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 7,9%.",
        metric=METRIC,
        snapshot_id="snap-2",
        groups=frozenset({"g1"}),
    )
    rows = _rows(source, candidate)
    assert [kind for kind, _text in rows] == ["other_record"]


# ─── прогноз — не независимое измерение (explorer-v9 правило 12) ─────────────

def test_forecast_candidate_is_marked_and_never_independent() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Прогноз: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 6,3%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    assert _rows(source, candidate) == [
        (
            "forecast_adjacent",
            "Смежный прогноз, не измерение: Прогноз: годовая инфляция в России по итогам 2025 года "
            "(декабрь 2025 к декабрю… — прогноз реализованного значения; независимым измерением "
            "этого показателя он не является.",
        ),
        ("no_independent", NO_INDEPENDENT),
    ]


# ─── смежный показатель (порог ниже гейта T7.83a) — стендовые пары ───────────

def test_stand_pair_observed_inflation_is_adjacent_not_same_indicator() -> None:
    """`391c4388` (14,5% наблюдаемой инфляции) для `9266248e` (ИПЦ 5,59%): смежный показатель."""
    rows = _rows(_source(), _candidate("c-391", OBSERVED_391C4388))
    assert rows == [
        (
            "adjacent_metric",
            "Смежный показатель: Наблюдаемая населением годовая инфляция в России в декабре 2025 "
            "года составила… — другой показатель, не является независимым измерением этого показателя.",
        ),
        ("no_independent", NO_INDEPENDENT),
    ]


def test_stand_pair_forecast_is_adjacent_with_forecast_mark() -> None:
    """`2d010d53` (прогноз 6,3%) для `9266248e`: смежный прогноз с явной пометкой.»"""
    rows = _rows(_source(), _candidate("c-2d0", FORECAST_2D010D53))
    assert rows == [
        (
            "forecast_adjacent",
            "Смежный прогноз, не измерение: Прогноз аналитиков по годовой инфляции в России на конец "
            "2025 года по данным… — прогноз реализованного значения; независимым измерением этого "
            "показателя он не является.",
        ),
        ("no_independent", NO_INDEPENDENT),
    ]


def test_stand_card_7ceb8047_shape_has_no_independent_measurement_row() -> None:
    """Стендовая карта 7ceb8047 в чистом виде: для `9266248e` — честная строка + два смежных.»"""
    rows = _rows(_source(), _candidate("c-391", OBSERVED_391C4388), _candidate("c-2d0", FORECAST_2D010D53))
    kinds = [kind for kind, _text in rows]
    assert kinds == ["adjacent_metric", "forecast_adjacent", "no_independent"]


# ─── период и территория ─────────────────────────────────────────────────────

def test_different_period_is_not_shown() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2023 года "
        "(декабрь 2023 к декабрю 2022) — 7,4%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    assert _rows(source, candidate) == [("no_independent", NO_INDEPENDENT)]


def test_partial_period_overlap_is_not_shown() -> None:
    """Частичное пересечение годов (ни одно множество не накрывает другое) — conservative-молчание."""
    source = _source(
        "Годовая инфляция в России по итогам 2025 года составила 5,59%.",
        metric=METRIC,
        as_of_year=2026,
    )
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2024 года составила 7,4%.",
        metric=METRIC,
        as_of_year=2025,
    )
    assert _rows(source, candidate) == [("no_independent", NO_INDEPENDENT)]


def test_period_covered_by_candidate_years_is_comparable() -> None:
    """Зеркальный случай: период источника целиком внутри периода кандидата — сопоставимо."""
    source = _source(
        "Годовая инфляция в России по итогам 2024 года составила 7,4%.",
        metric=METRIC,
        as_of_year=2025,
    )
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 5,59%.",
        metric=METRIC,
    )
    rows = _rows(source, candidate)
    assert [kind for kind, _text in rows] == ["other_record"]


def test_as_of_year_alone_gives_the_period() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам того же периода — 7,9%.",
        metric=METRIC,
        as_of_year=2025,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    rows = _rows(source, candidate)
    assert [kind for kind, _text in rows] == ["independent"]


def test_different_territory_is_not_shown_even_with_equal_metrics() -> None:
    """При равных объявленных метках `indicator_check` территорию не смотрит — guard подборщика
    обязан отклонить другой регион (иначе «независимая оценка» про другую страну)."""
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в Казахстане по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 12,1%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    assert _rows(source, candidate) == [("no_independent", NO_INDEPENDENT)]


# ─── значения и единицы ──────────────────────────────────────────────────────

def test_percent_word_counts_as_percent() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 7,5 процента.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    rows = _rows(source, candidate)
    assert rows[0][1].endswith("значение 7,5% — расходится на 1,91 п.п.")


def test_mixed_units_name_no_number() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 7,9 балла.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    rows = _rows(source, candidate)
    assert rows[0][1].endswith("значение 7,9 — расходятся")


def test_non_percent_units_name_no_number() -> None:
    source = _source(CPI_9266248E.replace("5,59%", "5,59 балла"), metric=METRIC)
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 7,9 балла.",
        metric=METRIC,
    )
    rows = _rows(source, candidate)
    assert rows[0][1].endswith("значение 7,9 — расходятся")


def test_several_values_are_not_compressed_into_one_number() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Независимая оценка: годовая инфляция в России по итогам 2025 года "
        "(декабрь 2025 к декабрю 2024) — 13,1% и 15,6%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    rows = _rows(source, candidate)
    assert rows[0][1].endswith("значение 13,1%, 15,6% — единицы значений неясны")


def test_candidate_without_decimal_value_is_ignored() -> None:
    """Целые без разделителя («13%») — не существенное значение гейта T7.83a: запись не участвует."""
    rows = _rows(_source(), _candidate("c-raw", "Годовая инфляция в России по итогам 2025 года выросла на 13%."))
    assert rows == [("no_independent", NO_INDEPENDENT)]


# ─── conservative-молчание и границы текста ──────────────────────────────────

def test_unsafe_snippet_candidate_is_dropped_entirely() -> None:
    """Формулировку со словом «проверено» (без отрицания) цитировать нельзя — запись исключается,
    и честная строка остаётся единственной (подраздел не утверждает проверку: ловушка T7.65)."""
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
        "проверена ведомством — 7,9%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    assert _rows(source, candidate) == [("no_independent", NO_INDEPENDENT)]


def test_hex_like_fragment_is_not_cited() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) — 7,9%. "
        "код 3f9ab12c4d.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    assert _rows(source, candidate) == [("no_independent", NO_INDEPENDENT)]


def test_negated_verification_word_is_citable() -> None:
    """«не проверена» — отрицание, утверждением проверки оно не становится (тот же lookbehind, что в тесте карточки)."""
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidate = _candidate(
        "cand-1",
        "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
        "не проверена — 7,9%.",
        metric=METRIC,
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    rows = _rows(source, candidate)
    assert [kind for kind, _text in rows] == ["independent"]


# ─── дубликаты, потолок и порядок ────────────────────────────────────────────

def test_self_candidate_is_never_shown() -> None:
    source = _source(metric=METRIC)
    assert _rows(source, _candidate("src-0001", CPI_9266248E, metric=METRIC)) == [
        ("no_independent", NO_INDEPENDENT)
    ]


def test_identical_statements_yield_a_single_row() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    duplicated = [
        _candidate(
            f"cand-{index}",
            "Независимая оценка: годовая инфляция в России по итогам 2025 года "
            "(декабрь 2025 к декабрю 2024) — 7,9%.",
            metric=METRIC,
            snapshot_id="snap-1",
            groups=frozenset({"g1"}),
        )
        for index in range(3)
    ]
    rows = _rows(source, *duplicated)
    assert len(rows) == 1


def test_row_cap_and_category_order() -> None:
    source = _source(metric=METRIC, snapshot_id="snap-1", groups=frozenset({"g0"}))
    candidates = []
    for index in range(9):
        group = "g1" if index < 4 else "g0"
        candidates.append(
            _candidate(
                f"cand-{index}",
                f"Независимая оценка номер {index}: годовая инфляция в России по итогам 2025 года "
                f"(декабрь 2025 к декабрю 2024) — {7 + index % 3},{index + 1}%.",
                metric=METRIC,
                snapshot_id="snap-1",
                groups=frozenset({group}),
            )
        )
    rows = _rows(source, *candidates)
    assert len(rows) == estimates.MAX_ESTIMATE_ROWS_PER_CLAIM
    kinds = [kind for kind, _text in rows]
    assert kinds.count("independent") == 4 and kinds[4:] == ["other_record", "other_record"]
    # честная строка не добавляется: записи того же показателя за этот период найдены
    assert "no_independent" not in kinds


# ─── ключевые константы модуля (обоснование — ADR-0035 §11 и STATUS T7.88) ──

def test_adjacent_threshold_is_strictly_below_the_duplicate_gate() -> None:
    from fractions import Fraction

    from apps.orchestrator.value_duplicate import INDICATOR_OVERLAP_MIN

    assert estimates.ADJACENT_OVERLAP_MIN < INDICATOR_OVERLAP_MIN
    # пары стенда (2/9 и 1/5) проходят смежный порог, шум «территория + одно слово» (≤1/7) — нет
    assert Fraction(2, 9) >= estimates.ADJACENT_OVERLAP_MIN
    assert Fraction(1, 5) >= estimates.ADJACENT_OVERLAP_MIN
    assert Fraction(1, 7) < estimates.ADJACENT_OVERLAP_MIN


def test_row_kinds_are_closed_set() -> None:
    assert estimates.ESTIMATE_ROW_KEYS == (
        "independent",
        "other_record",
        "adjacent_metric",
        "forecast_adjacent",
        "no_independent",
    )
