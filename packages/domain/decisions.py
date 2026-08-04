"""Schema for the single decision returned by an LLM model run."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from packages.domain._base import ContractModel, JsonObject, NonEmptyText, ShortReason
from packages.domain.enums import DecisionKind, ToolName


class ToolDecision(ContractModel):
    """One typed tool request without host-controlled identifiers."""

    kind: Literal[DecisionKind.TOOL]
    tool: ToolName
    arguments: JsonObject


class CompleteDecision(ContractModel):
    """Request an internal, fenced transition to consolidation."""

    kind: Literal[DecisionKind.COMPLETE]
    reason: ShortReason


Decision = Annotated[ToolDecision | CompleteDecision, Field(discriminator="kind")]


class DecisionEnvelope(ContractModel):
    """Exactly one public rationale and one typed decision."""

    public_rationale: NonEmptyText
    expected_information: NonEmptyText | None = None
    decision: Decision

    @model_validator(mode="after")
    def require_tool_expectation(self) -> DecisionEnvelope:
        if isinstance(self.decision, ToolDecision) and self.expected_information is None:
            raise ValueError("expected_information is required for a tool decision")
        return self
