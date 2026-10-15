"""Unit: T7.116 (часть б) — указатель страницы называет производителя строки витрины только
когда производитель назван в самом утверждении.

Стендовый дефект (сессия 53be7fd6, утверждение 85220305 — частное мнение инвестбанкира
Когана «…повысит прогноз инфляции на 2026 год до 7,0-7,5%»): улика на lenta.ru не имела
записи о происхождении значения, но её источник был привязан якорем-родителем к
minfin.gov.ru — и витрина подписала чужое мнение «Опубликовано производителем: Минфин
России», хотя Минфин в формулировке утверждения не упоминался вообще.

Закреплено (ADR-0035 дополнение): `scope.value_attribution` называет производителя как
прежде; запись о собственной публикации — как прежде (T7.101); указатель страницы
(`sources.parent_source_id → canonical_uri якоря`) участвует в выборе имени ТОЛЬКО когда
производитель назван словарным алиасом в формулировке утверждения (тот же словарь
`PRIMARY_SOURCES`, что и в детекторе). Строки без записей о значении с названным в
утверждении производителем подписываются указателем по-прежнему (аналог фикстур T7.101:
«…по данным Росстата» + якорь rosstat.gov.ru → «Росстат»).

Совместимость (ADR-0035 §Совместимость): вызывающий, не передавший утверждение, получает
указатель НЕучастным — молчание вместо подписи (fail-closed).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.web.producer_view import producer_publication_case, producer_publication_name

pytestmark = pytest.mark.unit

SHORTFALL: list[str] = ["insufficient_evidence"]

ROSSTAT = "https://rosstat.gov.ru/"
MINFIN = "https://minfin.gov.ru/"
CBR = "https://cbr.ru/"

STATEMENT_KOGAN = (
    "Инвестбанкир Евгений Коган предполагает, что Банк России повысит прогноз инфляции "
    "на 2026 год до 7,0-7,5%."
)
STATEMENT_ROSSTAT = "Инфляция в России по итогам 2025 года составила 5,59% по данным Росстата."
STATEMENT_NO_ALIAS = "Годовая инфляция в России по итогам 2025 года составила 5,59%."


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
    """Строка улики действующей оценки — те же колонки, что отдаёт карточка (тот же посев,
    что `tests/unit/test_web_producer_view_t7109.py` и `tests/fixtures/t7101/` формы)."""
    scope: dict[str, Any] = {"scope_schema": "host-scope-v1", "source_domain": "lenta.example"}
    if primary_key is not None:
        scope["value_attribution"] = {
            "schema": "host-value-attribution-v1",
            "primary_key": primary_key,
            "primary_name": primary_name,
            "primary_uri": primary_uri or CBR,
            "parent_source_id": str(uuid.uuid4()),
            "method": "host-value-attribution-v13",
            "basis_fragment": "по данным первоисточника",
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


def _name(rows: list[dict[str, Any]], statement: str | None) -> str | None:
    return producer_publication_name(rows, reasons=SHORTFALL, claim_statement=statement)


# ─── стендовый дефект: чужое мнение не подписывается чужим якорем ───────────────


def test_minfin_page_pointer_does_not_sign_an_opinion_that_names_minfin_nowhere() -> None:
    """Было (8ffb4c9): «Опубликовано производителем: Минфин России» на мнении Когана —
    указатель lenta → minfin.gov.ru назвал производителя без его упоминания в утверждении.
    Стало: подписи нет (Банк России в тексте утверждения — не словарь указателя строки)."""
    assert (
        _name([_row(parent_uri="https://minfin.gov.ru/press-center/...")], STATEMENT_KOGAN) is None
    )


def test_page_pointer_still_names_the_producer_the_statement_names() -> None:
    """Аналог строк фикстур T7.101: «… по данным Росстата» + якорь-родитель rosstat.gov.ru →
    подпись «Росстат» прежняя (условие не ослаблено, правило уточнено)."""
    assert _name([_row(parent_uri="https://rosstat.gov.ru/oper/12345")], STATEMENT_ROSSTAT) == "Росстат"


def test_statement_without_a_dictionary_alias_disables_the_pointer() -> None:
    """Утверждение без словарного алиаса — указатель не участвует вовсе: строка с якорем на
    Росстат остаётся без подписи (молчание дешевле ложной атрибуции)."""
    assert _name([_row(parent_uri="https://rosstat.gov.ru/oper/12345")], STATEMENT_NO_ALIAS) is None


def test_no_statement_passed_means_no_pointer_naming() -> None:
    """Fail-closed совместимости: вызывающий без утверждения не получает имени по указателю."""
    rows = [_row(parent_uri=ROSSTAT)]
    assert producer_publication_name(rows, reasons=SHORTFALL) is None
    assert producer_publication_case(rows, reasons=set(SHORTFALL)) is None


def test_case_builder_gets_the_statement_too() -> None:
    """Тот же контракт у построителя случая (его вызывают список вопросов и карточка)."""
    case = producer_publication_case(
        [_row(parent_uri=CBR)], reasons=set(SHORTFALL), claim_statement="Ставку сохранил Банк России."
    )
    assert case is not None and case.name == "Банк России", case
    assert producer_publication_case(
        [_row(parent_uri=CBR)], reasons=set(SHORTFALL), claim_statement="Ставку сохранили без изменений."
    ) is None


# ─── записи не затронуты: value_attribution и собственная публикация ────────────


def test_value_record_names_producer_regardless_of_the_statement() -> None:
    """Запись о происхождении значения — как прежде (T7.109): названа без алиаса в
    формулировке, подписывает строку; указатель той же строки не участвует."""
    assert _name(
        [_row(primary_key="cbr", primary_name="Банк России", primary_uri=CBR, parent_uri=ROSSTAT)],
        STATEMENT_NO_ALIAS,
    ) == "Банк России"


def test_self_publication_record_is_unaffected_but_named_pointer_still_conflicts() -> None:
    """T7.101 не тронут: собственная публикация названа — имя есть; но если утверждение
    называет ДРУГОГО словарного производителя, якорь-родитель участвует и подписи нет."""
    self_pub = {
        "schema": "host-value-self-publication-v1",
        "primary_key": "cbr",
        "primary_name": "Банк России",
        "primary_uri": CBR,
        "method": "host-value-attribution-v13",
    }
    assert _name([_row(parent_uri=ROSSTAT, self_publication=self_pub)], STATEMENT_NO_ALIAS) == "Банк России"
    assert _name([_row(parent_uri=ROSSTAT, self_publication=self_pub)], STATEMENT_ROSSTAT) is None


def test_two_rows_one_pointer_named_one_not_still_one_name() -> None:
    """Построчность правила: указатель названной строки и невыбранная указательная строка
    одной группы — одно имя, подпись есть."""
    assert (
        _name(
            [
                _row(parent_uri=ROSSTAT),
                _row(parent_uri="https://cbr.ru/press/1"),
            ],
            STATEMENT_ROSSTAT,
        )
        == "Росстат"
    )


def test_named_pointers_of_two_rows_naming_different_producers_stay_disagreement() -> None:
    """Прежний честный отказ сохранён: обе строки названы утверждением, имена разные."""
    statement = "Инфляцию оценили Росстат и Банк России отдельно."
    assert (
        _name([_row(parent_uri=ROSSTAT), _row(parent_uri=CBR)], statement) is None
    )
