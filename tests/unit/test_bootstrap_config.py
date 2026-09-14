"""Tests for the hash-pinned bootstrap config (T1.3)."""

from __future__ import annotations

import uuid

import pytest

from packages.domain.canonical import canonical_sha256
from packages.domain.config import (
    BOOTSTRAP_PAYLOAD,
    BOOTSTRAP_PAYLOAD_SHA256,
    BOOTSTRAP_SNAPSHOT_ID,
    QUESTION_UUID5_NAMESPACE,
    bootstrap_snapshot_sha256,
    config_snapshot_sha256,
)


@pytest.mark.unit
def test_payload_hash_matches_pinned_literal() -> None:
    """The migration's fail-closed check: recomputed == expected."""
    assert canonical_sha256(BOOTSTRAP_PAYLOAD) == BOOTSTRAP_PAYLOAD_SHA256


@pytest.mark.unit
def test_payload_is_complete_for_rules_engine() -> None:
    rules = BOOTSTRAP_PAYLOAD["claim_type_rules"]
    assert set(rules) == {
        "local_observation",
        "computed_result",
        "formal_theorem",
        "empirical_conjecture",
        "procedural",
        "external_fact",
        "temporal_fact",
        "self_model",
    }
    for name, rule in rules.items():
        assert rule["min_grade_for_supported"] in {"E0", "E1", "E2", "E3", "E4"}
        assert rule["min_support_evidence"] >= 1
        assert rule["allowed_kinds"], f"{name}: no allowed kinds"


@pytest.mark.unit
def test_token_budgets_sum_within_context_window() -> None:
    model = BOOTSTRAP_PAYLOAD["model"]
    budgets = BOOTSTRAP_PAYLOAD["token_budgets"]
    input_budget = (
        model["context_window"] - model["max_output_tokens"] - model["safety_margin_tokens"]
    )
    assert sum(budgets.values()) <= input_budget


@pytest.mark.unit
def test_snapshot_sha256_depends_on_base_and_payload() -> None:
    base = uuid.uuid4()
    a = config_snapshot_sha256(base, "p" * 64)
    b = config_snapshot_sha256(uuid.uuid4(), "p" * 64)
    c = config_snapshot_sha256(base, "q" * 64)
    assert a != b  # different base
    assert a != c  # different payload
    assert a == config_snapshot_sha256(base, "p" * 64)  # deterministic


@pytest.mark.unit
def test_bootstrap_snapshot_id_is_stable_uuid5() -> None:
    assert isinstance(BOOTSTRAP_SNAPSHOT_ID, uuid.UUID)
    assert BOOTSTRAP_SNAPSHOT_ID.version == 5
    # re-derivation gives the same id (restore compatibility)
    rederived = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/crowrain/noezema/bootstrap-config-snapshot")
    assert rederived == BOOTSTRAP_SNAPSHOT_ID


@pytest.mark.unit
def test_question_namespace_is_valid_uuid() -> None:
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    assert ns.version == 5
    assert bootstrap_snapshot_sha256() == config_snapshot_sha256(None, BOOTSTRAP_PAYLOAD_SHA256)
