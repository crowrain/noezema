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

T7.88a (дополнение ADR-0035 §12): частичное пересечение словаря показателя НЕ доказывает,
что показатель другой, — поэтому строка «смежный показатель» больше не утверждает
различие (она переименована в нейтральную «связанную запись»), появилась категория
«запись, вероятно, о том же показателе» (период совместим, значение совпадает с точностью
до округления), связанных строк не больше трёх, остаток сворачивается в одну строку со
счётчиком, а честная строка обязательна всегда, когда в подразделе нет ни одной
независимой оценки. Формулировки взяты дословно из дампа карточки стенда —
`fixtures-t788a/claims_live.tsv` (10 утверждений карт 7ceb8047 и 22d1748e); id в
комментариях — первые 8 знаков uuid той записи.
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
# ─── T7.88a: четыре формулировки той же карты 7ceb8047 дословно из claims_live.tsv ──
PRESS_73D52835 = "В пресс-релизе Банка России (event id 28251) годовая инфляция в 2025 году указана как 5,6%."
SBER_C970BC08 = (
    "Независимые оценки Сбера/СберCIB совпали с официальной цифрой Росстата: инфляция в России по "
    "итогам 2025 года составила 5,6% — расхождений с официальными данными нет"
)
COMMENT_E2C52955 = (
    "В информационно-аналитическом комментарии Банка России «Инфляция в России» № 12 (120) за декабрь "
    "2025 г. годовая инфляция за 2025 год приведена как 5,6%."
)
FACT_BELOW_FORECAST_7CC2B578 = (
    "Фактическая годовая инфляция в России по итогам 2025 года (5,59%) оказалась ниже всех прогнозов "
    "аналитиков: прогноз Банка России — 6,5–7%, Минэкономразвития — 6,8%, консенсус-прогноз ЦБ — 6,6%"
)

# даты как на стенде (claims_live.tsv: as_of есть не у всех записей)
AS_OF_SOURCE = 2026  # 9266248e: as_of 2026-01-21
AS_OF_OBSERVED = 2025  # 391c4388, e189c80d, 6d9a12ff: 2025-12-31
AS_OF_FORECAST = 2025  # 2d010d53, 58e2d5d4: 2025-12-24

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

# ─── дословные строки T7.88a (собранные сервером; закреплены и в сценарном слое) ──
ROW_LIKELY_PRESS = (
    "Запись, вероятно, о том же показателе: В пресс-релизе Банка России (event id 28251) годовая "
    "инфляция в 2025 году…, значение 5,6% совпадает с 5,59% после округления; независимость не "
    "установлена."
)
ROW_LIKELY_SBER = (
    "Запись, вероятно, о том же показателе: Независимые оценки Сбера/СберCIB совпали с официальной "
    "цифрой Росстата:…, значение 5,6% совпадает с 5,59% после округления; независимость не установлена."
)
ROW_LIKELY_COMMENT = (
    "Запись, вероятно, о том же показателе: В информационно-аналитическом комментарии Банка России "
    "«Инфляция в России» № 12…, значение 5,6% совпадает с 5,59% после округления; независимость не "
    "установлена."
)
ROW_LINKED_OBSERVED = (
    "Связанная запись: Наблюдаемая населением годовая инфляция в России в декабре 2025 года "
    "составила… — показатель автоматически не сопоставлен; независимым измерением не считается."
)
ROW_LINKED_MEDIAN = (
    "Связанная запись: Медианная оценка годовой наблюдаемой инфляции населением России в декабре "
    "2025… — показатель автоматически не сопоставлен; независимым измерением не считается."
)
ROW_FORECAST_7CC2B578 = (
    "Смежный прогноз, не измерение: Фактическая годовая инфляция в России по итогам 2025 года "
    "(5,59%) оказалась… — прогноз реализованного значения; независимым измерением этого показателя он "
    "не является."
)
ROW_OVERFLOW_4 = "Ещё связанных записей: 4."
ROW_FORECAST_2D010D53 = (
    "Смежный прогноз, не измерение: Прогноз аналитиков по годовой инфляции в России на конец 2025 "
    "года по данным… — прогноз реализованного значения; независимым измерением этого показателя он "
    "не является."
)


