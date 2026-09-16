"""Host-side adapter: typed observation → evidence record (T1.18).

The model never decides evidence identity or kind: the trusted host maps
each successful tool observation to a typed, hashed record. Failed
observations and host-deferred tools (question.create, message.reply)
produce no evidence.

``research.fetch`` maps to a ``source_assertion`` record (EVAL-3
precondition, ADR-0006 rev): the fetched page is an external source's
assertion about the world. Identity = ``source_assertion_identity``
over the ORIGINAL content hash + chunk + kind (stable across re-fetches
of the same content), provenance = the durable ``sources`` row
(``source_id`` / ``chunk_id``) — the source-independence graph groups
the evidence by registrable domain, which is what the external/temporal
claim rules (≥2 independent groups, E3) require.
"""

from __future__ import annotations

from apps.orchestrator.executor import Observation
from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import EvidenceKind
from packages.domain.schemas.evidence import EvidenceRecord
from packages.memory.evidence import source_assertion_identity


def observation_to_evidence(observation: Observation, arguments: JsonDict) -> EvidenceRecord | None:
    if not observation.ok:
        return None

    tool = observation.tool
    if tool == "python.execute":
        data = observation.data
        identity = canonical_sha256(
            {
                "tool": tool,
                "code": str(arguments.get("code", "")),
                "exit_code": data.get("exit_code"),
                "stdout": str(data.get("stdout", ""))[:4000],
            }
        )
        return EvidenceRecord(
            kind=EvidenceKind.COMPUTATION,
            identity_hash=identity,
            payload={
                "exit_code": data.get("exit_code"),
                "stdout": str(data.get("stdout", ""))[:2000],
                # the exact input (M3: the trusted host recomputes the
                # durable identity from result + inputs + tool fingerprint)
                "code": str(arguments.get("code", ""))[:4000],
            },
            note="python.execute (M1 stub executor)",
        )

    if tool == "workspace.read":
        content = str(observation.data.get("content", ""))
        identity = canonical_sha256(
            {"tool": tool, "path": str(arguments.get("path", "")), "content": content[:4000]}
        )
        return EvidenceRecord(
            kind=EvidenceKind.LOCAL_OBSERVATION,
            identity_hash=identity,
            payload={"path": str(observation.data.get("path", "")), "content": content[:2000]},
        )

    if tool == "workspace.list":
        identity = canonical_sha256(
            {"tool": tool, "path": str(arguments.get("path", "")), "entries": observation.data.get("entries")}
        )
        return EvidenceRecord(
            kind=EvidenceKind.LOCAL_OBSERVATION,
            identity_hash=identity,
            payload={"entries": observation.data.get("entries", [])[:100]},
        )

    if tool == "research.fetch":
        # EVAL-3 precondition (ADR-0006 rev): the only session path that
        # produces source-based evidence. The envelope data carries the
        # durable source reference (the proxy registered the sources row
        # in its own transaction); identity is over the ORIGINAL
        # content hash so a re-fetch of identical content dedupes, and
        # the truncated context text is NOT part of the identity (the
        # assertion is the source's, not the truncation's).
        data = observation.data or {}
        osha = str(data.get("original_sha256", ""))
        source_id = str(data.get("source_id", ""))
        if not osha or not source_id or len(source_id) != 36:
            return None  # no durable source reference → no provenance
        chunk_id = str(data.get("chunk_id", "chunk-0"))
        identity = source_assertion_identity(osha, chunk_id, EvidenceKind.SOURCE_ASSERTION.value)
        return EvidenceRecord(
            kind=EvidenceKind.SOURCE_ASSERTION,
            identity_hash=identity,
            payload={
                "url": str(data.get("url", ""))[:500],
                "original_sha256": osha,
                "normalized_sha256": str(data.get("normalized_sha256", "")),
                "chunk_id": chunk_id,
                # the exact input (the trusted host recomputes identity
                # from the provenance, not from this payload)
                "url_arg": str(arguments.get("url", ""))[:500],
            },
            source_id=source_id,
            chunk_id=chunk_id,
            note="research.fetch (untrusted external source)",
        )

    return None  # memory.search / question.create / message.reply / failures
