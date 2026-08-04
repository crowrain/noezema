"""Local OpenAI-compatible LLM gateway."""

from packages.llm_gateway.config import (
    BackendMetadata,
    ModelProfile,
    RuntimeSettings,
    SamplingSettings,
    StructuredOutputSettings,
    TransportConfig,
)
from packages.llm_gateway.errors import (
    BackendProtocolError,
    IncompleteModelOutputError,
    InvalidModelOutputError,
    LLMGatewayError,
    PermanentBackendError,
    RetryExhaustedError,
    TransientBackendError,
)
from packages.llm_gateway.fingerprint import (
    InvocationFingerprint,
    model_fingerprint_sha256,
    tool_schema_sha256,
)
from packages.llm_gateway.gateway import LLMGateway
from packages.llm_gateway.models import (
    ChatMessage,
    ChatRole,
    GatewayRequest,
    ModelPhase,
    ModelRole,
    ModelRunResult,
    RetryPolicy,
    TokenUsage,
)
from packages.llm_gateway.transport import OpenAICompatibleTransport

__all__ = [
    "BackendMetadata",
    "BackendProtocolError",
    "ChatMessage",
    "ChatRole",
    "GatewayRequest",
    "IncompleteModelOutputError",
    "InvalidModelOutputError",
    "InvocationFingerprint",
    "LLMGateway",
    "LLMGatewayError",
    "ModelPhase",
    "ModelProfile",
    "ModelRole",
    "ModelRunResult",
    "OpenAICompatibleTransport",
    "PermanentBackendError",
    "RetryExhaustedError",
    "RetryPolicy",
    "RuntimeSettings",
    "SamplingSettings",
    "StructuredOutputSettings",
    "TokenUsage",
    "TransientBackendError",
    "TransportConfig",
    "model_fingerprint_sha256",
    "tool_schema_sha256",
]
