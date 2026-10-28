"""T7.139 (часть 3, ADR-0038 §16): свежий прогноз той же серии — чистая функция.

Стенд 008d93ac: карточка потеряла свежий прогноз Банка России (`bcf49d07…`, as_of
2026-10-23), потому что куратор перепроверял только июльский (`136a0ca0…`), а снятая
перепроверка унесла его из ответа; в контекст-паке свежая запись была. Функция
`fresher_forecast_supplement` добавляет паковую запись в опору, только когда метка
самой свежей записи серии принадлежит паку, а ВСЕ карточные записи серии устарели.
Метки даёт витрина `apps/web/forecast_series.forecast_marks` по объединению карточных
и паковых записей (одинокая карточная запись серии без паковой метки не получила бы).

Красность на 256b16b: модуля `apps/orchestrator/relied_supplement.py` там нет вовсе.
"""

from __future__ import annotations

import datetime as dt

from apps.orchestrator.orchestrator import RELIED_CLAIMS_LIMIT
from apps.orchestrator.relied_supplement import SupplementRecord, fresher_forecast_supplement

CBR_JULY = "136a0ca0-f27b-4e5c-834e-064d62d558b6"
CBR_OCT = "bcf49d07-7f7d-477f-81ff-937ce2150879"
ALFA = "07126d06-8bf2-4f8f-bdc4-71944ef1d905"
VTB_HI = "ab8234ae-3fb9-4565-a696-1ddcedda4318"
VTB_UNDATED = "c263e93c-2e02-4a37-80bd-6d5ce091362e"
SBERCIB_CURRENT = "27806b5b-967b-434f-805d-54f5382eda2e"
SBERCIB_SEP = "759d6363-8120-4193-8c1e-21aa0619528a"
SBERCIB_JUL = "858e0b97-f986-4696-915b-bc0278d3ed2c"
SBERCIB_UNDATED_CLAIM = "44bd1460-bb72-4521-8d54-ffd884a93b20"
CBR_SURVEY_CONSENSUS = "4adb10fe-cff1-4fae-a341-4081b4f7f5a9"

D = dt.date  # короче для as_of: витрина принимает date и datetime

STATEMENTS = {
    CBR_JULY: "Банк России в июле 2026 года повысил прогноз инфляции на 2026 год до 6–7%.",
    CBR_OCT: "Банк России повысил прогноз годовой инфляции в России на 2026 год до 7,1–7,6% "
    "(по итогам заседания 23 октября 2026 года).",
    ALFA: "Аналитики Альфа-Банка прогнозируют инфляцию в России по итогам 2026 года на уровне 7,5%.",
    VTB_HI: "Аналитики ВТБ Мои Инвестиции прогнозируют инфляцию в России на конец 2026 года "
    "на уровне 7–7,5% (прогноз по состоянию на 3 октября 2026 года).",
    VTB_UNDATED: "Аналитики ВТБ ожидают, что инфляция в России по итогам 2026 года составит 5–6%.",
    SBERCIB_CURRENT: "Аналитики SberCIB (Сбербанк) прогнозируют инфляцию в России на конец 2026 года "
    "на уровне 6,8% (по данным публикации от 20 октября 2026 года).",
    SBERCIB_SEP: "Аналитики SberCIB ожидают инфляцию в России к концу 2026 года на уровне 6,5% "
    "(прогноз по состоянию на 29 сентября 2026 года).",
    SBERCIB_JUL: "Сбербанк повысил прогноз инфляции в России на конец 2026 года до 6,5–7% "
    "(с 6–6,5%) (прогноз по состоянию на 30 июля 2026 года).",
    SBERCIB_UNDATED_CLAIM: "Аналитики SberCIB прогнозируют инфляцию в России на конец 2026 года "
    "на уровне 6,5%.",
    CBR_SURVEY_CONSENSUS: "Консенсус-прогноз аналитиков по годовой инфляции в России на 2026 год "
    "составляет 7,1% (по данным макроэкономического опроса).",
}
AS_OF = {
    CBR_JULY: D(2026, 7, 24),
    CBR_OCT: D(2026, 10, 23),
    ALFA: D(2026, 8, 1),
    VTB_HI: D(2026, 10, 3),
    VTB_UNDATED: None,
    SBERCIB_CURRENT: D(2026, 10, 20),
    SBERCIB_SEP: D(2026, 9, 29),
    SBERCIB_JUL: D(2026, 7, 30),
    SBERCIB_UNDATED_CLAIM: D(2026, 9, 21),
    CBR_SURVEY_CONSENSUS: D(2026, 10, 14),
}


def rec(claim_id: str) -> SupplementRecord:
    return SupplementRecord(claim_id=claim_id, statement=STATEMENTS[claim_id], as_of=AS_OF[claim_id])


def _card_008d93ac() -> list[SupplementRecord]:
    """Карточка после части 2: действующая перепроверка SberCIB, ВТБ (актуальный и без
    даты) и перенесённый июльский прогноз Банка России; Альфа-Банк и Газпромбанк — тоже
    опора (перенос/куратор), свои серии — одиночки."""
    return [rec(x) for x in (SBERCIB_CURRENT, VTB_HI, VTB_UNDATED, CBR_JULY, ALFA)]


def _pack_008d93ac() -> list[SupplementRecord]:
    return [rec(x) for x in (CBR_OCT, SBERCIB_SEP, CBR_SURVEY_CONSENSUS)]


