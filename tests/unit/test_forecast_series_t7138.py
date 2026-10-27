r"""Unit: дополнение T7.138 к серии прогнозов карточки ответа (ADR-0035 §22.8).

Фикстура — выборка среза стенда `tests/fixtures/t7138/`: все 36 прогнозных утверждений
среза (`forecast_claims.json`, подлинные формулировки и якоря `as_of`) и четыре карточки,
где витрина ставит метки (`answer-*.json`, блоки `claims`/`relied_claims` в подлинном
порядке — карточки сняты со стенда, где витрина уже работала). Полный реплей 89 карточек
среза сделан прогоном вне репозитория; в репозиторий положены те карточки, где метки есть,
потому что карточка без меток не меняет ни одного поля.

Проверяется ровно то, что обещано дополнением:

* (2а) «на N год» стало горизонтом ВНУТРИ витрины; гейт периода при этом не изменён;
* (2б) Сбербанк словом каталога акторов сворачивается в серию `sbercib`, «СберИндекс» — нет;
* (2в) опрос/консенсус — отдельная серия, чужое ожидание — не прогноз производителя;
* (2г) дата решения: «по итогам заседания …» и «в <месяце> N года повысил прогноз»,
  порядок источников даты не изменился;
* (часть 3) устаревшая запись передвигается только внутри того блока, ей переданного;
* ловушки стенда остаются немечеными.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from apps.orchestrator.curator_period_gate import statement_period_tokens
from apps.web.forecast_series import ForecastRecord, apply_forecast_series, forecast_marks
from tests.unit.test_forecast_series_t7135 import (
    ALFA_SINGLE,
    SBER_CURRENT,
    SBER_STALE,
    VTB_DATED,
    VTB_UNDATED,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7138"

# подлинные id прогнозных утверждений среза стенда
CBR_MEETING = "bcf49d07-7f7d-477f-81ff-937ce2150879"  # по итогам заседания 23 октября 2026
CBR_JULY = "136a0ca0-f27b-4e5c-834e-064d62d558b6"  # в июле 2026 года повысил прогноз
CBR_JULY_DUP = "83590565-8155-4f28-a1bd-4458fd87cbbd"  # тот же прогноз, даты нет
SURVEY_OCT = "4adb10fe-cff1-4fae-a341-4081b4f7f5a9"  # консенсус по опросу за октябрь 2026
SURVEY_SEP = "2f456e85-6f68-46dc-b4d3-fce63f95f0d9"  # консенсус по опросу за сентябрь 2026
SBER_JULY = "858e0b97-f986-4696-915b-bc0278d3ed2c"  # «Сбербанк повысил прогноз…»
MINEC_2026 = "822072db-6d22-47dc-9585-d5017faa3a6e"  # Минэкономразвития, «на 2026 год»
OTHER_EXPECTATION = "85220305-cbfe-4e5a-acd9-5009e7116423"  # Коган предполагает, что Банк России…
REALIZED_2025 = "7cc2b578-e1e9-4474-8405-04ca3b9d0ea4"  # реализованная инфляция 2025 года
INTERFAX_SURVEY = "9eb38c86-39ec-4b5b-b505-dbdd987a7b93"  # опрос «Интерфакса», производителя нет

# подписи серии-опроса: то же имя производителя плюс уточнение в скобках
ACTUAL_SURVEY = "Актуальный прогноз: Банк России (опрос) от 14 октября 2026"
OUTDATED_SURVEY = "Устаревший прогноз: есть более свежий прогноз Банк России (опрос) от 14 октября 2026"

# синтетические формулировки: формы, которых на стенде ещё не было
SBER_NOMINATIVE = (
    "Сбербанк повысил прогноз инфляции в России на конец 2026 года до 8% "
    "(прогноз по состоянию на 25 октября 2026 года)."
)
SBER_GENITIVE = (
    "Аналитики Сбера прогнозируют инфляцию в России на конец 2026 года на уровне 7,4% "
    "(прогноз по состоянию на 12 октября 2026 года)."
)
SBER_LOCATIVE = (
    "Экономисты в Сбере ожидают инфляцию в России на конец 2026 года на уровне 7,3% "
    "(прогноз по состоянию на 5 октября 2026 года)."
)
SBERINDEX_ALONE = (
    "СберИндекс понизил прогноз инфляции в России на конец 2026 года до 7,7% "
    "(прогноз по состоянию на 27 октября 2026 года)."
)
MINEC_TARGET_PHRASE = (
    "Минэкономразвития прогнозирует инфляцию в России по итогам 2026 года на уровне 6,5% "
    "(прогноз по состоянию на 1 сентября 2026 года)."
)
CBR_MEETING_AND_DOCUMENT_DATE = (
    "Банк России по итогам заседания 23 октября 2026 года повысил прогноз инфляции в России "
    "на конец 2026 года до 7,1–7,6% (прогноз по состоянию на 26 октября 2026 года)."
)
CBR_MONTH_WITHOUT_MARKER = (
    "Банк России сохраняет прогноз инфляции в России на конец 2026 года на уровне 7,0% "
    "(данные за сентябрь 2026 года)."
)
CBR_DATED_BY_DOCUMENT = (
    "Банк России прогнозирует инфляцию в России на конец 2026 года на уровне 6,9% "
    "(прогноз по состоянию на 1 сентября 2026 года)."
)
CBR_DECISION_IN_JULY = (
    "Банк России в июле 2026 года повысил прогноз инфляции в России на конец 2026 года до 6–7%."
)


def _claims() -> dict[str, dict[str, Any]]:
    rows = json.loads((_FIXTURES / "forecast_claims.json").read_text(encoding="utf-8"))
    return {str(row["id"]): row for row in rows}


def _record(claim_id: str, statement: str, as_of: dt.date | None = None) -> ForecastRecord:
    return ForecastRecord(claim_id=claim_id, statement=statement, as_of=as_of)


def _stand_record(short_id: str) -> ForecastRecord:
    """Запись фикстуры: подлинная формулировка и подлинный якорь `as_of`."""
    row = next(row for key, row in _claims().items() if key.startswith(short_id))
    anchor = row["as_of"]
    return ForecastRecord(
        claim_id=str(row["id"]),
        statement=str(row["statement"]),
        as_of=None if anchor is None else dt.datetime.fromisoformat(str(anchor)),
    )


def _stand_statement(short_id: str) -> str:
    return str(next(row for key, row in _claims().items() if key.startswith(short_id))["statement"])


def _card(short: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / f"answer-{short}.json").read_text(encoding="utf-8"))


def _card_records(card: dict[str, Any]) -> list[ForecastRecord]:
    claims = _claims()
    records: list[ForecastRecord] = []
    for item in [*card["claims"], *card["relied_claims"]]:
        row = claims[str(item["id"])]
        anchor = row["as_of"]
        records.append(
            ForecastRecord(
                claim_id=str(row["id"]),
                statement=str(row["statement"]),
                as_of=None if anchor is None else dt.datetime.fromisoformat(str(anchor)),
            )
        )
    return records


def _apply_blocks(card: dict[str, Any], marks: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Два блока карточки обрабатываются отдельно — ровно так же, как это делает answer.py."""
    return (
        apply_forecast_series([dict(item) for item in card["claims"]], marks),
        apply_forecast_series([dict(item) for item in card["relied_claims"]], marks),
    )


