"""LLM Gateway configuration and model profile (T1.7, §12)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.domain.models.base import JsonDict


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Reproducibility profile of the local model (§12).

    Hashes are optional in MVP (filled when the artifact is pinned);
    the fingerprint is still computed deterministically from what is known.
    """

    model_alias: str
    provider: str = "openai-compatible"
    context_window: int = 32768
    max_output_tokens: int = 4096
    artifact_sha256: str | None = None
    tokenizer_sha256: str | None = None
    backend_name: str | None = None
    backend_version: str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "provider": self.provider,
            "model_alias": self.model_alias,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "artifact_sha256": self.artifact_sha256,
            "tokenizer_sha256": self.tokenizer_sha256,
            "backend": {"name": self.backend_name, "version": self.backend_version},
        }


class LLMGatewayConfig(BaseSettings):
    """Gateway runtime settings (NOEZEMA_LLM_ env prefix)."""

    model_config = SettingsConfigDict(env_prefix="NOEZEMA_LLM_", extra="ignore")

    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "thinker-local"
    api_key: str = ""
    timeout_seconds: float = 120.0
    max_output_tokens: int = 4096
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_multiplier: float = 2.0
