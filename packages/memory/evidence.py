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

import platform
import sys
from typing import Any

from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict

#: T7.17: the scope-coverage predicate became host-derived (see
#: packages.memory.scope) — the assessment records the engine version
#: so v1 (model-key coverage) and v2 (host-derived coverage) results
#: stay distinguishable
RULES_ENGINE_VERSION = "rules-v2"
URI_NORMALIZER_VERSION = "uri-normalizer-v1"
INDEPENDENCE_ALGORITHM_VERSION = "independence-v1"
#: T4.7 (§11.3): the full source-graph algorithm (parent sources,
#: dependency edges, operator corrections, identical content hashes)
SOURCE_GRAPH_ALGORITHM_VERSION = "independence-v2"
ENVIRONMENT_NORMALIZER_VERSION = "env-v2"

# §8.7.3: the framework implementation identity of a session run. Bumped
# by hand when the execution semantics change (a different noezema
# implementation running the same protocol is a different environment —
# but NEVER an independent replication: independence comes from the
# experiment's own protocol/implementation lineage, T4.6).
NOEZEMA_IMPLEMENTATION_HASH = "noezema-impl-v1"


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


def hardware_fingerprint() -> str:
    """Deterministic fingerprint of the host hardware (§14 manifest field
    ``hardware_hash``). A different GPU/host is a DIFFERENT environment —
    but §8.7.3: it never by itself creates an independent group."""
    u = platform.uname()
    return canonical_sha256(
        {
            "system": u.system,
            "machine": u.machine,
            "processor": u.processor or "",
            "node": u.node,
        }
    )


def session_environment_fields(
    *,
    protocol_hash: str,
    tool_schema_hash: str,
    seed: int | None,
) -> JsonDict:
    """The FULL §14 field set of the environment a SESSION run executed
    in (T4.6, §8.7.3). What was executed and where, minus wall-clock
    time and session identity: two sessions under the same protocol,
    implementation, runtime and hardware share ONE manifest (they are
    one environment — their repeats are ``repeatability``, not
    independent evidence). The LLM model fingerprint is NOT a manifest
    field (§14): it lives in model_runs, not in the experiment
    environment."""
    return {
        "protocol_hash": protocol_hash,
        "implementation_hash": NOEZEMA_IMPLEMENTATION_HASH,
        "code_lineage": None,
        "dataset_hash": None,
        "dataset_lineage": None,
        "toolchain_hash": tool_schema_hash,
        "dependency_hash": None,
        "runtime_hash": tool_fingerprint(),
        "hardware_hash": hardware_fingerprint(),
        "seed": seed,
        "data_order_hash": None,
        "normalizer_version": ENVIRONMENT_NORMALIZER_VERSION,
    }


def manifest_content_hash(fields: JsonDict) -> str:
    """Canonical content hash of a full environment manifest field set —
    the content-addressed identity (``environment_manifests.
    manifest_hash``, unique)."""
    return canonical_sha256(fields)


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
