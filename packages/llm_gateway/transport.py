"""OpenAI-compatible HTTP transport with classified errors."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from packages.llm_gateway.config import ModelProfile, TransportConfig
from packages.llm_gateway.errors import (
    BackendProtocolError,
    PermanentBackendError,
    TransientBackendError,
)
from packages.llm_gateway.models import BackendCompletion, TokenUsage


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _ProviderMessage(_ProviderModel):
    content: str


class _ProviderChoice(_ProviderModel):
    message: _ProviderMessage
    finish_reason: str


class _ProviderUsage(_ProviderModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class _ProviderResponse(_ProviderModel):
    model: str
    choices: list[_ProviderChoice]
    usage: _ProviderUsage


class OpenAICompatibleTransport:
    """POST chat completions to one configured OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        profile: ModelProfile,
        config: TransportConfig | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._profile = profile
        self._config = config or TransportConfig()
        self._owns_client = client is None
        if client is None:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._config.api_key is not None:
                headers["Authorization"] = f"Bearer {self._config.api_key.get_secret_value()}"
            timeout = httpx.Timeout(
                connect=self._config.connect_timeout_seconds,
                read=self._config.read_timeout_seconds,
                write=self._config.write_timeout_seconds,
                pool=self._config.pool_timeout_seconds,
            )
            client = httpx.Client(
                base_url=profile.normalized_base_url,
                headers=headers,
                timeout=timeout,
                verify=self._config.verify_tls,
            )
        self._client = client

    def complete(self, payload: dict[str, Any]) -> BackendCompletion:
        try:
            response = self._client.post("chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise TransientBackendError("local LLM transport unavailable") from error

        if response.status_code in {408, 425, 429} or response.status_code >= 500:
            raise TransientBackendError(f"local LLM transient HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PermanentBackendError(response.status_code)

        try:
            provider_response = _ProviderResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise BackendProtocolError("malformed OpenAI-compatible response") from error

        if len(provider_response.choices) != 1:
            raise BackendProtocolError("backend must return exactly one choice")
5ë_-¢G§²ÚîÆ­yÒ&÷W'G¢FVb66†VÖ÷6†#Sb‡6VÆb’Óâ7G# ¢&WGW&âFööÅ÷66†VÖ÷6†#Sb‚ 