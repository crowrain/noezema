"""T7.38: the claim_type↔evidence matrix in the curator prompt is a
single source of truth with the payload's claim_type_rules.

SMOKE-V8-K2 / EVAL-4d: K2 proposed pairs the rules engine rejects
(``computed_result ← local_observation`` ×7, ``local_observation ←
source_assertion``), because curator-v4 had no matrix and the rules
reject the WHOLE proposal on one bad pair (T7.9). curator-v5 states
the matrix; this test parses it out of the prompt and compares it
pair-by-pair with the payload's ``claim_type_rules.<type>.allowed_kinds``
— a future edit of the rules can never silently desync the prompt.

T7.39: extended to curator-v6 / config-v10 — the v5→v6 diff is the
reverify example only (a safe, corpus-free example), the matrix must
stay pair-identical.

T7.43: extended to curator-v7 / config-v11 — the v6→v7 diff is the
mandatory evidence binding (every claim ≥ 1 evidence link) and the
rule-7 negative example / weak-claim case; the matrix stays
pair-identical, and the prompt's one numeric evidence claim (a claim
with 0 evidence never passes the rules engine) is pinned against the
payload's min_support_evidence (single source of truth — the prompt
deliberately names no per-type numbers).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# A matrix row (indented inside the list item):
#     | `claim_type` | `kind`, `kind`, ... |
MATRIX_ROW = re.compile(r"^\s*\|\s*`([a-z_]+)`\s*\|\s*(.+?)\s*\|\s*$")
KIND = re.compile(r"`([a-z_]+)`")


def _parse_prompt_matrix(text: str) -> dict[str, set[str]]:
    """Parse the matrix table out of the prompt. Only backticked rows
    match, so the header (``| claim_type | ... |``) and the separator
    (``|---|---|``) are skipped naturally."""
    matrix: dict[str, set[str]] = {}
    for line in text.splitlines():
        m = MATRIX_ROW.match(line)
        if m is None:
            continue
        ctype, kinds_cell = m.groups()
        kinds = set(KIND.findall(kinds_cell))
        if kinds:
            matrix[ctype] = kinds
    return matrix


def _load_payload(name: str) -> dict:
    return json.loads((REPO_ROOT / "docs" / "eval" / name).read_text())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prompt_file", "payload_file"),
    [
        ("curator-v5.md", "config-v9-payload.json"),
        # T7.39: the v5→v6 diff is the example of rule 7 only — the
        # matrix must remain pair-identical to config-v10's rules.
        ("curator-v6.md", "config-v10-payload.json"),
        # T7.43: the v6→v7 diff is the evidence-binding rule and the
        # rule-7 negative example — the matrix must remain
        # pair-identical to config-v11's rules.
        ("curator-v7.md", "config-v11-payload.json"),
    ],
)
def test_curator_matrix_matches_claim_type_rules(
    prompt_file: str, payload_file: str
) -> None:
    prompt = (REPO_ROOT / "prompts" / "curator" / prompt_file).read_text(encoding="utf-8")
    prompt_matrix = _parse_prompt_matrix(prompt)
    assert len(prompt_matrix) == 8, f"expected 8 matrix rows, parsed {len(prompt_matrix)}"

    rules = _load_payload(payload_file)["claim_type_rules"]
    rule_pairs = {(t, k) for t, r in rules.items() for k in r["allowed_kinds"]}
    prompt_pairs = {(t, k) for t, kinds in prompt_matrix.items() for k in kinds}

    # a type in the prompt but absent from the rules is an error
    # (the model would be told a type the rules do not know)
    unknown = set(prompt_matrix) - set(rules)
    assert not unknown, f"prompt matrix types missing from claim_type_rules: {sorted(unknown)}"

    # the pair sets are equal: no prompt-only pair, no rules-only pair
    assert prompt_pairs == rule_pairs, (
        f"prompt-only pairs: {sorted(prompt_pairs - rule_pairs)}; "
        f"rules-only pairs: {sorted(rule_pairs - prompt_pairs)}"
    )


@pytest.mark.unit
def test_min_support_evidence_floor_matches_prompt_claim() -> None:
    """T7.43 single source of truth for the evidence minimums.

    curator-v7 makes exactly ONE numeric claim about evidence minimums:
    a claim with 0 evidence never passes the rules engine. It states the
    floor in words («минимум одну запись», «минимум одно evidence
    допустимого для типа вида; минимум два, где требуется») and
    deliberately names NO per-type numbers — because
    ``claim_type_rules`` sets ``min_support_evidence = 2`` for FOUR
    types (external_fact, temporal_fact, empirical_conjecture,
    procedural), not just external_fact/temporal_fact, so a per-type
    enumeration in the prompt would desync on the next rule change. The
    floor it does assert — ``min_support_evidence >= 1`` for every claim
    type — must hold in the payload v7 pins to (config-v11), or the
    prompt's «0 evidence никогда не проходит» claim becomes false.
    """
    rules = _load_payload("config-v11-payload.json")["claim_type_rules"]
    assert rules, "no claim_type_rules in payload"
    bad = {
        t: r["min_support_evidence"]
        for t, r in rules.items()
        if r["min_support_evidence"] < 1
    }
    assert not bad, f"claim types with min_support_evidence < 1: {bad}"
