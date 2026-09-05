"""Production runtime configuration and composition-root tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.runtime import RuntimeConfig, build_runtime


def _environment(tmp_path: Path) -> dict[str, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    project_root = Path(__file__).resolve().parents[3]
    return {
        "NOEZEMA_DATABASE_URL": f"sqlite+pysqlite:///{(tmp_path / 'runtime.db').as_posix()}",
        "NOEZEMA_WORKSPACE_ROOT": str(workspace),
        "NOEZEMA_ARTIFACT_STORE_ROOT": str(tmp_path / "artifacts"),
        "NOEZEMA_PROMPT_ROOT": str(project_root / "prompts"),
        "NOEZEMA_LLM_BASE_URL": "http://127.0.0.1:8080/v1",
        "NOEZEMA_LLM_MODEL": "thinker-local",
        "NOEZEMA_LLM_MODEL_SHA256": "1" * 64,
        "NOEZEMA_LLM_TOKENIZER_SHA256": "2" * 64,
        "NOEZEMA_LLM_TEMPLATE_SHA256": "3" * 64,
        "NOEZEMA_LLM_GRAMMAR_SHA256": "4" * 64,
        "NOEZEMA_LLM_BACKEND": "llama.cpp",
        "NOEZEMA_LLM_BACKEND_VERSION": "b6000",
        "NOEZEMA_LLM_BUILD_FINGERPRINT": "commit+cuda-flags",
    }


def test_environment_materializes_a_local_runtime_and_prompt_fingerprints(
    tmp_path: Path,
) -> None:
    config = RuntimeConfig.from_environment(_environment(tmp_path))

    assert config.model_profile.normalized_base_url == "http://127.0.0.1:8080/v1/"
    assert config.model_profile.allow_remote is False
    assert config.identity_prompt_sha256 != config.explorer_prompt_sha256
    assert len(config.curator_prompt_sha256) == 64
    assert config.min_free_disk_bytes == 1_073_741_824


def test_environment_rejects_missing_fingerprint_and_remote_http_endpoint(
    tmp_path: Path,
) -> None:
    missing = _environment(tmp_path)
    del missing["NOEZEMA_LLM_MODEL_SHA256"]
    with pytest.raises(ValueError, match="NOEZEMA_LLM_MODEL_SHA256 is required"):
        RuntimeConfig.from_environment(missing)

    remote = _environment(tmp_path)
    remote["NOEZEMA_LLM_BASE_URL"] = "http://llm.internal/v1"
    remote["NOEZEMA_LLM_ALLOW_REMOTE"] = "true"
    with pytest.raises(ValidationError, match="remote LLM endpoint requires HTTPS"):
        RuntimeConfig.from_environment(remote)


def test_environment_rejects_nonpositive_disk_reserve(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    environment["NOEZEMA_MIN_FREE_DISK_BYTES"] = "0"

    with pytest.raises(ValueError, match="NOEZEMA_MIN_FREE_DISK_BYTES must be positive"):
        RuntimeConfig.from_environment(environment)


def test_explicit_empty_environment_does_not_fall_back_to_process_state() -> None:
    with pytest.raises(ValueError, match="NOEZEMA_LLM_BASE_URL is required"):
        RuntimeConfig.from_environment({})


def test_build_runtime_wires_components_without_contacting_the_backend(tmp_path: Path) -> None:
    config = RuntimeConfig.from_environment(_environment(tmp_path))

    with build_runtime(config, owner="runtime-test") as runtime:
        assert runtime.supervisor is not None
        assert config.artifact_store_root.is_dir()


def test_build_runtime_rejects_artifacts_inside_workspace_before_creating_them(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    forbidden = Path(environment["NOEZEMA_WORKSPACE_ROOT"]) / "artifacts"
    environment["NOEZEMA_ARTIFACT_STORE_ROOT"] = str(forbidden)
    config = RuntimeConfig.from_environment(environment)

    with pytest.raises(ValueError, match="artifact store must be outside"):
        build_runtime(config, owner="runtime-test")
    assert not forbidden.exists()
