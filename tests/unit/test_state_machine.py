"""Tests for the session state machine (T1.14, §6)."""

from __future__ import annotations

from itertools import pairwise

import pytest

from apps.orchestrator.state_machine import TRANSITIONS, InvalidTransitionError, can_transition, transition
from packages.domain.models.enums import SessionState as S


@pytest.mark.unit
def test_happy_path_is_valid() -> None:
    path = [
        S.CREATED, S.WAKING, S.ORIENTING, S.SELECTING_QUESTION, S.PLANNING,
        S.EXPLORING, S.VERIFYING, S.CONSOLIDATING, S.REPORTING, S.COMMITTING,
        S.SUCCEEDED,
    ]
    for src, dst in pairwise(path):
        assert can_transition(src, dst), f"{src} -> {dst}"
        assert transition(src, dst) is dst


@pytest.mark.unit
def test_stop_path_is_valid() -> None:
    assert can_transition(S.EXPLORING, S.STOPPING)
    assert can_transition(S.STOPPING, S.CONSOLIDATING)
    assert can_transition(S.CONSOLIDATING, S.SUCCEEDED_PARTIAL) is False  # via committing
    assert can_transition(S.STOPPING, S.CONSOLIDATING)


@pytest.mark.unit
def test_abort_and_cancel() -> None:
    assert can_transition(S.CREATED, S.ABORTING)
    assert can_transition(S.ABORTING, S.CANCELLED)
    assert can_transition(S.EXPLORING, S.CANCELLED)


@pytest.mark.unit
def test_terminal_states_are_absorbing() -> None:
    for src in (S.SUCCEEDED, S.SUCCEEDED_PARTIAL, S.FAILED, S.CANCELLED):
        assert TRANSITIONS[src] == frozenset()
        for dst in S:
            assert not can_transition(src, dst)


@pytest.mark.unit
@pytest.mark.parametrize(
    "src,dst",
    [
        (S.CREATED, S.EXPLORING),
        (S.COMMITTING, S.EXPLORING),
        (S.RECONCILING_COMMIT, S.COMMITTING),
        (S.SUCCEEDED, S.FAILED),
        (S.FAILED, S.SUCCEEDED),
    ],
)
def test_illegal_transitions_raise(src: S, dst: S) -> None:
    assert not can_transition(src, dst)
    with pytest.raises(InvalidTransitionError):
        transition(src, dst)
