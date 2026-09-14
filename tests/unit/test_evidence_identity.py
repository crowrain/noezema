"""Unit: evidence identity — per-kind canonical hash + dedup (T3.2, §14.3)."""

from __future__ import annotations

from packages.domain.models.enums import EvidenceKind
from packages.memory.evidence import (
    canonical_equation,
    computation_identity,
    local_observation_identity,
    source_assertion_identity,
    tool_fingerprint,
)


def test_canonical_equation_reversed_is_same():
    assert canonical_equation("6*7=42") == canonical_equation("42=6*7")


def test_canonical_equation_collapses_whitespace():
    assert canonical_equation("   42=6*7   ") == canonical_equation("42=6*7")


def test_canonical_equation_non_equation_trimmed():
    assert canonical_equation("  hello   world  ") == "hello world"


def test_computation_identity_reversed_result_not_new_evidence():
    """A reversed repetition of the same computation is NOT new evidence."""
    a = computation_identity("6*7=42", "print(6*7)", "artifact-1")
    b = computation_identity("42=6*7", "print(6*7)", "artifact-1")
    assert a == b


def test_computation_identity_differs_by_input():
    a = computation_identity("42", "print(6*7)", "artifact-1")
    b = computation_identity("42", "print(7*6)", "artifact-1")
    assert a != b


def test_computation_identity_differs_by_result():
    a = computation_identity("42", "print(6*7)", "artifact-1")
    b = computation_identity("43", "print(6*7)", "artifact-1")
    assert a != b


def test_local_observation_identity_depends_on_environment():
    a = local_observation_identity("content", "env-A")
    b = local_observation_identity("content", "env-B")
    assert a != b


def test_local_observation_identity_same_content_same_env():
    a = local_observation_identity("content", "env-A")
    b = local_observation_identity("content", "env-A")
    assert a == b


def test_source_assertion_identity_by_range():
    a = source_assertion_identity("src-hash", "p.10-12", "source_assertion")
    b = source_assertion_identity("src-hash", "p.13-15", "source_assertion")
    assert a != b


def test_tool_fingerprint_is_deterministic():
    assert tool_fingerprint() == tool_fingerprint()


def test_evidence_kind_values_match_schema():
    # the per-kind CHECK in the migration uses these exact wire values
    assert EvidenceKind.COMPUTATION.value == "computation"
    assert EvidenceKind.LOCAL_OBSERVATION.value == "local_observation"
    assert EvidenceKind.FORMAL_CHECK.value == "formal_check"
