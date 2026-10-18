"""T7.123 (часть 2, ADR-0038 §2 Дополнение): гейты значения и периода по КАЖДОЙ новой связи.

Проверяются ЧИСТЫЕ функции — ни БД, ни LLM:

- `apps/orchestrator/curator_rejections.py::links_without_claim_value` — у утверждения без
  измеренного профиля значением считаются «голые» десятичные токены формулировки (тот же
  профиль, что у pairing-фильтров T7.120): стендовая дыра к1 («9,6» без единицы измерения
  проходила гейт незамеченной, и связь вела на страницу, где значения нет). Формулировка
  обращённой и явной перепроверки — формулировка ОПЕРАЦИИ: при пустой собственной строке
  берётся формулировка цели из `statement_overrides` (ADR-0033: перепроверка наследует
  значение цели).
- `apps/orchestrator/curator_period_gate.py::claim_period_rejections_by_links` — период
  проверяется по каждой оставшейся `supports`-связи `source_assertion`, перепроверки —
  наравне с новыми claims (было: «перепроверку ведёт якорь», перепроверки не проверялись —
  дыра к2). Снимается СВЯЗЬ (строка формата T7.108); операция снимается, только если ни
  одна связь не подтвердила период (байт-прежние строки причин T7.121/T7.121a).

Фикстуры формулировок — реальные формулировки зафиксированных предложений куратора
(аудит-фикстуры 64837cb5, 1e4ef28d, b7e93ff6); страницы — реальные нормализованные тексты
(tests/fixtures/t7121) и синтетические вымышленные случаи.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.orchestrator.curator_period_gate import (
    PeriodGateDecision,
    claim_period_rejections_by_links,
)
from apps.orchestrator.curator_rejections import links_without_claim_value
from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, EvidenceLink
from tests.unit.test_curator_period_gate_t7121 import C1_CREATED, FINMARKET

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "t7121"


def _proposal(
    claims: list[ClaimProposal], links: list[EvidenceLink]
) -> CuratorProposal:
    return CuratorProposal(summary="итог", claims=claims, evidence_links=links)


def _new_claim(statement: str, *, existing_claim_id: str | None = None) -> ClaimProposal:
    return ClaimProposal(
        statement=statement,
        claim_type="external_fact",
        scope={"metric": "hh-индекс", "region": "Россия"},
        existing_claim_id=existing_claim_id,
    )


# реальные страницы стенда 64837cb5 (к1): страница ключевой ставки ЦБ (значения 9,6 нет)
# и обзор hr-ratings (9,6 названо рядом с hh-индексом)
SBERCIB_KEY_RATE = Path(
    Path(__file__).parent.parent / "fixtures" / "t7123" / "sbercib-key-rate.normalized.txt"
).read_text(encoding="utf-8")
HR_RATINGS_HH = Path(
    Path(__file__).parent.parent / "fixtures" / "t7123" / "hr-ratings-hh-index.normalized.txt"
).read_text(encoding="utf-8")

# дословная формулировка claim[3] предложения куратора 64837cb5 (стенд к1)
HH_CLAIM = "По данным HR-Ratings, hh-индекс в России в 2026 году составил 9,6."

_HONEST_PERIOD_PAGE = (
    "По данным Росстата, в октябре 2026 года уровень безработицы в России по методологии МОТ "
    "составил 2,2 процента."
)
_VALUELESS_PERIOD_PAGE = "В октябре 2026 года состоялась пресс-конференция о рынке труда."
_VALUELESS_TARGET_PAGE = "Уровень безработицы по методологии МОТ: 2,2 процента."


# ── гейт значения: голое десятичное значение проверяется (дыра к1) ────────────


def test_bare_decimal_claim_is_checked_and_dies_without_value_on_page() -> None:
    # было (до T7.123): «9,6» без единицы измерения — пустой измеренный профиль, гейт молчал,
    # связь с порочащей страницей проходила. стало: голое значение проверяется, связь снимается,
    # оставшаяся без связей операция снимается с прежней причиной T7.108
    proposal = _proposal(
        [_new_claim(HH_CLAIM)],
        [EvidenceLink(evidence_index=3, claim_index=0, relation="supports")],
    )
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={0: (None, SBERCIB_KEY_RATE)},
    )
    assert removed == {0: "evidence_link[0]: evidence 3 does not contain the value of claim 0"}
    assert claim_drops == {0: "claim[0]: no linked evidence contains the claim value"}


def test_bare_value_honest_link_survives_operation_survives() -> None:
    # форма к1 (задача): две связи — sbercib без значения и hr-ratings со значением 9,6;
    # снимается СВЯЗЬ sbercib, операция (в стенде — перепроверка) остаётся со честной связью
    proposal = _proposal(
        [_new_claim(HH_CLAIM, existing_claim_id="83e7a1c8-7b6a-4660-a04c-e12d66179c04")],
        [
            EvidenceLink(evidence_index=3, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=4, claim_index=0, relation="supports"),
        ],
    )
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={0: (None, SBERCIB_KEY_RATE), 1: (None, HR_RATINGS_HH)},
    )
    assert removed == {0: "evidence_link[0]: evidence 3 does not contain the value of claim 0"}
    assert claim_drops == {}


def test_claim_without_numeric_profile_stays_silent() -> None:
    # прежнее молчание сохранено: ни измеренного, ни голого значения — гейт не проверяет
    proposal = _proposal(
        [_new_claim("Число вакансий сокращалось год от года без названных цифр.")],
        [EvidenceLink(evidence_index=0, claim_index=0, relation="supports")],
    )
    removed, claim_drops = links_without_claim_value(
        proposal=proposal, dropped={}, link_texts={0: (None, HR_RATINGS_HH)}
    )
    assert removed == {} and claim_drops == {}


def test_reverify_statement_override_drives_the_value_profile() -> None:
    # ADR-0033: предмет перепроверки — формулировка ЦЕЛИ (собственная строка операции есть
    # пересказ, audit-only): профиль значения берётся из override даже при непустой собственной
    # строке без цифр (иначе перепроверка проходила бы гейт незамеченной — форма дыры к1)
    claim = _new_claim(
        "Перепроверка ранее зафиксированного значения по новым источникам рынка труда.",
        existing_claim_id="83e7a1c8-7b6a-4660-a04c-e12d66179c04",
    )
    proposal = _proposal(
        [claim], [EvidenceLink(evidence_index=3, claim_index=0, relation="supports")]
    )
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={0: (None, SBERCIB_KEY_RATE)},
        statement_overrides={0: HH_CLAIM},
    )
    assert removed == {0: "evidence_link[0]: evidence 3 does not contain the value of claim 0"}
    assert claim_drops == {0: "claim[0]: no linked evidence contains the claim value"}

    # та же перепроверка со связью на страницу, где целевое значение названо, остаётся
    removed_ok, claim_drops_ok = links_without_claim_value(
        proposal=_proposal(
            [claim], [EvidenceLink(evidence_index=4, claim_index=0, relation="supports")]
        ),
        dropped={},
        link_texts={0: (None, HR_RATINGS_HH)},
        statement_overrides={0: HH_CLAIM},
    )
    assert removed_ok == {} and claim_drops_ok == {}


# ── гейт периода по каждой связи (дыра к2) ───────────────────────────────────


def _decision(*, existing_claim_id: str | None = None) -> PeriodGateDecision:
    return claim_period_rejections_by_links(
        claims=[_new_claim(C1_CREATED, existing_claim_id=existing_claim_id)],
        texts_by_link={0: [FINMARKET]},
        links_by_claim={0: (0,)},
        evidence_index_by_link={0: 4},
        already_dropped=frozenset(),
    )


def test_period_gate_checks_links_and_rejects_the_link_by_name() -> None:
    # finmarket-страница стенда к2: значение 2,2 есть, периода «октябре 2026» рядом нет —
    # связь снимается с именованной причиной формата T7.108
    decision = _decision()
    assert decision.link_rejections == {
        0: "evidence_link[0]: evidence 4 does not confirm the period of claim 0: «октябре 2026»"
    }


def test_operation_dies_only_when_no_link_confirms_period() -> None:
    decision = _decision()
    assert decision.claim_rejections == {
        0: "claim[0]: период «октябре 2026» не найден рядом со значением на связанной "
        "странице — возьми период из текста или напиши, что период не указан"
    }
    # вторая связь ведёт на страницу, где период назван рядом со значением: операция выживает,
    # снятая связь остаётся снятой (форма отказа к2 после исправления)
    both = claim_period_rejections_by_links(
        claims=[_new_claim(C1_CREATED)],
        texts_by_link={0: [FINMARKET], 1: [_HONEST_PERIOD_PAGE]},
        links_by_claim={0: (0, 1)},
        evidence_index_by_link={0: 4, 1: 5},
        already_dropped=frozenset(),
    )
    assert list(both.link_rejections) == [0]
    assert both.claim_rejections == {}


def test_reverify_is_period_checked_like_a_new_claim() -> None:
    # было (T7.121): `existing_claim_id` — операция вне гейта («перепроверку ведёт якорь»);
    # стало (T7.123): обращённая/явная перепроверка проверяется наравне, строки те же
    decision = _decision(existing_claim_id="e5a64868-11b3-4f3f-8df1-b92fc33d14c9")
    assert list(decision.link_rejections) == [0]
    assert list(decision.claim_rejections) == [0]


def test_already_dropped_operation_stays_skipped() -> None:
    decision = _decision()
    skipped = claim_period_rejections_by_links(
        claims=[_new_claim(C1_CREATED)],
        texts_by_link={0: [FINMARKET]},
        links_by_claim={0: (0,)},
        evidence_index_by_link={0: 4},
        already_dropped=frozenset({0}),
    )
    assert skipped.link_rejections == {} and skipped.claim_rejections == {}
    assert decision.claim_rejections  # без already_dropped та же операция снята бы была


def test_link_without_claim_value_is_not_period_checked() -> None:
    # юрисдикция связи без значения — гейт значения T7.108: период по такой связи не проверяют
    # (тот же `usable`-предикат); снятие получает только связь со значением без периода
    decision = claim_period_rejections_by_links(
        claims=[_new_claim(C1_CREATED)],
        texts_by_link={0: [_VALUELESS_PERIOD_PAGE], 1: [_VALUELESS_TARGET_PAGE]},
        links_by_claim={0: (0, 1)},
        evidence_index_by_link={0: 6, 1: 7},
        already_dropped=frozenset(),
    )
    assert list(decision.link_rejections) == [1]
    assert decision.claim_rejections == {
        0: "claim[0]: период «октябре 2026» не найден рядом со значением на связанной "
        "странице — возьми период из текста или напиши, что период не указан"
    }


def test_valueless_claim_and_other_types_stay_outside_period_gate_by_links() -> None:
    decision = claim_period_rejections_by_links(
        claims=[_new_claim("Уровень безработицы в октябре 2026 года остался минимальным.")],
        texts_by_link={0: [FINMARKET], 1: [_HONEST_PERIOD_PAGE]},
        links_by_claim={0: (0, 1)},
        evidence_index_by_link={0: 0, 1: 1},
        already_dropped=frozenset(),
    )
    assert decision.link_rejections == {} and decision.claim_rejections == {}
