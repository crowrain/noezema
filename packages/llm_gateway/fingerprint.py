"""Call fingerprint (§12, T1.8).

A call is reproducible by its inputs: model profile + prompt version +
tool schema hash + policy version. The fingerprint is stored in
``model_runs.model_fingerprint`` and is part of the session config record.
Token-by-token regeneration identity is NOT promised (§12).
"""

from __future__ import annotations

from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict
from packages.llm_gateway.config import ModelProfile


def build_model_fingerprint(
    profile: ModelProfile,
    *,
    prompt_version: str,
    tool_schema_hash: str | None = None,
    policy_version: str | None = None,
) -> JsonDict:
    body: JsonDict = {
        "model": profile.to_dict(),
        "prompt_version": prompt_version,
        "tool_schema_hash": tool_schema_hash,
        "policy_version": policy_version,
    }
    body["fingerprint_sha256"] = canonical_sha256(body)
    return body