def _stand_candidates() -> list[estimates.EstimateCandidate]:
    """Все утверждения карты 7ceb8047, кроме самого `9266248e`, — дословно из claims_live.tsv.
    id кандидатов начинаются как настоящие uuid записей, поэтому порядок подборки (по id)
    совпадает с порядком стенда."""
    return [
        _candidate("c-2d010d53", FORECAST_2D010D53, as_of_year=AS_OF_FORECAST),
        _candidate("c-391c4388", OBSERVED_391C4388, as_of_year=AS_OF_OBSERVED),
        _candidate("c-58e2d5d4", FORECAST_2D010D53, as_of_year=AS_OF_FORECAST),
        _candidate("c-6d9a12ff", MEDIAN_6D9A12FF, as_of_year=AS_OF_OBSERVED),
        _candidate("c-73d52835", PRESS_73D52835),
        _candidate("c-7cc2b578", FACT_BELOW_FORECAST_7CC2B578),
        _candidate("c-c970bc08", SBER_C970BC08),
        _candidate("c-e189c80d", OFFICIAL_E189C80D, as_of_year=AS_OF_OBSERVED),
        _candidate("c-e2c52955", COMMENT_E2C52955),
    ]


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
    # T7.88a: такая запись — не независимая оценка, поэтому честная строка обязана остаться:
    # соседство с дублирующей записью того же показателя её не отменяет.
    assert _rows(source, candidate) == [
        (
            "other_record",
            "Другая запись того же показателя: Независимая оценка: годовая инфляция в России "
            "по итогам 2025 года (декабрь 2025…, значение 7,9% — расходится на 2,31 п.п.",
        ),
        ("no_independent", NO_INDEPENDENT),
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
    # T7.88a: «другая запись» независимой оценкой не становится — честная строка положена рядом
    assert [kind for kind, _text in rows] == ["other_record", "no_independent"]


def test_stand_pair_official_duplicate_is_other_record_matching_value() -> None:
    """Стендовая пара `e189c80d`/`9266248e` (карта 22d1748e): тот же показатель и значение,
    но записанных групп/производителей нет → независимость не выводится → «другая запись».
    T7.88a: этого рядом стоящего дубля недостаточно — независимого измерения по-прежнему нет,
    и честная строка обязана остаться."""
    rows = _rows(_source(OFFICIAL_E189C80D), _candidate("c-926", CPI_9266248E))
    assert rows == [
        (
            "other_record",
            "Другая запись того же показателя: Годовая инфляция в России по итогам 2025 года "
            "(декабрь 2025 к декабрю 2024)…, значение 5,59% — совпадает с 5,59%",
        ),
        ("no_independent", NO_INDEPENDENT),
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
    assert [kind for kind, _text in rows] == ["other_record", "no_independent"]


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
    """`391c4388` (14,5% наблюдаемой инфляции) для `9266248e` (ИПЦ 5,59%): связанная запись.
    T7.88a: словарь показывает лишь частичное пересечение — называть показатель ДРУГИМ витрина
    не вправе (значение 14,5% не округляется до 5,59%, записи разного типа), поэтому строка
    нейтральная и слова «другой показатель» в ней нет."""
    rows = _rows(_source(), _candidate("c-391", OBSERVED_391C4388, as_of_year=AS_OF_OBSERVED))
    assert rows == [
        ("adjacent_metric", ROW_LINKED_OBSERVED),
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
    """Стендовая карта 7ceb8047 в чистом виде (две связанные строки из десяти утверждений):
    прогноз идёт раньше нейтрально связанной записи, честная строка — последней."""
    rows = _rows(
        _source(as_of_year=AS_OF_SOURCE),
        _candidate("c-391", OBSERVED_391C4388, as_of_year=AS_OF_OBSERVED),
        _candidate("c-2d0", FORECAST_2D010D53, as_of_year=AS_OF_FORECAST),
    )
    kinds = [kind for kind, _text in rows]
    assert kinds == ["forecast_adjacent", "adjacent_metric", "no_independent"]
    assert [text for _kind, text in rows] == [ROW_FORECAST_2D010D53, ROW_LINKED_OBSERVED, NO_INDEPENDENT]


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
    assert [kind for kind, _text in rows] == ["other_record", "no_independent"]


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
    """Закрытый набор категорий строки. Порядок = приоритет подбора и он же порядок показа:
    независимая оценка → другая запись того же показателя → вероятно тот же показатель →
    прогноз → связанная запись → строка-счётчик → честная строка (ADR-0035 §12)."""
    assert estimates.ESTIMATE_ROW_KEYS == (
        "independent",
        "other_record",
        "likely_same_indicator",
        "forecast_adjacent",
        "adjacent_metric",
        "related_overflow",
        "no_independent",
    )


def test_no_row_and_no_label_claim_another_indicator() -> None:
    """Словарная смежность — не доказательство другого показателя (ADR-0035 §12): ни одна строка
    подраздела и ни одна подпись категории не утверждает, что запись «о другом показателе».
    Проверено на обеих стендовых картах целиком (9 утверждений против `9266248e` и 8 против
    `391c4388`). Различие признаётся только записанной меткой показателя — тогда строка молчит."""
    from apps.web.labels import describe

    for key in estimates.ESTIMATE_ROW_KEYS:
        entry = describe("estimate_row", key)
        for field in ("label", "hint", "action"):
            assert "другой показатель" not in entry[field], (key, field)

    def _texts(source: estimates.EstimateSource) -> list[str]:
        others = [c for c in _stand_candidates() if c.statement != source.statement]
        return [text for _kind, text in _rows(source, *others)]

    for text in _texts(_source(as_of_year=AS_OF_SOURCE)):
        assert "другой показатель" not in text
    for text in _texts(_source(OBSERVED_391C4388, as_of_year=AS_OF_OBSERVED)):
        assert "другой показатель" not in text


# ─── T7.88a (1): частичная смежность словаря НЕ доказывает другой показатель ──

def test_stand_press_release_record_is_likely_the_same_indicator() -> None:
    """`73d52835` (claims_live.tsv): «В пресс-релизе Банка России … 5,6%» для `9266248e`
    (5,59%). Пересечение словаря 2/9 — ниже гейта T7.83a, но период совместим и значение
    является округлением значения карточки до меньшего числа знаков → «вероятно, тот же
    показатель» с явной оговоркой про независимость, а не утверждение о другом показателе."""
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), _candidate("c-73d52835", PRESS_73D52835))
    assert rows == [("likely_same_indicator", ROW_LIKELY_PRESS), ("no_independent", NO_INDEPENDENT)]


def test_sber_retelling_of_the_official_figure_is_likely_the_same_indicator() -> None:
    """`c970bc08`: «Независимые оценки Сбера/СберCIB совпали с официальной цифрой Росстата: …
    5,6%». По правилам T7.88 эта запись НЕ могла попасть в «другая запись того же показателя»
    (пересечение словаря 1/4 ниже 3/5) и не может называться независимой оценкой — записанных
    групп и производителей нет. Честный вывод: вероятно тот же показатель, округлённый до 5,6%.
    Слово «независимые» из самой формулировки витрина не переносит в статус записи."""
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), _candidate("c-c970bc08", SBER_C970BC08))
    assert rows == [("likely_same_indicator", ROW_LIKELY_SBER), ("no_independent", NO_INDEPENDENT)]


