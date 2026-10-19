"""Unit (pure): гейт почти-дубликатов по значению — два стендовых склейки T7.126 (часть б)
и его границы (ADR-0033 §7.1, решение пользователя 2026-10-19 «да»).

Дефект (.92, после отзывов T7.125): обе пары одного факта записаны ОТДЕЛЬНЫМИ строками.

* `b262da08` «По данным hh.ru, в августе 2026 года hh-индекс (конкуренция соискателей на
  вакансию) составил 8,7.» рядом с `5538afc2` «По данным hh.ru, hh-индекс … составил 8,7,
  что выше показателя год назад (6,1)»: числа операции {8,7} — ПОДМНОЖество чисел кандидата
  {8,7; 6,1}, прежний гейт требовал равенства множеств и промолчал.
* `841224a7` «…составил 2,2% по данным Росстата.» (temporal_fact) рядом с `7cfbe942`
  «…составил 2,2% по данным Росстата (ILO methodology).» (external_fact): тот же текст плюс
  скобочная оговорка и другой тип — не видит ни дедуп T7.9 (тип разный), ни числовой гейт
  (первое его условие — равный тип).

Правило пользователя: то же значение (значения утверждения-предложения ⊆ значений кандидата),
совместимый период (тот же месяц/год либо кандидат без периода), тот же показатель и тот же
тип — дубль ДАЖЕ если кандидат не объявлен в `relied_claim_ids`, ЕСЛИ кандидат единственный.
Два кандидата — прежняя неоднозначность; обратное включение значений (операция приносит число,
которого кандидат не называет) — по-прежнему другое наблюдение, не пересказ.

Формулировки взяты из фикстуры `tests/fixtures/t7126/claims_dup_pairs.jsonl` (SELECT из
noezema-dev), а не пересказаны.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.orchestrator.value_duplicate import (
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    find_identical_statement_duplicates,
    find_value_duplicates,
    value_duplicate_pair,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PAIRS_PATH = REPO_ROOT / "tests/fixtures/t7126/claims_dup_pairs.jsonl"

# фикстура содержит полные uuid; в тесте пары названы первых восьмью знаками
_STAND: dict[str, dict[str, str]] = {}
for _line in PAIRS_PATH.read_text(encoding="utf-8").split("\n"):
    if _line.strip():
        _row = json.loads(_line)
        _STAND[_row["id"][:8]] = _row


def _stand(claim_id: str) -> tuple[str, str]:
    row = _STAND[claim_id]
    return row["statement"], row["type"]


def _full(claim_id: str) -> str:
    return _STAND[claim_id]["id"]


HH_OP_ID, HH_CAND_ID = "b262da08", "5538afc2"
UNEMP_OP_ID, UNEMP_CAND_ID = "841224a7", "7cfbe942"


def _op(
    claim_id: str,
    *,
    statement: str | None = None,
    as_of_year: int | None = 2026,
    metric: str | None = None,
) -> ValueDuplicateClaim:
    text, claim_type = _stand(claim_id)
    return ValueDuplicateClaim(
        index=0,
        statement=statement or text,
        claim_type=claim_type,
        as_of_year=as_of_year,
        has_support_link=True,
        has_counter_link=False,
        metric=metric,
    )


def _candidate(
    claim_id: str, *, relied: bool = False, metric: str | None = None
) -> ValueDuplicateCandidate:
    text, claim_type = _stand(claim_id)
    return ValueDuplicateCandidate(
        claim_id=_full(claim_id),
        statement=text,
        claim_type=claim_type,
        as_of_year=2026,
        relied=relied,
        metric=metric,
    )


# ── т4а: подмножество значений + единственный кандидат без опоры ────────────────────────


def test_hh_index_pair_from_the_stand_is_merged_into_the_record_that_names_more_numbers() -> None:
    """Было: «no value-duplicate candidate» / отдельная строка. Стало: перепроверка в
    `5538afc2` — запись, которая называет и 8,7, и сравнение год назад."""
    decisions = find_value_duplicates(
        [_op(HH_OP_ID)], [_candidate(HH_CAND_ID)]
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "reverified", entry
    assert entry["target"] == _full(HH_CAND_ID), entry
    assert "are contained in the candidate's values" in entry["reason"], entry
    assert "ONLY candidate" in entry["reason"], entry


def test_showcase_sees_the_subset_pair_too() -> None:
    """Витринный предикат живёт на том же условии покрытия значений: карточка обязана
    показывать пару склейкой, раз склеивает гейт (иначе витрина врёт о том, что хост сделал)."""
    assert (
        value_duplicate_pair(
            op_statement=_stand(HH_OP_ID)[0],
            op_claim_type=_stand(HH_OP_ID)[1],
            op_as_of_year=2026,
            candidate_statement=_stand(HH_CAND_ID)[0],
            candidate_claim_type=_stand(HH_CAND_ID)[1],
            candidate_as_of_year=2026,
        )
        is True
    )


def test_a_number_the_candidate_does_not_name_is_a_different_observation() -> None:
    """Обратное включение запрещено: пересказ, принёсший сравнение «год назад (6,1)» там, где
    запись его не называет, остаётся отдельным утверждением."""
    broader = (
        "По данным hh.ru, hh-индекс в августе 2026 года составил 8,7, что выше показателя "
        "год назад (6,1)."
    )
    decisions = find_value_duplicates(
        [_op(HH_CAND_ID, statement=broader)], [_candidate(HH_OP_ID)]
    )
    assert decisions == [], decisions


def test_two_candidates_without_reliance_stay_ambiguous_as_before() -> None:
    """Релаксация T7.126 (часть б) — про единственный кандидат. Два кандидата вне опоры были
    и остаются неоднозначностью: выбирать, к какому из записанных утверждений относится вывод,
    должен куратор."""
    other = ValueDuplicateCandidate(
        claim_id="00000000-0000-4000-8000-000000000712",
        statement=(
            "По данным hh.ru, hh-индекс (конкуренция соискателей на вакансию) в августе 2026 "
            "года составил 8,7."
        ),
        claim_type="external_fact",
        as_of_year=2026,
        relied=False,
        metric=None,
    )
    decisions = find_value_duplicates(
        [_op(HH_OP_ID)], [_candidate(HH_CAND_ID), other]
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "kept", entry
    assert "2 candidates share type, values and period" in entry["reason"], entry


def test_different_named_month_of_the_candidate_is_another_observation() -> None:
    """Тот же показатель и то же число, но названный месяц другой — периода не покрыто:
    отдельная строка (месячный guard T7.121 не тронут)."""
    september = (
        "По данным hh.ru, hh-индекс (конкуренция соискателей на вакансию) в сентябре 2026 "
        "года составил 8,7."
    )
    decisions = find_value_duplicates(
        [_op(HH_OP_ID, statement=september)], [_candidate(HH_CAND_ID)]
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "kept", entry
    assert "names months [8], the op names [9]" in entry["reason"], entry


# ── т4б: скобочная оговорка и смена типа ────────────────────────────────────────────────


def test_parenthetical_qualification_with_type_relabel_is_folded() -> None:
    """`841224a7` (temporal_fact) против `7cfbe942` (external_fact, «(ILO methodology)»):
    хост склеивает перепроверкой в прежнюю запись и оценивает по типу записи."""
    decisions = find_identical_statement_duplicates(
        [_op(UNEMP_OP_ID)], [_candidate(UNEMP_CAND_ID)]
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "reverified", entry
    assert entry["target"] == _full(UNEMP_CAND_ID), entry
    assert "except for a parenthetical qualification" in entry["reason"], entry
    assert "temporal_fact → external_fact" in entry["reason"], entry


def test_parenthetical_branch_needs_the_value_set_of_the_originals_to_be_equal() -> None:
    """Скобочная ветка обязывает равные множества значений ИСХОДНЫХ формулировок: снятие
    скобок не должно превращать «(6,1)» в невидимку и склеивать разные наблюдения."""
    wider = (
        "Уровень безработицы в России в августе 2026 года составил 2,2% по данным Росстата "
        "при 1,3% годом ранее."
    )
    decisions = find_identical_statement_duplicates(
        [_op(UNEMP_OP_ID, statement=wider)], [_candidate(UNEMP_CAND_ID)]
    )
    assert decisions == [], decisions


def test_parenthetical_branch_refuses_a_different_declared_indicator() -> None:
    """Вето индикатора в скобочной ветке проверяется по ИСХОДНЫМ формулировкам и объявленным
    меткам показателя: та же скобочная форма, но куратор подписал операцию другим показателем —
    склейки нет, причина названа."""
    decisions = find_identical_statement_duplicates(
        [_op(UNEMP_OP_ID, metric="индекс потребительских цен")],
        [_candidate(UNEMP_CAND_ID, metric="уровень безработицы")],
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "kept", entry
    assert "the indicator does not match" in entry["reason"], entry


def test_an_unrelated_formulation_stays_silent() -> None:
    """Формулировка, которая не равна кандидатской даже после снятия скобок, в эту ветку не
    попадает вовсе: пустой список решений = никакого ключа в payload = прежние аудит-события."""
    other_indicator = (
        "Индекс потребительских цен в России в августе 2026 года составил 2,2% по данным "
        "Росстата."
    )
    decisions = find_identical_statement_duplicates(
        [_op(UNEMP_OP_ID, statement=other_indicator)], [_candidate(UNEMP_CAND_ID)]
    )
    assert decisions == [], decisions
