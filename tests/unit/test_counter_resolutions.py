"""Unit: T4.8 (§8.7.4) — the pure parts of counterevidence resolutions:
the transitive claim-dependency check (a resolution basis must not be
transitively dependent on the target — circular justification is
rejected)."""

from __future__ import annotations

import uuid

import pytest

from packages.memory.resolutions import claim_depends_on

A = uuid.uuid4()
B = uuid.uuid4()
C = uuid.uuid4()
D = uuid.uuid4()


def test_same_claim_is_a_dependency():
    assert claim_depends_on(A, A, ()) is True


def test_no_edges_no_dependency():
    assert claim_depends_on(A, B, ()) is False


def test_direct_dependency():
    assert claim_depends_on(A, B, ((A, B),)) is True


def test_no_dependency_in_reverse_direction():
    # (B, A) = B depends on A: A does NOT depend on B
    assert claim_depends_on(A, B, ((B, A),)) is False


def test_transitive_dependency():
    # A -> B -> C: A transitively depends on C
    assert claim_depends_on(A, C, ((A, B), (B, C))) is True


def test_diamond_stops_at_target():
    # A -> B, A -> C, B -> D, C -> D: A depends on D
    assert claim_depends_on(A, D, ((A, B), (A, C), (B, D), (C, D))) is True


def test_cycle_without_target_is_not_a_dependency():
    # A -> B -> A (cycle) and no path to C
    assert claim_depends_on(A, C, ((A, B), (B, A))) is False


def test_unrelated_component():
    assert claim_depends_on(A, C, ((B, D),)) is False


def test_cycle_that_reaches_target():
    # A -> B -> A and B -> C: A reaches C through the cycle
    assert claim_depends_on(A, C, ((A, B), (B, A), (B, C))) is True


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
