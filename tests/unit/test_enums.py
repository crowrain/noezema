"""Tests for canonical enums (T1.1)."""

from __future__ import annotations

import pytest

from packages.domain.models.enums import (
    ActionState,
    AssessmentState,
    ClaimDateAnchor,
    ClaimType,
    CommitAttemptStatus,
    DecisionKind,
    EffectiveGrade,
    EpistemicStatus,
    EvidenceKind,
    FreshnessStatus,
    IdempotencyClass,
    MessageState,
    NodeState,
    OperatorCommandState,
    OperatorCommandType,
    PolicyDecision,
    QuestionOrigin,
    QuestionState,
    SessionState,
)


@pytest.mark.unit
def test_session_terminal_states() -> None:
    terminal = {s for s in SessionState if s.is_terminal}
    assert terminal == {
        SessionState.SUCCEEDED,
        SessionState.SUCCEEDED_PARTIAL,
        SessionState.FAILED,
        SessionState.CANCELLED,
    }


@pytest.mark.unit
def test_session_stop_range_waking_through_verifying() -> None:
    stoppable = {s for s in SessionState if s.allows_stop}
    assert stoppable == {
        SessionState.WAKING,
        SessionState.ORIENTING,
        SessionState.SELECTING_QUESTION,
        SessionState.PLANNING,
        SessionState.EXPLORING,
        SessionState.VERIFYING,
    }
    assert not SessionState.CREATED.allows_stop
    assert not SessionState.COMMITTING.allows_stop


@pytest.mark.unit
def test_session_abort_range() -> None:
    abortable = {s for s in SessionState if s.allows_abort}
    assert SessionState.CREATED in abortable
    assert SessionState.REPORTING in abortable
    assert not SessionState.COMMITTING.allows_abort
    assert not SessionState.RECONCILING_COMMIT.allows_abort
    for s in SessionState:
        if s.is_terminal:
            assert not s.allows_abort


@pytest.mark.unit
def test_grade_levels_are_monotonic() -> None:
    grades = [EffectiveGrade.E0, EffectiveGrade.E1, EffectiveGrade.E2, EffectiveGrade.E3, EffectiveGrade.E4]
    levels = [g.level for g in grades]
    assert levels == [0, 1, 2, 3, 4]


@pytest.mark.unit
def test_commit_attempt_unresolved_set() -> None:
    unresolved = {s for s in CommitAttemptStatus if s.is_unresolved}
    assert unresolved == {CommitAttemptStatus.PREPARED, CommitAttemptStatus.RECONCILING}


@pytest.mark.unit
def test_question_resolved_set() -> None:
    resolved = {q for q in QuestionState if q.is_resolved}
    assert resolved == {QuestionState.VERIFIED, QuestionState.REJECTED}


@pytest.mark.unit
@pytest.mark.parametrize(
    "enum_cls,expected_size",
    [
        (SessionState, 17),
        (NodeState, 2),
        (DecisionKind, 2),
        (PolicyDecision, 3),
        (ActionState, 7),
        (IdempotencyClass, 4),
        (EvidenceKind, 6),
        (EpistemicStatus, 5),
        (AssessmentState, 3),
        (FreshnessStatus, 5),  # T7.32 (ADR-0017): + evergreen
        (ClaimDateAnchor, 3),  # T7.32 (ADR-0017): the question's date anchor
        (ClaimType, 8),
        (QuestionState, 7),
        (QuestionOrigin, 9),
        (MessageState, 6),
        (OperatorCommandType, 8),
        (OperatorCommandState, 6),
    ],
)
def test_closed_enum_sizes(enum_cls: type, expected_size: int) -> None:
    """Registry drift guard: every enum is a closed set (§8.7, §13.2)."""
    assert len(list(enum_cls)) == expected_size
