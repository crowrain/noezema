"""Deterministic model and invocation fingerprints."""

from __future__ import annotations

from packages.domain import DecisionEnvelope, canonical_json_sha256
from packages.domain._base import ContractModel, Sha256Hex
from packages.llm_gateway.config import ModelProfile
from packages.llm_gateway.models import GatewayRequest


def tool_schema_sha256() -> str:
    return canonical_json_sha256(DecisionEnvelope.model_json_schema())


def model_fingerprint_sha256(profile: ModelProfile) -> str:
    material = profile.model_dump(
        mode="json",
        exclude={"base_url", "allow_remote"},
    )
    return canonical_json_sha256(material)


class InvocationFingerprint(ContractModel):
    model_fingerprint_sha256: Sha256Hex
    prompt_version: str
    context_manifest_sha256: Sha256Hex
    tool_schema_sha256: Sha256Hex
    policy_version: str
    role: str
    phase: str
    sha256: Sha256Hex

    @classmethod
    def create(cls, *, profile: ModelProfile, request: GatewayRequest) -> InvocationFingerprint:
        model_hash = model_fingerprint_sha256(profile)
        schema_hash = tool_schema_sha256()
        material = {
            "model_fingerprint_sha256": model_hash,
            "prompt_version": request.prompt_version,
            "context_manifest_sha256": request.context_manifest_sha256,
            "tool_schema_sha256": schema_hash,
            "policy_version": request.policy_version,
            "role": request.role.value,
            "phase": request.phase.value,
        }
        return cls(**material, sha256=canonical_json_sha256(material))
