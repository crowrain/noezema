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
        """Map reason to the closed enum; unknown reasons stay open strings."""
        if self.kind is not DecisionKind.COMPLETE or self.reason is None:
            return None
        try:
            return CompleteReason(self.reason)
        except ValueError:
            return None


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
