"""Unit: ТИХАЯ серия прогнозов (T7.135a, ADR-0035 §22.7).

Метка отвечает на один вопрос: какая запись серии свежее. Если в серии нечего
различать — у всех записей названа ОДНА И ТА ЖЕ дата прогноза (или все записи
недатированные), — ни одной метки быть не должно. На наборе из 30 прогнозов стенда
(`tests/fixtures/t7135/stand_forecast_claims.json`) это был шум: две записи-дубля
одного и того же прогноза по декабрьскому макроэкономическому опросу Банка России
(`2d010d53` и `58e2d5d4`, обе от 24 декабря 2025) получали по «Актуальному прогнозу»,
хотя различать их по свежести нечем.

Проверяется:

* (б) пара дублей стенда — без меток, и вообще в наборе стенда помечены ровно те
  записи, где даты действительно разные либо датированная запись стоит рядом с
  недатированной;
* (в) пары ВТБ и SberCIB на подлинной карте `c3-a370a68` — прежние метки и прежние
  строки подписей (регрессия: тихая серия не отнимает подписи там, где есть что
  различать);
* (г) серия из двух недатированных записей — без меток (прежнее поведение);
* синтетическая пара с одной датой — без меток, три записи с двумя датами — помечены;
* запись тихой серии не получает ни одного нового поля.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from apps.web.forecast_series import ForecastRecord, apply_forecast_series, forecast_marks
from tests.unit.test_forecast_series_t7135 import (
    ACTUAL_SBER,
    ACTUAL_VTB,
    OUTDATED_SBER,
    SBER_CURRENT,
    SBER_STALE,
    UNDATED_VTB,
    VTB_DATED,
    VTB_SECOND_UNDATED,
    VTB_STALE_BY_STATEMENT,
    VTB_UNDATED,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7135"

# подлинные id и формулировки стендового набора прогнозов: две записи одного и того же
# прогноза по декабрьскому опросу Банка России, обе датированы 24 декабря 2025 года
DUB_CBR_FIRST = "2d010d53-8818-4362-9475-387b71b63bb5"
DUB_CBR_SECOND = "58e2d5d4-fed4-443f-aba3-b5835a61f638"
DUB_STATEMENT = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
    "декабрьского макроэкономического опроса Банка России составил 6,3%."
)

# подлинные id записей, которые помечены и после тихой серии: пара ВТБ (3 октября 2026
# против записи без даты) и тройка SberCIB (20 октября, 29 сентября, 21 сентября)
VTB_DATED_ID = "ab8234ae-3fb9-4565-a696-1ddcedda4318"
VTB_UNDATED_ID = "c263e93c-2e02-4a37-80bd-6d5ce091362e"
SBER_CURRENT_ID = "27806b5b-967b-434f-805d-54f5382eda2e"
SBER_STALE_ID = "759d6363-8120-4193-8c1e-21aa0619528a"
SBER_ANCHOR_ID = "44bd1460-bb72-4521-8d54-ffd884a93b20"

_STAND_MARKED = {VTB_DATED_ID, VTB_UNDATED_ID, SBER_CURRENT_ID, SBER_STALE_ID, SBER_ANCHOR_ID}

VTB_SAME_DATE_SECOND = (
    "Аналитики ВТБ прогнозируют инфляцию в России на конец 2026 года на уровне 8% "
    "(прогноз по состоянию на 3 октября 2026 года)."
)


def _stand_rows() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / "stand_forecast_claims.json").read_text(encoding="utf-8"))


def _stand_records() -> list[ForecastRecord]:
    records: list[ForecastRecord] = []
    for row in _stand_rows():
        anchor = row["as_of"]
        records.append(
            ForecastRecord(
                claim_id=str(row["id"]),
                statement=str(row["statement"]),
                as_of=None if anchor is None else dt.datetime.fromisoformat(str(anchor)),
            )
        )
    return records


def _stand_card() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    card = json.loads((_FIXTURES / "answer-c3-a370a68-v29-52b2d24b.json").read_text(encoding="utf-8"))
    knowledge = {str(row["id"]): row for row in _stand_rows()}
    items = [dict(item) for item in card["claims"] + card["relied_claims"]]
    return items, knowledge


def _id_of(knowledge: dict[str, dict[str, Any]], statement: str) -> str:
    return next(key for key, row in knowledge.items() if row["statement"] == statement)


def _record(claim_id: str, statement: str, as_of: dt.date | None = None) -> ForecastRecord:
    return ForecastRecord(claim_id=claim_id, statement=statement, as_of=as_of)


def _series_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key.startswith("forecast_series")}


# ─── (б) пара дублей стенда: меток нет ни у кого ─────────────────────────────────────────


def test_stand_duplicate_pair_of_one_survey_gets_no_marks() -> None:
    """Было → стало. Было: обе записи пары стояли с меткой «Актуальный прогноз» при
    одной и той же дате прогноза (24 декабря 2025) — сравнивать нечего, а на экране
    шум. Стало: тихая серия молчит, ни одна запись пары метки не получает."""
    marks = forecast_marks(_stand_records())

    assert DUB_CBR_FIRST not in marks, marks.get(DUB_CBR_FIRST)
    assert DUB_CBR_SECOND not in marks, marks.get(DUB_CBR_SECOND)

    # в наборе из 30 прогнозов помечены ровно те записи, где есть что различать:
    # пара ВТБ (дата против отсутствия даты) и тройка SberCIB (три разные даты)
    assert set(marks) == _STAND_MARKED, sorted(marks)


def test_stand_duplicate_pair_rows_gain_no_new_fields() -> None:
    """Тихая серия не оставляет в строке карточки ни одного нового поля."""
    rows = [
        {"id": DUB_CBR_FIRST, "statement": DUB_STATEMENT},
        {"id": DUB_CBR_SECOND, "statement": DUB_STATEMENT},
    ]
    out = apply_forecast_series([dict(row) for row in rows], forecast_marks(_stand_records()))
    assert out == rows, out
    assert all(_series_fields(row) == {} for row in out)


def test_two_records_of_the_same_date_are_quiet() -> None:
    """Синтетическая пара: у обеих записей названа одна дата прогноза — меток нет."""
    assert forecast_marks([_record("first", VTB_DATED), _record("second", VTB_SAME_DATE_SECOND)]) == {}


def test_series_with_two_different_dates_is_still_marked() -> None:
    """Три записи с двумя разными датами: метки остаются — различать есть что."""
    marks = forecast_marks(
        [
            _record("first", VTB_DATED),
            _record("second", VTB_SAME_DATE_SECOND),
            _record("earlier", VTB_STALE_BY_STATEMENT),
        ]
    )
    assert marks["first"].kind == "actual"
    assert marks["second"].kind == "actual"
    assert marks["earlier"].kind == "outdated"
    assert marks["earlier"].date_label == "3 октября 2026"


def test_dated_record_against_an_undated_one_is_not_quiet() -> None:
    """Дата у серии одна — но рядом запись без даты: это различие, витрина его показывает."""
    marks = forecast_marks([_record("dated", VTB_DATED), _record("undated", VTB_UNDATED)])
    assert marks["dated"].kind == "actual"
    assert marks["undated"].kind == "undated"


# ─── (в) пары ВТБ и SberCIB на карте 52b2d24b — прежние метки (регрессия) ─────────────────


def test_card_52b2d24b_pairs_keep_their_marks_and_texts() -> None:
    items, knowledge = _stand_card()
    records = [
        ForecastRecord(
            claim_id=str(item["id"]),
            statement=str(knowledge[str(item["id"])]["statement"]),
            as_of=(
                None
                if knowledge[str(item["id"])]["as_of"] is None
                else dt.datetime.fromisoformat(str(knowledge[str(item["id"])]["as_of"]))
            ),
        )
        for item in items
    ]
    marks = forecast_marks(records)

    dated_vtb = _id_of(knowledge, VTB_DATED)
    undated_vtb = _id_of(knowledge, VTB_UNDATED)
    fresh_sber = _id_of(knowledge, SBER_CURRENT)
    stale_sber = _id_of(knowledge, SBER_STALE)

    assert marks[dated_vtb].kind == "actual"
    assert marks[undated_vtb].kind == "undated"
    assert marks[fresh_sber].kind == "actual"
    assert marks[stale_sber].kind == "outdated"

    texts: dict[str, str] = {}
    for item in items:
        applied = apply_forecast_series([dict(item)], marks)[0]
        texts[str(item["id"])] = str(applied.get("forecast_series_text", ""))
    assert texts[dated_vtb] == ACTUAL_VTB
    assert texts[undated_vtb] == UNDATED_VTB
    assert texts[fresh_sber] == ACTUAL_SBER
    assert texts[stale_sber] == OUTDATED_SBER


# ─── (г) две недатированные записи — без меток (прежнее поведение) ────────────────────────


def test_two_undated_records_stay_quiet() -> None:
    assert forecast_marks([_record("first", VTB_UNDATED), _record("second", VTB_SECOND_UNDATED)]) == {}
