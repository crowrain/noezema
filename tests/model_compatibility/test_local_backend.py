"""Opt-in compatibility check for a real local OpenAI-compatible backend."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from packages.cognition import (
    CuratorContext,
    CuratorProposal,
    PromptBundle,
    PromptKind,
    PromptSnapshot,
    ProtocolQuestion,
    build_curator_request,
    validate_curator_proposal,
)
from packages.domain import ClaimType, QuestionId, ToolDecision, ToolName
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
PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
        prompt_sha256="0" * 64,
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


def test_local_backend_returns_host_valid_curator_proposal() -> None:
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
    identity_path = PROJECT_ROOT / "prompts" / "identity.md"
    curator_path = PROJECT_ROOT / "prompts" / "curator.md"
    prompts = PromptBundle(
        identity=PromptSnapshot.load(
            identity_path,
            kind=PromptKind.IDENTITY,
            version="identity/v1",
            expected_sha256=hashlib.sha256(identity_path.read_bytes()).hexdigest(),
        ),
        role=PromptSnapshot.load(
            curator_path,
            kind=PromptKind.CURATOR,
            version="curator/v1",
            expected_sha256=hashlib.sha256(curator_path.read_bytes()).hexdigest(),
        ),
    )
    context = CuratorContext(
        question=ProtocolQuestion(
            id=QuestionId.new(),
            text="What can be concluded without any observations?",
        ),
        observations=(),
        allowed_claim_types=(ClaimType.EXTERNAL_FACT,),
        remaining_claim_budget=0,
        remaining_evidence_link_budget=0,
        remaining_handoff_budget=1,
    )
    request = build_curator_request(
        context,
        prompts=prompts,
        policy_version="compatibility/policy-v1",
    )

    with LLMGateway(profile=profile, transport=transport) as gateway:
        result = gateway.generate_structured(
            request,
            response_model=CuratorProposal,
            schema_name="noezema_curator_proposal_v1",
        )

    validate_curator_proposal(result.output, context=context)
    assert not result.output.claims
    assert result.usage.total_tokens > 0
