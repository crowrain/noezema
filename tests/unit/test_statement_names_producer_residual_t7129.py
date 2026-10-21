"""Unit: остаток T7.129 — утверждение называет производителя, а страница — нет.

Стенд `.92` на `686d1e8-v29` дал три новых ложных «E3 · Проверено» (утверждения 060d979a,
738548ca, a7cac406, b262da08): вторая «независимая» группа на деле пересказывала того же
производителя, чьё имя СТОИТ В САМОМ УТВЕРЖДЕНИИ («по данным Росстата», «По данным hh.ru»),
но НЕ стоит рядом со значением на прочитанной странице. Ни строгий pairing, ни окно
публикации, ни библиография, ни упоминание известного производителя не срабатывают: все они
требуют, чтобы производитель был назван СТРАНИЦЕЙ.

Запланированное правило (метод `host-value-attribution-v17`, ADR-0029 §26) в этой задаче НЕ
реализовано: новое имя решения обязано входить в закрытый словарь `VALUE_ATTRIBUTION_PAIRINGS`
(`packages/memory/scope.py`) — иначе `build_value_attribution` отклоняет запись, а
`parse_value_attribution` её читает. Этот файл вне разрешённых путей T7.129, и задача
останавливается на этом пункте (критерий СТОП «путь вне разрешённых»).

Что здесь закреплено и почему эти тесты зелены и на 686d1e8:
- пересказ из фикстуры сегодня НЕ атрибутирован — это и есть остаток, а не новая норма;
- страница самого производителя не становится чужим пересказом (правило v17 не должно этого
  менять);
- утверждение без названного производителя остаётся точно в таком же отказе;
- атрибуции нет, если рядом со значением стоит собственная оценка другого актора, и нет, если
  число на странице относится к другому показателю (границы вето и совместимости показателя);
- там, где производитель назван страницей рядом со значением, pairing работает как раньше —
  правка словаря `hh` его не сломала.

Тексты страниц — дословные нормализованные снимки стенда (`tests/fixtures/t7129/`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.research_proxy.value_attribution import attribute_value_in_fragment

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "t7129"
_CASES: dict[str, Any] = json.loads((FIXTURE_DIR / "cases.json").read_text())
_BY_ID: dict[str, Any] = {case["claim_id"][:8]: case for case in _CASES["cases"]}


def _decide(case_id: str, domain: str) -> Any:
    case = _BY_ID[case_id]
    evidence = next(e for e in case["evidence"] if e["domain"] == domain)
    text = (FIXTURE_DIR / evidence["page"]).read_text()
    return attribute_value_in_fragment(
        canonical_uri=evidence["uri"], text=text, claim_statement=case["statement"]
    )


@pytest.mark.parametrize(
    ("case_id", "domain"),
    [
        ("060d979a", "garant.ru"),  # копия таблицы Росстата, «по данным Росстата» только в утверждении
        ("738548ca", "sberbank.ru"),  # дашборд ЦМИ: значение есть, имени Росстата у него нет
        ("a7cac406", "smart-lab.ru"),  # пост «Хэдхантер. Рынок труда…» без шаблона атрибуции
        ("a7cac406", "setka.ru"),  # пользовательский пересказ данных hh в соцсети от hh
        ("b262da08", "rambler.ru"),
    ],
)
def test_retelling_named_only_in_the_claim_is_still_not_attributed(
    case_id: str, domain: str
) -> None:
    """Остаток T7.129: страница со значением без имени производителя у значения не атрибутируется."""
    decision = _decide(case_id, domain)
    assert decision.status != "derivative", (case_id, domain, decision.status, decision.primary_key)
    assert decision.primary_key is None
    assert decision.parent_uri is None


def test_producer_own_page_never_becomes_a_retelling_of_itself() -> None:
    """Выпуск Росстата (домашний хост) остаётся самой публикацией, а не пересказом."""
    decision = _decide("060d979a", "gov.ru")
    assert decision.status != "derivative"
    assert decision.primary_key != "rosstat" or decision.parent_uri is None


def test_claim_without_a_named_producer_keeps_the_same_refusal() -> None:
    """Без имени производителя в утверждении новое правило не о чём было бы говорить."""
    case = _BY_ID["060d979a"]
    evidence = next(e for e in case["evidence"] if e["domain"] == "garant.ru")
    text = (FIXTURE_DIR / evidence["page"]).read_text()
    decision = attribute_value_in_fragment(
        canonical_uri=evidence["uri"],
        text=text,
        claim_statement="Официальная годовая инфляция в России в сентябре 2026 года составила 6,35%.",
    )
    assert decision.status != "derivative"
    assert decision.primary_key is None


def test_page_that_names_the_producer_near_the_value_is_still_attributed() -> None:
    """Положительный контроль: страница, называющая производителя у значения, атрибутируется."""
    decision = _decide("060d979a", "cbr.ru")
    assert (decision.status, decision.primary_key) == ("derivative", "rosstat")
    assert decision.pairing == "value_and_primary_in_fragment"


def test_own_assessment_of_another_actor_near_the_value_still_blocks_attribution() -> None:
    """Собственная оценка другого актора у значения — прежний отказ (вето не снимается)."""
    text = (
        "По данным Росстата, годовая инфляция в сентябре составила 6,35%. "
        "Аналитический центр «Меридиан» даёт свою оценку: по нашей оценке, "
        "реальные доходы населения выросли на 6,35%."
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://analytics.example/notes",
        text=text,
        claim_statement="Реальные доходы населения в сентябре 2026 года выросли на 6,35% (по данным Росстата).",
        known_claim_primaries=frozenset({"rosstat"}),
    )
    assert decision.status != "derivative"


def test_value_of_another_indicator_near_the_claim_number_still_blocks_attribution() -> None:
    """То же число в чужом показателе — прежний отказ (совместимость показателя, v16)."""
    decision = attribute_value_in_fragment(
        canonical_uri="https://blog.example/post",
        text="Рост реальных денежных доходов составил 8,7% — сообщает hh.ru.",
        claim_statement="По данным hh.ru, в августе 2026 года hh-индекс составил 8,7.",
    )
    assert decision.primary_key != "hh"
