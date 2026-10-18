"""Unit: engine reasoning profiles and truncation classification (T7.80, ADR-0030).

Pure-module tests: the request-body additions per (engine profile, phase mode),
the snapshot policy resolution, the fail-closed startup check for an unknown
profile, and the "output limit" classification. The gateway behavior itself is
tested in `tests/unit/test_llm_truncation.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.llm_gateway.config import LLMGatewayConfig
from packages.llm_gateway.reasoning_compat import (
    MODE_OFF,
    MODE_ON,
    PHASE_CONSOLIDATION,
    PHASE_EXPLORATION,
    PHASE_EXTRACTION,
    PHASE_PLANNING,
    PHASE_VERIFICATION,
    REASONING_PHASES,
    ReasoningPolicy,
    can_disable_reasoning,
    completion_was_truncated,
    known_reasoning_profiles,
    reasoning_body_additions,
    reasoning_payload_problems,
    require_reasoning_profile,
    resolve_reasoning_policy,
)

pytestmark = [pytest.mark.unit]


def test_phase_vocabulary_is_closed_and_names_llm_calls() -> None:
    """The five call phases the orchestrator can name. These are NOT session
    states (model_runs.phase keeps writing exploring/consolidating/…)."""
    assert frozenset(
        {
            PHASE_EXPLORATION,
            PHASE_PLANNING,
            PHASE_EXTRACTION,
            PHASE_VERIFICATION,
            PHASE_CONSOLIDATION,
        }
    ) == REASONING_PHASES


def test_profiles_registry_is_closed() -> None:
    assert known_reasoning_profiles() == ("chat-template", "halogen", "none")
    assert require_reasoning_profile("none").off_additions is None
    assert can_disable_reasoning("none") is False
    assert can_disable_reasoning("halogen") is True
    assert can_disable_reasoning("chat-template") is True


def test_halogen_off_adds_reasoning_effort_none() -> None:
    """Measured on .141: `reasoning_effort: "none"` gives reasoning_tokens 0.
    ("low" and thinking_budget are ignored by this build — not offered.)"""
    assert reasoning_body_additions("halogen", MODE_OFF) == {"reasoning_effort": "none"}


def test_chat_template_off_adds_enable_thinking_false() -> None:
    """llama.cpp chat templates (Qwen3.x): the switch lives in the template kwargs."""
    assert reasoning_body_additions("chat-template", MODE_OFF) == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_on_and_unknown_mode_add_nothing() -> None:
    """mode "on" and "no policy known" must not change the request at all."""
    for profile in known_reasoning_profiles():
        assert reasoning_body_additions(profile, MODE_ON) == {}
        assert reasoning_body_additions(profile, None) == {}


def test_profile_none_off_adds_nothing_and_cannot_disable() -> None:
    """Default deployment: no capability is invented — the gateway reports the
    truncation instead of sending a parameter this engine would ignore."""
    assert reasoning_body_additions("none", MODE_OFF) == {}
    assert can_disable_reasoning("none") is False


def test_unknown_reasoning_profile_fails_closed_at_startup() -> None:
    with pytest.raises(ValidationError):
        LLMGatewayConfig(reasoning_profile="bogus-engine")
    # the default is the pre-T7.80 behavior
    assert LLMGatewayConfig().reasoning_profile == "none"
    assert LLMGatewayConfig(reasoning_profile="halogen").reasoning_profile == "halogen"
    assert LLMGatewayConfig(reasoning_profile="chat-template").reasoning_profile == "chat-template"


def test_reasoning_profile_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deployment capability, not research policy: the profile is read from env
    (the same channel as NOEZEMA_LLM_SCHEMA_PROFILE), the phase policy from the
    snapshot. An unknown value in the env fails the process at startup."""
    monkeypatch.setenv("NOEZEMA_LLM_REASONING_PROFILE", "chat-template")
    assert LLMGatewayConfig().reasoning_profile == "chat-template"
    monkeypatch.setenv("NOEZEMA_LLM_REASONING_PROFILE", "no-such-engine")
    with pytest.raises(ValidationError):
        LLMGatewayConfig()


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        (None, {}),
        ({}, {}),
        ({"reasoning_by_phase": None}, {}),
        ({"reasoning_by_phase": {}}, {}),
        ({"reasoning_by_phase": {PHASE_CONSOLIDATION: MODE_OFF}}, {PHASE_CONSOLIDATION: "off"}),
        (
            {"reasoning_by_phase": {PHASE_EXPLORATION: MODE_ON, PHASE_EXTRACTION: MODE_OFF}},
            {PHASE_EXPLORATION: "on", PHASE_EXTRACTION: "off"},
        ),
    ],
)
def test_resolve_reasoning_policy_maps_snapshot_to_modes(
    section: object, expected: dict[str, str]
) -> None:
    policy = resolve_reasoning_policy(section)  # type: ignore[arg-type]
    assert isinstance(policy, ReasoningPolicy)
    assert dict(policy.modes) == expected
    for phase in REASONING_PHASES:
        assert policy.mode_for(phase) == expected.get(phase), phase


def test_resolve_reasoning_policy_rejects_malformed_section() -> None:
    """A snapshot the activation accepted must not be guessed at here."""
    with pytest.raises(ValueError):
        resolve_reasoning_policy({"reasoning_by_phase": {PHASE_CONSOLIDATION: "always"}})
    with pytest.raises(ValueError):
        resolve_reasoning_policy({"reasoning_by_phase": {PHASE_VERIFICATION: 0}})
    with pytest.raises(ValueError):
        resolve_reasoning_policy({"reasoning_by_phase": ["off"]})


@pytest.mark.parametrize(
    ("phase", "mode", "fragment"),
    [
        (PHASE_CONSOLIDATION, "always", "must be 'on' or 'off'"),
        ("curating", MODE_OFF, "unknown phase"),
    ],
)
def test_activation_validation_flags_bad_new_key(phase: str, mode: object, fragment: str) -> None:
    problems = reasoning_payload_problems({"model": {"reasoning_by_phase": {phase: mode}}})
    assert problems, problems
    assert fragment in " ".join(problems)


def test_activation_validation_accepts_absent_or_valid_section() -> None:
    assert reasoning_payload_problems({}) == []
    assert reasoning_payload_problems({"model": {}}) == []
    assert reasoning_payload_problems({"model": {"reasoning_by_phase": None}}) == []
    assert (
        reasoning_payload_problems(
            {"model": {"reasoning_by_phase": {PHASE_CONSOLIDATION: MODE_OFF, PHASE_EXPLORATION: MODE_ON}}}
        )
        == []
    )
    # a non-object section is named as such, not silently ignored
    assert reasoning_payload_problems({"model": {"reasoning_by_phase": ["off"]}})


@pytest.mark.parametrize(
    ("finish_reason", "output_tokens", "cap", "expected"),
    [
        ("length", 8192, 8192, True),  # the OpenAI-compatible statement of it
        ("length", 8192, 16384, True),  # stop reason alone is enough
        ("stop", 8192, 8192, True),  # measured halogen: an answer at the cap, reported "stop"
        ("stop", 8193, 8192, True),
        ("stop", 7419, 8192, False),  # a long but complete answer
        ("stop", None, 8192, False),  # an engine that reports no usage
        (None, 8192, 8192, True),
        ("stop", 20, 4096, False),  # the ordinary test case
    ],
)
def test_completion_was_truncated(
    finish_reason: str | None, output_tokens: int | None, cap: int, expected: bool
) -> None:
    assert (
        completion_was_truncated(
            finish_reason=finish_reason, output_tokens=output_tokens, max_output_tokens=cap
        )
        is expected
    )
