"""Unit: остаток T7.129 — утверждение называет производителя, а страница — нет.

Стенд `.92` на `686d1e8-v29` дал три новых ложных «E3 · Проверено» (утверждения 060d979a,
738548ca, a7cac406, b262da08): вторая «независимая» группа на деле пересказывала того же
производителя, чьё имя СТОИТ В САМОМ УТВЕРЖДЕНИИ («по данным Росстата», «По данным hh.ru»),
но НЕ стоит рядом со значением на прочитанной странице. Ни строгий pairing, ни окно
публикации, ни библиография, ни упоминание известного производителя не срабатывают: все они
требуют, чтобы производитель был назван СТРАНИЦЕЙ.

Правило реализовано в T7.129a (метод `host-value-attribution-v18`, pairing
`statement_names_producer`, ADR-0029 §26.3): новое имя решения внесено в закрытый словарь
`VALUE_ATTRIBUTION_PAIRINGS` (`packages/memory/scope.py`), и сборка/чтение записи прошли.
Поэтому остаточные ожидания этого файла перевёрнуты — ровно те, которые переворачивает новое
правило (`test_retelling_named_only_in_the_claim_is_now_attributed`), с причиной у каждой строки.

Что здесь закреплено без изменений (эти тесты зеленые и до, и после правки):
- страница самого производителя (домашний хост) не становится чужим пересказом;
- утверждение без названного производителя остаётся точно в таком же отказе;
- атрибуции нет, если рядом со значением стоит собственная оценка другого актора, и нет, если
  число на странице относится к другому показателю (границы вето и совместимости показателя);
- там, где производитель назван страницей рядом со значением, pairing работает как раньше.

Полный набор новых проверок правила — tests/unit/test_statement_names_producer_t7129a.py.

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
    ("case_id", "domain", "primary_key", "parent_uri"),
    [
        # копия таблицы Росстата, «по данных Росстата» только в утверждении; само значение на
        # странице существует лишь индексной формой (106,35 / 100,35 / 105,04)
        ("060d979a", "garant.ru", "rosstat", "https://rosstat.gov.ru/"),
        # дашборд ЦМИ: значение есть, имени Росстата у него нет
        ("738548ca", "sberbank.ru", "rosstat", "https://rosstat.gov.ru/"),
        # пост «Хэдхантер. Рынок труда…» без шаблона атрибуции
        ("a7cac406", "smart-lab.ru", "hh", "https://hh.ru/"),
        # пользовательский пересказ данных hh в соцсети от hh (не домашний хост `hh`)
        ("a7cac406", "setka.ru", "hh", "https://hh.ru/"),
        # rambler: имя производителя далеко от значения
        ("b262da08", "rambler.ru", "hh", "https://hh.ru/"),
    ],
)
def test_retelling_named_only_in_the_claim_is_now_attributed(
    case_id: str, domain: str, primary_key: str, parent_uri: str
) -> None:
    """Остаток T7.129 закрыт правилом v17: страницу со значением без имени производителя у значения
    приписывают тому, кого назвало само утверждение (ADR-0029 §26.3).

    Было (остаток T7.129): `no_value_attribution` / `value_not_attributed`, производитель не назван,
    родителя нет. Стало: `derivative` → производитель из утверждения, pairing пятого режима,
    родитель — якорь названного производителя. Причина: имя взято из `PRIMARY_SOURCES` закрытым
    шаблоном утверждения, значение стоит на странице тем же сравнением, что гейт «значение на связи»
    (для garant — индексной формой T7.128), вето собственной оценки и несовместимость показателя не
    сработали, страница не домашняя."""
    decision = _decide(case_id, domain)
    assert (decision.status, decision.primary_key) == ("derivative", primary_key), (
        case_id,
        domain,
        decision.status,
        decision.primary_key,
    )
    assert decision.pairing == "statement_names_producer", decision
    assert decision.parent_uri == parent_uri, decision


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
