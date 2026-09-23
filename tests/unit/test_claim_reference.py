"""Unit: T7.34 (ADR-0018) — resolve_claim_reference, the fail-closed
resolution of the model's reference to an EXISTING claim (full UUID or
a unique prefix among the session-visible claims).

Pure function — no DB: the visible set is a list of UUIDs (at the
curator boundary — the context pack's [c:<uuid>] lines; at the commit
boundary — the claims with a head in the session's snapshot, T7.9).
"""

from __future__ import annotations

import uuid

import pytest

from packages.memory.service import resolve_claim_reference

pytestmark = [pytest.mark.unit]

#: A and B share the first 16 hex chars ("6e70379ee1f52284" — the
#: observed truncation length, EVAL-4d pack 7); C does not match them.
A = uuid.UUID("6e70379e-e1f5-2284-aaaa-bbbbccccdddd")
B = uuid.UUID("6e70379e-e1f5-2284-0000-111122223333")
C = uuid.UUID("11111111-2222-3333-4444-555566667777")


def test_full_uuid_visible_resolves() -> None:
    resolved, problem = resolve_claim_reference(str(A), [A, C])
    assert resolved == A and problem is None


def test_full_uuid_uppercase_resolves() -> None:
    # the model may echo the id in any case
    resolved, problem = resolve_claim_reference(str(A).upper(), [A, C])
    assert resolved == A and problem is None


def test_full_uuid_not_visible_rejected() -> None:
    other = uuid.uuid4()
    resolved, problem = resolve_claim_reference(str(other), [A, C])
    assert resolved is None
    assert problem is not None and "not visible" in problem


def test_32_hex_without_dashes_is_full_uuid() -> None:
    nodash = str(A).replace("-", "")
    resolved, problem = resolve_claim_reference(nodash, [A, C])
    assert resolved == A and problem is None
    # ...and the same 32-hex string of a NON-visible claim is rejected
    other = uuid.uuid4()
    resolved, problem = resolve_claim_reference(str(other).replace("-", ""), [A, C])
    assert resolved is None and "not visible" in problem


def test_unique_prefix_resolves() -> None:
    # the observed truncation length (EVAL-4d pack 7): 16 hex chars
    resolved, problem = resolve_claim_reference("6e70379ee1f52284", [A, C])
    assert resolved == A and problem is None


def test_prefix_with_dash_in_canonical_form_resolves() -> None:
    # a truncation that keeps the canonical dash ("6e70379e-e1f5…")
    resolved, problem = resolve_claim_reference("6e70379e-e1f5", [A, C])
    assert resolved == A and problem is None


def test_min_prefix_length_is_eight() -> None:
    resolved, problem = resolve_claim_reference("6e70379e", [A, C])
    assert resolved == A and problem is None
    resolved, problem = resolve_claim_reference("6e70379", [A, C])
    assert resolved is None and "unparseable" in problem


def test_ambiguous_prefix_rejected() -> None:
    # A and B share the first 16 hex chars — the prefix must NOT guess
    resolved, problem = resolve_claim_reference("6e70379ee1f52284", [A, B, C])
    assert resolved is None
    assert problem is not None and "ambiguous" in problem


def test_no_match_prefix_rejected() -> None:
    resolved, problem = resolve_claim_reference("ffffffffffffffff", [A, C])
    assert resolved is None
    assert problem is not None and "no visible claim matches" in problem


def test_garbage_rejected() -> None:
    for raw in ("c:6e70379ee1f52284", "6e70379e-e1f5-xxxx", "6e70379ee1f5228g", "123"):
        resolved, problem = resolve_claim_reference(raw, [A, C])
        assert resolved is None
        assert problem is not None and "unparseable" in problem, raw


def test_empty_visible_set_rejects_everything() -> None:
    resolved, problem = resolve_claim_reference(str(A), [])
    assert resolved is None and "not visible" in problem
    resolved, problem = resolve_claim_reference("6e70379ee1f52284", [])
    assert resolved is None and "no visible claim matches" in problem


def test_whitespace_tolerated() -> None:
    resolved, problem = resolve_claim_reference(f"  {A!s}  ", [A, C])
    assert resolved == A and problem is None