def _texts(items: list[dict[str, Any]]) -> dict[str, str]:
    return {str(item["id"])[:8]: str(item.get("forecast_series_text", "")) for item in items}


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [str(item["id"])[:8] for item in items]


# ─── (а) карта c3: подписи, тексты и порядок ──────────────────────────────────────────────


def test_c3_card_labels_texts_and_order() -> None:
    """Ожидаемый итог карты `1b236737`: прогноз Банка России от 23 октября 2026 — актуальный,
    июльская запись Банка России — устаревшая со ссылкой на него; пара SberCIB и пара ВТБ —
    как раньше; консенсус-опрос, Газпромбанк и Альфа-Банк подписей не получают."""
    card = _card("1b236737")
    marks = forecast_marks(_card_records(card))
    claims_out, relied_out = _apply_blocks(card, marks)

    assert _texts(claims_out) == {
        "27806b5b": "Актуальный прогноз: SberCIB от 20 октября 2026",
        "4adb10fe": "",
        "bcf49d07": "Актуальный прогноз: Банк России от 23 октября 2026",
    }
    assert _texts(relied_out) == {
        "07126d06": "",
        "136a0ca0": "Устаревший прогноз: есть более свежий прогноз Банк России от 23 октября 2026",
        "471bd83d": "",
        "858e0b97": "Устаревший прогноз: есть более свежий прогноз SberCIB от 20 октября 2026",
        "a3d430d8": "",
        "ab8234ae": "Актуальный прогноз: ВТБ от 3 октября 2026",
        "c263e93c": "Дата прогноза не указана; есть датированный прогноз ВТБ от 3 октября 2026",
    }
    assert _ids(claims_out) == _ids(card["claims"])
    assert _ids(relied_out) == _ids(card["relied_claims"])
    # немеченые записи не получают ни одного нового поля
    for item in [*claims_out, *relied_out]:
        if str(item["id"])[:8] in {"4adb10fe", "07126d06", "471bd83d", "a3d430d8"}:
            assert not [key for key in item if key.startswith("forecast_series")], item


