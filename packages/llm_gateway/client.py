"""LLM Gateway client (T1.7, T1.8, T1.9).

Single async client over an OpenAI-compatible endpoint. Responsibilities:
  - structured output (json_schema) with response-schema validation;
  - retries only for transient errors (connection, timeout, 5xx, 429);
  - token/latency accounting;
  - call fingerprint propagation.

The model is NOT trusted: it may return malformed JSON or a wrong schema.
The gateway validates and surfaces a typed error; the orchestrator decides
the session outcome (host-generated failure report, §6.5).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from packages.domain.models.base import JsonDict
from packages.llm_gateway.config import LLMGatewayConfig


class LLMError(RuntimeError):
    """Base for gateway errors."""


class LLMTransientError(LLMError):
    """Exhausted retries on a transient failure (network/5xx/timeout)."""


class LLMSchemaError(LLMError):
    """The model produced output that fails response-schema validation."""


@dataclass(slots=True)
class CallRecord:
    """Accounting metadata for one logical chat() call."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    attempts: int = 0
    output_schema_valid: bool = False
    fingerprint: JsonDict = field(default_factory=dict)


class LLMMiddleware:
    def __init__(self, config: LLMGatewayConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client if client is not None else httpx.AsyncClient(
            base_url=config.base_url, timeout=config.timeout_seconds
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    @staticmethod
    def _is_transient(status_code: int, exc: Exception | None) -> bool:
        if exc is not None:
            # connection / timeout errors are transient
            return True
        return status_code in {408, 429, 500, 502, 503, 504}

    async def _request_once(self, body: dict[str, Any]) -> tuple[httpx.Response, float]:
        started = time.perf_counter()
        response = await self._client.post("/chat/completions", headers=self._headers(), json=body)
        return response, (time.perf_counter() - started) * 1000.0

    async def chat(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        fingerprint: JsonDict | None = None,
    ) -> tuple[BaseModel, CallRecord]:
        """One structured chat call. Returns (validated model, record).

        Raises LLMSchemaError (bad schema, not retried further once the
        model has had its retries) or LLMTransientError (network/5xx after
        exhausting retries).
        """
        schema = response_schema.model_json_schema()
        body: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": response_schema.__name__, "strict": True, "schema": schema},
            },
        }

        record = CallRecord(model=self.config.model, fingerprint=fingerprint or {})
        last_error: Exception | None = None
        delay = self.config.retry_base_delay

        for attempt in range(1, self.config.max_retries + 1):
            record.attempts = attempt
            try:
                response, latency_ms = await self._request_once(body)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    await asyncio.sleep(delay)
                    delay *= self.config.retry_multiplier
                    continue
                raise LLMTransientError(f"network failure after {attempt} attempts: {exc}") from exc

            record.latency_ms += latency_ms

            if response.status_code != 200:
                if self._is_transient(response.status_code, None):
                    last_error = LLMError(f"HTTP {response.status_code}: {response.text[:200]}")
                    if attempt < self.config.max_retries:
                        await asyncio.sleep(delay)
                        delay *= self.config.retry_multiplier
                        continue
                    raise LLMTransientError(f"transient HTTP {response.status_code} after {attempt} attempts") from (
                        last_error
                    )
                raise LLMError(f"HTTP {response.status_code}: {response.text[:500]}")

            data = response.json()
            self._fill_usage(record, data)
            content = data["choices"][0]["message"]["content"]
            try:
                parsed = json.loads(content)
                validated = response_schema.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
                record.output_schema_valid = False
                # A schema failure is a model-side hiccup; retry like transient,
                # but surface a schema error if it persists.
                last_error = exc
                if attempt < self.config.max_retries:
                    await asyncio.sleep(delay)
                    delay *= self.config.retry_multiplier
                    continue
                raise LLMSchemaError(f"model output failed schema after {attempt} attempts: {exc}") from exc

            record.output_schema_valid = True
            return validated, record

        raise LLMTransientError(f"retries exhausted: {last_error}")  # pragma: no cover

    @staticmethod
    def _fill_usage(record: CallRecord, data: dict[str, Any]) -> None:
        usage = data.get("usage") or {}
        record.input_tokens = int(usage.get("prompt_tokens", record.input_tokens))
        record.output_tokens = int(usage.get("completion_tokens", record.output_tokens))
        record.finish_reason = data.get("choices", [{}])[0].get("finish_reason")
