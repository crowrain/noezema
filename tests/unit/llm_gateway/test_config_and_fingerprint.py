"""Tests for local endpoint policy and reproducibility fingerprints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.llm_gateway import (
    ChatMessage,
    ChatRole,
    GatewayRequest,
    InvocationFingerprint,
    ModelPhase,
    ModelProfile,
    ModelRole,
    model_fingerprint_sha256,
)


def _request() -> GatewayRequest:
    return GatewayRequest(
        messages=(ChatMessage(role=ChatRole.USER, content="Исследуй вопрос"),),
        role=ModelRole.EXPLORER,
        phase=ModelPhase.EXPLORATION,
        prompt_version="explorer/v1",
        context_manifest_sha256="5" * 64,
        policy_version="policy/v1",
    )


def test_profile_is_loopback_only_by_default(model_profile: ModelProfile) -> None:
    payload = model_profile.model_dump(mode="json")
    payload["base_url"] = "https://models.example/v1"

    with pytest.raises(ValidationError, match="allow_remote=true"):
        ModelProfile.model_validate(payload)

    payload["allow_remote"] = True
    assert ModelProfile.model_validate(payload).allow_remote


def test_model_fingerprint_ignores_endpoint_location(model_profile: ModelProfile) -> None:
    payload = model_profile.model_dump(mode="json")
    payload["base_url"] = "http://localhost:9090/v1"
    moved = ModelProfile.model_validate(payload)

    assert model_fingerprint_sha256(moved) == model_fingerprint_sha256(model_profile)


def test_model_fingerprint_changes_with_backend_build(model_profile: ModelProfile) -> None:
    payload = model_profile.model_dump(mode="json")
    payload["backend"]["build_fingerprint"] = "other-build"
    changed = ModelProfile.model_validate(payload)

    assert model_fingerprint_sha256(changed) != model_fingerprint_sha256(model_profile)


def test_invocation_fingerprint_covers_context_and_protocol(model_profile: ModelProfile) -> None:
    first = InvocationFingerprint.create(profile=model_profile, request=_request())
    changed_request = _request().model_copy(update={"policy_version": "policy/v2"})
    second = InvocationFingerprint.create(profile=model_profile, request=changed_request)

    assert first.sha256 != second.sha256
    assert first.tool_schema_sha256