def test_outdated_record_in_the_other_block_links_the_current_one() -> None:
    """Часть 3: июльская запись Банка России лежит в блоке «Использовано из знаний»,
    актуальная — в блоке утверждений вопроса. Серия одна, поэтому устаревшая запись называет
    более свежий прогноз; переноса между блоками витрина не делает."""
    card = _card("1b236737")
    marks = forecast_marks(_card_records(card))
    assert marks[CBR_JULY].series == marks[CBR_MEETING].series
    assert marks[CBR_JULY].kind == "outdated"
    assert marks[CBR_JULY].date_label == "23 октября 2026"

    _claims_out, relied_out = _apply_blocks(card, marks)
    assert _ids(relied_out) == _ids(card["relied_claims"])

    # карточка `0986f022`: в нижнем блоке два устаревших члена серии SberCIB, актуальный —
    # в верхнем; порядок нижнего блока остаётся прежним целиком
    second = _card("0986f022")
    second_marks = forecast_marks(_card_records(second))
    _second_claims, second_relied = _apply_blocks(second, second_marks)
    assert _ids(second_relied) == _ids(second["relied_claims"])


def test_stale_rows_move_only_inside_the_block_they_are_passed_in() -> None:
    """Передвижение устаревшей записи работает внутри переданного списка и не создаёт
    переносов между блоками: вызов по одному блоку ничего не знает о другом."""
    marks = forecast_marks([_record("current", SBER_CURRENT), _record("stale", SBER_STALE)])
    rows = [
        {"id": "stale", "statement": SBER_STALE},
        {"id": "other", "statement": ALFA_SINGLE},
        {"id": "current", "statement": SBER_CURRENT},
    ]
    out = apply_forecast_series(rows, marks)
    assert [str(row["id"]) for row in out] == ["other", "current", "stale"]


# ─── (б) карточки выборки: метки совпадают с таблицей части 1 ─────────────────────────────

# карточка -> {id записи: состояние метки} по замеру среза 2026-10-28 (часть 1)
_CARD_MARKS: dict[str, dict[str, str]] = {
    "0986f022": {
        "27806b5b": "actual",
        "759d6363": "outdated",
        "858e0b97": "outdated",
        "4adb10fe": "actual",
        "2f456e85": "outdated",
    },
    "1b236737": {
        "27806b5b": "actual",
        "858e0b97": "outdated",
        "bcf49d07": "actual",
        "136a0ca0": "outdated",
        "ab8234ae": "actual",
        "c263e93c": "undated",
    },
    "52b2d24b": {
        "27806b5b": "actual",
        "759d6363": "outdated",
        "ab8234ae": "actual",
        "c263e93c": "undated",
    },
    "bc50a576": {
        "27806b5b": "actual",
        "759d6363": "outdated",
        "858e0b97": "outdated",
    },
}

# метки, которые ставила витрина на 865ca43: они обязаны сохраниться
_PREVIOUS_MARKS: dict[str, dict[str, str]] = {
    "0986f022": {"27806b5b": "actual", "759d6363": "outdated"},
    "1b236737": {"ab8234ae": "actual", "c263e93c": "undated"},
    "52b2d24b": {
        "27806b5b": "actual",
        "759d6363": "outdated",
        "ab8234ae": "actual",
        "c263e93c": "undated",
    },
    "bc50a576": {"27806b5b": "actual", "759d6363": "outdated"},
}


