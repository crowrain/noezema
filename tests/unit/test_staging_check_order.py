"""T7.84 (часть B): гейт схемы разделён — проверка опорной даты исполняется ПОСЛЕ того,
как вид операции определён окончательно.

Ловушка T7.73 повторялась на другом конце конвейера: `validate_against` требовал `as_of`
от каждой `temporal_fact` ДО разрешения перепроверок (подмена типа якоря), до разрешения
`relied_claim_ids` и до гейта дублей по значению (T7.83a). Кураторский датless-пересказ уже
записанного утверждения (стенд ed36f4a0) отклонялся целиком, хотя следующим же шагом хост
превратил бы его в перепроверку, где дату опустить можно (её сохраняет якорь).

Разделение аддитивное: `validate_against` остаётся и отвечает ровно то же самое теми же
строками — на нём закреплены прежние тесты (`tests/unit/test_staging_schema.py`). Добавлены
две половины: структурные проверки (связи, бюджеты, search_statements, дубли зависимостей)
и отдельная проверка дат, принимающая exemption по индексам операций.
"""

from __future__ import annotations

import uuid

import pytest

from packages.domain.models.enums import ClaimType, DependencyKind, QuestionOrigin
from packages.domain.schemas.staging import (
    ClaimDependencyProposal,
    ClaimProposal,
    CuratorProposal,
    EvidenceLink,
    QuestionProposal,
)

pytestmark = pytest.mark.unit


def _proposal(**kwargs: object) -> CuratorProposal:
    return CuratorProposal.model_validate(
        {
            "summary": "s",
            "claims": [],
            "evidence_links": [],
            "new_questions": [],
            "relied_claim_ids": [],
            **kwargs,
        }
    )


TEMPORAL_NO_DATE = {"statement": "Инфляция 2025 года — 5,59%.", "claim_type": "temporal_fact"}
REVERIFY_OP = {
    "statement": "Инфляция 2025 года: пересказ.",
    "claim_type": "temporal_fact",
    "existing_claim_id": "11111111-1111-4111-8111-111111111111",
}


def test_structural_checks_stay_first_and_do_not_look_at_the_operation_kind() -> None:
    """Индексы связей, пустые search_statements, дубли зависимостей и бюджет вопросов —
    структурны и не зависят от того, чем операция станет после гейтов."""
    target = uuid.uuid4()
    proposal = _proposal(
        claims=[
            {**TEMPORAL_NO_DATE, "search_statements": ["  ", "x" * 301], "dependencies": [
                {"claim_id": str(target), "kind": "evidential"},
                {"claim_id": str(target), "kind": "evidential"},
            ]},
        ],
        evidence_links=[{"evidence_index": 3, "claim_index": 5, "relation": "supports"}],
        new_questions=[{"text": "уточнить", "origin": "model_proposal"}] * 5,
    )

    assert proposal.structural_problems(evidence_count=2, questions_max=4) == [
        "evidence_link[0]: index 3 out of range",
        "evidence_link[0]: claim index 5 out of range",
        "claim[0].search_statements[0]: empty",
        "claim[0].search_statements[1]: > 300 chars",
        f"claim[0].dependencies[1]: duplicate {str(target)[:8]}",
        "new_questions: 5 > 4",
    ]


def test_validate_against_messages_and_their_interleaved_order_are_byte_identical() -> None:
    """Прежний гейт не ослаблен и не переформулирован: ровно те же строки, в том же
    порядке (проверка дат идёт внутри обхода claims перед search_statements)."""
    target = uuid.uuid4()
    proposal = _proposal(
        claims=[
            {**TEMPORAL_NO_DATE, "search_statements": [""], "dependencies": [
                {"claim_id": str(target), "kind": "evidential"},
                {"claim_id": str(target), "kind": "evidential"},
            ]},
            REVERIFY_OP,
        ],
        evidence_links=[{"evidence_index": 0, "claim_index": 9, "relation": "counters"}],
        new_questions=[{"text": "уточнить", "origin": "model_proposal"}] * 6,
    )
    assert proposal.validate_against(evidence_count=1, questions_max=4) == [
        "evidence_link[0]: claim index 9 out of range",
        "claim[0]: temporal_fact requires as_of",
        "claim[0].search_statements[0]: empty",
        f"claim[0].dependencies[1]: duplicate {str(target)[:8]}",
        "new_questions: 6 > 4",
    ]


