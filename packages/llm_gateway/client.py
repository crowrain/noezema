"""LLM Gateway client (T1.7, T1.8, T1.9).

Single async client over an OpenAI-compatible endpoint. Responsibilities:
  - structured output (json_schema) with response-schema validation;
  - retries only for transient errors (connection, timeout, 5xx, 429);
  - token/latency accounting;
  - call fingerprint propagation;
  - T7.80 (ADR-0030): per-call reasoning control by engine profile, and the
    output limit as its own outcome class instead of a schema hiccup.

The model is NOT trusted: it may return malformed JSON or a wrong schema.
The gateway validates and surfaces a typed error; the orchestrator decides
the session outcome (host-generated failure report, §6.5).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from packages.domain.models.base import JsonDict
from packages.domain.sanitization import mask_nul_deep
from packages.llm_gateway.config import LLMGatewayConfig
from packages.llm_gateway.reasoning_compat import (
    MODE_OFF,
    REASONING_MODES,
    REASONING_PHASES,
    ReasoningProfile,
    completion_was_truncated,
    reasoning_body_additions,
    require_reasoning_profile,
)
from packages.llm_gateway.schema_compat import SCHEMA_PROFILES, strip_schema_keywords

TModel = TypeVar("TModel", bound=BaseModel)


class LLMError(RuntimeError):
    """Base for gateway errors."""


class LLMTransientError(LLMError):
    """Exhausted retries on a transient failure (network/5xx/timeout)."""


class LLMAnswerUnusableError(LLMError):
    """Base for "the engine answered, the host could not use the answer".

    Carries every answered attempt so the orchestrator can write them into
    model_runs (finish_reason / output_tokens / output_schema_valid=false):
    a failed attempt must be visible in the journal, not only in the engine's
    own log (T7.80).
    """

    def __init__(self, message: str, *, attempts: list[AttemptRecord] | None = None) -> None:
        super().__init__(message)
        self.attempts: list[AttemptRecord] = list(attempts or [])


class LLMSchemaError(LLMAnswerUnusableError):
    """The model produced output that fails response-schema validation."""


class LLMTruncatedResponseError(LLMAnswerUnusableError):
    """The answer was STOPPED BY THE OUTPUT LIMIT (T7.80, ADR-0030).

    A distinct class on purpose: it is not a schema hiccup. Repeating the same
    request cannot help — halogen reserves a fixed answer room (~1000 tokens:
    reasoning is closed "by answer_room" at max_tokens - 1000, so the cut moves
    with max_tokens), and a server running at temperature 0 returns the same
    bytes anyway. The gateway makes AT MOST one follow-up request with reasoning
    disabled (only when the engine profile can disable it and was not already
    asking for that) and then reports the truncation honestly. A document that
    happens to parse but was cut by the limit is refused too: a value may have
    been severed mid-string.
    """


class LLMRequestRejectedError(LLMError):
    """The engine REFUSED THE REQUEST ITSELF (HTTP 4xx, T7.23).

    The endpoint is reachable, but it will refuse the same request
    again (schema keyword/parameter the engine does not support, unknown
    model, bad auth): NOT transient, never retried, and — unlike
    LLMTransientError — the model is up. The host records a distinct
    audit marker so an engine-side refusal is distinguishable from
    "model unavailable" in the journal (ADR-0012).
    """


@dataclass(slots=True)
class AttemptRecord:
    """One HTTP attempt that produced an answer, and what the host made of it.

    Only answered attempts are recorded: a 503 never became a model answer and
    has nothing to say about finish_reason or schema validity.
    """

    index: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    finish_reason: str | None
    output_schema_valid: bool
    reasoning_mode: str | None = None
    truncated: bool = False


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
    #: engine reasoning profile in effect for this call ("none" = request untouched)
    reasoning_profile: str = "none"
    #: reasoning mode of the LAST attempt: None (no policy), "on", "off"
    reasoning_mode: str | None = None
    #: at least one attempt was stopped by the output limit
    truncated: bool = False
    #: every answered attempt, in order (the failed ones included)
    attempts_detail: list[AttemptRecord] = field(default_factory=list)


# internal classification of one HTTP attempt
_OK = "ok"
_NETWORK = "network"
_TRANSIENT_HTTP = "transient_http"
_REJECTED_HTTP = "rejected_http"
_HTTP_ERROR = "http_error"
_SCHEMA_INVALID = "schema_invalid"
_TRUNCATED = "truncated"


@dataclass(slots=True)
class _AttemptOutcome:
    """What one HTTP attempt produced; chat() decides the retry policy."""

    status: str
    model: Any = None
    error: Exception | None = None
    attempt: AttemptRecord | None = None
    status_code: int | None = None


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

    def _build_body(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        schema: JsonDict,
        profile: ReasoningProfile,
        mode: str | None,
    ) -> dict[str, Any]:
        """The request body for one (engine profile, phase reasoning mode).

        Profile "none", mode None, mode "on" and a profile that has no switch
        all produce byte-for-byte the body the gateway sent before T7.80.
        """
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
        body.update(reasoning_body_additions(profile.name, mode))
        return body

    async def chat(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[TModel],
        fingerprint: JsonDict | None = None,
        phase: str | None = None,
        reasoning_mode: str | None = None,
    ) -> tuple[TModel, CallRecord]:
        """One structured chat call. Returns (validated model, record).

        Raises LLMSchemaError (bad schema, not retried further once the
        model has had its retries), LLMTransientError (network/5xx after
        exhausting retries), LLMRequestRejectedError (the engine refused the
        request itself — HTTP 4xx, e.g. a schema keyword it does not support;
        not retried, distinct from "model unavailable") or
        LLMTruncatedResponseError (the answer was cut by the output limit).

        T7.23 (ADR-0012): when config.schema_profile strips keywords, only
        the schema SENT TO THE ENGINE is reduced (the engine refuses the
        full strict schema). The model's ANSWER is still parsed and
        validated against the FULL ``response_schema`` (uuid, date-time,
        every constraint) — host-side validation is never weakened.

        T7.80 (ADR-0030): ``phase`` names the orchestrator call (exploration /
        planning / extraction / verification / consolidation — NOT a session
        state) and ``reasoning_mode`` is what the effective snapshot's
        ``model.reasoning_by_phase`` says about it. The engine profile
        (NOEZEMA_LLM_REASONING_PROFILE) decides HOW "off" is asked for; with
        profile "none" no reasoning parameter is sent at all, so existing
        deployments and frozen comparisons stay byte-identical. An answer cut
        by the output limit gets exactly ONE follow-up request with reasoning
        disabled (when this engine can disable it and was not already asking for
        that) — never a repeat of the identical request.

        ``phase`` is accepted for the record and for call-site explicitness;
        the mode itself is resolved by the orchestrator from the effective
        snapshot (§3 "effective config"), not from the environment.
        """
        schema = response_schema.model_json_schema()
        stripped = SCHEMA_PROFILES[self.config.schema_profile]
        if stripped:
            schema = strip_schema_keywords(schema, stripped)

        profile = require_reasoning_profile(self.config.reasoning_profile)
        if phase is not None and phase not in REASONING_PHASES:
            # an unknown call phase is a programming mistake at a call site:
            # fail closed rather than silently applying no policy
            known = ", ".join(sorted(REASONING_PHASES))
            raise ValueError(f"unknown reasoning phase {phase!r}; known phases: {known}")
        mode = reasoning_mode if reasoning_mode in REASONING_MODES else None
        record = CallRecord(
            model=self.config.model,
            fingerprint=fingerprint or {},
            reasoning_profile=profile.name,
            reasoning_mode=mode,
        )
        body = self._build_body(
            system=system,
            user=user,
            response_schema=response_schema,
            schema=schema,
            profile=profile,
            mode=mode,
        )

        delay = self.config.retry_base_delay
        truncated: _AttemptOutcome | None = None

        while True:
            attempt_index = record.attempts + 1
            outcome = await self._one_attempt(
                body=body, response_schema=response_schema, mode=mode, record=record
            )
            if outcome.attempt is not None:
                record.attempts_detail.append(outcome.attempt)

            if outcome.status == _OK:
                record.output_schema_valid = True
                record.reasoning_mode = mode
                record.truncated = any(a.truncated for a in record.attempts_detail)
                return outcome.model, record

            if outcome.status == _TRUNCATED:
                # NOT a schema hiccup: the identical request would be cut again.
                truncated = outcome
                break

            if outcome.status in (_NETWORK, _TRANSIENT_HTTP):
                if attempt_index < self.config.max_retries:
                    await asyncio.sleep(delay)
                    delay *= self.config.retry_multiplier
                    continue
                if outcome.status == _NETWORK:
                    raise LLMTransientError(
                        f"network failure after {attempt_index} attempts: {outcome.error}"
                    ) from outcome.error
                raise LLMTransientError(
                    f"transient HTTP {outcome.status_code} after {attempt_index} attempts"
                ) from outcome.error

            if outcome.status == _REJECTED_HTTP:
                # T7.23: the engine is up but refuses THIS request (schema/params/
                # model/auth) — distinct from "model unavailable" in the journal.
                raise LLMRequestRejectedError(str(outcome.error)) from outcome.error

            if outcome.status == _HTTP_ERROR:
                raise LLMError(str(outcome.error)) from outcome.error

            # _SCHEMA_INVALID: a model-side hiccup; retry like transient,
            # but surface a schema error if it persists.
            if attempt_index < self.config.max_retries:
                await asyncio.sleep(delay)
                delay *= self.config.retry_multiplier
                continue
            raise LLMSchemaError(
                f"model output failed schema after {attempt_index} attempts: {outcome.error}",
                attempts=[a for a in record.attempts_detail if not a.output_schema_valid],
            ) from outcome.error

        # ── the answer was cut by the output limit (T7.80, ADR-0030) ─────
        assert truncated is not None  # only path that breaks the loop
        detail = truncated.attempt
        can_retry_differently = profile.can_disable_reasoning and mode != MODE_OFF
        if can_retry_differently:
            # ONE follow-up, deliberately without backoff: it is a DIFFERENT
            # request (reasoning disabled), not a rate-limited repeat.
            mode = MODE_OFF
            body = self._build_body(
                system=system,
                user=user,
                response_schema=response_schema,
                schema=schema,
                profile=profile,
                mode=mode,
            )
            retry = await self._one_attempt(
                body=body, response_schema=response_schema, mode=mode, record=record
            )
            if retry.attempt is not None:
                record.attempts_detail.append(retry.attempt)
            if retry.status == _OK:
                record.output_schema_valid = True
                record.reasoning_mode = mode
                # the recovered answer is still evidence that the first one was cut
                record.truncated = True
                return retry.model, record
            if retry.status in (_NETWORK, _TRANSIENT_HTTP):
                raise LLMTransientError(
                    f"reasoning-off retry after a truncated answer failed transiently: {retry.error}"
                ) from retry.error
            if retry.status == _REJECTED_HTTP:
                raise LLMRequestRejectedError(str(retry.error)) from retry.error

        raise LLMTruncatedResponseError(
            "model answer truncated by the output limit: finish_reason="
            f"{detail.finish_reason if detail else None} completion_tokens="
            f"{detail.output_tokens if detail else 0} of max_output_tokens="
            f"{self.config.max_output_tokens}; reasoning-off retry "
            + (
                "applied and was truncated too"
                if can_retry_differently
                else f"not available for engine profile {profile.name}"
            ),
            attempts=[a for a in record.attempts_detail if not a.output_schema_valid],
        )

    async def _one_attempt(
        self,
        *,
        body: dict[str, Any],
        response_schema: type[TModel],
        mode: str | None,
        record: CallRecord,
    ) -> _AttemptOutcome:
        """One HTTP request, classified; the retry policy is chat()'s decision."""
        attempt_index = record.attempts + 1
        try:
            response, latency_ms = await self._request_once(body)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            return _AttemptOutcome(status=_NETWORK, error=exc)

        record.attempts = attempt_index
        record.latency_ms += latency_ms

        if response.status_code != 200:
            if self._is_transient(response.status_code, None):
                return _AttemptOutcome(
                    status=_TRANSIENT_HTTP,
                    error=LLMError(f"HTTP {response.status_code}: {response.text[:200]}"),
                    status_code=response.status_code,
                )
            if 400 <= response.status_code < 500:
                return _AttemptOutcome(
                    status=_REJECTED_HTTP,
                    error=LLMError(
                        f"engine refused the request, HTTP {response.status_code}: {response.text[:500]}"
                    ),
                    status_code=response.status_code,
                )
            return _AttemptOutcome(
                status=_HTTP_ERROR,
                error=LLMError(f"HTTP {response.status_code}: {response.text[:500]}"),
                status_code=response.status_code,
            )

        data = response.json()
        self._fill_usage(record, data)
        usage = data.get("usage") or {}
        output_tokens = int(usage.get("completion_tokens", record.output_tokens))
        truncated = completion_was_truncated(
            finish_reason=record.finish_reason,
            output_tokens=output_tokens,
            max_output_tokens=self.config.max_output_tokens,
        )

        model: Any = None
        schema_valid = False
        error: Exception | None = None
        try:
            parsed = json.loads(data["choices"][0]["message"]["content"])
            # T7.47a (ADR-0020): the model-text ENTRY boundary. The
            # response is the only model-text source reaching the
            # host; mask NUL here — before schema validation, caps
            # and any hash over the parsed value — so every
            # downstream consumer (staging, plan, verification,
            # extraction, complete reason, audit) stores/hashes the
            # SAME masked text. mask_nul_deep is the identity on
            # NUL-free input (byte-identical, dedup-safe).
            parsed = mask_nul_deep(parsed)
            model = response_schema.model_validate(parsed)
            schema_valid = True
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            error = exc
            record.output_schema_valid = False

        attempt = AttemptRecord(
            index=attempt_index,
            input_tokens=record.input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            finish_reason=record.finish_reason,
            output_schema_valid=schema_valid,
            reasoning_mode=mode,
            truncated=truncated,
        )

        if truncated:
            # checked AFTER parsing so the attempt still records what the engine
            # really returned; a cut answer is refused even when it parsed.
            record.truncated = True
            return _AttemptOutcome(status=_TRUNCATED, model=model, error=error, attempt=attempt)
        if not schema_valid:
            return _AttemptOutcome(status=_SCHEMA_INVALID, error=error, attempt=attempt)
        return _AttemptOutcome(status=_OK, model=model, attempt=attempt)

    @staticmethod
    def _fill_usage(record: CallRecord, data: dict[str, Any]) -> None:
        usage = data.get("usage") or {}
        record.input_tokens = int(usage.get("prompt_tokens", record.input_tokens))
        record.output_tokens = int(usage.get("completion_tokens", record.output_tokens))
        record.finish_reason = data.get("choices", [{}])[0].get("finish_reason")