@pytest.mark.parametrize("short", sorted(_CARD_MARKS))
def test_card_marks_match_the_part_one_table(short: str) -> None:
    card = _card(short)
    marks = forecast_marks(_card_records(card))
    got = {str(claim_id)[:8]: mark.kind for claim_id, mark in marks.items()}
    assert got == _CARD_MARKS[short], sorted(got.items())


@pytest.mark.parametrize("short", sorted(_PREVIOUS_MARKS))
def test_records_marked_before_keep_their_kind(short: str) -> None:
    """Ни одна подпись, поставленная до T7.138, не исчезла и не изменила состояние:
    дополнение только добавляет записи в серии."""
    card = _card(short)
    marks = forecast_marks(_card_records(card))
    by_short = {str(claim_id)[:8]: mark for claim_id, mark in marks.items()}
    for short_id, kind in _PREVIOUS_MARKS[short].items():
        assert by_short[short_id].kind == kind, (short, short_id, by_short[short_id].kind)


# ─── (2а) горизонт «на N год»: витрина считает, гейт — по-прежнему нет ─────────────────────


def test_vitrine_horizon_phrase_compares_the_records() -> None:
    """«на 2026 год» без целевой фразы гейта — тот же горизонт внутри витрины: стендовая
    формулировка Минэкономразвития встаёт в одну серию с записью той же цели."""
    marks = forecast_marks([_stand_record("822072db"), _record("target", MINEC_TARGET_PHRASE)])
    assert {mark.kind for mark in marks.values()} == {"actual", "outdated"}
    assert {mark.series.split(":")[0] for mark in marks.values()} == {"minec"}
    assert marks[MINEC_2026].date_label == "22 сентября 2026"


def test_the_period_gate_still_does_not_see_a_target_in_na_year() -> None:
    """Гейт периода не тронут: «на 2026 год» не даёт токена цели, «по итогам 2026 года» —
    даёт. Различие проверено на одном и том же показателе."""
    without_target = statement_period_tokens(
        "Прогноз Банка России по инфляции в России на 2026 год составляет 6,0–7,0%."
    )
    assert not [token for token in without_target if token.kind == "target"]
    with_target = statement_period_tokens(
        "Прогноз Банка России по инфляции в России по итогам 2026 года составляет 6,0–7,0%."
    )
    assert [token.year for token in with_target if token.kind == "target"] == [2026]


def test_a_horizon_alone_does_not_invent_a_series() -> None:
    """Опрос без названного производителя молчит и с новым горизонтом: одного горизонта мало,
    нужен ещё производитель и тот же показатель."""
    assert forecast_marks([_stand_record("9eb38c86"), _stand_record("4adb10fe")]) == {}


# ─── (2б) мост Сбербанка ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("statement", "bank_kind"),
    [(SBER_NOMINATIVE, "actual"), (SBER_GENITIVE, "outdated"), (SBER_LOCATIVE, "outdated")],
)
def test_sberbank_name_forms_join_the_sbercib_series(statement: str, bank_kind: str) -> None:
    """«Сбербанк», «Сбера», «в Сбере» — производитель словаря `sbercib`: форма имени берётся
    из того же закрытого шаблона, что и каталог акторов, отдельного списка форм здесь нет.
    Свежесть считает дата прогноза: октябрьская запись банка — актуальная, сентябрьская —
    устаревшая против публикации SberCIB."""
    marks = forecast_marks([_record("bank", statement), _record("cib", SBER_CURRENT)])
    assert marks["bank"].kind == bank_kind
    assert marks["cib"].kind == ("outdated" if bank_kind == "actual" else "actual")
    assert marks["bank"].producer == "SberCIB"
    assert marks["bank"].series == marks["cib"].series


def test_the_stand_sberbank_record_joins_the_series() -> None:
    """Подлинная запись среза `858e0b97` («Сбербанк повысил прогноз инфляции…»): на 865ca43
    она молчала, теперь это устаревшая запись серии SberCIB."""
    marks = forecast_marks([_stand_record("858e0b97"), _record("cib", SBER_CURRENT)])
    assert marks[SBER_JULY].kind == "outdated"
    assert marks[SBER_JULY].producer == "SberCIB"
    assert marks[SBER_JULY].date_label == "20 октября 2026"


