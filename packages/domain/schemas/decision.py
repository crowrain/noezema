"""Structured decision protocol (§7, T1.2).

The model returns exactly one decision per turn. All causal/idempotency
identifiers (turn_id, model_run_id, action_id, idempotency_key) are created
by the trusted host — the envelope deliberately has no id fields and
rejects extras, so the LLM cannot choose a deduplication key (§20.10).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.domain.models.enums import CompleteReason, DecisionKind

_TOOL_NAME_RE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"  # namespace.name

# T7.49 (ADR-0022): the host must NEVER derive a session SUCCESS from free
# text by meaning or keywords (a false succeeded is worse than a false
# partial — the safe direction is partial, like unknown_action_outcome →
# failed). Only a completion reason in which the model EXPLICITLY names a
# CompleteReason token at the START of the string is recognized:
#  - trim; case-insensitive;
#  - surrounding quotes / backticks / a period are allowed;
#  - the token must end at a word boundary: end of string or a separator
#    (space, em/en dash, hyphen, ':', ';', ',', '.', '(');
#  - ``goal_reachedX``, ``not goal_reached`` and text where the token is
#    only mentioned (not at the start) do NOT match (None);
#  - the first token wins (if the suffix mentions another token);
#  - non-string / empty → None.
_COMPLETE_TOKENS = frozenset(member.value for member in CompleteReason)
_REASON_SEPARATORS = frozenset(" \u2014\u2013-:;,.( ")
_REASON_OPENERS = frozenset("\"'`")
_REASON_CLOSERS = frozenset("\"'`.")


def normalize_complete_reason(reason: str | None) -> CompleteReason | None:
    """Map a raw model completion reason to the closed CompleteReason enum.

    The single host-side recognition rule (T7.49, ADR-0022, T7.37b): an
    explicit enum token at the START of the string, up to a word boundary.
    Free-form reasons that merely describe the outcome (class D of the
    SMOKE measurement) and token mentions inside the text stay None —
    the host treats them exactly as before (succeeded_partial), never as
    success. No Russian-phrase dictionaries, no heuristics, no LLM.
    """
    if not isinstance(reason, str):
        return None
    s = reason.strip()
    while s and s[0] in _REASON_OPENERS:
        s = s[1:].lstrip()
    while s and s[-1] in _REASON_CLOSERS:
        s = s[:-1].rstrip()
    s = s.lower()
    if not s:
        return None
    for token in _COMPLETE_TOKENS:
        if s == token:
            return CompleteReason(token)
        if s.startswith(token) and s[len(token)] in _REASON_SEPARATORS:
            return CompleteReason(token)
    return None


class Decision(BaseModel):
    """A single typed decision: one tool call or completion."""

    model_config = ConfigDict(extra="forbid")

    kind: DecisionKind
    tool: str | None = Field(default=None, description="Required iff kind=tool.")
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(
        default=None,
        description="Required iff kind=complete; one of CompleteReason values or a host-defined extension.",
    )

    @field_validator("tool")
    @classmethod
    def _validate_tool_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(_TOOL_NAME_RE, value):
            raise ValueError(f"tool name must match namespace.name, got {value!r}")
        return value

    @model_validator(mode="after")
    def _check_kind_fields(self) -> Decision:
        if self.kind is DecisionKind.TOOL:
            if self.tool is None:
                raise ValueError("kind=tool requires 'tool'")
            if self.reason is not None:
                raise ValueError("kind=tool must not carry 'reason'")
        else:  # COMPLETE
            if self.tool is not None:
                raise ValueError("kind=complete must not carry 'tool'")
            if not self.reason:
                raise ValueError("kind=complete requires 'reason'")
        return self

    @property
    def is_complete(self) -> bool:
        return self.kind is DecisionKind.COMPLETE

    @property
    def normalized_reason(self) -> CompleteReason | None:
        """Map reason to the closed enum (T7.49, ADR-0022).

        Delegates to :func:`normalize_complete_reason`: only an explicit
        token at the start of the string is recognized; unknown reasons
        stay open strings (None).
        """
        if self.kind is not DecisionKind.COMPLETE or self.reason is None:
            return None
        return normalize_complete_reason(self.reason)


class ModelResponse(BaseModel):
    """Full structured response envelope (§7).

    ``public_rationale`` and ``expected_information`` are the only free
    text the model provides; the rest is machine-checked structure.
    """

    model_config = ConfigDict(extra="forbid")

    public_rationale: str = Field(min_length=1, max_length=4000)
    expected_information: str | None = Field(default=None, max_length=2000)
    decision: Decision

    @property
    def is_complete(self) -> bool:
        return self.decision.is_complete
