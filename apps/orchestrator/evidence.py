"""Host-side adapter: typed observation → evidence record (T1.18).

The model never decides evidence identity or kind: the trusted host maps
each successful tool observation to a typed, hashed record. Failed
observations and host-deferred tools (question.create, message.reply)
produce no evidence.
"""

from __future__ import annotations

from apps.orchestrator.executor import Observation
from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import EvidenceKind
from packages.domain.schemas.evidence import EvidenceRecord


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

    return None  # memory.search / question.create / message.reply / failures
