"""T7.136 (части 2 и 5, ADR-0038 дополнение): оборванная на потолке сводка — дефект ответа.

Стенд 33298707 (контрольный прогон `aaca104-v30`, вопрос c3 «прогнозы инфляции на 2026 год от
аналитиков крупных банков»): рассуждение фазы consolidation ВЫКЛЮЧЕНО в конфигурации стенда
(config-v18…v30, ADR-0030), и модель повела рассуждение прямо в поле `summary` ответа куратора,
уперлась в `max_length` этого поля и оборвала текст на полуслове — дословно на «…Нельзя за».
JSON при этом валиден, `finish_reason = stop`, `claims` пуст, опоры нет: хост принимал такой
ответ как честный итог правила 16, карточка вопроса сказала «Ответ не записан», а новая оценка
Банка России потерялась.

Зафиксировано тестами на ДОСЛОВНЫХ сводках стенда (фикстуры `tests/fixtures/t7136/`, снятые с
`claim-created-all.jsonl` без правок):

* потолок сводки берётся ИЗ СХЕМЫ `CuratorProposal.summary`, а не литералом (п. 1);
* оборванная сводка при пустых `claims`, `relied_claim_ids` и `new_questions` — отказ с новым
  `curator_reject_kind` и причиной, которая называет потолок и говорит модели, что делать (п. 2);
* расширение закрытого списка формулировок ловит стенд fa5448d5 (п. 3);
* пять честных пустых итогов стенда не задеваются ни одним основанием (п. 4 и 5);
* сводка на потолке, но с claims / опорой / вопросами — вне гейта (п. 6).

Модуль чистый: ни БД, ни LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from apps.orchestrator.curator_declared_claims import (
    CURATOR_REJECT_KIND_DECLARED_EMPTY,
    CURATOR_REJECT_KIND_SUMMARY_CUT,
    DECLARED_EMPTY_REASON,
    SUMMARY_CUT_PUBLIC_SUMMARY,
    SUMMARY_CUT_REASON,
    SUMMARY_MAX_CHARS,
    empty_proposal_defect,
    summary_cut_at_limit,
    summary_declares_new_claim,
)
from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, QuestionProposal

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "t7136"


def _summary(name: str) -> str:
    """Дословная сводка стенда (файл записан побайтово из payload события `claim_created`)."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def _proposal(
    summary: str,
    *,
    claims: int = 0,
    relied: int = 0,
    questions: int = 0,
) -> CuratorProposal:
    return CuratorProposal(
        summary=summary,
        claims=[
            ClaimProposal(statement=f"Утверждение {i}.", claim_type="external_fact")
            for i in range(claims)
        ],
        relied_claim_ids=[f"{'0' * 32}{i}" for i in range(relied)],
        new_questions=[
            QuestionProposal(text=f"Вопрос {i}?", origin="model_proposal") for i in range(questions)
        ],
    )


#: четыре оборванной сводки таблицы T7.136 — все четыре дефект
CUT_FIXTURES: Final[tuple[str, ...]] = (
    "summary-33298707-c3-cut-at-limit.txt",
    "summary-b7e93ff6-cut-at-limit.txt",
    "summary-2d682ab0-correction-cut-at-limit.txt",
    "summary-abef06b7-correction-cut-at-limit.txt",
)

#: пять честных пустых итогов стенда: сводка, её реальные `new_questions` и `relied_claim_ids`
HONEST_FIXTURES: Final[tuple[tuple[str, int, int], ...]] = (
    ("summary-099ddccb-honest.txt", 0, 0),
    ("summary-96c710ff-honest.txt", 1, 0),
    ("summary-96a0c535-honest.txt", 4, 0),
    ("summary-eaacdb15-honest.txt", 1, 1),
    ("summary-8a97308b-honest.txt", 2, 8),
)


def _schema_cap() -> int:
    """Потолок поля `summary` — независимым чтением той же схемы."""
    for meta in CuratorProposal.model_fields["summary"].metadata:
        limit = getattr(meta, "max_length", None)
        if isinstance(limit, int):
            return limit
    raise AssertionError("у поля CuratorProposal.summary нет max_length")


# ── 1. потолок берётся из схемы ───────────────────────────────────────────────


def test_ceiling_is_read_from_the_curator_proposal_schema() -> None:
    assert _schema_cap() == SUMMARY_MAX_CHARS
    assert SUMMARY_MAX_CHARS > 0


def test_cut_predicate_flips_exactly_at_the_schema_ceiling() -> None:
    filler = "а"
    assert not summary_cut_at_limit(filler * (SUMMARY_MAX_CHARS - 1))
    assert summary_cut_at_limit(filler * SUMMARY_MAX_CHARS)
    # короче потолка — не оборвана (честные пустые итоги стенда короче втрое)
    assert not summary_cut_at_limit("")


# ── 2. (а) оборванная сводка при пустых списках ──────────────────────────────


def test_stand_33298707_c3_summary_is_cut_at_the_ceiling_verbatim() -> None:
    summary = _summary("summary-33298707-c3-cut-at-limit.txt")
    assert len(summary) == SUMMARY_MAX_CHARS
    # сводка обрывается внутри слова: последний знак — середина слова, не конец предложения
    assert not summary.rstrip().endswith((".", "!", "?", ":", ";"))


