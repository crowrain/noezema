"""Frozen EVAL-3 payload budget validation (T7.7, §5.4.1, §22.2).

Regression: the first EVAL-3 launch (2026-09-16) failed with 0/50
sessions — ``token budgets invalid: section limits sum 26624 >
input_budget 22528`` — because max_output_tokens was raised 4096 → 8192
at freeze while the section sum (byte-identical to EVAL-2) was not
recomputed against the new budget. These tests load the actual frozen
payload files and require a valid budget before any session can start.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.cognition.tokenizer import TokenBudgets

REPO_ROOT = Path(__file__).resolve().parents[2]

# Section budgets byte-identical to EVAL-2/bootstrap (Σ = 26624) —
# A/B comparability of EVAL-3 against the EVAL-2 baseline (§22.2).
EVAL2_SECTIONS = {
    "claims_evidence": 8192,
    "contradictions": 3072,
    "identity": 2048,
    "last_session": 2048,
    "messages": 2048,
    "protocol": 4096,
    "question_plan": 3072,
    "recent_errors": 2048,
}


def _load(name: str) -> dict:
    return json.loads((REPO_ROOT / "docs" / "eval" / name).read_text())


@pytest.mark.unit
@pytest.mark.parametrize("name", ["config-v2-payload.json", "config-v3-payload.json"])
def test_frozen_payload_token_budgets_valid(name: str) -> None:
    payload = _load(name)
    budgets = TokenBudgets.from_snapshot(payload["model"], payload["token_budgets"])
    # The config must be startable: no budget problem at all.
    assert budgets.validate() == []
    # A/B comparability: the section budgets stay as in EVAL-2/bootstrap.
    assert budgets.section_limits == EVAL2_SECTIONS
    assert sum(budgets.section_limits.values()) == 26624
    assert sum(budgets.section_limits.values()) <= budgets.input_budget


@pytest.mark.unit
def test_frozen_payloads_v2_v3_budgets_identical() -> None:
    """The v2→v3 diff is exactly the two volatility lines: the model
    block and the section budgets must not drift, because the mid-run
    v2→v3 activation must not change the budget the model sees."""
    v2 = _load("config-v2-payload.json")
    v3 = _load("config-v3-payload.json")
    assert v2["model"] == v3["model"]
    assert v2["token_budgets"] == v3["token_budgets"]