def test_sberindex_is_a_different_producer_and_is_not_bridged() -> None:
    """«СберИндекс» — другой производитель закрытого словаря: мост к нему не применяется,
    смешения с SberCIB нет, подпись не ставится."""
    marks = forecast_marks([_record("index", SBERINDEX_ALONE), _record("cib", SBER_CURRENT)])
    assert marks == {}


def test_named_cib_and_bank_stay_one_producer() -> None:
    """Формулировка «SberCIB (Сбербанк)» называет и бренд, и банк: производитель по-прежнему
    один, серия из двух записей остаётся серией."""
    marks = forecast_marks([_record("cib", SBER_CURRENT), _record("stale", SBER_STALE)])
    assert marks["cib"].kind == "actual"
    assert marks["stale"].kind == "outdated"
    assert marks["cib"].series == marks["stale"].series


# ─── (2в) роли: опрос и чужое ожидание ─────────────────────────────────────────────────────


def test_survey_is_its_own_series_not_the_producers_forecast() -> None:
    """Консенсус-прогноз по макроэкономическому опросу Банка России — серия «опрос Банка
    России»: он сравнивается сам с собой (октябрь против сентября), но никогда с прогнозом
    самого Банка России."""
    marks = forecast_marks(
        [_stand_record("4adb10fe"), _stand_record("2f456e85"), _stand_record("bcf49d07")]
    )
    assert marks[SURVEY_OCT].kind == "actual"
    assert marks[SURVEY_SEP].kind == "outdated"
    assert marks[SURVEY_OCT].producer == "Банк России (опрос)"
    assert marks[SURVEY_OCT].series == marks[SURVEY_SEP].series
    # прогноз Банка России в эту серию не входит и сравнивать ему не с чем — метки нет
    assert CBR_MEETING not in marks
    assert marks[SURVEY_SEP].series.startswith("опрос:")

    items = [
        {"id": SURVEY_OCT, "statement": _stand_statement("4adb10fe")},
        {"id": SURVEY_SEP, "statement": _stand_statement("2f456e85")},
    ]
    out = apply_forecast_series(items, marks)
    texts = {str(row["id"])[:8]: str(row["forecast_series_text"]) for row in out}
    assert texts == {"4adb10fe": ACTUAL_SURVEY, "2f456e85": OUTDATED_SURVEY}


def test_survey_of_an_unnamed_producer_stays_silent() -> None:
    """Опрос, не названный словарём производителей («Интерфакс»), подписи не получает:
    series-правило про производителя не ослаблено."""
    assert forecast_marks([_stand_record("9eb38c86")]) == {}
    marks = forecast_marks([_stand_record("9eb38c86"), _stand_record("4adb10fe")])
    assert INTERFAX_SURVEY not in marks


def test_another_actors_expectation_is_not_the_producers_forecast() -> None:
    """«Инвестбанкир Евгений Коган предполагает, что Банк России повысит прогноз…» — чужое
    ожидание о Банке России, а не прогноз Банка России: в его серию запись не входит."""
    marks = forecast_marks([_stand_record("85220305"), _stand_record("bcf49d07")])
    assert OTHER_EXPECTATION not in marks
    assert CBR_MEETING not in marks  # вторых записей в серии нет — молчание прежнее


def test_own_expectation_verb_still_names_its_producer() -> None:
    """«Аналитики ВТБ ожидают, что инфляция…» — ВТБ назван ДО придаточного, это прогноз ВТБ:
    прежняя пара остаётся подписанной."""
    marks = forecast_marks([_record("dated", VTB_DATED), _record("undated", VTB_UNDATED)])
    assert marks["dated"].kind == "actual"
    assert marks["undated"].kind == "undated"


def test_realized_measurement_stays_out_of_the_series() -> None:
    """Запись реализованного значения (инфляция 2025 года, названная вместе с прогнозами)
    в серию не входит — правило ролей прежнее, горизонта ему мало."""
    marks = forecast_marks([_stand_record("7cc2b578"), _stand_record("bcf49d07")])
    assert REALIZED_2025 not in marks
    assert CBR_MEETING not in marks


