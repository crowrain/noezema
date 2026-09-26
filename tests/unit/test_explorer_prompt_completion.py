"""T7.50 (ADR-0022, option A): the explorer prompt's completion protocol
is locked to the host-side enum and normalizer.

The class-D defect (15 of 20 partial sessions across smokes V8–V13): the
model wrote free text into ``decision.reason`` ("Вопрос отвечен и
подтверждён двумя источниками…") instead of a completion token. The fix
lives at the source — the prompt (explorer-v5): ``reason`` is exactly
one token from a closed list, the explanation goes to
``public_rationale``. The host never derives a session success from free
text (ADR-0022, T7.49): ``normalize_complete_reason`` recognizes only an
explicit ``CompleteReason`` token at the START of the string. These
tests keep the prompt and the enum from drifting apart:

1. the ``CompleteReason`` tokens named by the prompt are exactly the
   enum minus ``operator_stop`` — that token is set by the HOST on an
   operator stop (``apps/orchestrator/orchestrator.py``, not the model),
   so the prompt must not offer it to the model at all;
2. the prompt's JSON examples are valid envelope responses, the correct
   one normalizes to ``GOAL_REACHED`` and the incorrect one (free-text
   ``reason``) to ``None`` — the host accepts exactly the correct shape
   (link to T7.49).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from packages.domain.models.enums import CompleteReason
from packages.domain.schemas.decision import ModelResponse, normalize_complete_reason

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPLORER_V5 = REPO_ROOT / "prompts" / "explorer" / "explorer-v5.md"
EXAMPLE_JSON = re.compile(r"```json\n(.*?)```", re.S)

# operator_stop is a host-set extension token: on a host-side operator
# stop the orchestrator assigns it to ctx.complete_reason itself
# (apps/orchestrator/orchestrator.py) — the model never produces it, so
# the prompt (which teaches the model) must not name it.
HOST_ONLY_TOKENS = frozenset({CompleteReason.OPERATOR_STOP.value})


def _prompt_text() -> str:
    assert EXPLORER_V5.is_file(), "explorer-v5.md is missing"
    return EXPLORER_V5.read_text(encoding="utf-8")


def _examples() -> list[dict]:
    blocks = EXAMPLE_JSON.findall(_prompt_text())
    assert len(blocks) == 2, f"expected 2 JSON examples, found {len(blocks)}"
    return [json.loads(b) for b in blocks]


@pytest.mark.unit
def test_prompt_completion_tokens_match_enum_minus_operator_stop() -> None:
    """The prompt's token list and the enum are one source of truth
    (ADR-0022): every token the prompt names is a real enum value, all
    model-settable enum values are named, and the host-only token is
    absent."""
    text = _prompt_text()
    enum_values = {member.value for member in CompleteReason}
    named = {value for value in enum_values if value in text}
    modelable = enum_values - HOST_ONLY_TOKENS
    assert named == modelable, (
        f"the prompt must name exactly the model-settable CompleteReason "
        f"tokens (the enum minus the host-only {sorted(HOST_ONLY_TOKENS)}): "
        f"missing {sorted(modelable - named)}, extra {sorted(named - modelable)}"
    )
    # and none of the host-only tokens is mentioned at all
    assert not (named & HOST_ONLY_TOKENS)


@pytest.mark.unit
def test_examples_are_valid_model_responses() -> None:
    """Both JSON examples parse and are valid ``ModelResponse``
    envelopes (T7.39/T7.43 pattern: examples must be well-formed; the
    incorrect one is incorrect by SEMANTICS — free text in ``reason``
    — not by syntax: the schema stays open by design, ADR-0022
    option B was rejected)."""
    for i, example in enumerate(_examples()):
        ModelResponse.model_validate(example)
        assert example["decision"]["kind"] == "complete", f"example[{i}] is not a completion"


@pytest.mark.unit
def test_correct_example_normalizes_to_goal_reached_and_incorrect_to_none() -> None:
    """Link to T7.49: the host's ``normalize_complete_reason`` accepts
    exactly the correct example's ``reason`` (GOAL_REACHED) and rejects
    the incorrect one (free text → None → succeeded_partial, the safe
    direction)."""
    outcomes = [
        normalize_complete_reason(example["decision"]["reason"]) for example in _examples()
    ]
    good = [o for o in outcomes if o is not None]
    assert len(good) == 1, f"exactly one example must be recognized, got {outcomes}"
    assert good[0] is CompleteReason.GOAL_REACHED, f"recognized token: {good[0]}"
    assert len([o for o in outcomes if o is None]) == 1, (
        f"exactly one example must stay unrecognized (free text), got {outcomes}"
    )