def test_cbr_comment_record_is_likely_the_same_after_rounding() -> None:
    """`e2c52955`: комментарий Банка России «Инфляция в России» № 12 (120) — годовая инфляция
    за 2025 год приведена как 5,6%. Значение одно и оно округляется до значения карточки →
    та же категория; независимость не устанавливается: записанных групп у этой оценки нет."""
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), _candidate("c-e2c52955", COMMENT_E2C52955))
    assert rows == [("likely_same_indicator", ROW_LIKELY_COMMENT), ("no_independent", NO_INDEPENDENT)]


def test_equal_value_in_the_adjacency_band_needs_no_rounding_word() -> None:
    """Тот же кейс без округления: значение кандидата равно значению карточки (5,59%) —
    оговорка та же, слово «округление» не появляется."""
    candidate = PRESS_73D52835.replace("как 5,6%", "как 5,59%")
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), _candidate("c-73d-eq", candidate))
    assert len(rows) == 2
    assert rows[0][0] == "likely_same_indicator"
    assert rows[0][1].endswith("значение 5,59% совпадает с 5,59%; независимость не установлена.")
    assert "округлен" not in rows[0][1]


def test_value_that_does_not_round_to_the_source_stays_a_related_record() -> None:
    """`73d52835` с другим значением (5,7%): 5,59 → 5,6 при округлении до одного знака, а не 5,7.
    Значит это не «тот же показатель с точностью до округления» — остаётся нейтральная
    связанная запись без утверждений о значении."""
    candidate = PRESS_73D52835.replace("как 5,6%", "как 5,7%")
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), _candidate("c-73d-57", candidate))
    assert [kind for kind, _text in rows] == ["adjacent_metric", "no_independent"]


