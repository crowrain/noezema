"""Model-independent gateway that validates strict structured output."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from packages.domain import DecisionEnvelope
from packages.llm_gateway.config import ModelProfile
from packages.llm_gateway.errors import (
    IncompleteModelOutputError,
    InvalidModelOutputError,
    RetryExhaustedError,
    TransientBackendError,
)
from packages.llm_gateway.fingerprint import InvocationFingerprint, tool_schema_sha256
from packages.llm_gateway.models import (
    GatewayRequest,
    ModelRunResult,
    RetryPolicy,
    StructuredRunResult,
)
from packages.llm_gateway.transport import OpenAICompatibleTransport

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


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
        run = self.generate_structured(
            request,
            response_model=DecisionEnvelope,
            schema_name="noezema_action_envelope_v1",
        )
        return ModelRunResult(
            decision=run.output,
            finish_reason=run.finish_reason,
            backend_model=run.backend_model,
            usage=run.usage,
            latency_ms=run.latency_ms,
            attempts=run.attempts,
            model_fingerprint_sha256=run.model_fingerprint_sha256,
            invocation_fingerprint_sha256=run.invocation_fingerprint_sha256,
            tool_schema_sha256=run.output_schema_sha256,
        )

    def generate_structured(
        self,
        request: GatewayRequest,
        *,
        response_model: type[StructuredOutputT],
        schema_name: str,
    ) -> StructuredRunResult[StructuredOutputT]:
        """Run the backend once and validate against the caller-selected schema."""

        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", schema_name) is None:
            raise ValueError("schema_name must be a portable JSON Schema identifier")
        invocation = InvocationFingerprint.create(
            profile=self._profile,
            request=request,
            response_model=response_model,
        )
        payload = self._request_payload(
            request,
            response_model=response_model,
            schema_name=schema_name,
        )
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
            output = response_model.model_validate_json(completion.content)
        except ValidationError as error:
            raise InvalidModelOutputError(
                f"model output failed {response_model.__name__} validation"
            ) from error

        return StructuredRunResult[StructuredOutputT](
            output=output,
            finish_reason=completion.finish_reason,
            backend_model=completion.backend_model,
            usage=completion.usage,
            latency_ms=latency_ms,
            attempts=attempts,
            model_fingerprint_sha256=invocation.model_fingerprint_sha256,
            invocation_fingerprint_sha256=invocation.sha256,
            output_schema_sha256=invocation.tool_schema_sha256,
        )

    def _request_payload(
        self,
        request: GatewayRequest,
        *,
        response_model: type[BaseModel],
        schema_name: str,
    ) -> dict[str, Any]:
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
                    "name": schema_name,
                    "strict": True,
                    "schema": response_model.model_json_schema(),
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
