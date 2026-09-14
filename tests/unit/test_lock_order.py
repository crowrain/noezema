"""Tests for the canonical partial lock order (T2.15, §5.2.2)."""

from __future__ import annotations

import pytest

from packages.domain.services.locks import CANONICAL_ORDER, LockOrderViolation, validate_lock_order

pytestmark = [pytest.mark.unit]


def test_full_canonical_order_is_valid() -> None:
    validate_lock_order(list(CANONICAL_ORDER))


def test_subsequence_is_valid() -> None:
    # skipping rows is allowed
    validate_lock_order([("sessions", ""), ("commit_attempts", "")])
    validate_lock_order([("domain_revisions", "knowledge")])
    validate_lock_order([])


def test_duplicates_allowed() -> None:
    validate_lock_order([("sessions", ""), ("sessions", "")])


@pytest.mark.parametrize(
    "bad",
    [
        [("commit_attempts", ""), ("sessions", "")],  # attempt before session
        [("domain_revisions", "knowledge"), ("sessions", "")],  # revision before session
        [
            ("domain_revisions", "dependency_graph"),
            ("domain_revisions", "knowledge"),
        ],  # graph before knowledge
        [("unknown", "x"), ("sessions", "")],  # unknown token
    ],
)
def test_out_of_order_rejected(bad: list[tuple[str, str]]) -> None:
    with pytest.raises(LockOrderViolation):
        validate_lock_order(bad)