def test_stand_33298707_empty_proposal_is_rejected_with_the_new_kind() -> None:
    defect = empty_proposal_defect(_proposal(_summary("summary-33298707-c3-cut-at-limit.txt")))
    assert defect is not None
    assert defect.kind == CURATOR_REJECT_KIND_SUMMARY_CUT == "claims_empty_summary_cut_at_limit"
    assert defect.reason == SUMMARY_CUT_REASON
    assert defect.public_summary == SUMMARY_CUT_PUBLIC_SUMMARY
    # причиной правит ровно одно основание (ровно одна строка `curator_rejected`)
    assert "\n" not in defect.reason


def test_cut_reason_names_the_ceiling_and_tells_the_model_what_to_do() -> None:
    assert str(SUMMARY_MAX_CHARS) in SUMMARY_CUT_REASON
    assert "сводка оборвалась на пределе" in SUMMARY_CUT_REASON
    assert "сводка — 1–3 фразы итога, не рассуждение" in SUMMARY_CUT_REASON
    assert "предложи claims по уликам, о которых пишешь" in SUMMARY_CUT_REASON
    assert "почему записывать нечего" in SUMMARY_CUT_REASON


@pytest.mark.parametrize("name", CUT_FIXTURES)
def test_every_cut_stand_summary_with_empty_lists_is_rejected(name: str) -> None:
    summary = _summary(name)
    assert len(summary) == SUMMARY_MAX_CHARS, name
    defect = empty_proposal_defect(_proposal(summary))
    assert defect is not None, name
    assert defect.kind == CURATOR_REJECT_KIND_SUMMARY_CUT, name


# ── 3. (б) расширение закрытого списка формулировок ──────────────────────────


def test_stand_fa5448d5_declared_two_computed_result_claims() -> None:
    summary = _summary("summary-fa5448d5-declared-two-claims.txt")
    assert len(summary) < SUMMARY_MAX_CHARS  # не потолок — ловит именно формулировка
    assert summary_declares_new_claim(summary)
    defect = empty_proposal_defect(_proposal(summary))
    assert defect is not None
    assert defect.kind == CURATOR_REJECT_KIND_DECLARED_EMPTY
    assert defect.reason == DECLARED_EMPTY_REASON


def test_stand_33298707_is_caught_by_both_grounds_and_named_as_truncation() -> None:
    # обе формулировки есть в сводке 33298707, но основанием называется (а): она полезнее
    summary = _summary("summary-33298707-c3-cut-at-limit.txt")
    assert summary_declares_new_claim(summary)
    defect = empty_proposal_defect(_proposal(summary))
    assert defect is not None and defect.kind == CURATOR_REJECT_KIND_SUMMARY_CUT


def test_new_bases_do_not_reach_the_honest_wordings_of_the_same_situation() -> None:
    # дословные фрагменты честных пустых итогов стенда: «перепроверка» и «не предлагается» в них
    # есть, а объявления записать операцию — нет
    assert not summary_declares_new_claim(
        "В предоставленном evidence нет данных, позволяющих сформулировать новое утверждение "
        "о прогнозе инфляции, поэтому новое утверждение не предлагается."
    )
    assert not summary_declares_new_claim(
        "Новых claims не предлагается: в evidence нет цифр, которых нет уже в знании."
    )
    assert not summary_declares_new_claim(
        "Требуется перепроверка более раннего прогноза, но данных для новой записи нет."
    )
    assert summary_declares_new_claim("Предлагаются перепроверки существующих прогнозов.")
    assert summary_declares_new_claim(
        "Предлагаю два computed_result-claim: количество и полный список."
    )


# ── 4 и 5. честные пустые итоги не задеваются ────────────────────────────────


@pytest.mark.parametrize(("name", "questions", "relied"), HONEST_FIXTURES)
def test_honest_empty_stand_outcomes_are_not_defects(
    name: str, questions: int, relied: int
) -> None:
    summary = _summary(name)
    # самый длинный честный итог вдвое короче потолка: основание (а) его не касается
    assert len(summary) < SUMMARY_MAX_CHARS, name
    assert empty_proposal_defect(_proposal(summary, relied=relied, questions=questions)) is None
    # и формулировочное основание его не ловит
    assert not summary_declares_new_claim(summary), name


# ── 6. сводка на потолке, но предложение не пустое ───────────────────────────


def test_cut_summary_with_claims_is_outside_the_gate() -> None:
    summary = _summary("summary-33298707-c3-cut-at-limit.txt")
    assert empty_proposal_defect(_proposal(summary, claims=1)) is None


def test_cut_summary_with_reliance_is_outside_the_gate() -> None:
    # стенды 8181a76a и 066e1e5f: сводка ровно 2000 знаков, но ответ опирается на знание
    summary = _summary("summary-b7e93ff6-cut-at-limit.txt")
    assert empty_proposal_defect(_proposal(summary, relied=5)) is None


def test_cut_summary_with_new_questions_is_outside_the_gate() -> None:
    summary = _summary("summary-abef06b7-correction-cut-at-limit.txt")
    assert empty_proposal_defect(_proposal(summary, questions=2)) is None
