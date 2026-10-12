"""T7.105 (ADR-0034 вариант B): чистые хелперы пооперационного отказа.

Модуль `apps/orchestrator/curator_rejections.py` — разбор строк отказа и записи снятых
операций. Тестируется отдельно от оркестратора (AGENTS §4: вынесенная чистая логика
тестируется своей проверкой): здесь закреплены РОВНО те формы строк, которые дают
структурный гейт (`packages/domain/schemas/staging.py`) и предпроверка правил
(`packages/memory/service.py::validate_claim_proposal`), и форма записи отказа.
"""

from __future__ import annotations

import uuid

from apps.orchestrator.curator_rejections import (
    REJECTED_OPERATIONS_LIMIT,
    claim_index_from_line,
    rejected_operation_entries,
    rules_problems_by_operation,
    split_structural_problems,
)
from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, EvidenceLink


def _proposal() -> CuratorProposal:
    return CuratorProposal(
        summary="итог",
        claims=[
            ClaimProposal(
                statement="Новое утверждение",
                claim_type="temporal_fact",
                scope={"metric": "безработица", "region": "Тармения"},
                search_statements=[""],
                dependencies=[
                    {"claim_id": uuid.UUID(int=7), "kind": "evidential"},
                    {"claim_id": uuid.UUID(int=8), "kind": "research"},
                ],
            ),
            ClaimProposal(
                statement="Второе утверждение",
                claim_type="computed_result",
                scope={"expr": "6*7"},
            ),
        ],
        evidence_links=[
            EvidenceLink(evidence_index=0, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=1, claim_index=1, relation="counters"),
        ],
    )


# ── строка называет операцию или свойство предложения ────────────────────────


def test_claim_index_from_line_covers_all_named_forms() -> None:
    # as_of-гейт и reverify-гейт (форма `claim[<i>]: …`)
    assert claim_index_from_line("claim[0]: temporal_fact requires as_of") == 0
    assert claim_index_from_line(
        "claim[12]: reverify reference claim 550e8400 is not visible to the session"
    ) == 12
    # search_statements (форма `claim[<i>].search_statements[<k>]: …`)
    assert claim_index_from_line("claim[3].search_statements[1]: empty") == 3
    # обе формы строк движка правил
    assert claim_index_from_line("claim 2 (empirical_conjecture): bad as_of") == 2
    assert claim_index_from_line("claim 0: unknown claim_type 'bogus'") == 0
    # строки свойств предложения целиком — не про операцию
    assert claim_index_from_line("evidence_link[1]: index 99 out of range") is None
    assert claim_index_from_line("evidence_link[0]: claim index 7 out of range") is None
    assert claim_index_from_line("new_questions: 5 > 4") is None
    # dependencies: строка называет индекс (этот парсер не решает маршрутизацию —
    # гейт дублей зависимостей НЕ разбирается пооперационно, см. split ниже)
    assert claim_index_from_line("claim[1].dependencies[0]: duplicate 550e8400") == 1


def test_search_statement_lines_are_per_operation_others_whole() -> None:
    """Из строк структурного гейта пооперационна РОВНО проверка search_statements;
    индексы связей, бюджеты вопросов и дубли dependencies — свойства предложения."""
    problems = [
        "evidence_link[1]: index 99 out of range",
        "claim[0].search_statements[1]: empty",
        "claim[2].search_statements[0]: > 300 chars",
        "claim[2].search_statements[1]: empty",
        "claim[1].dependencies[0]: duplicate 550e8400",
        "new_questions: 5 > 4",
    ]
    whole, per_operation = split_structural_problems(problems)
    assert whole == [
        "evidence_link[1]: index 99 out of range",
        "claim[1].dependencies[0]: duplicate 550e8400",
        "new_questions: 5 > 4",
    ]
    assert per_operation == {
        0: ["claim[0].search_statements[1]: empty"],
        2: ["claim[2].search_statements[0]: > 300 chars", "claim[2].search_statements[1]: empty"],
    }