def test_the_date_check_is_a_separate_half_that_names_the_same_problem() -> None:
    proposal = _proposal(claims=[TEMPORAL_NO_DATE, REVERIFY_OP])

    assert proposal.temporal_as_of_problems() == ["claim[0]: temporal_fact requires as_of"]
    # операция, которую хост уже сделал перепроверкой (гейт дублей подставил якорь),
    # от даты не зависит: её дату сохраняет якорь (T7.73)
    assert proposal.temporal_as_of_problems(as_of_exempt={0}) == []


def test_the_two_halves_together_cover_exactly_what_validate_against_covers() -> None:
    """Разделение не потеряло ни одной проверки: объединение половин = множество проблем
    прежнего гейта на тех же предложениях."""
    shapes = [
        _proposal(claims=[TEMPORAL_NO_DATE]),
        _proposal(claims=[REVERIFY_OP]),
        _proposal(
            claims=[{**TEMPORAL_NO_DATE, "claim_type": "external_fact"}],
            evidence_links=[{"evidence_index": 4, "claim_index": 0, "relation": "supports"}],
        ),
        _proposal(
            claims=[TEMPORAL_NO_DATE, {**TEMPORAL_NO_DATE, "as_of": "2025-12-31T00:00:00"}],
            new_questions=[{"text": "q", "origin": "model_proposal"}] * 4,
        ),
    ]
    for proposal in shapes:
        combined = set(proposal.structural_problems(evidence_count=1)) | set(proposal.temporal_as_of_problems())
        assert combined == set(proposal.validate_against(1)), proposal


def test_structural_half_still_rejects_a_link_that_points_at_nothing() -> None:
    """Главный fail-closed части B: индекс связи проверяется ДО гейтов, потому что после
    конвертации в перепроверку у предложения меняется только вид операции, не индексы."""
    proposal = _proposal(
        claims=[TEMPORAL_NO_DATE],
        evidence_links=[{"evidence_index": 0, "claim_index": 1, "relation": "supports"}],
    )
    problems = proposal.structural_problems(evidence_count=1)
    assert problems == ["evidence_link[0]: claim index 1 out of range"]
    assert proposal.temporal_as_of_problems() == ["claim[0]: temporal_fact requires as_of"]


def test_enums_used_by_the_halves_are_the_ones_the_model_may_send() -> None:
    """Проверки не полагаются на текст формулировки — только на тип и наличие ссылки."""
    assert ClaimType.TEMPORAL_FACT.value == "temporal_fact"
    assert DependencyKind.EVIDENTIAL.value == "evidential"
    assert QuestionOrigin.MODEL_PROPOSAL.value == "model_proposal"
    proposal = _proposal(claims=[{**TEMPORAL_NO_DATE, "claim_type": "external_fact"}])
    assert proposal.temporal_as_of_problems() == []


def test_dependency_objects_are_validated_before_the_halves_run() -> None:
    dependency = ClaimDependencyProposal(claim_id=uuid.uuid4(), kind=DependencyKind.EVIDENTIAL)
    claim = ClaimProposal.model_validate({**TEMPORAL_NO_DATE, "dependencies": [dependency]})
    link = EvidenceLink(evidence_index=0, claim_index=0, relation="supports")
    question = QuestionProposal(text="q", origin=QuestionOrigin.MODEL_PROPOSAL)

    proposal = CuratorProposal(summary="s", claims=[claim], evidence_links=[link], new_questions=[question])
    assert proposal.structural_problems(evidence_count=1) == []
    assert proposal.temporal_as_of_problems() == ["claim[0]: temporal_fact requires as_of"]
