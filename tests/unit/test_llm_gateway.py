"""Tests for the LLM Gateway (T1.7-T1.9) against the fake LLM."""

from __future__ import annotations

import pytest

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import DecisionKind
from packages.domain.schemas.decision import ModelResponse
from packages.llm_gateway.client import (
    LLMError,
    LLMMiddleware,
    LLMSchemaError,
    LLMTransientError,
)
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.llm_gateway.fingerprint import build_model_fingerprint
from tests.conftest import FakeLLM

TOOL_RESPONSE: JsonDict = {
    "public_rationale": "Проверить источник",
    "expected_information": "Первичный документ",
    "decision": {"kind": "tool", "tool": "web.search", "arguments": {"query": "spec"}},
}
COMPLETE_RESPONSE: JsonDict = {
    "public_rationale": "Цель достигнута",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}


def _gateway(fake: FakeLLM, **overrides: object) -> LLMMiddleware:
    params: dict[str, object] = {
        "base_url": fake.base_url,
        "model": "fake-thinker",
        "max_retries": 3,
        "retry_base_delay": 0.01,
    }
    params.update(overrides)  # type: ignore[arg-type]
    return LLMMiddleware(LLMGatewayConfig(**params))  # type: ignore[arg-type]


@pytest.mark.unit
async def test_chat_returns_validated_model(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)
    try:
        model, record = await gateway.chat(
            system="sys", user="user", response_schema=ModelResponse
        )
    finally:
        await gateway.close()

    assert isinstance(model, ModelResponse)
    assert model.decision.kind is DecisionKind.TOOL
    assert model.decision.tool == "web.search"
    assert record.output_schema_valid is True
    assert record.attempts == 1
    assert record.input_tokens == 10
    assert record.output_tokens == 20
    assert record.latency_ms >= 0
    assert record.finish_reason == "stop"


@pytest.mark.unit
async def test_transient_error_is_retried(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": 503}, {"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)
    try:
        _model, record = await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert record.attempts == 2
    assert record.output_schema_valid is True


@pytest.mark.unit
async def test_transient_exhausted_raises(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": 500}, {"error": 502}, {"error": 503}])
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMTransientError):
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert fake_llm.state()["request_count"] == 3


@pytest.mark.unit
async def test_permanent_error_not_retried(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": 400}])
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMError) as excinfo:
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert "400" in str(excinfo.value)
    assert fake_llm.state()["request_count"] == 1  # no retry


@pytest.mark.unit
async def test_schema_error_recovered_on_retry(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": "invalid_json"}, {"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)
    try:
        model, record = await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert record.attempts == 2
    assert record.output_schema_valid is True
    assert isinstance(model, ModelResponse)


@pytest.mark.unit
async def test_schema_error_exhausted_raises(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": "invalid_json"}] * 3)
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMSchemaError):
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()


@pytest.mark.unit
def test_fingerprint_is_deterministic_and_versioned() -> None:
    profile = ModelProfile(model_alias="thinker-local", artifact_sha256="a" * 64)
    fp1 = build_model_fingerprint(profile, prompt_version="explorer-v1", tool_schema_hash="t")
    fp2 = build_model_fingerprint(profile, prompt_version="explorer-v1", tool_schema_hash="t")
    fp3 = build_model_fingerprint(profile, prompt_version="explorer-v2", tool_schema_hash="t")
    assert fp1 == fp2
    assert fp1["fingerprint_sha256"] == fp2["fingerprint_sha256"]
    assert fp1["fingerprint_sha256"] != fp3["fingerprint_sha256"]
    assert fp1["model"]["model_alias"] == "thinker-local"
