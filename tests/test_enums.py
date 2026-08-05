"""Tests for domain enums."""

from packages.domain.models.enums import SessionState, CommitAttemptStatus


def test_terminal_states():
    assert SessionState.SUCCEEDED.is_terminal
    assert SessionState.FAILED.is_terminal
    assert not SessionState.EXPLORING.is_terminal


def test_commit_attempt_unresolved():
    assert CommitAttemptStatus.PREPARED.is_unresolved
    assert CommitAttemptStatus.RECONCILING.is_unresolved
    assert not CommitAttemptStatus.COMMITTED.is_unresolved
    assert not CommitAttemptStatus.ABORTED.is_unresolved
