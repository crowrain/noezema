"""Engine reasoning profiles (T7.80, ADR-0030).

Two different things are combined into one gateway request here and must not be
confused:

* WHICH phases may think — a research-policy decision, kept in the effective
  config snapshot as ``model.reasoning_by_phase`` (§3 "effective config": rules
  and budgets live in the snapshot, not in code);
* HOW to tell this engine to stop thinking — a property of the ENGINE build on
  the other side of the socket, kept in the environment
  (``NOEZEMA_LLM_REASONING_PROFILE``, same reasoning as ``schema_profile`` in
  ADR-0012 §3: a capability of the deployment, not of the research).

The measured gap this module exists for (stand .92 with halogen-flash-next on
.141, session 1d0886fa, 2026-10-08): the curator answer was cut by the output
limit three times in a row — each attempt returned EXACTLY ``max_output_tokens``
completion tokens (~143–175 s each), the JSON document ended mid-string, and the
gateway repeated THE SAME request (reasoning included) until its retry budget ran
out. halogen reserves a fixed answer room of ~1000 tokens: reasoning is closed
"by answer_room" at ``max_tokens - 1000``, so raising ``max_output_tokens`` does
not buy the missing room — asking the engine not to think does (reasoning_tokens
0, schema-valid JSON in a fraction of a second).

Phase names here name LLM CALLS, not session states: ``model_runs.phase`` keeps
writing ``session.state`` ("exploring", "consolidating", …) and is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from packages.domain.models.base import JsonDict

# ── phases of the orchestrator's LLM calls ─────────────────────────────

#: Explorer loop: tool choice / completion decision. Thinking is the work here.
PHASE_EXPLORATION = "exploration"
#: Planner: a plan proposal for the step budget.
PHASE_PLANNING = "planning"
#: Extractor profile: verbatim quotes + chunk offsets from a document.
PHASE_EXTRACTION = "extraction"
#: Verifier: re-checks of claims against evidence already held.
PHASE_VERIFICATION = "verification"
#: Curator: the final proposal (claims / links / questions) — the call whose
#: payload is the longest and the one that was observed truncated.
PHASE_CONSOLIDATION = "consolidation"

#: The closed vocabulary of call phases the snapshot may name.
REASONING_PHASES: frozenset[str] = frozenset(
    {
        PHASE_EXPLORATION,
        PHASE_PLANNING,
        PHASE_EXTRACTION,
        PHASE_VERIFICATION,
        PHASE_CONSOLIDATION,
    }
)

#: A phase policy value: "on" = do not touch the request (the engine reasons as
#: it was configured to), "off" = ask this engine not to reason.
MODE_ON = "on"
MODE_OFF = "off"
REASONING_MODES: frozenset[str] = frozenset({MODE_ON, MODE_OFF})

#: ``finish_reason`` values that mean "the output limit stopped the answer".
TRUNCATION_FINISH_REASONS: frozenset[str] = frozenset({"length"})


# ── engine capability profiles ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReasoningProfile:
    """What this engine understands about turning reasoning off."""

    name: str
    #: Request parameters added when a phase is switched "off".
    #: ``None`` = this engine cannot be asked to stop reasoning at all; an
    #: honest gateway then reports the truncation instead of pretending.
    off_additions: JsonDict | None
    #: What has been measured on a real engine with this profile.
    evidence: str

    @property
    def can_disable_reasoning(self) -> bool:
        return self.off_additions is not None


#: "none" — the default: the gateway sends exactly what it sent before this
#: feature existed and never asks about reasoning (byte-for-byte comparability).
#:
#: "halogen" — halogen-flash-next: ``reasoning_effort: "none"`` measured on
#: .141 (2026-10-08) to give reasoning_tokens 0; ``reasoning_effort: "low"`` and
#: ``thinking_budget`` are IGNORED by this build, so only "none" is offered.
#:
#: "chat-template" — llama.cpp chat templates (Qwen3.x on .42 and the K2 builds):
#: ``chat_template_kwargs: {"enable_thinking": false}`` measured to give
#: reasoning_tokens 0.
REASONING_PROFILES: Mapping[str, ReasoningProfile] = {
    "none": ReasoningProfile(
        name="none",
        off_additions=None,
        evidence="no reasoning parameter is sent at all: request bodies stay byte-for-byte "
        "identical to the pre-T7.80 gateway",
    ),
    "halogen": ReasoningProfile(
        name="halogen",
        off_additions={"reasoning_effort": "none"},
        evidence="measured on halogen-flash-next (.141): reasoning_tokens 0, schema JSON without "
        "reasoning; 'low' and thinking_budget are ignored by this build",
    ),
    "chat-template": ReasoningProfile(
        name="chat-template",
        off_additions={"chat_template_kwargs": {"enable_thinking": False}},
        evidence="measured on llama.cpp Qwen3.x: enable_thinking=false gives reasoning_tokens 0",
    ),
}


def known_reasoning_profiles() -> tuple[str, ...]:
    return tuple(sorted(REASONING_PROFILES))


def require_reasoning_profile(name: str) -> ReasoningProfile:
    """Fail-closed lookup: an unknown profile is a deployment mistake and must
    stop the process at startup, not degrade silently into "no control"."""
    profile = REASONING_PROFILES.get(name)
    if profile is None:
        known = ", ".join(known_reasoning_profiles())
        raise ValueError(f"unknown reasoning_profile {name!r}; known profiles: {known}")
    return profile


def reasoning_body_additions(profile_name: str, mode: str | None) -> JsonDict:
    """Pure: the extra request parameters for one (engine profile, phase mode).

    ``None`` and ``"on"`` add NOTHING — a call whose phase has no policy is the
    same request the gateway sent before this feature existed. Only ``"off"``
    adds the engine's "stop reasoning" parameters; a profile that cannot disable
    reasoning adds nothing (the gateway then reports truncation honestly rather
    than sending a parameter the engine would ignore).
    """
    if mode != MODE_OFF:
        return {}
    additions = require_reasoning_profile(profile_name).off_additions
    if additions is None:
        return {}
    return dict(additions)


def can_disable_reasoning(profile_name: str) -> bool:
    return require_reasoning_profile(profile_name).can_disable_reasoning


# ── the snapshot policy: which phases think ─────────────────────────────


@dataclass(frozen=True, slots=True)
class ReasoningPolicy:
    """Resolved ``model.reasoning_by_phase`` of ONE effective snapshot.

    A phase that the snapshot does not name resolves to ``None`` — the gateway
    is then asked not to change the request at all (previous behavior). Absent
    section = absent policy for every phase, so config-v1…v17 behave exactly as
    before.
    """

    modes: Mapping[str, str]

    def mode_for(self, phase: str) -> str | None:
        return self.modes.get(phase)


def reasoning_payload_problems(payload: Mapping[str, Any]) -> list[str]:
    """Problems with ``payload["model"]["reasoning_by_phase"]``; [] = acceptable.

    Checked BEFORE a snapshot is published (activation is fail-closed): a typo
    in the new key must not become a runtime failure of every session that reads
    the snapshot. An absent key or an empty section is fine — it means "no
    policy", i.e. the requests stay as they are today.
    """
    section = payload.get("model")
    if not isinstance(section, Mapping):
        return []  # model section shape is the existing validation's business
    raw = section.get("reasoning_by_phase")
    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        return ["model.reasoning_by_phase must be an object mapping phase -> 'on' | 'off'"]
    problems: list[str] = []
    for phase, mode in raw.items():
        if not isinstance(phase, str) or phase not in REASONING_PHASES:
            problems.append(f"model.reasoning_by_phase: unknown phase {phase!r}")
            continue
        if mode not in REASONING_MODES:
            problems.append(
                f"model.reasoning_by_phase.{phase} must be 'on' or 'off', got {mode!r}"
            )
    return problems


def resolve_reasoning_policy(model_section: Mapping[str, Any] | None) -> ReasoningPolicy:
    """Pure resolution of the snapshot section into per-phase modes.

    Malformed content raises: a snapshot that activation accepted must not be
    guessed at here (fail-closed), and activation validates this key first
    (`reasoning_payload_problems`).
    """
    if model_section is None:
        return ReasoningPolicy(modes={})
    problems = reasoning_payload_problems({"model": dict(model_section)})
    if problems:
        raise ValueError("invalid model.reasoning_by_phase in the effective snapshot: " + "; ".join(problems))
    raw = model_section.get("reasoning_by_phase")
    if not isinstance(raw, Mapping):
        return ReasoningPolicy(modes={})
    return ReasoningPolicy(modes={str(k): str(v) for k, v in raw.items()})


# ── truncation: an output-limit stop is not a schema hiccup ────────────


def completion_was_truncated(
    *, finish_reason: str | None, output_tokens: int | None, max_output_tokens: int
) -> bool:
    """Pure: did the engine STOP because of the output limit?

    Two signals, either is enough:
    * ``finish_reason == "length"`` — the OpenAI-compatible statement of it;
    * completion tokens at or above the requested cap. The measured halogen
      behavior (three attempts, all exactly 8192 = the requested max) shows a
      stop reason alone is not enough to rely on: a capped answer that reports
      "stop" is still an answer cut by the limit.
    """
    if finish_reason in TRUNCATION_FINISH_REASONS:
        return True
    return output_tokens is not None and output_tokens >= max_output_tokens
