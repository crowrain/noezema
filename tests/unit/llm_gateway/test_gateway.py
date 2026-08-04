"""Tests for request shape, retry classification, and decision parsing."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from packages.domain import ToolDecision, ToolName
from packages.llm_gateway import (
    BackendProtocolError,
    ChatMessage,
    ChatRole,
    GatewayRequest,
    IncompleteModelOutputError,
    InvalidModelOutputError,
    LLMGateway,
    ModelPhase,
    ModelProfile,
    ModelRole,
    OpenAICompatibleTransport,
    PermanentBackendError,
    RetryExhaustedError,
    RetryPolicy,
)


def _request() -> GatewayRequest:
    return GatewayRequest(
        messages=(
            ChatMessage(role=ChatRole.SYSTEM, content="Return one structured decision."),
            ChatMessage(role=ChatRole.USER, content="Search memory for NOEZEMA."),
        ),
        role=ModelRole.EXPLORER,
        phase=ModelPhase.EXPLORATION,
        prompt_version="explorer/v1",
        context_manifest_sha256="5" * 64,
        policy_version="policy/v1",
    )


def _provider_response(*, content: str, finish_reason: str = "stop") -> dict[str, object]:
    return {
        "model": "thinker-local",
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def _valid_content() -> str:
    return json.dumps(
        {
            "public_rationale": "Проверить долговременную память",
            "expected_information": "Найти связанные утверждения",
            "decision": {
                "kind": "tool",
                "tool": "memory.search",
                "arguments": {"query": "NOEZEMA"},
            },
        }
    )


def _gateway(
    model_profile: ModelProfile,
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: object,
) -> LLMGateway:
    client = httpx.Client(
        base_url=model_profile.normalized_base_url,
        transport=httpx.MockTransport(handler),
    )
    transport = OpenAICompatibleTransport(profile=model_profile, client=client)
    return LLMGateway(profile=model_profile, transport=transport, **kwargs)


def test_gateway_sends_json_schema_and_parses_one_decision(model_profile: ModelProfile) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_provider_response(content=_valid_content()))

    clock = iter((10.0, 10.125))
    gateway = _gateway(model_profile, handler, monotonic=lambda: next(clock))
    result = gateway.generate_decision(_request())

    assert isinstance(result.decision.decision, ToolDecision)
    assert result.decision.decision.tool is ToolName.MEMORY_SEARCH
    assert result.usage.total_tokens == 18
    assert result.latency_ms == 125
    assert captured["model"] == "thinker-local"
    response_format = captured["response_format"]
    assert isinstance(response_format, dict)
    schema = response_format["json_schema"]
    assert isinstance(schema, dict)
    assert schema["strict"] is True
    assert "idempotency_key" not in json.dumps(schema)


def test_gateway_retries_only_transient_backend_failures(model_profile: ModelProfile) -> None:
    calls = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_provider_response(content=_valid_content()))

    gateway = _gateway(
        model_profile,
        handler,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.1),
        sleeper=delays.append,
    )
    result = gateway.generate_decision(_request())

    assert result.attempts == 2
    assert calls == 2
    assert delays == [0.1]


def test_gateway_retries_transport_timeout(model_profile: ModelProfile) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=_provider_response(content=_valid_content()))

    gateway = _gateway(model_profile, handler, sleeper=lambda _: None)

    assert gateway.generate_decision(_request()).attempts == 2
    assert calls == 2


def test_gateway_does_not_retry_permanent_http_error(model_profile: ModelProfile) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400)

    gateway = _gateway(model_profile, handler, sleeper=lambda _: pytest.fail("unexpected retry"))

    with pytest.raises(PermanentBackendError):
        gateway.generate_decision(_request())
    assert calls == 1


def test_gateway_does_not_retry_malformed_provider_response(model_profile: ModelProfile) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"model": "thinker-local"})

    gateway = _gateway(model_profile, handler, sleeper=lambda _: pytest.fail("unexpected retry"))

    with pytest.raises(BackendProtocolError):
        gateway.generate_decision(_request())
    assert calls == 1


def test_gateway_does_not_retry_invalid_model_output(model_profile: ModelProfile) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_provider_response(content='{"decision": {}}'))

    gateway = _gateway(model_profile, handler, sleeper=lambda _: pytest.fail("unexpected retry"))

    with pytest.raises(InvalidModelOutputError):
        gateway.generate_decision(_request())
    assert calls == 1


def test_gateway_rejects_truncated_generation(model_profile: ModelProfile) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_provider_response(content=_valid_content(), finish_reason="length"),
        )

    gateway = _gateway(model_profile, handler)

    with pytest.raises(IncompleteModelOutputError):
        gateway.generate_decision(_request())


def test_retry_exhaustion_is_classified(model_profile: ModelProfile) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    gateway = _gateway(
        model_profile,
        handler,
        retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0),
        sleeper=lambda _: None,
    )

    with pytest.raises(RetryExhaustedError) as error:
        gateway.generate_decision(_request())
    assert error.value.attempts == 2
