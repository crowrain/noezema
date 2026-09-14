"""Session state machine (T1.14, §6).

Transitions are a closed table; the repository never guesses. Terminal
states are absorbing. ``reconciling_commit`` is not terminal but freezes
the session (M2 adds the fenced protocol behind it).
"""

from __future__ import annotations

from packages.domain.models.enums import SessionState

S = SessionState

TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    S.CREATED: frozenset({S.WAKING, S.ABORTING, S.CANCELLED, S.FAILED}),
    S.WAKING: frozenset({S.ORIENTING, S.STOPPING, S.ABORTING, S.CANCELLED, S.FAILED}),
    S.ORIENTING: frozenset({S.SELECTING_QUESTION, S.STOPPING, S.ABORTING, S.CANCELLED, S.FAILED}),
    S.SELECTING_QUESTION: frozenset({S.PLANNING, S.STOPPING, S.ABORTING, S.CANCELLED, S.FAILED}),
    S.PLANNING: frozenset({S.EXPLORING, S.STOPPING, S.ABORTING, S.CANCELLED, S.FAILED}),
    S.EXPLORING: frozenset({S.VERIFYING, S.STOPPING, S.ABORTING, S.CANCELLED, S.FAILED}),
    S.VERIFYING: frozenset({S.CONSOLIDATING, S.STOPPING, S.ABORTING, S.CANCELLED, S.FAILED}),
    S.STOPPING: frozenset({S.CONSOLIDATING, S.FAILED, S.CANCELLED}),
    S.CONSOLIDATING: frozenset({S.REPORTING, S.COMMITTING, S.FAILED, S.CANCELLED}),
    S.REPORTING: frozenset({S.COMMITTING, S.FAILED, S.CANCELLED}),
    S.COMMITTING: frozenset({S.SUCCEEDED, S.SUCCEEDED_PARTIAL, S.FAILED, S.RECONCILING_COMMIT}),
    S.RECONCILING_COMMIT: frozenset({S.SUCCEEDED, S.SUCCEEDED_PARTIAL, S.FAILED, S.CANCELLED}),
    S.ABORTING: frozenset({S.FAILED, S.CANCELLED}),
    # absorbing
    S.SUCCEEDED: frozenset(),
    S.SUCCEEDED_PARTIAL: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}


class InvalidTransitionError(RuntimeError):
    def __init__(self, src: SessionState, dst: SessionState) -> None:
        super().__init__(f"illegal session transition: {src.value} -> {dst.value}")
        self.src = src
        self.dst = dst


def can_transition(src: SessionState, dst: SessionState) -> bool:
    return dst in TRANSITIONS[src]


def transition(src: SessionState, dst: SessionState) -> SessionState:
    if not can_transition(src, dst):
        raise InvalidTransitionError(src, dst)
    return dst