def test_stand_008d93ac_adds_fresh_cbr_forecast_replacing_the_stale_one() -> None:
    added, rejected = fresher_forecast_supplement(
        _card_008d93ac(), _pack_008d93ac(), len(_card_008d93ac()), RELIED_CLAIMS_LIMIT
    )
    assert rejected == []
    assert len(added) == 1
    entry = added[0]
    assert entry["claim_id"] == CBR_OCT
    assert entry["series"] == "cbr:1"  # своя серия собственных прогнозов Банка России
    assert entry["replaces"] == [CBR_JULY]


def test_pack_record_of_a_series_absent_from_the_card_is_not_added() -> None:
    """Консенсус опроса (`опрос:cbr:*`) — НЕ собственный прогноз Банка России: своей
    карточной записи этой серии нет — не добавляем (опрос и прогноз ведомства — разные
    серии); сентябрьский SberCIB в паке устарел сам — тоже не кандидат."""
    card = [rec(CBR_JULY), rec(ALFA)]
    added, _ = fresher_forecast_supplement(card, [rec(CBR_SURVEY_CONSENSUS)], 2, RELIED_CLAIMS_LIMIT)
    assert added == []
    added, _ = fresher_forecast_supplement([rec(SBERCIB_CURRENT)], [rec(SBERCIB_SEP)], 1, RELIED_CLAIMS_LIMIT)
    assert added == []


def test_stand_c7e825e6_new_proposal_is_outdated_and_fresh_pack_record_replaces_it() -> None:
    """Стенд c7e825e6: карточная запись — НОВОЕ утверждение предложения (`new0`,
    Сбербанк от 30.07.2026); в паке — сентябрьский прогноз SberCIB (тот же производитель
    по мосту «Сбербанк» → sbercib, тот же горизонт). Замещение названо ключом `new0`."""
    card = [SupplementRecord("new0", STATEMENTS[SBERCIB_JUL], AS_OF[SBERCIB_JUL]), rec(ALFA)]
    added, rejected = fresher_forecast_supplement(card, [rec(SBERCIB_SEP)], 2, RELIED_CLAIMS_LIMIT)
    assert rejected == []
    assert added == [{"claim_id": SBERCIB_SEP, "series": "sbercib:1", "replaces": ["new0"]}]


def test_undated_card_record_of_the_series_blocks_the_addition() -> None:
    """Стенды 53be7fd6 и 1e4ef28d: на карточке — новая запись серии БЕЗ даты (`undated`).
    Правило (в): недатированная карточная запись замещать нечем — паковая не добавляется."""
    new_undated = SupplementRecord(
        "new0", "Аналитики SberCIB прогнозируют инфляцию в России на конец 2026 года на уровне 6,5%.", None
    )
    added, _ = fresher_forecast_supplement([new_undated], [rec(SBERCIB_UNDATED_CLAIM)], 1, RELIED_CLAIMS_LIMIT)
    assert added == []

    # стенд 1e4ef28d: «Банк России повысил прогноз …» без даты решения и без as_of — undated
    new_cbr_undated = SupplementRecord(
        "new1", "Банк России повысил прогноз инфляции на 2026 год до 6–7%.", None
    )
    added, _ = fresher_forecast_supplement([new_cbr_undated], [rec(CBR_OCT)], 1, RELIED_CLAIMS_LIMIT)
    assert added == []


def test_actual_card_record_of_the_series_blocks_the_addition() -> None:
    """Стенд 70b19b00: на карточке уже АКТУАЛЬНАЯ запись SberCIB (27806b5b, 20.10.2026) —
    более ранние паковые записи не добавляются."""
    added, _ = fresher_forecast_supplement(
        [rec(SBERCIB_CURRENT), rec(ALFA)], [rec(SBERCIB_SEP), rec(SBERCIB_JUL)], 2, RELIED_CLAIMS_LIMIT
    )
    assert added == []


def test_different_horizon_or_producer_is_a_different_series_and_stays_quiet() -> None:
    """Другой горизонт («на 2027 год») и другой производитель — другие серии: карточная
    запись этой серии отсутствует, паковая молчит."""
    card = [SupplementRecord(CBR_JULY, STATEMENTS[CBR_JULY], AS_OF[CBR_JULY])]
    pack_2027 = SupplementRecord(
        "00000000-0000-4000-8000-00000000000a",
        "Банк России повысил прогноз годовой инфляции в России на 2027 год до 4–5% "
        "(по итогам заседания 23 октября 2026 года).",
        D(2026, 10, 23),
    )
    added, _ = fresher_forecast_supplement(card, [pack_2027], 1, RELIED_CLAIMS_LIMIT)
    assert added == []


def test_shared_ceiling_refuses_by_name_with_pack_position() -> None:
    relied_count = RELIED_CLAIMS_LIMIT  # опора уже на потолке
    added, rejected = fresher_forecast_supplement(_card_008d93ac(), _pack_008d93ac(), relied_count, RELIED_CLAIMS_LIMIT)
    assert added == []
    assert rejected == [f"fresher_forecast[0]: more than {RELIED_CLAIMS_LIMIT} relied ids"]


def test_quiet_series_and_singletons_stay_silent() -> None:
    """Одиночные прогнозы (Альфа-Банк, Газпромбанк) и тихие серии молчат: поля нет —
    карточка без свежей замещающей записи остаётся прежней."""
    card = [rec(ALFA)]
    added, rejected = fresher_forecast_supplement(card, [rec(VTB_HI)], 1, RELIED_CLAIMS_LIMIT)
    assert added == [] and rejected == []


def test_empty_inputs_yield_empty_result() -> None:
    added, rejected = fresher_forecast_supplement([], [], 0, RELIED_CLAIMS_LIMIT)
    assert added == []
    assert rejected == []
