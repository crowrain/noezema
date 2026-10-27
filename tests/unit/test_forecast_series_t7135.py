"""Unit: серия прогнозов карточки ответа (T7.135, ADR-0035 §22).

База не нужна: витрина — чистая функция над записями одной карточки. Фикстура —
подлинные формулировки стендовой карты `c3-a370a68` (срез менеджера,
`tests/fixtures/t7135/`), где на одном экране лежали четыре прогноза инфляции в
России на конец 2026 года, записанные в разное время: ВТБ от 3 октября 2026 и
ВТБ без даты, SberCIB от 20 октября 2026 и SberCIB от 29 сентября 2026.

Проверяется ровно то, что обещано задачей:

* (а) самая поздняя по дате прогноза запись серии названа актуальной, более
  ранняя — устаревшей с датой более свежего, недатированная — без даты с названным
  датированным;
* разные производители и разные горизонты между собой не сравниваются;
* датой прогноза не считается время создания записи (`created_at`);
* записи, не являющиеся прогнозами, меток не получают и остаются прежними;
* ничего не скрывается: метка есть у каждой записи серии, в том числе у актуальной.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from apps.web import labels as ui_labels
from apps.web.forecast_series import (
    FORECAST_SERIES_KEYS,
    ForecastRecord,
    apply_forecast_series,
    forecast_marks,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7135"

# подлинные формулировки стендовой карты c3-a370a68 (тот же текст, что в базе стенда)
VTB_DATED = (
    "Аналитики ВТБ Мои Инвестиции прогнозируют инфляцию в России на конец 2026 года "
    "на уровне 7–7,5% (прогноз по состоянию на 3 октября 2026 года)."
)
VTB_UNDATED = "Аналитики ВТБ ожидают, что инфляция в России по итогам 2026 года составит 5–6%."
VTB_SECOND_UNDATED = "Аналитики ВТБ ожидают, что инфляция в России по итогам 2026 года ускорится до 8%."
VTB_STALE_BY_STATEMENT = (
    "Аналитики ВТБ прогнозируют инфляцию в России на конец 2026 года на уровне 7,2% "
    "(прогноз по состоянию на 20 сентября 2026 года)."
)
VTB_2027 = "Аналитики ВТБ прогнозируют инфляцию в России на конец 2027 года на уровне 4,5%."
VTB_RATE = "Аналитики ВТБ ожидают, что ключевая ставка в России по итогам 2026 года составит 12%."
SBER_CURRENT = (
    "Аналитики SberCIB (Сбербанк) прогнозируют инфляцию в России на конец 2026 года "
    "на уровне 6,8% (по данным публикации от 20 октября 2026 года)."
)
SBER_STALE = (
    "Аналитики SberCIB ожидают инфляцию в России к концу 2026 года на уровне 6,5% "
    "(прогноз по состоянию на 29 сентября 2026 года)."
)
ALFA_SINGLE = (
    "Аналитики Альфа-Банка прогнозируют инфляцию в России по итогам 2026 года на уровне 7,5%."
)
GPB_SINGLE = (
    "Аналитики Газпромбанка прогнозируют инфляцию в России на конец 2026 года "
    "на уровне 6,0–6,5% (декабрь к декабрю)."
)
CBR_NO_TARGET = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."
CBR_TARGET_NEW = (
    "Банк России прогнозирует инфляцию в России по итогам 2026 года на уровне 6,5% "
    "(прогноз по состоянию на 10 октября 2026 года)."
)
CBR_TARGET_OLD = (
    "Банк России прогнозирует инфляцию в России по итогам 2026 года на уровне 7,0% "
    "(прогноз по состоянию на 1 сентября 2026 года)."
)
SBER_PLAIN_NAME_LATER = (
    "Сбербанк повысил прогноз инфляции в России на конец 2026 года до 8% "
    "(прогноз по состоянию на 25 октября 2026 года)."
)
OTHER_BROKER_LATER = (
    "Аналитики БКС Мир инвестиций прогнозируют инфляцию в России на конец 2026 года "
    "на уровне 7,9% (прогноз по состоянию на 25 октября 2026 года)."
)
CBR_HORIZON_PHRASE = (
    "Банк России ожидает инфляцию в России на 2026 год на уровне 7,4% "
    "(прогноз по состоянию на 20 октября 2026 года)."
)
OBSERVED_EXPECTATIONS = (
    "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2% "
    "(по данным опроса ООО «инФОМ»)."
)

ACTUAL_VTB = "Актуальный прогноз: ВТБ от 3 октября 2026"
ACTUAL_SBER = "Актуальный прогноз: SberCIB от 20 октября 2026"
OUTDATED_SBER = "Устаревший прогноз: есть более свежий прогноз SberCIB от 20 октября 2026"
UNDATED_VTB = "Дата прогноза не указана; есть датированный прогноз ВТБ от 3 октября 2026"


def _item(claim_id: str, statement: str = VTB_DATED) -> dict[str, Any]:
    return {"id": claim_id, "statement": statement}


def _record(claim_id: str, statement: str, as_of: dt.date | None = None) -> ForecastRecord:
    return ForecastRecord(claim_id=claim_id, statement=statement, as_of=as_of)


def _stand_card() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    card = json.loads((_FIXTURES / "answer-c3-a370a68-v29-52b2d24b.json").read_text(encoding="utf-8"))
    knowledge = {
        str(row["id"]): row
        for row in json.loads((_FIXTURES / "stand_forecast_claims.json").read_text(encoding="utf-8"))
    }
    items = [dict(item) for item in card["claims"] + card["relied_claims"]]
    return items, knowledge


def _records_of(items: list[dict[str, Any]], knowledge: dict[str, dict[str, Any]]) -> list[ForecastRecord]:
    records: list[ForecastRecord] = []
    for item in items:
        row = knowledge[str(item["id"])]
        anchor = row["as_of"]
        records.append(
            ForecastRecord(
                claim_id=str(item["id"]),
                statement=str(row["statement"]),
                as_of=None if anchor is None else dt.datetime.fromisoformat(str(anchor)),
            )
        )
    return records


def _id_of(knowledge: dict[str, dict[str, Any]], statement: str) -> str:
    return next(key for key, row in knowledge.items() if row["statement"] == statement)


# ─── (а) подлинная карта c3-a370a68: две серии на одном экране ───────────────────────────


def test_card_52b2d24b_marks_both_forecast_series() -> None:
    items, knowledge = _stand_card()
    marks = forecast_marks(_records_of(items, knowledge))

    dated_vtb = _id_of(knowledge, VTB_DATED)
    undated_vtb = _id_of(knowledge, VTB_UNDATED)
    fresh_sber = _id_of(knowledge, SBER_CURRENT)
    stale_sber = _id_of(knowledge, SBER_STALE)

    assert marks[dated_vtb].kind == "actual"
    assert marks[undated_vtb].kind == "undated"
    assert marks[fresh_sber].kind == "actual"
    assert marks[stale_sber].kind == "outdated"
    # серии не смешаны: у пары ВТБ своя серия, у пары SberCIB — своя
    assert marks[dated_vtb].series == marks[undated_vtb].series
    assert marks[fresh_sber].series == marks[stale_sber].series
    assert marks[dated_vtb].series != marks[fresh_sber].series
    assert marks[stale_sber].producer == "SberCIB"
    assert marks[undated_vtb].producer == "ВТБ"
    # одиночные записи и запись без фразы-горизонта молчат: сравнения на экране нет
    for statement in (ALFA_SINGLE, GPB_SINGLE, CBR_NO_TARGET):
        assert marks.get(_id_of(knowledge, statement)) is None, statement
    # метка есть у каждой записи серии, актуальная тоже подписана: витрина ничего не скрывает
    assert {mark.kind for mark in marks.values()} == {"actual", "outdated", "undated"}

    texts: dict[str, str] = {}
    for item in items:
        applied = apply_forecast_series([dict(item)], marks)[0]
        texts[str(item["id"])] = str(applied.get("forecast_series_text", ""))
    assert texts[fresh_sber] == ACTUAL_SBER
    assert texts[stale_sber] == OUTDATED_SBER
    assert texts[dated_vtb] == ACTUAL_VTB
    assert texts[undated_vtb] == UNDATED_VTB
    for statement in (ALFA_SINGLE, GPB_SINGLE, CBR_NO_TARGET):
        assert texts[_id_of(knowledge, statement)] == ""


def test_card_52b2d24b_labels_and_hints_come_from_the_dictionary() -> None:
    items, knowledge = _stand_card()
    marks = forecast_marks(_records_of(items, knowledge))
    marked = apply_forecast_series([dict(item) for item in items], marks)
    for item in marked:
        if "forecast_series" not in item:
            continue
        entry = ui_labels.describe("forecast_series", str(item["forecast_series"]))
        assert item["forecast_series_label"] == entry["label"]
        assert item["forecast_series_hint"] == entry["hint"]
        assert entry["action"]
        assert str(item["forecast_series_text"]).startswith(entry["label"])
    assert sum(1 for item in marked if "forecast_series" in item) == len(marks)


# ─── (б) разные производители и разные горизонты не сравниваются ──────────────────────────


def test_different_producers_are_never_compared() -> None:
    marks = forecast_marks(
        [
            _record("current", SBER_CURRENT),
            _record("alfa", ALFA_SINGLE, as_of=dt.date(2026, 8, 1)),
            _record("gpb", GPB_SINGLE),
        ]
    )
    assert marks == {}


def test_different_horizon_is_never_compared() -> None:
    assert forecast_marks([_record("current", VTB_DATED), _record("next", VTB_2027)]) == {}


def test_different_indicator_is_never_compared() -> None:
    assert forecast_marks([_record("current", VTB_DATED), _record("rate", VTB_RATE)]) == {}


def test_a_horizon_phrase_without_a_gate_target_is_a_horizon_now() -> None:
    """Было → стало (T7.138 2а, ADR-0035 §22.8).

    Было: витрина брала горизонт только из целевых фраз гейта периода («по итогам N года»),
    и запись с «на 2026 год» оставалась вне серии без подписи — прежний тест
    `test_record_without_a_horizon_phrase_is_not_compared` закреплял именно это.
    Стало: «на N год» читается внутри витрины, такая запись встаёт в ту же серию своего
    производителя и становится актуальной по дате своего прогноза; гейт периода при этом не
    тронут (проверка — `test_the_period_gate_still_does_not_see_a_target_in_na_year`)."""
    marks = forecast_marks(
        [
            _record("target_new", CBR_TARGET_NEW),
            _record("target_old", CBR_TARGET_OLD),
            _record("horizon", CBR_HORIZON_PHRASE),
        ]
    )
    assert marks["horizon"].kind == "actual"
    assert marks["horizon"].date_label == "20 октября 2026"
    assert marks["target_new"].kind == "outdated"
    assert marks["target_new"].date_label == "20 октября 2026"
    assert marks["target_old"].kind == "outdated"


# ─── (в) дата прогноза: формулировка, затем якорь; время создания — никогда ────────────────


def test_created_at_is_not_a_forecast_date() -> None:
    """Время появления строки в базе не приходит в подборщик вовсе: в записи витрины нет
    поля `created_at`, поэтому датировать прогноз оно не может."""
    assert not any(field.name.startswith("created") for field in dataclasses.fields(ForecastRecord))


def test_statement_date_wins_over_the_recorded_anchor() -> None:
    """Дата, названная в формулировке, важнее записанного якоря `as_of`: якорь — дата
    измерения или публикации строки, а не дата прогноза."""
    marks = forecast_marks(
        [
            _record("older", VTB_STALE_BY_STATEMENT, as_of=dt.date(2026, 10, 31)),
            _record("newer", VTB_DATED, as_of=dt.date(2026, 9, 1)),
        ]
    )
    assert marks["newer"].kind == "actual"
    assert marks["older"].kind == "outdated"
    assert marks["older"].date_label == "3 октября 2026"


def test_undated_statement_becomes_dated_by_its_anchor() -> None:
    """Формулировка без даты прогноза получает дату из записанного якоря `as_of`."""
    marks = forecast_marks(
        [
            _record("anchor", VTB_UNDATED, as_of=dt.date(2026, 10, 15)),
            _record("earlier", VTB_STALE_BY_STATEMENT),
        ]
    )
    assert marks["anchor"].kind == "actual"
    assert marks["anchor"].date_label == "15 октября 2026"
    assert marks["earlier"].kind == "outdated"


def test_two_undated_records_are_silent() -> None:
    """Без единой даты сравнения нет: витрина не решает, кто свежее, по своему вкусу."""
    assert forecast_marks([_record("first", VTB_UNDATED), _record("second", VTB_SECOND_UNDATED)]) == {}


# ─── (г) не прогнозы и формулировки вне словаря производителя ─────────────────────────────


def test_measurement_of_expectations_is_not_a_forecast() -> None:
    """Реализованное значение измерения — не прогноз: запись опроса не попадает в серию
    и не становится ни актуальной, ни устаревшей."""
    marks = forecast_marks(
        [
            _record("measured", OBSERVED_EXPECTATIONS, as_of=dt.date(2026, 9, 29)),
            _record("dated", VTB_DATED),
            _record("undated", VTB_UNDATED),
        ]
    )
    assert "measured" not in marks
    assert {marks["dated"].kind, marks["undated"].kind} == {"actual", "undated"}


def test_producer_outside_both_dictionaries_stays_silent() -> None:
    """Закрытый словарь производителей остался закрытым: имя, которого нет ни в словаре
    производителей, ни в каталоге названных акторов, подписи не даёт — своя подпись такой
    записи не ставится, чужое авторство ей не приписывается, и её более поздняя дата не
    делает записью «актуальный» уже названный прогноз SberCIB."""
    marks = forecast_marks(
        [
            _record("broker", OTHER_BROKER_LATER),
            _record("current", SBER_CURRENT),
            _record("stale", SBER_STALE),
        ]
    )
    assert "broker" not in marks
    assert marks["current"].kind == "actual"
    assert marks["stale"].kind == "outdated"
    assert marks["stale"].date_label == "20 октября 2026"


def test_sberbank_name_without_cib_joins_the_sbercib_series() -> None:
    """Было → стало (T7.138 2б, ADR-0035 §22.8).

    Было: «Сбербанк» без «CIB» закрытым словарём производителей не назывался, и прогноз
    оставался без подписи — прежний тест `test_producer_outside_the_dictionary_stays_silent`
    закреплял именно это (перечитывание замера среза: на стенде так молчала запись
    `858e0b97` «Сбербанк повысил прогноз инфляции…»).
    Стало: «Сбербанк» — имя закрытого каталога названных акторов
    (`apps/orchestrator/question_actors.py::names_sberbank`), и витрина сворачивает его в ту
    же серию `sbercib`; форма имени берётся из того же шаблона, что и распознавание акторов,
    отдельного списка форм в витрине нет. Это решение менеджера, обратимое пользователем
    (ADR-0035 §22.8). Более поздняя дата такой записи теперь делает её актуальной, а
    публикацию SberCIB — устаревшей."""
    marks = forecast_marks(
        [
            _record("sber", SBER_PLAIN_NAME_LATER),
            _record("current", SBER_CURRENT),
            _record("stale", SBER_STALE),
        ]
    )
    assert marks["sber"].kind == "actual"
    assert marks["sber"].producer == "SberCIB"
    assert marks["current"].kind == "outdated"
    assert marks["stale"].kind == "outdated"
    assert marks["stale"].date_label == "25 октября 2026"


# ─── (д) записи без метки не получают ни одного нового поля ────────────────────────────────


def test_rows_without_a_mark_keep_their_fields_untouched() -> None:
    rows = [_item("1"), _item("2", ALFA_SINGLE), _item("3", GPB_SINGLE)]
    before = [dict(row) for row in rows]
    out = apply_forecast_series(rows, {})
    assert out == before
    assert [row["id"] for row in out] == ["1", "2", "3"]


def test_stale_rows_move_after_the_current_one_of_their_series() -> None:
    marks = forecast_marks([_record("stale", SBER_STALE), _record("current", SBER_CURRENT)])
    rows = [_item("stale", SBER_STALE), _item("other", ALFA_SINGLE), _item("current", SBER_CURRENT)]
    out = apply_forecast_series(rows, marks)
    assert [row["id"] for row in out] == ["other", "current", "stale"]
    assert out[1]["forecast_series"] == "actual"
    assert out[2]["forecast_series"] == "outdated"


def test_every_dictionary_key_is_signed() -> None:
    for key in FORECAST_SERIES_KEYS:
        entry = ui_labels.describe("forecast_series", key)
        assert entry["label"] and entry["hint"] and entry["action"]
