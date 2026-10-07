"""Unit (pure): reference date and scope of a reverify/reuse commit
(T7.73, уточнение ADR-0018).

Чистый модуль `packages/memory/reverify.py` — та же дисциплина, что у
resolver'а ссылки (`test_claim_reference.py`): три случая решаются без БД и
без часов, а рабочий `MemoryService` только вызывает эту функцию.

Краснота на прежнем коде доказывает, что тест про дефект, а не про реализацию:
кейс 2 (вопрос без даты + `as_of: null` в предложении) раньше давал
`ClaimAsOf(None, none)` — именно он срезал E3 supported до E1 hypothesis.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from packages.memory.reverify import (
    merge_reverify_scope,
    resolve_reverify_reference,
    reverify_as_of_audit,
)
from packages.memory.scope import derive_claim_scope

pytestmark = pytest.mark.unit

#: Опорная дата якоря — как на подставке: момент session 1 (день N).
ANCHOR_AS_OF = datetime(2026, 1, 21, 9, 30, tzinfo=UTC)
SESSION_DAY = date(2026, 2, 3)

ANCHOR_SCOPE: dict[str, object] = {
    "scope_schema": "host-scope-v1",
    "as_of": "2026-01-21",
    "date_anchor": "relative",
    "source_domains": ["cbr.example"],
}

#: Вопрос перепроверки без даты (ровно форма curator-v7 правило 7).
QUESTION_NO_DATE = "Перепроверь значение годовой инфляции по независимым источникам."
#: Вопрос перепроверки с относительной привязкой («на текущую дату»).
QUESTION_RELATIVE = "Какова годовая инфляция на текущую дату?"
#: Вопрос перепроверки с явной датой.
QUESTION_EXPLICIT = "По состоянию на 15 декабря 2025 года: какова годовая инфляция?"


# ─── кейс 2: сессия ничего не приносит → дата якоря остаётся ──────────────


def test_dateless_question_and_empty_proposal_keep_the_anchor_date() -> None:
    """Тот самый случай подставки: ни вопрос, ни предложение даты не несут.
    Прежний код вернул бы (None, none) —assessment получил бы as_of_missing."""
    ref = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    assert ref.as_of == ANCHOR_AS_OF
    assert ref.carried_existing is True
    assert ref.proposed_conflict is None
    assert ref.changed_from is None


def test_carried_anchor_kind_is_the_persisted_one_not_none() -> None:
    """Дата носится ВМЕСТЕ со своим якорем: relative-утверждение не должно
    тихо стать evergreen (ADR-0017), explicit — не получить срок."""
    ref = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope={**ANCHOR_SCOPE, "date_anchor": "explicit"},
    )
    assert ref.anchor.value == "explicit"

    relative = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    assert relative.anchor.value == "relative"


def test_anchor_without_a_date_stays_without_a_date() -> None:
    """Нечего переносить — остаётся прежний вывод (нет даты), ничего не
    придумывается."""
    ref = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=None,
        existing_scope=None,
    )
    assert ref.as_of is None
    assert ref.anchor.value == "none"
    assert ref.carried_existing is False


# ─── кейс 1: сессия приносит якорь → предписанный пересчёт (ADR-0016 §4) ──


def test_relative_question_reanchors_the_existing_claim() -> None:
    """Относительный вопрос перепроверки — предписанный пересчёт даты и
    новый момент проверки (ADR-0016 §4 + ADR-0017); сдвиг записывается."""
    ref = resolve_reverify_reference(
        question=QUESTION_RELATIVE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    assert ref.as_of == datetime(2026, 2, 3, tzinfo=UTC)
    assert ref.anchor.value == "relative"
    assert ref.carried_existing is False
    assert ref.changed_from == ANCHOR_AS_OF


def test_explicit_question_date_wins_over_the_stored_one() -> None:
    ref = resolve_reverify_reference(
        question=QUESTION_EXPLICIT,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    assert ref.as_of == datetime(2025, 12, 15, tzinfo=UTC)
    assert ref.anchor.value == "explicit"
    assert ref.changed_from == ANCHOR_AS_OF


def test_reanchor_to_the_same_day_is_not_recorded_as_a_move() -> None:
    """Тот же день — не движение; в ленте не появляется ложного «сдвинули»."""
    same_day = datetime(2026, 2, 3, 7, 0, tzinfo=UTC)
    ref = resolve_reverify_reference(
        question=QUESTION_RELATIVE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=same_day,
        existing_scope=ANCHOR_SCOPE,
    )
    assert ref.changed_from is None


# ─── кейс 3: предложение с ДРУГОЙ датой → молчаливой подмены нет ────────────


def test_different_proposed_date_is_not_applied_but_recorded() -> None:
    """ADR-0018: значение существующего утверждения — значение якоря; менять
    его — ревизия, а не перепроверка. Отказанное значение видно в аудите."""
    other = datetime(2025, 12, 1, tzinfo=UTC)
    ref = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=other,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    assert ref.as_of == ANCHOR_AS_OF
    assert ref.carried_existing is True
    assert ref.proposed_conflict == other

    audit = reverify_as_of_audit(ref, existing_as_of=ANCHOR_AS_OF)
    assert audit["anchor_kept"] is True
    assert audit["stored_as_of"] == "2026-01-21T09:30:00+00:00"
    assert audit["as_of_conflict"] == "2025-12-01T00:00:00+00:00"
    assert "anchor_date_changed" not in audit


def test_proposal_date_same_day_keeps_the_anchor_instant() -> None:
    """Та же опорная дата, другой момент — значение якоря остаётся."""
    same_day = datetime(2026, 1, 21, 3, 0, tzinfo=UTC)
    ref = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=same_day,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    assert ref.as_of == ANCHOR_AS_OF
    assert ref.proposed_conflict is None


def test_proposal_date_fills_a_gap_but_is_not_a_conflict() -> None:
    """У якоря даты не было — предложение её заполняет (это не подмена)."""
    proposed = datetime(2026, 1, 21, tzinfo=UTC)
    ref = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=proposed,
        session_date=SESSION_DAY,
        existing_as_of=None,
        existing_scope=None,
    )
    assert ref.as_of == proposed
    assert ref.proposed_conflict is None
    audit = reverify_as_of_audit(ref, existing_as_of=None)
    assert "as_of_conflict" not in audit
    assert audit["anchor_kept"] is False


# ─── область перепроверки: объединение, а не замена ─────────────────────────


def test_scope_union_keeps_the_anchors_domains_and_adds_the_new_ones() -> None:
    """Источник этого случая (curator-v7 правило 7) не называет источников;
    если бы область якоря просто заменили новой, собственные доказательства
    якоря перестали бы покрываться — тот же обман через другую дверь."""
    question_scope = derive_claim_scope(
        question=QUESTION_NO_DATE, as_of=None, session_date=SESSION_DAY
    )
    ref = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    scope = merge_reverify_scope(
        question_scope=question_scope, existing_scope=ANCHOR_SCOPE, reference=ref
    )
    assert scope["scope_schema"] == "host-scope-v1"
    assert scope["as_of"] == "2026-01-21"
    assert scope["date_anchor"] == "relative"
    assert scope["source_domains"] == ["cbr.example"]


def test_scope_union_with_a_question_that_names_sources() -> None:
    question_scope = derive_claim_scope(
        question=(
            "Какова инфляция на текущую дату строго по этим источникам: "
            "http://interfax.example/news и http://ria.example/news"
        ),
        as_of=None,
        session_date=SESSION_DAY,
    )
    ref = resolve_reverify_reference(
        question="Какова инфляция на текущую дату?",
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    scope = merge_reverify_scope(
        question_scope=question_scope, existing_scope=ANCHOR_SCOPE, reference=ref
    )
    assert sorted(scope["source_domains"]) == ["cbr.example", "interfax.example", "ria.example"]
    assert scope["as_of"] == "2026-02-03"


def test_scope_carries_no_date_when_there_is_none_to_carry() -> None:
    question_scope = derive_claim_scope(
        question=QUESTION_NO_DATE, as_of=None, session_date=SESSION_DAY
    )
    ref = resolve_reverify_reference(
        question=QUESTION_NO_DATE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=None,
        existing_scope=None,
    )
    scope = merge_reverify_scope(
        question_scope=question_scope, existing_scope=None, reference=ref
    )
    assert scope["as_of"] is None
    assert scope["date_anchor"] == "none"


def test_audit_names_every_decision_about_the_reference_date() -> None:
    """Ни одно движение даты не остаётся молчаливым: в аудите перепроверки
    есть и «оставлено», и «сдвинуто»."""
    ref = resolve_reverify_reference(
        question=QUESTION_RELATIVE,
        proposal_as_of=None,
        session_date=SESSION_DAY,
        existing_as_of=ANCHOR_AS_OF,
        existing_scope=ANCHOR_SCOPE,
    )
    audit = reverify_as_of_audit(ref, existing_as_of=ANCHOR_AS_OF)
    assert audit["anchor_kept"] is False
    assert audit["stored_as_of"] == "2026-01-21T09:30:00+00:00"
    assert audit["anchor_date_changed"] == {
        "from": "2026-01-21T09:30:00+00:00",
        "to": "2026-02-03T00:00:00+00:00",
    }