def test_folded_duplicate_is_not_a_card_item() -> None:
    """Свёрнутый дубль `83590565` не является строкой карточки: он висит в `duplicates`
    записи `136a0ca0`, в подборщик витрине не передаётся и подписи не получает."""
    card = _card("1b236737")
    items = [*card["claims"], *card["relied_claims"]]
    assert CBR_JULY_DUP not in [str(item["id"]) for item in items]
    owner = next(item for item in items if str(item["id"]) == CBR_JULY)
    assert CBR_JULY_DUP in [str(dup["id"]) for dup in owner["duplicates"]]

    marks = forecast_marks(_card_records(card))
    assert CBR_JULY_DUP not in marks
    claims_out, relied_out = _apply_blocks(card, marks)
    for item in [*claims_out, *relied_out]:
        if str(item["id"]) == CBR_JULY_DUP:  # строки нет в списке — поля не появляются
            raise AssertionError("свёрнутый дубль вернулся в список карточки")


# ─── (2г) дата решения и порядок источников даты ──────────────────────────────────────────


def test_meeting_marker_dates_the_forecast() -> None:
    """«по итогам заседания 23 октября 2026 года» — дата решения: без якоря `as_of` запись
    всё равно датирована и стоит актуальной против сентябрьского прогноза."""
    marks = forecast_marks(
        [
            _record("meeting", _stand_statement("bcf49d07"), as_of=None),
            _record("september", CBR_DATED_BY_DOCUMENT),
        ]
    )
    assert marks["meeting"].kind == "actual"
    assert marks["september"].kind == "outdated"
    assert marks["september"].date_label == "23 октября 2026"


def test_month_and_verb_date_the_forecast() -> None:
    """«Банк России в июле 2026 года повысил прогноз…» — дата решения из месяца с годом:
    запись датирована (июль 2026), а не осталась недатированной."""
    marks = forecast_marks([_record("july", CBR_DECISION_IN_JULY, as_of=None), _record("sept", CBR_DATED_BY_DOCUMENT)])
    assert marks["july"].kind == "outdated"
    assert marks["july"].date_label == "1 сентября 2026"
    # без маркера дата была бы другой: та же формулировка с более поздним якорем — актуальна
    without_marker = forecast_marks(
        [
            _record("july", CBR_DECISION_IN_JULY.replace("в июле 2026 года повысил", "повысил"), as_of=None),
            _record("sept", CBR_DATED_BY_DOCUMENT),
        ]
    )
    assert without_marker["july"].kind == "undated"


def test_document_date_marker_wins_over_the_decision_date() -> None:
    """Порядок источников даты не изменился: дата документа из формулировки важнее даты
    решения. «по итогам заседания 23 октября …» плюс «прогноз по состоянию на 26 октября» —
    датой прогноза считается 26 октября."""
    marks = forecast_marks(
        [
            _record("meeting", CBR_MEETING_AND_DOCUMENT_DATE, as_of=None),
            _record("september", CBR_DATED_BY_DOCUMENT),
        ]
    )
    assert marks["meeting"].kind == "actual"
    assert marks["september"].date_label == "26 октября 2026"


def test_a_month_without_a_marker_falls_back_to_the_anchor() -> None:
    """«данные за сентябрь 2026 года» маркером даты решения не является: датой прогноза
    становится записанный якорь `as_of` — прежний шаг порядка."""
    marks = forecast_marks(
        [
            _record("month", CBR_MONTH_WITHOUT_MARKER, as_of=dt.date(2026, 9, 30)),
            _record("undated", CBR_DECISION_IN_JULY.replace("в июле 2026 года повысил", "повысил")),
        ]
    )
    assert marks["month"].kind == "actual"
    assert marks["undated"].kind == "undated"
    assert marks["undated"].date_label == "30 сентября 2026"


def test_no_marker_and_no_anchor_stays_undated() -> None:
    """Ни маркера, ни якоря — запись остаётся недатированной (прежнее поведение):
    стендовый дубль июльского прогноза без даты и без якоря."""
    marks = forecast_marks([_stand_record("83590565"), _stand_record("bcf49d07")])
    assert marks[CBR_JULY_DUP].kind == "undated"
    assert marks[CBR_MEETING].kind == "actual"
