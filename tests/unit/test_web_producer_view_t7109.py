"""Unit: T7.109 (часть б3, решение пользователя 2026-10-13) — витрина называет производителя
по записи о происхождении значения, страничный указатель для этой строки отступает.

Было (T7.87): все три указателя строки равноправны, разночтение записи и якоря-родителя —
«один производитель не доказан» → витрина молчит. Стало: если `value_attribution` называет
производителя, якорь-родитель ЭТОЙ ЖЕ строки в выборке имени не участвует (запись — решение
детектора значения, прочитанного прямо на улике). Строки без записи о значении называются
якорем-родителем по-прежнему; запись о собственной публикации — как сейчас (T7.101).

Fail-closed по краям закреплён отдельными тестами: битая запись (указатель есть, производитель
не читается) — подписи нет; разночтение записей ДВУХ разных строк — подписи нет; приоритет
построконечный, а не глобальный.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.web.producer_view import producer_publication_name
from packages.memory.scope import VALUE_ATTRIBUTION_SCHEMA

pytestmark = pytest.mark.unit

SHORTFALL: list[str] = ["insufficient_evidence"]

ROSSTAT = "https://rosstat.gov.ru/"
CBR = "https://cbr.ru/"
ECONOMY = "https://www.economy.gov.ru/"

MINEC_NAME = "Минэкономразвития России"


def _row(
    *,
    group: str | None = "g0",
    role: str | None = "support",
    primary_key: str | None = None,
    primary_name: str | None = None,
    primary_uri: str | None = None,
    parent_uri: str | None = None,
    self_publication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Строка улики действующей оценки — те же колонки, что отдаёт карточка."""
    scope: dict[str, Any] = {"scope_schema": "host-scope-v1", "source_domain": "interfax.example"}
    if primary_key is not None:
        scope["value_attribution"] = {
            "schema": VALUE_ATTRIBUTION_SCHEMA,
            "primary_key": primary_key,
            "primary_name": primary_name,
            "primary_uri": primary_uri or ECONOMY,
            "parent_source_id": str(uuid.uuid4()),
            "method": "host-value-attribution-v8",
            "basis_fragment": "прогноз Минэкономразвития",
            "pairing": "value_and_primary_in_fragment",
        }
    if self_publication is not None:
        scope["value_self_publication"] = self_publication
    return {
        "assessment_role": role,
        "group_id": group,
        "scope": scope,
        "parent_source_id": str(uuid.uuid4()) if parent_uri is not None else None,
        "parent_uri": parent_uri,
    }


def _name(*rows: dict[str, Any]) -> str | None:
    return producer_publication_name(list(rows), reasons=SHORTFALL)


# ─── т7 главный случай: запись A + якорь B → имя A ───────────────────────────


def test_value_record_names_the_producer_and_the_page_pointer_steps_back() -> None:
    """Запись значения называет Минэкономразвития, а якорь-родитель той же строки ведёт на
    Росстат: подписью становится запись (было: молчание «разночтение»)."""
    assert _name(
        _row(
            primary_key="minec",
            primary_name=MINEC_NAME,
            primary_uri=ECONOMY,
            parent_uri=ROSSTAT,
        )
    ) == MINEC_NAME
    # и на хосте Банка России та же логика: якорь не участвует, когда запись названа
    assert _name(
        _row(primary_key="minec", primary_name=MINEC_NAME, primary_uri=ECONOMY, parent_uri=CBR)
    ) == MINEC_NAME


def test_row_without_a_value_record_is_still_named_by_the_page_pointer() -> None:
    """Строка без записи о значении — прежнее поведение: указывает якорь-родитель."""
    assert _name(_row(parent_uri=ROSSTAT)) == "Росстат"


def test_broken_parent_does_not_disqualify_a_named_value_record() -> None:
    """Якорь есть, но не читается (пустой адрес при записанном parent_source_id): строка с
    названной записью значения от этого не ломается — чужой указатель не читается вовсе."""
    assert _name(
        _row(primary_key="minec", primary_name=MINEC_NAME, parent_uri="")
    ) == MINEC_NAME


def test_broken_value_record_still_disqualifies_the_whole_case() -> None:
    """Fail-closed (условие (в)): указатель на значение ЕСТЬ, но производитель по нему не
    читается (имя не совпадает со словарём) — подписи нет, даже если якорь-родитель ведёт
    на Росстат."""
    assert _name(_row(primary_key="rosstat", primary_name="Росстата", parent_uri=ROSSTAT)) is None


def test_self_publication_record_and_page_pointer_disagreement_stays_disqualifying() -> None:
    """T7.101 не тронут, но и близость T7.109 к нему не применяется: записи о значении нет,
    поэтому якорь-родитель участвует — и называет другого производителя."""
    assert _name(
        _row(
            parent_uri=ROSSTAT,
            self_publication={
                "schema": "host-value-self-publication-v1",
                "primary_key": "cbr",
                "primary_name": "Банк России",
                "primary_uri": CBR,
                "method": "host-value-attribution-v8",
            },
        )
    ) is None  # self-запись называет ЦБ, якорь — Росстат: прежний честный отказ
    assert _name(
        _row(
            self_publication={
                "schema": "host-value-self-publication-v1",
                "primary_key": "cbr",
                "primary_name": "Банк России",
                "primary_uri": CBR,
                "method": "host-value-attribution-v8",
            },
        )
    ) == "Банк России"


def test_priority_is_per_row_two_records_naming_different_producers_stay_disagreement() -> None:
    """Приоритет записи действует на строку, а не на всю витрину: две СТРОКИ с записями о
    разных производителях — прежний отказ."""
    assert _name(
        _row(primary_key="minec", primary_name=MINEC_NAME),
        _row(primary_key="rosstat", primary_name="Росстат", primary_uri=ROSSTAT),
    ) is None


def test_named_record_row_and_pointer_row_for_the_same_producer_still_agree() -> None:
    """Согласие не сломано: строка с записью Минэкономразвития + строка без записей, чей
    якорь ведёт на economy.gov.ru, — один производитель."""
    assert _name(
        _row(primary_key="minec", primary_name=MINEC_NAME),
        _row(parent_uri="https://economy.gov.ru/press/"),
    ) == MINEC_NAME
