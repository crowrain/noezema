"""Opt-in compatibility check for a real local OpenAI-compatible backend."""

from __future__ import annotations

import os

import pytest

from packages.domain import ToolDecision, ToolName
from packages.llm_gateway import (
    BackendMetadata,
    ChatMessage,
    ChatRole,
    GatewayRequest,
    LLMGateway,
    ModelPhase,
    ModelProfile,
    ModelRole,
    OpenAICompatibleTransport,
    SamplingSettings,
    StructuredOutputSettings,
    TransportConfig,
)

pytestmark = pytest.mark.model_compatibility

_REQUIRED_ENV = (
    "NOEZEMA_LLM_BASE_URL",
    "NOEZEMA_LLM_MODEL",
    "NOEZEMA_LLM_MODEL_SHA256",
    "NOEZEMA_LLM_TOKENIZER_SHA256",
    "NOEZEMA_LLM_TEMPLATE_SHA256",
    "NOEZEMA_LLM_GRAMMAR_SHA256",
    "NOEZEMA_LLM_BACKEND",
    "NOEZEMA_LLM_BACKEND_VERSION",
    "NOEZEMA_LLM_BUILD_FINGERPRINT",
)


def _required_environment() -> dict[str, str]:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        pytest.skip(f"local LLM profile is not configured; missing: {', '.join(missing)}")
    return {name: os.environ[name] for name in _REQUIRED_ENV}


def test_local_backend_returns_schema_valid_tool_decision() -> None:
    environment = _required_environment()
    profile = ModelProfile(
        base_url=environment["NOEZEMA_LLM_BASE_URL"],
        model_alias=environment["NOEZEMA_LLM_MODEL"],
        model_artifact_sha256=environment["NOEZEMA_LLM_MODEL_SHA256"],
        quantization=os.environ.get("NOEZEMA_LLM_QUANTIZATION", "unknown"),
        tokenizer_sha256=environment["NOEZEMA_LLM_TOKENIZER_SHA256"],
        chat_template_sha256=environment["NOEZEMA_LLM_TEMPLATE_SHA256"],
        backend=BackendMetadata(
            name=environment["NOEZEMA_LLM_BACKEND"],
            version=environment["NOEZEMA_LLM_BACKEND_VERSION"],
            build_fingerprint=environment["NOEZEMA_LLM_BUILD_FINGERPRINT"],
        ),
        context_window=32768,
        max_output_tokens=1024,
        safety_margin_tokens=2048,
        structured_output=StructuredOutputSettings(
            grammar_sha256=environment["NOEZEMA_LLM_GRAMMAR_SHA256"]
        ),
        sampling=SamplingSettings(seed=42, temperature=0, top_p=1),
    )
    transport = OpenAICompatibleTransport(
        profile=profile,
        config=TransportConfig(api_key=os.environ.get("NOEZEMA_LLM_API_KEY")),
    )
    request = GatewayRequest(
        messages=(
            ChatMessage(
                role=ChatRole.SYSTEM,
                content="Верни ровно одно решение по заданной JSON Schema.",
            ),
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    "Выбери инструмент memory.search и найди в памяти сведения о NOEZEMA. "
                    "Не завершай сессию."
                ),
            ),
        ),
        role=ModelRole.EXPLORER,
        phase=ModelPhase.EXPLORATION,
        prompt_version="compatibility/explorer-v1",
        context_manifest_sha256="0" * 64,
        policy_version="compatibility/policy-v1",
    )

    with LLMGateway(profile=profile, transport=transport) as gateway:
        result = gateway.generate_decision(request)

    assert isinstance(result.decision.decision, ToolDecision)
    assert result.decision.decision.tool is ToolName.MEMORY_SEARCH
    assert result.usage.total_tokens > 0
    assert result.model_fingerprint_sha256
    assert result.invocation_fingerprint_sha256