def test_several_decimal_places_more_than_the_constant_is_not_rounding() -> None:
    """Правило округления ограничено константой: 5,58715 (5 знаков) против 5,59 (2 знака) —
    разница в 3 знака. Называть такое «тем же значением» нельзя, строка остаётся нейтральной."""
    candidate = PRESS_73D52835.replace("как 5,6%", "как 5,58715%")
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), _candidate("c-73d-far", candidate))
    assert [kind for kind, _text in rows] == ["adjacent_metric", "no_independent"]
    assert estimates.ROUNDING_MAX_PLACE_GAP == 2


def test_multi_valued_record_is_never_called_the_same_indicator() -> None:
    """`7cc2b578` (claims_live.tsv) содержит и 5,59%, и четыре прогноза: выбрать, какое из
    значений относится к показателю карточки, нельзя → «вероятно тот же показатель» не
    утверждается. Формулировка при этом прогнозная — остаётся отдельной категорией прогноза."""
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), _candidate("c-7cc2b578", FACT_BELOW_FORECAST_7CC2B578))
    assert rows == [("forecast_adjacent", ROW_FORECAST_7CC2B578), ("no_independent", NO_INDEPENDENT)]


def test_multi_valued_record_without_forecast_stays_related() -> None:
    """`6d9a12ff` (медианная оценка наблюдаемой инфляции, 13,1% и 15,6%): ни одного значения,
    совпадающего с 5,59%, и несколько значений в формулировке → нейтральная связанная запись."""
    rows = _rows(
        _source(as_of_year=AS_OF_SOURCE),
        _candidate("c-6d9a12ff", MEDIAN_6D9A12FF, as_of_year=AS_OF_OBSERVED),
    )
    assert rows == [("adjacent_metric", ROW_LINKED_MEDIAN), ("no_independent", NO_INDEPENDENT)]


def test_declared_different_metrics_block_the_likely_same_row() -> None:
    """Записанная метка показателя сильнее формулировки: если обе оценки объявили РАЗНЫЕ метки,
    витрина не имеет права называть запись «вероятно тем же показателем» даже при значении,
    совпадающем с точностью до округления (5,6% против 5,59%)."""
    source = _source(metric="годовая инфляция")
    candidate = _candidate(
        "c-metric",
        "Наблюдаемая населением годовая инфляция в России по итогам 2025 года составила 5,6%.",
        metric="наблюдаемая инфляция",
    )
    rows = _rows(source, candidate)
    kinds = [kind for kind, _text in rows]
    assert kinds == ["adjacent_metric", "no_independent"]
    assert rows[0][1].startswith("Связанная запись:")
    assert "вероятно" not in rows[0][1]


# ─── T7.88a (2): честная строка обязательна, когда независимой оценки нет ──────

def test_other_record_alone_does_not_cancel_the_honest_row() -> None:
    """Стендовый дефект карты 7ceb8047: у `9266248e` была «другая запись того же показателя»
    (`e189c80d`, тот же производитель), но строки «нет независимых измерений» не было.
    Дубль того же кластера независимой оценкой не является — честная строка обязана быть."""
    rows = _rows(
        _source(as_of_year=AS_OF_SOURCE),
        _candidate("c-e189c80d", OFFICIAL_E189C80D, as_of_year=AS_OF_OBSERVED),
    )
    assert [kind for kind, _text in rows] == ["other_record", "no_independent"]
    assert rows[-1][1] == NO_INDEPENDENT


def test_related_and_forecast_rows_do_not_cancel_the_honest_row() -> None:
    """Ни одной независимой оценки нет — честная строка стоит последней, даже когда подраздел
    заполнен связанными записями и прогнозами."""
    rows = _rows(
        _source(as_of_year=AS_OF_SOURCE),
        _candidate("c-391c4388", OBSERVED_391C4388, as_of_year=AS_OF_OBSERVED),
        _candidate("c-2d010d53", FORECAST_2D010D53, as_of_year=AS_OF_FORECAST),
    )
    assert any(row[1].startswith("Связанная запись:") for row in rows)
    assert rows[-1] == ("no_independent", NO_INDEPENDENT)


