"""Validated configuration for a reproducible local model profile."""

from __future__ import annotations

import ipaddress
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from packages.domain._base import Sha256Hex

NonEmptyConfigText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class BackendMetadata(_ConfigModel):
    name: NonEmptyConfigText
    version: NonEmptyConfigText
    build_fingerprint: NonEmptyConfigText


class StructuredOutputSettings(_ConfigModel):
    mode: Literal["json_schema"] = "json_schema"
    schema_version: Literal["action-envelope/v1"] = "action-envelope/v1"
    grammar_sha256: Sha256Hex


class SamplingSettings(_ConfigModel):
    seed: int = 42
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=0.95, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1)


class RuntimeSettings(_ConfigModel):
    gpu_layers: int = -1
    tensor_split: tuple[float, ...] | None = None

    @field_validator("tensor_split")
    @classmethod
    def validate_tensor_split(cls, value: tuple[float, ...] | None) -> tuple[float, ...] | None:
        if value is not None and (not value or any(part <= 0 for part in value)):
            raise ValueError("tensor_split must contain positive values")
        return value


class ModelProfile(_ConfigModel):
    provider: Literal["openai-compatible"] = "openai-compatible"
    base_url: AnyHttpUrl
    allow_remote: bool = False
    model_alias: NonEmptyConfigText
    model_artifact_sha256: Sha256Hex
    quantization: NonEmptyConfigText
    tokenizer_sha256: Sha256Hex
    chat_template_sha256: Sha256Hex
    backend: BackendMetadata
    context_window: PositiveInt
    max_output_tokens: PositiveInt
    safety_margin_tokens: int = Field(ge=0)
    structured_output: StructuredOutputSettings
    sampling: SamplingSettings = Field(default_factory=SamplingSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)

    @model_validator(mode="after")
    def validate_local_endpoint_and_budget(self) -> ModelProfile:
        if not self.base_url.path.rstrip("/").endswith("/v1"):
            raise ValueError("base_url must end with /v1")
        if any(
            value is not None
            for value in (
                self.base_url.username,
                self.base_url.password,
                self.base_url.query,
                self.base_url.fragment,
            )
        ):
            raise ValueError("base_url must not contain credentials, query, or fragment")

        host = self.base_url.host
        is_loopback = host == "localhost"
        if host is not None and not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(host.strip("[]")).is_loopback
            except ValueError:
                is_loopback = False
        if not self.allow_remote and not is_loopback:
            raise ValueError("remote LLM endpoint requires allow_remote=true")
        if self.allow_remote and not is_loopback and self.base_url.scheme != "https":
            raise ValueError("remote LLM endpoint requires HTTPS")

        if self.max_output_tokens + self.safety_margin_tokens >= self.context_window:
            raise ValueError("output and safety margins must leave a positive input budget")
        return self

    @property
    def normalized_base_url(self) -> str:
        return f"{str(self.base_url).rstrip('/')}/"


class TransportConfig(_ConfigModel):
    api_key: SecretStr | None = None
    connect_timeout_seconds: PositiveFloat = 5.0
    read_timeout_seconds: PositiveFloat = 120.0
    write_timeout_seconds: PositiveFloat = 10.0
    pool_timeout_seconds: PositiveFloat = 5.0
    verify_tls: bool = True
