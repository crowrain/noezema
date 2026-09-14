"""Evidence identity — the trusted host computes it (T3.2, §14.3).

``evidence.identity_hash`` is canonical content/provenance computed by the
trusted contour, never proposed by the model. Per-kind composition:

- source evidence: source content hash, normalized range and kind;
- experiment/local observation: observation content hash, environment
  manifest hash and kind;
- computation/formal check: result artifact hash, exact inputs and the
  tool fingerprint.

Deduplication is structural: UNIQUE(claim_id, evidence_kind,
identity_hash). A "reversed" repetition of the same content (e.g. the
equation ``6*7=42`` re-stated as ``42=6*7``) normalizes to the same
identity and is NOT new evidence.
"""

from __future__ import annotations

import sys
from typing import Any

from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict

RULES_ENGINE_VERSION = "rules-v1"
URI_NORMALIZER_VERSION = "uri-normalizer-v1"
INDEPENDENCE_ALGORITHM_VERSION = "independence-v1"
ENVIRONMENT_NORMALIZER_VERSION = "env-v1"


def canonical_equation(text: str) -> str:
    """Normalize a simple equation so that ``a=b`` and ``b=a`` have one
    canonical form. Non-equations are returned trimmed and
    whitespace-collapsed."""
    collapsed = " ".join(text.strip().split())
    if "=" in collapsed:
        sides = [" ".join(part.split()) for part in collapsed.split("=")]
        return "=" .join(sorted(sides))
    return collapsed


def tool_fingerprint() -> str:
    """Deterministic fingerprint of the execution tool (interpreter)."""
    return f"python-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def environment_manifest_hash(session_id: str, model_fingerprint: JsonDict, tool_schema_hash: str) -> str:
    """Canonical hash of the session environment manifest (T3.5/M4
    scaffold): what was executed and where, minus wall-clock time."""
    return canonical_sha256(
        {
            "session_id": session_id,
            "model_fingerprint": model_fingerprint,
            "tool_schema_hash": tool_schema_hash,
            "runtime": tool_fingerprint(),
            "normalizer_version": ENVIRONMENT_NORMALIZER_VERSION,
        }
    )


def computation_identity(result: str, inputs: str, artifact_hash: str) -> str:
    """computation / formal_check: result artifact hash + exact inputs +
    tool fingerprint. The result is canonicalized (reversed equations are
    the same identity)."""
    return canonical_sha256(
        {
            "kind": "computation",
            "result_artifact_hash": artifact_hash,
            "result": canonical_equation(result),
            "inputs": inputs,
            "tool_fingerprint": tool_fingerprint(),
        }
    )


def local_observation_identity(observation: str, env_hash: str) -> str:
    """experiment_run / local_observation: observation content hash +
    environment manifest hash."""
    return canonical_sha256(
        {
            "kind": "local_observation",
            "observation_content_hash": canonical_sha256(observation),
            "environment_manifest_hash": env_hash,
        }
    )


def source_assertion_identity(source_content_hash: str, normalized_range: str, kind: str) -> str:
    """source_assertion / quote_integrity: source content hash,
    normalized range and kind."""
    return canonical_sha256(
        {
            "kind": kind,
            "source_content_hash": source_content_hash,
            "normalized_range": normalized_range,
        }
    )


def observation_artifact_hash(payload: JsonDict) -> str:
    """Content hash of a canonical observation payload (the artifact the
    evidence row references)."""
    return canonical_sha256(payload)


def evidence_set_hash(identities: list[str]) -> str:
    """Canonical hash over the sorted evidence identity set — the exact
    set an assessment was computed from (T3.4)."""
    return canonical_sha256(sorted(identities))


def rules_hash(rules: dict[str, Any]) -> str:
    """Canonical hash of the claim-type rules for one snapshot."""
    return canonical_sha256(rules)
