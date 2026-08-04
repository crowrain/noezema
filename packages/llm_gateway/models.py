"""Gateway request, response, and retry contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, NonNegativeInt, StringConstraints, model_validator

from packages.domain import DecisionEnvelope
from packages.domain._base import ContractModel, Sha256Hex

MessageContent = Annotated[
    str,
    StringConstraints(min_length=1, max_length=1_000_000),
]
VersionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ModelRole(StrEnum):
    EXPLORER = "explorer"
    CURATOR = "curator"


class ModelPhase(StrEnum):
    PLANNING = "planning"
    EXPLORATION = "exploration"
    VERIFICATION = "verification"
    CONSOLIDATION = "consolidation"


class ChatMessage(ContractModel):
    role: ChatRole
    content: MessageContent


class GatewayRequest(ContractModel):
    messages: tuple[ChatMessage, ...]
    role: ModelRole
    phase: ModelPhase
    prompt_version: VersionText
    context_manifest_sha256: Sha256Hex
    policy_version: VersionText

    @model_validator(mode="after")
    def require_messages(self) -> GatewayRequest:
        if not self.messages:
            raise ValueError("at least one chat message is required")
        return self


class RetryPolicy(ContractModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_backoff_seconds: float = Field(default=0.25, ge=0.0)
    multiplier: float = Field(default=2.0, ge=1.0)
    max_backoff_seconds: float = Field(default=2.0, ge=0.0)


class TokenUsage(ContractModel):
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    total_tokens: NonNegativeInt


class BackendCompletion(ContractModel):
    content: str
    finish_reason: str
    backend_model: str
    usage: TokenUsage


class ModelRunResult(ContractModel):
    decision: DecisionEnvelope
    finish_reason: str
    backend_model: str
    usage: TokenUsage
    latency_ms: NonNegativeInt
    attempts: int = Field(ge=1)
    model_fingerprint_sha256: Sha256Hex
    invocation_fingerprint_sha256: Sha256Hex
    tool_schema_sha256: Sha256Hex
