"""Unit: таблица 20 записей стендовой фикстуры T7.100 — метка витрины «было → стало».

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
    # ─── меняются правкой T7.100: единственная улика + записанный указатель ───
    (
        "6d9a12ff", "external_fact", FEW,
        [_row(parent_uri=CBR_FORECAST_PDF)],
        WEAK_LABEL, PRODUCER_LABEL, "Банк России",
    ),
    (
        "7cc2b578", "external_fact", FEW,
        [_va("rosstat", "Росстат")],
        WEAK_LABEL, PRODUCER_LABEL, "Росстат",
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


def _badge(claim_type: str, reasons: list[str], rows: list[dict[str, Any]]) -> dict[str, str]:
    """Тот же путь витрины, что у карточки: правило → `reliability` (grade/E1/hypothesis — из фикстуры)."""
    case = producer_publication_case(rows, reasons=reasons)
    return reliability(
        claim_type,
        "hypothesis",
        "E1",
        head_state="current",
        single_producer=case.name if case is not None else None,
        producer_supports=case.supports if case is not None else None,
    )


def test_table_covers_the_whole_fixture() -> None:
    assert len(TABLE) == 20
    assert len({row[0] for row in TABLE}) == 20


def test_labels_after_the_change() -> None:
    changed: list[str] = []
    for claim8, claim_type, reasons, rows, was, now, producer in TABLE:
        badge = _badge(claim_type, reasons, rows)
        assert badge["label"] == now, claim8
        if was != now:
            changed.append(claim8)
        if producer is not None:
            assert producer in badge["hint"], claim8
        else:
            assert "производител" not in badge["hint"], claim8
    # правка T7.100 меняет ровно две записи фикстуры — те, где единственная улика
    # объяснена `insufficient_evidence`, и есть записанный указатель на производителя
    assert changed == ["6d9a12ff", "7cc2b578"]


def test_single_evidence_cases_get_the_single_hint() -> None:
    for claim8, claim_type, reasons, rows, _was, now, producer in TABLE:
        if now != PRODUCER_LABEL:
            continue
        hint = _badge(claim_type, reasons, rows)["hint"]
        if claim8 in ("6d9a12ff", "7cc2b578"):
            assert "одна публикация" in hint and "кластер" not in hint, claim8
        else:
            assert "один кластер" in hint, claim8
        assert producer is not None and producer in hint


def test_level_and_color_are_untouched_for_every_record() -> None:
    plain = reliability("external_fact", "hypothesis", "E1", head_state="current")
    for claim8, claim_type, reasons, rows, _was, _now, _producer in TABLE:
        badge = _badge(claim_type, reasons, rows)
        assert badge["level"] == LEVEL_WEAK, claim8
        assert badge["color"] == plain["color"], claim8
