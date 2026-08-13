"""Trusted-host session budget snapshots and usage contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from packages.domain._base import ContractModel


class BudgetExhaustionReason(StrEnum):
    HOST_DEADLINE = "host_deadline"
    COGNITIVE_DEADLINE = "cognitive_deadline"
    MODEL_TURNS = "model_turns"
    TOOL_ACTIONS = "tool_actions"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"


class SessionBudget(ContractModel):
    """Immutable limits pinned when a session is admitted."""

    max_model_turns: int = Field(default=32, ge=1, le=10_000)
    max_tool_actions: int = Field(default=24, ge=1, le=10_000)
    max_input_tokens: int = Field(default=131_072, ge=1, le=100_000_000)
    max_output_tokens: int = Field(default=32_768, ge=1, le=100_000_000)
    cognitive_reserve_model_turns: int = Field(default=2, ge=1, le=1_000)
    cognitive_reserve_input_tokens: int = Field(default=16_384, ge=1)
    cognitive_reserve_output_tokens: int = Field(default=4_096, ge=1)
    cognitive_duration_seconds: int = Field(default=1_500, ge=1, le=604_800)
    host_reserve_seconds: int = Field(default=300, ge=1, le=86_400)

    @model_validator(mode="after")
    def reserve_must_fit_inside_total_budget(self) -> SessionBudget:
        if self.cognitive_reserve_model_turns >= self.max_model_turns:
            raise ValueError("model-turn reserve must leave at least one exploration turn")
        if self.cognitive_reserve_input_tokens >= self.max_input_tokens:
            raise ValueError("input-token reserve must leave a positive exploration budget")
        if self.cognitive_reserve_output_tokens >= self.max_output_tokens:
            raise ValueError("output-token reserve must leave a positive exploration budget")
        return self

    @property
    def exploration_model_turn_limit(self) -> int:
        return self.max_model_turns - self.cognitive_reserve_model_turns

    @property
    def exploration_input_token_limit(self) -> int:
        return self.max_input_tokens - self.cognitive_reserve_input_tokens

    @property
    def exploration_output_token_limit(self) -> int:
        return self.max_output_tokens - self.cognitive_reserve_output_tokens


class SessionBudgetUsage(ContractModel):
    """Durably derived consumption at one safe-boundary check."""

    model_turns: int = Field(ge=0)
    tool_actions: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    remaining_exploration_model_turns: int = Field(ge=0)
    remaining_tool_actions: int = Field(ge=0)
    remaining_exploration_input_tokens: int = Field(ge=0)
    remaining_exploration_output_tokens: int = Field(ge=0)
    exhaustion_reason: BudgetExhaustionReason | None = None


def default_session_budget() -> SessionBudget:
    return SessionBudget()
