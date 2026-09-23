"""Call fingerprint (§12, T1.8; T7.35/ADR-0019: prompt content hash).

A call is reproducible by its inputs: model profile + prompt version +
prompt content sha256 + tool schema hash + policy version. The
fingerprint is stored in ``model_runs.model_fingerprint`` and is part
of the session config record. Token-by-token regeneration identity is
NOT promised (§12).
"""

from __future__ import annotations

from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict
from packages.llm_gateway.config import ModelProfile


def build_model_fingerprint(
    profile: ModelProfile,
    *,
    prompt_version: str,
    prompt_sha256: str | None = None,
    tool_schema_hash: str | None = None,
    policy_version: str | None = None,
) -> JsonDict:
    body: JsonDict = {
        "model": profile.to_dict(),
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256,
        "tool_schema_hash": tool_schema_hash,
        "policy_version": policy_version,
    }
    body["fingerprint_sha256"] = canonical_sha256(body)
    return body
