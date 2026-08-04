"""Model-independent gateway that validates one structured decision."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from packages.domain import DecisionEnvelope
from packages.llm_gateway.config import ModelProfile
from packages.llm_gateway.errors import (
    IncompleteModelOutputError,
    InvalidModelOutputError,
    RetryExhaustedError,
    TransientBackendError,
)
from packages.llm_gateway.fingerprint import InvocationFingerprint, tool_schema_sha256
from packages.llm_gateway.models import GatewayRequest, ModelRunResult, RetryPolicy
from packages.llm_gateway.transport import OpenAICompatibleTransport


class LLMGateway:
    """Execute one local model run and return a validated decision."""

    def __init__(
        self,
        *,
        profile: ModelProfile,
        transport: OpenAICompatibleTransport,
        retry_policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._profile = profile
        self._transport = transport
        self._retry = retry_policy or RetryPolicy()
        self._sleep = sleeper
        self._monotonic = monotonic

    def generate_decision(self, request: GatewayRequest) -> ModelRunResult:
        invocation = InvocationFingerprint.create(profile=self._profile, request=request)
        payload = self._request_payload(request)
        started_at = self._monotonic()
        attempts = 0
        delay = self._retry.initial_backoff_seconds

        while True:
            attempts += 1
            try:
                completion = self._transport.complete(payload)
                break
            except TransientBackendError as error:
                if attempts >= self._retry.max_attempts:
                    raise RetryExhaustedError(attempts) from error
                self._sleep(min(delay, self._retry.max_backoff_seconds))
                delay *= self._retry.multiplier

        latency_ms = max(0, round((self._monotonic() - started_at) * 1000))
        if completion.finish_reason != "stop":
            raise IncompleteModelOutputError(completion.finish_reason)

        try:
            decision = DecisionEnvelope.model_validate_json(completion.content)
        except ValidationError as error:
            raise InvalidModelOutputError(
                "model output failed DecisionEnvelope validation"
            ) from error

        return ModelRunResult(
            decision=decision,
            finish_reason=completion.finish_reason,
            backend_model=completion.backend_model,
            usage=completion.usage,
            latency_ms=latency_ms,
            attempts=attempts,
            model_fingerprint_sha256=invocation.model_fingerprint_sha256,
            invocation_fingerprint_sha256=invocation.sha256,
            tool_schema_sha256=invocation.tool_schema_sha256,
        )

    def _request_payload(self, request: GatewayRequest) -> dict[str, Any]:
        sampling = self._profile.sampling
        payload: dict[str, Any] = {
            "model": self._profile.model_alias,
            "messages": [message.model_dump(mode="json") for message in request.messages],
            "max_tokens": self._profile.max_output_tokens,
            "temperature": sampling.temperature,
            "top_p": sampling.top_p,
            "seed": sampling.seed,
            "n": 1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "noezema_action_envelope_v1",
                    "strict": True,
                    "schema": DecisionEnvelope.model_json_schema(),
                },
            },
        }
        if sampling.top_k is not None:
            payload["top_k"] = sampling.top_k
        return payload

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> LLMGateway:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def schema_sha256(self) -> str:
        return tool_schema_sha256()
