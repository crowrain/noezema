"""T7.101 (ADR-0029 дополнение, ADR-0035 §19): запись о собственной публикации производителя.

Три слоя проверки:

1. форма записи (`packages/memory/scope.py`): сборка/разбор fail-closed, перенос при
   пересчёте скоупа (T7.17: без переноса запись молча стиралась бы при следующем коммите);
2. решение (`apps/research_proxy/value_attribution.py::self_publication_candidate`):
   решение принимает ТОТ ЖЕ `attribute_value_in_fragment`, функция лишь называет производителя
   и проверяет вето третьего актора. Стендовые тексты зафиксированы побайтово
   (`tests/fixtures/t7101/`, sha256 = `normalized_sha256` строк источников базы .92):

       65af8c97e7f9…  https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/                       (c6729d4c)
       2bb395fbe861…  https://www.cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_26-09/
       7e2eb44100d5…  https://cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_25-12/  (2d010d53, 58e2d5d4)
       218e9a1ea998…  https://cbr.ru/analytics/dkp/dinamic/CPD_2025-12/                      (ловушка T7.87)
       7fc081956fff…  https://expert.ru/news/godovaya-inflyatsiya-v-rossii-v-2025-godu-sostavila-5-59
       14b070da98f1…  https://www.un.org/en/about-us

   Ловушка T7.87 обязана выдержать: страница ЦБ с цифрой Росстата (CPD_2025-12, 5,59%) НЕ даёт
   self_primary — значение на странице не атрибутировано вовсе (`value_not_attributed`), и
   запись о «собственной публикации Росстата/ЦБ» не появится ни при каких условиях.
   Честность ADR-0035 §15: «Аналитики прогнозируют…» на странице ЦБ — self_primary с вето
   третьего актора: запись не пишется, витрина молчит, как молчала.
3. витрина (`apps/web/producer_view.py`): запись читается как указатель на производителя,
   но проверяется заново (имя из словаря, адрес на домашнем хосте); противоречие записи со
   словарём или с другой записью — fail-closed (метки нет).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from apps.research_proxy.value_attribution import (
    STATUS_SELF_PRIMARY,
    attribute_value_in_fragment,
    self_publication_candidate,
)
from apps.web.producer_view import producer_publication_case
from packages.memory.scope import (
    EVIDENCE_SELF_PUBLICATION_KEY,
    EVIDENCE_VALUE_ATTRIBUTION_KEY,
    SELF_PUBLICATION_SCHEMA,
    build_self_publication,
    carry_value_attribution,
    parse_self_publication,
)

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "t7101"

STAND_URI: dict[str, str] = {
    "cbr_CPD_2026-7": "https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/",
    "cbr_Infl_exp_26-09": "https://www.cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_26-09/",
    "cbr_Infl_exp_25-12": "https://cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_25-12/",
    "cbr_CPD_2025-12": "https://cbr.ru/analytics/dkp/dinamic/CPD_2025-12/",
    "expert_5-59": "https://expert.ru/news/godovaya-inflyatsiya-v-rossii-v-2025-godu-sostavila-5-59",
    "un_about": "https://www.un.org/en/about-us",
}

#: нормализованные sha256 артефактов стенда .92 (копии зафиксированы побайтово, файл назван
#: полным sha256; `_load` сверяет хеш содержимого с именем файла)
_PREFIX: dict[str, str] = {
    "cbr_CPD_2026-7": "65af8c97e7f9",
    "cbr_Infl_exp_26-09": "2bb395fbe861",
    "cbr_CPD_2025-12": "218e9a1ea998",
    "cbr_Infl_exp_25-12": "7e2eb44100d5",
    "expert_5-59": "7fc081956fff",
    "un_about": "14b070da98f1",
}

STATEMENT_607 = (
    "Годовая инфляция в России по итогам 2026 года по прогнозу Банка России составит 6,0–7,0%."
)
STATEMENT_63 = (
    "Прогноз аналитиков, опрошенных Банком России, по годовой инфляции на конец 2025 года "
    "составил 6,3%, на конец 2026 года — 5,1%."
)
STATEMENT_559 = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила 5,59%."
)
STATEMENT_UN = "Организация Объединённых Наций состоит из 193 государств-членов."


def _load(name: str) -> str:
    sha_prefix = _PREFIX[name]
    files = [p for p in FIXTURE_DIR.glob(f"{sha_prefix}*.txt*") if p.name.startswith(sha_prefix)]
    assert len(files) == 1, (name, files)
    path = files[0]
    if path.name.endswith(".json"):
        # страница с CJK-ссылками сохранена JSON-экранированной (diff только ASCII);
        # декод — побайтово исходный нормализованный текст
        data = json.loads(path.read_text(encoding="ascii")).encode("utf-8")
        sha_name = path.name[: -len(".txt.json")]
    else:
        data = path.read_bytes()
        sha_name = path.name[: -len(".txt")]
    assert hashlib.sha256(data).hexdigest() == sha_name, name
    return data.decode("utf-8")


# ─── 1: форма записи ───────────────────────────────────────────────────────────────


def test_self_publication_record_shape() -> None:
    record = build_self_publication(
        primary_key="cbr",
        primary_name="Банк России",
        primary_uri="https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/",
        method="host-value-attribution-v6",
        basis_fragment="По прогнозу Банка России, годовая инфляция составит 6,0–7,0%",
    )
    assert record is not None
    scope = record.as_scope()
    assert scope["schema"] == SELF_PUBLICATION_SCHEMA
    assert scope["primary_key"] == "cbr"
    # родителя нет по построению: первоисточник не может быть родителем самого себя
    assert "parent_source_id" not in scope
    parsed = parse_self_publication({EVIDENCE_SELF_PUBLICATION_KEY: scope})
    assert parsed == record


@pytest.mark.parametrize(
    "kwargs",
    [
        {"primary_key": "", "primary_name": "Банк России", "primary_uri": "https://cbr.ru/", "method": "m"},
        {"primary_key": "cbr", "primary_name": " ", "primary_uri": "https://cbr.ru/", "method": "m"},
        {"primary_key": "cbr", "primary_name": "Банк России", "primary_uri": "", "method": "m"},
        {"primary_key": "cbr", "primary_name": "Банк России", "primary_uri": "https://cbr.ru/", "method": ""},
        {"primary_key": 7, "primary_name": "Банк России", "primary_uri": "https://cbr.ru/", "method": "m"},
        {
            "primary_key": "cbr",
            "primary_name": "Банк России",
            "primary_uri": "https://cbr.ru/" + "x" * 600,
            "method": "m",
        },
    ],
)
def test_self_publication_build_is_strict(kwargs: dict[str, Any]) -> None:
    assert build_self_publication(**kwargs) is None


def test_self_publication_parse_fail_closed() -> None:
    assert parse_self_publication(None) is None
    assert parse_self_publication({}) is None
    assert parse_self_publication({"scope_schema": "host-scope-v1"}) is None
    assert parse_self_publication({EVIDENCE_SELF_PUBLICATION_KEY: "мусор"}) is None
    assert parse_self_publication({EVIDENCE_SELF_PUBLICATION_KEY: {"schema": "чужая-схема"}}) is None
    broken = {
        EVIDENCE_SELF_PUBLICATION_KEY: {
            "schema": SELF_PUBLICATION_SCHEMA,
            "primary_key": "",
            "primary_name": "Банк России",
            "primary_uri": "https://cbr.ru/",
            "method": "m",
        }
    }
    assert parse_self_publication(broken) is None


def test_carry_carries_both_host_keys() -> None:
    """T7.17 пересчитывает скоуп улики из провенанса: без переноса исчезало бы решение.
    С T7.101 переносятся оба хостовых ключа."""
    value_record = build_self_publication(
        primary_key="cbr",
        primary_name="Банк России",
        primary_uri="https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/",
        method="host-value-attribution-v6",
    )
    assert value_record is not None
    current = {
        "scope_schema": "host-scope-v1",
        "source_domain": "cbr.ru",
        EVIDENCE_SELF_PUBLICATION_KEY: value_record.as_scope(),
    }
    derived = {"scope_schema": "host-scope-v1", "source_domain": "cbr.ru", "as_of": "2026-09-01"}
    carried = carry_value_attribution(current, derived)
    assert carried[EVIDENCE_SELF_PUBLICATION_KEY] == value_record.as_scope()
    # канонические измерения берутся из свежего провенанса, а не из переносимой записи
    assert carried["as_of"] == "2026-09-01"


def test_carry_keeps_derived_scope_when_nothing_to_carry() -> None:
    derived = {"scope_schema": "host-scope-v1", "source_domain": "cbr.ru"}
    assert carry_value_attribution({"source_domain": "cbr.ru"}, derived) is derived
    assert carry_value_attribution(None, derived) is derived


# ─── 2: решение о записи ───────────────────────────────────────────────────────────


def test_candidate_records_own_forecast_on_producer_home_host() -> None:
    """Стендовый случай `c6729d4c`: страница ЦБ с собственным прогнозом (CPD_2026-7)."""
    uri = STAND_URI["cbr_CPD_2026-7"]
    text = _load("cbr_CPD_2026-7")
    decision = attribute_value_in_fragment(canonical_uri=uri, text=text, claim_statement=STATEMENT_607)
    assert decision.status == STATUS_SELF_PRIMARY, decision
    # решение об атрибуции не меняется: self_primary по-прежнему без primary_key
    assert decision.primary_key is None and decision.parent_uri is None
    candidate = self_publication_candidate(canonical_uri=uri, text=text, claim_statement=STATEMENT_607)
    assert candidate is not None
    assert (candidate.primary_key, candidate.primary_name) == ("cbr", "Банк России")
    assert candidate.actor_veto is False
    assert "По прогнозу Банка России" in candidate.basis_fragment


def test_candidate_records_second_cbr_page_of_same_claim() -> None:
    """Вторая страница того же утверждения (Infl_exp_26-09, www.cbr.ru) — та же запись."""
    uri = STAND_URI["cbr_Infl_exp_26-09"]
    text = _load("cbr_Infl_exp_26-09")
    candidate = self_publication_candidate(canonical_uri=uri, text=text, claim_statement=STATEMENT_607)
    assert candidate is not None
    assert (candidate.primary_key, candidate.actor_veto) == ("cbr", False)


def test_candidate_veto_third_actor_keeps_record_unwritten() -> None:
    """ADR-0035 §15: «Аналитики прогнозируют…» на странице ЦБ (стендовые 2d010d53/58e2d5d4).

    Детектор честен: значение принадлежит прочитанной странице (relay на домашнем хосте ЦБ),
    но фрагмент называет ТРЕТЬЕГО актора создателем значения — хост здесь только издатель.
    Запись не пишется: витрина молчит, как молчала до правки."""
    uri = STAND_URI["cbr_Infl_exp_25-12"]
    text = _load("cbr_Infl_exp_25-12")
    decision = attribute_value_in_fragment(canonical_uri=uri, text=text, claim_statement=STATEMENT_63)
    assert decision.status == STATUS_SELF_PRIMARY, decision
    candidate = self_publication_candidate(canonical_uri=uri, text=text, claim_statement=STATEMENT_63)
    assert candidate is not None
    assert candidate.actor_veto is True


def test_candidate_none_on_t787_trap_page() -> None:
    """Ловушка T7.87 обязана выдержать: CPD_2025-12 с цифрой Росстата (5,59%).

    Значение на странице ЦБ не атрибутировано вовсе (`value_not_attributed`): ни атрибуции,
    ни собственной публикации Росстата/ЦБ запись не получает — self_primary по этой улике
    не возникает ни при каких условиях."""
    uri = STAND_URI["cbr_CPD_2025-12"]
    text = _load("cbr_CPD_2025-12")
    decision = attribute_value_in_fragment(canonical_uri=uri, text=text, claim_statement=STATEMENT_559)
    assert decision.status == "value_not_attributed", decision
    assert self_publication_candidate(canonical_uri=uri, text=text, claim_statement=STATEMENT_559) is None


def test_candidate_none_for_derivative_and_foreign_pages() -> None:
    """Пересказ чужого значения и страница вне словаря не получают записи."""
    decision_expert = attribute_value_in_fragment(
        canonical_uri=STAND_URI["expert_5-59"],
        text=_load("expert_5-59"),
        claim_statement=STATEMENT_559,
    )
    assert decision_expert.is_derivative
    assert (
        self_publication_candidate(
            canonical_uri=STAND_URI["expert_5-59"],
            text=_load("expert_5-59"),
            claim_statement=STATEMENT_559,
        )
        is None
    )
    assert (
        self_publication_candidate(
            canonical_uri=STAND_URI["un_about"],
            text=_load("un_about"),
            claim_statement=STATEMENT_UN,
        )
        is None
    )


def test_candidate_none_without_page_address() -> None:
    """Без адреса прочитанной страницы домашний хост не проверить — записи нет."""
    text = _load("cbr_CPD_2026-7")
    assert self_publication_candidate(canonical_uri=None, text=text, claim_statement=STATEMENT_607) is None


# ─── 3: витрина читает запись как указатель (fail-closed) ──────────────────────────


def _row(
    *,
    group_id: str = "g0",
    role: str = "support",
    self_publication: dict[str, Any] | None = None,
    value_attribution: dict[str, Any] | None = None,
    parent_uri: str | None = None,
) -> dict[str, Any]:
    scope: dict[str, Any] = {"scope_schema": "host-scope-v1", "source_domain": "cbr.ru"}
    if self_publication is not None:
        scope[EVIDENCE_SELF_PUBLICATION_KEY] = self_publication
    if value_attribution is not None:
        scope[EVIDENCE_VALUE_ATTRIBUTION_KEY] = value_attribution
    return {
        "assessment_role": role,
        "group_id": group_id,
        "scope": scope,
        "parent_uri": parent_uri,
        "parent_source_id": None,
    }


_SELF_OK: dict[str, Any] = {
    "schema": SELF_PUBLICATION_SCHEMA,
    "primary_key": "cbr",
    "primary_name": "Банк России",
    "primary_uri": "https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/",
    "method": "host-value-attribution-v6",
    "basis_fragment": "По прогнозу Банка России",
}


def test_case_recognized_from_self_publication_record() -> None:
    case = producer_publication_case(
        [_row(self_publication=dict(_SELF_OK)), _row(self_publication=dict(_SELF_OK))],
        reasons=["insufficient_independence"],
    )
    assert case is not None
    assert (case.name, case.supports) == ("Банк России", 2)


def test_case_fail_closed_on_record_contradictions() -> None:
    # имя не из словаря
    bad_name = dict(_SELF_OK, primary_name="Минфин России")
    assert producer_publication_case([_row(self_publication=bad_name)], reasons=["insufficient_evidence"]) is None
    # ключ не из словаря
    bad_key = dict(_SELF_OK, primary_key="gazprom")
    assert producer_publication_case([_row(self_publication=bad_key)], reasons=["insufficient_evidence"]) is None
    # адрес записи вне домашнего хоста производителя
    bad_uri = dict(_SELF_OK, primary_uri="https://evil.example/CPD_2026-7/")
    assert producer_publication_case([_row(self_publication=bad_uri)], reasons=["insufficient_evidence"]) is None
    # незнакомая схема записи — указателя нет, случай не признан (fail-closed)
    bad_schema = dict(_SELF_OK, schema="host-value-self-publication-v999")
    assert producer_publication_case([_row(self_publication=bad_schema)], reasons=["insufficient_evidence"]) is None


def test_case_fail_closed_when_pointers_name_different_producers() -> None:
    """Запись о собственной публикации ЦБ + запись об атрибуции на Росстат в одной группе —
    разночтение: витрина обязана молчать."""
    attribution = {
        "schema": "host-value-attribution-v1",
        "primary_key": "rosstat",
        "primary_name": "Росстат",
        "primary_uri": "https://rosstat.gov.ru/",
        "parent_source_id": "33912509-6fa7-44a4-ba30-c7224cc8c36e",
        "method": "host-value-attribution-v6",
        "basis_fragment": "По данным Росстата",
        "pairing": "value_and_primary_in_fragment",
    }
    rows = [
        _row(self_publication=dict(_SELF_OK)),
        _row(value_attribution=attribution),
    ]
    assert producer_publication_case(rows, reasons=["insufficient_independence"]) is None


def test_case_agrees_when_both_pointers_name_the_same_producer() -> None:
    attribution = {
        "schema": "host-value-attribution-v1",
        "primary_key": "cbr",
        "primary_name": "Банк России",
        "primary_uri": "https://cbr.ru/",
        "parent_source_id": "88fb9e2f-0000-0000-0000-000000000000",
        "method": "host-value-attribution-v6",
        "basis_fragment": "следует из данных Банка России",
        "pairing": "value_and_primary_in_fragment",
    }
    case = producer_publication_case(
        [_row(self_publication=dict(_SELF_OK)), _row(value_attribution=attribution)],
        reasons=["insufficient_independence"],
    )
    assert case is not None and case.name == "Банк России" and case.supports == 2


def test_case_still_requires_single_group_and_reason_gate() -> None:
    rows = [_row(group_id="g0", self_publication=dict(_SELF_OK)), _row(group_id="g1", self_publication=dict(_SELF_OK))]
    assert producer_publication_case(rows, reasons=["insufficient_independence"]) is None
    single = [_row(self_publication=dict(_SELF_OK))]
    assert producer_publication_case(single, reasons=["no_evidence"]) is None
    assert producer_publication_case(single, reasons=["independence_repeated_method"]) is None
