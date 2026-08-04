"""Fixtures shared by LLM gateway tests."""

from __future__ import annotations

import pytest

from packages.llm_gateway import (
    BackendMetadata,
    ModelProfile,
    RuntimeSettings,
    SamplingSettings,
    StructuredOutputSettings,
)


@pytest.fixture
def model_profile() -> ModelProfile:
    return ModelProfile(
        base_url="http://127.0.0.1:8080/v1",
        model_alias="thinker-local",
        model_artifact_sha256="1" * 64,
        quantization="Q4_K_M",
        tokenizer_sha256="2" * 64,
        chat_template_sha256="3" * 64,
        backend=BackendMetadata(
            name="llama.cpp",
            version="b6000",
            build_fingerprint="commit+cuda-flags",
        ),
        context_window=32768,
        max_output_tokens=4096,
        safety_margin_tokens=2048,
        structured_output=StructuredOutputSettings(grammar_sha256="4" * 64),
        sampling=SamplingSettings(seed=42, temperature=0.2, top_p=0.95, top_k=40),
        runtime=RuntimeSettings(gpu_layers=-1),
    )