def test_honest_row_is_never_cut_by_the_related_cap() -> None:
    """Потолок связанных строк (3 + строка-счётчик) не должен вытеснять честную строку."""
    candidates = [
        _candidate(
            f"c-many-{index}",
            f"Оценка наблюдаемой инфляции в России, выпуск {index}: годовая инфляция "
            f"населением по итогам 2025 года составила {13 + index},{index}%.",
            as_of_year=AS_OF_OBSERVED,
        )
        for index in range(7)
    ]
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), *candidates)
    kinds = [kind for kind, _text in rows]
    assert kinds.count("adjacent_metric") == estimates.MAX_ADJACENT_ROWS_PER_CLAIM
    assert "related_overflow" in kinds and "no_independent" in kinds
    assert rows[-1] == ("no_independent", NO_INDEPENDENT)


# ─── T7.88a (3): потолок связанных строк и строка-счётчик вместо шума ────────

def test_stand_card_rows_are_capped_and_the_rest_is_counted() -> None:
    """Карта 7ceb8047 целиком (9 утверждений из claims_live.tsv против `9266248e`):
    независимых оценок нет; другая запись того же показателя — вне потолка; связанных строк
    не больше трёх, приоритет «вероятно тот же → прогноз → связанная»; остаток — одна строка
    со счётчиком без текстов записей; честная строка последняя."""
    rows = _rows(_source(as_of_year=AS_OF_SOURCE), *_stand_candidates())
    kinds = [kind for kind, _text in rows]
    assert kinds == [
        "other_record",
        "likely_same_indicator",
        "likely_same_indicator",
        "likely_same_indicator",
        "related_overflow",
        "no_independent",
    ]
    assert rows[0][1].startswith("Другая запись того же показателя: Официальная годовая инфляция")
    assert [row[1] for row in rows[1:4]] == [ROW_LIKELY_PRESS, ROW_LIKELY_SBER, ROW_LIKELY_COMMENT]
    assert rows[4][1] == ROW_OVERFLOW_4
    assert rows[-1][1] == NO_INDEPENDENT


def test_overflow_row_carries_only_a_count() -> None:
    """Строка-счётчик не цитирует формулировки и не указывает запись: это ровно число."""
    result = estimates.estimate_rows_for_source(
        _source(as_of_year=AS_OF_SOURCE), list(_stand_candidates())
    )
    overflow = [row for row in result if row.kind == "related_overflow"]
    assert len(overflow) == 1
    assert overflow[0].candidate_id is None
    assert overflow[0].text == ROW_OVERFLOW_4
    assert "…" not in overflow[0].text


def test_observed_inflation_claim_is_capped_the_same_way() -> None:
    """`391c4388` (14,5% наблюдаемой инфляции) против тех же девяти записей: независимых
    измерений нет → связанных строк три (прогнозы приоритетнее нейтральных записей),
    остаток — счётчик, честная строка последняя."""
    candidates = [c for c in _stand_candidates() if c.claim_id != "c-391c4388"]
    # в реальном пуле карты есть и запись `9266248e`: в юнит-слое она — сам источник,
    # поэтому здесь её добавляю явно, чтобы подсчёт скрытых записей совпал с картой
    candidates.append(_candidate("c-9266248e", CPI_9266248E, as_of_year=AS_OF_SOURCE))
    rows = _rows(_source(OBSERVED_391C4388, as_of_year=AS_OF_OBSERVED), *candidates)
    kinds = [kind for kind, _text in rows]
    assert kinds == [
        "forecast_adjacent",
        "forecast_adjacent",
        "adjacent_metric",
        "related_overflow",
        "no_independent",
    ]
    assert [row[1] for row in rows[:3]] == [ROW_FORECAST_2D010D53, ROW_FORECAST_7CC2B578, ROW_LINKED_MEDIAN]
    assert rows[3][1] == ROW_OVERFLOW_4


def test_related_cap_constant() -> None:
    assert estimates.MAX_ADJACENT_ROWS_PER_CLAIM == 3
