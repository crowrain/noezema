"""Unit: таблица 21 записи стендовой фикстуры T7.100 (20 записей + 1 запись T7.116) — метка
витрины «было → стало».

Фикстура: `~/fixtures-t7100/claims_detail_provenance.json` (стенд .92, карта 22d1748e и
сессия T7.97). Для каждой записи воспроизведены те колонки действующей оценки, которые
витрина и передаёт правилу: записанные причины оценки, роли улик, записанная группа
независимости, запись `value_attribution` и якорь-родитель источника. Оценку здесь никто
не вычисляет: grade/E1/hypothesis взяты из фикстуры, правило только называет случай
(ADR-0026).

Что закреплено:
* правка T7.100 (ADR-0035 §18) меняет метку РОВНО на двух записях — `6d9a12ff…` (единственная
  улика-forbes с якорем-родителем на документ ЦБ) и `7cc2b578…` (единственная улика-sbercib
  с записью об атрибутции значения на Росстат); обе объяснены `insufficient_evidence`;
* третьи собственные оценки (Альфа-Банк, SberCIB, Т-Инвестиции, ACI Russia) и записи без
  записанного указателя (страницы самого ЦБ, garant.ru, un.org) остаются на «Подтверждено
  слабо» — fail-closed: называть производителя по хосту прочитанной страницы правило не
  имеет права;
* уровень, цвет и grade не меняются ни у одной записи: метка — только перевод подписи.

С T7.116 (часть б, решение пользователя 2026-10-15) страничный указатель называет строку
только когда формулировка утверждения называет того же производителя словарным алиасом;
записи `value_attribution`/`value_self_publication` называют по-прежнему. Формулировки —
долговременные `claims.statement` тех же записей (`tests/fixtures/t7101/claims_detail_provenance.json`).
Здесь закреплена и правка T7.116: ровно две записи теряют подпись «Опубликовано
производителем» — `6d9a12ff…` (единственная улика-forbes с якорем на документ ЦБ, но
формулировка ссылается на опрос «инФОМ» и словарного производителя не называет; этим
отменяется ВСЁ изменение метки T7.100 для этой записи) и `85220305…` (стенд 53be7fd6:
мнение инвестбанкира с якорем-родителем на minfin.gov.ru, формулировка производителя не
называет). `9266248e…` («Банк России и Росстат…») и `7cc2b578…` (запись об атрибуции)
подпись сохраняют: называющая формулировка и запись сильнее указателя. Уровень и цвет
по-прежнему не меняются ни у одной записи.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.web.producer_view import producer_publication_case
from apps.web.reliability import LEVEL_WEAK, PRODUCER_LABEL, reliability

pytestmark = pytest.mark.unit

WEAK_LABEL = "Подтверждено слабо"

ROSSTAT = "https://rosstat.gov.ru/"
CBR = "https://cbr.ru/"
CBR_FORECAST_PDF = "https://www.cbr.ru/Content/Document/File/194258/forecast_260724.pdf"
MINFIN = "https://minfin.gov.ru/"


def _row(
    *,
    group: str | None = "g0",
    role: str | None = "support",
    va_key: str | None = None,
    va_name: str | None = None,
    parent_uri: str | None = None,
) -> dict[str, Any]:
    """Одна строка улики действующей оценки — колонки витринного запроса (T7.87/T7.100)."""
    scope: dict[str, Any] = {"scope_schema": "host-scope-v1", "source_domain": "example"}
    if va_key is not None:
        scope["value_attribution"] = {
            "schema": "host-value-attribution-v1",
            "primary_key": va_key,
            "primary_name": va_name,
            "primary_uri": ROSSTAT if va_key == "rosstat" else CBR,
            "parent_source_id": str(uuid.uuid4()),
            "method": "host-value-attribution-v1",
            "basis_fragment": "По данным первоисточника",
            "pairing": "value_and_primary_in_fragment",
        }
    return {
        "assessment_role": role,
        "group_id": group,
        "scope": scope,
        "parent_source_id": str(uuid.uuid4()) if parent_uri is not None else None,
        "parent_uri": parent_uri,
    }


def _va(key: str, name: str) -> dict[str, Any]:
    return _row(va_key=key, va_name=name)


INDEP = ["insufficient_independence"]
FEW = ["insufficient_evidence"]

# (id8, claim_type, причины оценки, улики действующей оценки, метка «было», метка «стало», производитель)
TABLE: list[tuple[str, str, list[str], list[dict[str, Any]], str, str, str | None]] = [
    # ─── уже подписаны случаем (T7.87) — остаются подписанными ───
    (
        "9266248e", "temporal_fact", INDEP,
        [_va("rosstat", "Росстат"), _row(), _va("rosstat", "Росстат"), _va("rosstat", "Росстат"),
         _va("rosstat", "Росстат"), _va("rosstat", "Росстат"), _row(), _row(parent_uri=ROSSTAT)],
        PRODUCER_LABEL, PRODUCER_LABEL, "Росстат",
    ),
    (
        "391c4388", "temporal_fact", INDEP,
        [
            _row(),
            _row(parent_uri=CBR_FORECAST_PDF, va_key="cbr", va_name="Банк России"),
            _va("cbr", "Банк России"),
        ],
        PRODUCER_LABEL, PRODUCER_LABEL, "Банк России",
    ),
    (
        "136a0ca0", "external_fact", INDEP,
        [_va("cbr", "Банк России"), _va("cbr", "Банк России")],
        PRODUCER_LABEL, PRODUCER_LABEL, "Банк России",
    ),
    # ─── менялись правкой T7.100; правка T7.116 (часть б) пересматривает их заново ───
    (
        "6d9a12ff", "external_fact", FEW,
        [_row(parent_uri=CBR_FORECAST_PDF)],
        # было (после T7.100): PRODUCER — единственная улика с якорем на документ ЦБ. Стало
        # (T7.116 часть б): WEAK — формулировка называет опрос «инФОМ» (не словарный
        # производитель), поэтому указатель имени не даёт; изменение метки T7.100 для этой
        # записи отменяется целиком.
        PRODUCER_LABEL, WEAK_LABEL, None,
    ),
    (
        "7cc2b578", "external_fact", FEW,
        [_va("rosstat", "Росстат")],
        # было (после T7.100): PRODUCER. Стало: PRODUCER — запись об атрибуции значения
        # называет Росстат (запись сильнее указателя, T7.109) и формулировка называет
        # производителей; правка T7.116 на записи не отражается.
        PRODUCER_LABEL, PRODUCER_LABEL, "Росстат",
    ),
    # ─── T7.116 (часть б): прямая закрепка снятия подписи по указателю (стенд 53be7fd6) ───
    (
        "85220305", "external_fact", FEW,
        [_row(parent_uri=MINFIN)],
        # было (до T7.116): PRODUCER по якорю-родителю на minfin.gov.ru. Стало: WEAK —
        # мнение third party, формулировка Минфина не называет; страница-улика не домашняя
        # для него и значения Минфина не публиковала.
        PRODUCER_LABEL, WEAK_LABEL, None,
    ),
    # ─── собственные оценки третьих акторов: указателя нет — прежняя подпись ───
    ("07126d06", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("44bd1460", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("7d810f8c", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("e61b4a58", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    # ─── страницы самого ЦБ без записанного решения о происхождении значения: fail-closed ───
    ("2f456e85", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("7c8c9afe", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("c6729d4c", "external_fact", INDEP, [_row(), _row()], WEAK_LABEL, WEAK_LABEL, None),
    ("cc75e97f", "external_fact", INDEP, [_row(), _row()], WEAK_LABEL, WEAK_LABEL, None),
    ("58e2d5d4", "temporal_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("2d010d53", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("73d52835", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("e2c52955", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    # ─── прочие записи без указателя ───
    ("e189c80d", "temporal_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
    ("c970bc08", "external_fact", INDEP, [_row(), _row()], WEAK_LABEL, WEAK_LABEL, None),
    ("d8155167", "external_fact", FEW, [_row()], WEAK_LABEL, WEAK_LABEL, None),
]


#: Долговременные формулировки тех же записей (`claims.statement` из фикстуры
#: `tests/fixtures/t7101/claims_detail_provenance.json`; `85220305…` — стенд 53be7fd6).
#: С T7.116 (часть б) витрина передаёт их правилу вместе с уликами.
STATEMENTS: dict[str, str] = {
    "9266248e": "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
                "составила 5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)",
    "391c4388": "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила "
                "14,5% (по данным опроса инФОМ для Банка России).",
    "136a0ca0": "Банк России в июле 2026 года повысил прогноз инфляции на 2026 год до 6–7%.",
    "6d9a12ff": "Медианная оценка годовой наблюдаемой инфляции населением России в декабре 2025 "
                "года составила 13,1% для граждан с накоплениями и 15,6% для граждан без "
                "накоплений (по данным опроса «инФОМ»).",
    "7cc2b578": "Фактическая годовая инфляция в России по итогам 2025 года (5,59%) оказалась ниже "
                "всех прогнозов аналитиков: прогноз Банка России — 6,5–7%, Минэкономразвития — 6,8%.",
    "85220305": "Инвестбанкир Дмитрий Коган ожидает ускорения инфляции в России до 7,0-7,5% "
                "в 2026 году.",
    "07126d06": "Аналитики Альфа-Банка прогнозируют инфляцию в России по итогам 2026 года на уровне 7,5%.",
    "44bd1460": "Аналитики SberCIB прогнозируют инфляцию в России на конец 2026 года на уровне 6,5%.",
    "7d810f8c": "Софья Донец из Т-Инвестиций оценивает, что повышение НДС добавит 1,5% к инфляции "
                "на горизонте 5-6 месяцев в 2026 году.",
    "e61b4a58": "Евгений Егоров из ACI Russia оценивает, что цены вырастут на дополнительные 2% "
                "из-за налоговых инициатив в 2026 году.",
    "2f456e85": "Консенсус-прогноз аналитиков по годовой инфляции в России на 2026 год составляет "
                "6,6% (по данным макроэкономического опроса Банка России за сентябрь 2026 года).",
    "7c8c9afe": "Наблюдаемая населением годовая инфляция в России в сентябре 2026 года увеличилась "
                "до 15,1% (по данным опроса ООО «инФОМ»).",
    "c6729d4c": "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%.",
    "cc75e97f": "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2% "
                "(по данным опроса ООО «инФОМ»).",
    "58e2d5d4": "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
                "декабрьского макроэкономического опроса Банка России составил 6,3%.",
    "2d010d53": "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
                "декабрьского макроэкономического опроса Банка России составил 6,3%.",
    "73d52835": "В пресс-релизе Банка России (event id 28251) годовая инфляция в 2025 году указана как 5,6%.",
    "e2c52955": "В информационно-аналитическом комментарии Банка России «Инфляция в России» № 12 (120) "
                "за декабрь 2025 г. годовая инфляция за 2025 год приведена как 5,6%.",
    "e189c80d": "Официальная годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
                "составила 5,59% (по данным Росстата).",
    "c970bc08": "Независимые оценки Сбера/СберCIB совпали с официальной цифрой Росстата: инфляция "
                "в России по итогам 2025 года составила 5,6% — расхождений с официальными данными нет.",
    "d8155167": "ООН состоит из 193 государств-членов.",
}


def _badge(
    claim_type: str,
    reasons: list[str],
    rows: list[dict[str, Any]],
    statement: str | None = None,
) -> dict[str, str]:
    """Тот же путь витрины, что у карточки: правило → `reliability` (grade/E1/hypothesis — из фикстуры)."""
    case = producer_publication_case(rows, reasons=reasons, claim_statement=statement)
    return reliability(
        claim_type,
        "hypothesis",
        "E1",
        head_state="current",
        single_producer=case.name if case is not None else None,
        producer_supports=case.supports if case is not None else None,
    )


def test_table_covers_the_whole_fixture() -> None:
    # 20 записей стендовой фикстуры T7.100 + 1 запись-закрепка T7.116 (стенд 53be7fd6)
    assert len(TABLE) == 21
    assert len({row[0] for row in TABLE}) == 21
    assert set(STATEMENTS) == {row[0] for row in TABLE}


def test_labels_after_the_change() -> None:
    changed: list[str] = []
    for claim8, claim_type, reasons, rows, was, now, producer in TABLE:
        badge = _badge(claim_type, reasons, rows, STATEMENTS.get(claim8))
        assert badge["label"] == now, claim8
        if was != now:
            changed.append(claim8)
        if producer is not None:
            assert producer in badge["hint"], claim8
        else:
            assert "производител" not in badge["hint"], claim8
    # «было» — метка до правки T7.116 (часть б), «стало» — после. Правка снимает подпись
    # ровно на двух записях, где имя давал один страничный указатель, а формулировка
    # производителя не называет: 6d9a12ff… (отменяет изменение T7.100 целиком) и
    # 85220305…. Все записанные атрибутции (7cc2b578…) и называющие формулировки (9266248e…)
    # остаются подписанными.
    assert changed == ["6d9a12ff", "85220305"]


def test_single_evidence_cases_get_the_single_hint() -> None:
    for claim8, claim_type, reasons, rows, _was, now, producer in TABLE:
        if now != PRODUCER_LABEL:
            continue
        hint = _badge(claim_type, reasons, rows, STATEMENTS.get(claim8))["hint"]
        if claim8 == "7cc2b578":
            # единственная FEW-подпись, оставшаяся после T7.116 (часть б): она держится
            # записью об атрибуции, а не указателем — «одна публикация», не «кластер»
            assert "одна публикация" in hint and "кластер" not in hint, claim8
        else:
            assert "один кластер" in hint, claim8
        assert producer is not None and producer in hint


def test_level_and_color_are_untouched_for_every_record() -> None:
    plain = reliability("external_fact", "hypothesis", "E1", head_state="current")
    for claim8, claim_type, reasons, rows, _was, _now, _producer in TABLE:
        badge = _badge(claim_type, reasons, rows, STATEMENTS.get(claim8))
        assert badge["level"] == LEVEL_WEAK, claim8
        assert badge["color"] == plain["color"], claim8
