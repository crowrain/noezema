"""Strict environment materialization for the trusted runtime process."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from apps.orchestrator import SessionRunnerLimits, SupervisorPolicy
from packages.llm_gateway import (
    BackendMetadata,
    ModelProfile,
    RuntimeSettings,
    SamplingSettings,
    StructuredOutputSettings,
    TransportConfig,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    database_url: str
    workspace_root: Path
    artifact_store_root: Path
    prompt_root: Path
    identity_prompt_sha256: str
    explorer_prompt_sha256: str
    curator_prompt_sha256: str
    model_profile: ModelProfile
    transport_config: TransportConfig
    supervisor_policy: SupervisorPolicy
    runner_limits: SessionRunnerLimits
    min_free_disk_bytes: int

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> RuntimeConfig:
        values = os.environ if environment is None else environment
        prompt_root = Path(values.get("NOEZEMA_PROMPT_ROOT", _PROJECT_ROOT / "prompts"))
        profile = ModelProfile(
            base_url=_required(values, "NOEZEMA_LLM_BASE_URL"),
            allow_remote=_boolean(values, "NOEZEMA_LLM_ALLOW_REMOTE", False),
            model_alias=_required(values, "NOEZEMA_LLM_MODEL"),
            model_artifact_sha256=_required(values, "NOEZEMA_LLM_MODEL_SHA256"),
            quantization=values.get("NOEZEMA_LLM_QUANTIZATION", "unknown"),
            tokenizer_sha256=_required(values, "NOEZEMA_LLM_TOKENIZER_SHA256"),
            chat_template_sha256=_required(values, "NOEZEMA_LLM_TEMPLATE_SHA256"),
            backend=BackendMetadata(
                name=_required(values, "NOEZEMA_LLM_BACKEND"),
                version=_required(values, "NOEZEMA_LLM_BACKEND_VERSION"),
                build_fingerprint=_required(values, "NOEZEMA_LLM_BUILD_FINGERPRINT"),
            ),
            context_window=_integer(values, "NOEZEMA_LLM_CONTEXT_WINDOW", 32_768),
            max_output_tokens=_integer(values, "NOEZEMA_LLM_MAX_OUTPUT_TOKENS", 2_048),
            safety_margin_tokens=_integer(values, "NOEZEMA_LLM_SAFETY_MARGIN_TOKENS", 2_048),
            structured_output=StructuredOutputSettings(
                grammar_sha256=_required(values, "NOEZEMA_LLM_GRAMMAR_SHA256")
            ),
            sampling=SamplingSettings(
                seed=_integer(values, "NOEZEMA_LLM_SEED", 42),
                temperature=_float(values, "NOEZEMA_LLM_TEMPERATURE", 0.2),
                top_p=_float(values, "NOEZEMA_LLM_TOP_P", 0.95),
            ),
            runtime=RuntimeSettings(
                gpu_layers=_integer(values, "NOEZEMA_LLM_GPU_LAYERS", -1),
            ),
        )
        api_key = values.get("NOEZEMA_LLM_API_KEY") or None
        return cls(
            database_url=_required(values, "NOEZEMA_DATABASE_URL"),
            workspace_root=Path(_required(values, "NOEZEMA_WORKSPACE_ROOT")),
            artifact_store_root=Path(_required(values, "NOEZEMA_ARTIFACT_STORE_ROOT")),
            prompt_root=prompt_root,
            identity_prompt_sha256=_prompt_digest(
                values,
                "NOEZEMA_IDENTITY_PROMPT_SHA256",
                prompt_root / "identity.md",
            ),
            explorer_prompt_sha256=_prompt_digest(
                values,
                "NOEZEMA_EXPLORER_PROMPT_SHA256",
                prompt_root / "explorer.md",
            ),
            curator_prompt_sha256=_prompt_digest(
                values,
                "NOEZEMA_CURATOR_PROMPT_SHA256",
                prompt_root / "curator.md",
            ),
            model_profile=profile,
            transport_config=TransportConfig(
                api_key=api_key,
                connect_timeout_seconds=_float(values, "NOEZEMA_LLM_CONNECT_TIMEOUT_SECONDS", 5.0),
                read_timeout_seconds=_float(values, "NOEZEMA_LLM_READ_TIMEOUT_SECONDS", 120.0),
                write_timeout_seconds=_float(values, "NOEZEMA_LLM_WRITE_TIMEOUT_SECONDS", 10.0),
                pool_timeout_seconds=_float(values, "NOEZEMA_LLM_POOL_TIMEOUT_SECONDS", 5.0),
                verify_tls=_boolean(values, "NOEZEMA_LLM_VERIFY_TLS", True),
            ),
            supervisor_policy=SupervisorPolicy(
                schedule_interval_seconds=_integer(
                    values, "NOEZEMA_SCHEDULE_INTERVAL_SECONDS", 3_600
                ),
                poll_interval_seconds=_float(values, "NOEZEMA_POLL_INTERVAL_SECONDS", 5.0),
                lease_ttl_seconds=_integer(values, "NOEZEMA_SCHEDULER_LEASE_TTL_SECONDS", 300),
                failure_backoff_initial_seconds=_integer(
                    values, "NOEZEMA_FAILURE_BACKOFF_INITIAL_SECONDS", 60
                ),
                failure_backoff_multiplier=_integer(
                    values, "NOEZEMA_FAILURE_BACKOFF_MULTIPLIER", 2
                ),
                failure_backoff_max_seconds=_integer(
                    values, "NOEZEMA_FAILURE_BACKOFF_MAX_SECONDS", 3_600
                ),
                pause_after_consecutive_failures=_integer(
                    values, "NOEZEMA_PAUSE_AFTER_FAILURES", 5
                ),
                max_session_steps=_integer(values, "NOEZEMA_SUPERVISOR_MAX_STEPS", 256),
                command_batch_limit=_integer(values, "NOEZEMA_COMMAND_BATCH_LIMIT", 128),
            ),
            runner_limits=SessionRunnerLimits(
                max_steps=_integer(values, "NOEZEMA_SESSION_MAX_STEPS", 128),
                max_claims=_integer(values, "NOEZEMA_SESSION_MAX_CLAIMS", 8),
                max_evidence_links=_integer(values, "NOEZEMA_SESSION_MAX_EVIDENCE_LINKS", 64),
            ),
            min_free_disk_bytes=_positive_integer(
                values,
                "NOEZEMA_MIN_FREE_DISK_BYTES",
                1_073_741_824,
            ),
        )


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _integer(values: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(values.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _positive_integer(values: Mapping[str, str], name: str, default: int) -> int:
    value = _integer(values, name, default)
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _float(values: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(values.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _boolean(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name, "true" if default else "false").strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return raw == "true"


def _prompt_digest(values: Mapping[str, str], name: str, path: Path) -> str:
    configured = values.get(name)
    if configured:
        return configured.strip().lower()
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"{name} is required when {path} cannot be read") from exc