def test_split_keeps_order_and_content_of_whole_lines() -> None:
    """Свойства предложения возвращаются без перестановки и переформулировки — прежний
    `curator_rejected` остаётся прежним (байт-совместимость отказа целиком)."""
    problems = ["new_questions: 5 > 4", "evidence_link[0]: claim index 9 out of range"]
    whole, per_operation = split_structural_problems(problems)
    assert whole == problems
    assert per_operation == {}


# ── группировка строк предпроверки правил ────────────────────────────────────


def test_rules_problems_group_by_named_claim() -> None:
    grouped = rules_problems_by_operation(
        [
            "claim 0 (computed_result): support evidence kind 'local_observation' not allowed",
            "claim 2 (empirical_conjecture): support evidence kind 'source_assertion' not allowed",
            "claim 2 (empirical_conjecture): as_of missing",
        ]
    )
    assert grouped == {
        0: ["claim 0 (computed_result): support evidence kind 'local_observation' not allowed"],
        2: [
            "claim 2 (empirical_conjecture): support evidence kind 'source_assertion' not allowed",
            "claim 2 (empirical_conjecture): as_of missing",
        ],
    }


def test_rules_continuation_lines_attach_to_current_claim() -> None:
    grouped = rules_problems_by_operation(
        [
            "claim 1 (temporal_fact): bad as_of date",
            "detail: as_of is in the future",
            "claim 1 (temporal_fact): scope mismatch",
        ]
    )
    assert grouped == {
        1: [
            "claim 1 (temporal_fact): bad as_of date",
            "detail: as_of is in the future",
            "claim 1 (temporal_fact): scope mismatch",
        ]
    }
    # строка без индекса ДО первой названной операции не приписывается никому
    assert rules_problems_by_operation(["detached note"]) == {}


def test_rules_unknown_claim_type_form_groups() -> None:
    grouped = rules_problems_by_operation(["claim 0: unknown claim_type 'bogus'"])
    assert grouped == {0: ["claim 0: unknown claim_type 'bogus'"]}


# ── запись снятой операции ───────────────────────────────────────────────────


def test_rejected_operation_entry_names_reason_digest_links_and_dependencies() -> None:
    proposal = _proposal()
    entries = rejected_operation_entries(proposal, {0: "claim[0]: temporal_fact requires as_of"})
    assert len(entries) == 1
    entry = entries[0]
    assert entry["claim_index"] == 0
    assert entry["reason"] == "claim[0]: temporal_fact requires as_of"
    # digest операции — тот же снимок, что дайджест T7.84 (форма `_claim_entry`)
    assert entry["operation"]["statement"] == "Новое утверждение"
    assert entry["operation"]["claim_type"] == "temporal_fact"
    assert entry["operation"]["as_of"] is None
    assert entry["operation"]["existing_claim_id"] is None
    assert entry["operation"]["scope_keys"] == ["metric", "region"]
    # снятая связь названа; чужая (claim 1) — не попала
    assert entry["evidence_links_rejected"] == [
        {"claim_index": 0, "evidence_index": 0, "relation": "supports"}
    ]
    # dependencies снятой операции называются той же причиной
    assert entry["dependencies_rejected"] == [
        {"claim_id": str(uuid.UUID(int=7)), "kind": "evidential"},
        {"claim_id": str(uuid.UUID(int=8)), "kind": "research"},
    ]


def test_rejected_entries_only_for_dropped_operations_and_capped() -> None:
    proposal = _proposal()
    # выжившая операция не попадает в запись отказа, порядок — по индексу
    entries = rejected_operation_entries(
        proposal, {1: "claim 1 (computed_result): bad", 0: "claim[0]: temporal_fact requires as_of"}
    )
    assert [entry["claim_index"] for entry in entries] == [0, 1]
    # потолок — тот же порядок, что problems[:10]
    many = CuratorProposal(
        summary="итог",
        claims=[
            ClaimProposal(statement=f"утверждение {i}", claim_type="computed_result")
            for i in range(REJECTED_OPERATIONS_LIMIT + 5)
        ],
    )
    dropped = {i: f"claim {i} (computed_result): bad" for i in range(len(many.claims))}
    capped = rejected_operation_entries(many, dropped)
    assert len(capped) == REJECTED_OPERATIONS_LIMIT
    assert [entry["claim_index"] for entry in capped] == list(range(REJECTED_OPERATIONS_LIMIT))
