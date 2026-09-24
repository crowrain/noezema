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
from packages.llm_gateway.roles import Role, resolve_prompts

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


@pytest.mark.unit
def test_config_v8_pins_explorer_v4_and_differs_from_v7_only_there() -> None:
    """T7.35 follow-up (ADR-0019): config-v7 copied v6's stale
    ``explorer-v2`` label, while every run since T7.21 actually used
    ``explorer-v4`` (ADR-0019 forensics). With content pinning a stale pin
    becomes real behaviour, so v8 pins ``explorer-v4`` — and changes
    nothing else: v7 stays as committed (payloads are never rewritten)."""
    v7 = _load("config-v7-payload.json")
    v8 = _load("config-v8-payload.json")
    assert {k for k in v8 if v8[k] != v7[k]} == {"prompts"}
    assert {r for r in v8["prompts"] if v8["prompts"][r] != v7["prompts"][r]} == {"explorer"}
    resolved = resolve_prompts(v8["prompts"], REPO_ROOT)
    assert resolved[Role.EXPLORER].version == "explorer-v4"
    assert resolved[Role.CURATOR].version == "curator-v4"
    budgets = TokenBudgets.from_snapshot(v8["model"], v8["token_budgets"])
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v9_pins_curator_v5_and_differs_from_v8_only_there() -> None:
    """T7.38 (SMOKE-V8-K2 proposals 1/2/5 — one prompt file, one new
    payload): config-v9 = v8 with the single change
    ``prompts.curator`` → curator-v5 (the claim_type↔evidence matrix,
    the reverify rule with a concrete example, ``dependencies: []``).
    v8 stays as committed (payloads are never rewritten)."""
    v8 = _load("config-v8-payload.json")
    v9 = _load("config-v9-payload.json")
    assert {k for k in v9 if v9[k] != v8[k]} == {"prompts"}
    assert {r for r in v9["prompts"] if v9["prompts"][r] != v8["prompts"][r]} == {"curator"}
    assert v9["prompts"]["curator"]["version"] == "curator-v5"
    assert v9["prompts"]["curator"]["path"] == "prompts/curator/curator-v5.md"
    resolved = resolve_prompts(v9["prompts"], REPO_ROOT)
    assert resolved[Role.CURATOR].version == "curator-v5"
    assert resolved[Role.EXPLORER].version == "explorer-v4"
    budgets = TokenBudgets.from_snapshot(v9["model"], v9["token_budgets"])
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v10_pins_curator_v6_and_differs_from_v9_only_there() -> None:
    """T7.39 (T7.38 acceptance: the reverify example used a REAL claim
    id from SMOKE-V8-K2 — on a fresh DB the host would reject the
    copied reference and kill the whole proposal): config-v10 = v9
    with the single change ``prompts.curator`` → curator-v6 (the
    example swapped for a corpus-free fact + a fresh UUID; the matrix
    is unchanged). v9 stays as committed (payloads are never
    rewritten)."""
    v9 = _load("config-v9-payload.json")
    v10 = _load("config-v10-payload.json")
    assert {k for k in v10 if v10[k] != v9[k]} == {"prompts"}
    assert {r for r in v10["prompts"] if v10["prompts"][r] != v9["prompts"][r]} == {"curator"}
    assert v10["prompts"]["curator"]["version"] == "curator-v6"
    assert v10["prompts"]["curator"]["path"] == "prompts/curator/curator-v6.md"
    resolved = resolve_prompts(v10["prompts"], REPO_ROOT)
    assert resolved[Role.CURATOR].version == "curator-v6"
    assert resolved[Role.EXPLORER].version == "explorer-v4"
    budgets = TokenBudgets.from_snapshot(v10["model"], v10["token_budgets"])
    assert budgets.validate() == []
