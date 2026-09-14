"""Environment independence — the versioned algorithm (T4.6, §8.7.3).

One opaque ``environment_fingerprint`` is not enough: every experiment
run references a versioned environment manifest (§14 full field set),
and the algorithm classifies the RELATION between manifests:

- ``repeatability`` — same protocol/implementation/data/environment:
  stability of execution, never independent confirmation;
- ``reproducibility`` — same method (protocol+implementation+data
  lineage) but a different runtime/hardware/toolchain: portability
  inside the declared scope, still not an independent group;
- ``independent_replication`` — an independently implemented protocol
  OR implementation, and — when the claim depends on data — an
  independent dataset lineage: only this may lift a grade to E3;
- ``variation`` — different groups but the same method (e.g. the same
  implementation on a different dataset lineage): not independent;
- ``untracked`` — one side has no recorded lineage: unknown lineage
  NEVER creates independence (fail-closed, §19 gate);
- ``none`` — a single manifest, no pair.

GROUPS are built over (protocol, implementation, dataset lineage) ONLY:
a different GPU/backend, seed or data order never by itself creates an
independent group. A claim assessment records a snapshot of the
algorithm's output and counts DISTINCT GROUPS — never manifest hashes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.canonical import canonical_sha256
from packages.domain.models.memory import (
    ORMEnvironmentIndependenceMember,
    ORMEnvironmentIndependenceSnapshot,
    ORMEnvironmentManifest,
    ORMEvidence,
)

ENV_INDEPENDENCE_ALGORITHM_VERSION = "env-independence-v1"

REPEATABILITY = "repeatability"
REPRODUCIBILITY = "reproducibility"
INDEPENDENT_REPLICATION = "independent_replication"
VARIATION = "variation"
UNTRACKED = "untracked"
NONE = "none"

#: strength order used for the per-member "strongest pair" reduction and
#: for the rules engine's ``required_independence`` check. ``variation``
#: (different groups, same method) carries no independence either — it
#: ranks with ``untracked``, below any positive relation.
RELATION_RANK = {
    NONE: 0,
    UNTRACKED: 1,
    VARIATION: 1,
    REPEATABILITY: 2,
    REPRODUCIBILITY: 3,
    INDEPENDENT_REPLICATION: 4,
}

UNTRACKED_GROUP = "envgrp:untracked"


@dataclass(frozen=True)
class EnvManifestView:
    """The algorithm's pure view over one environment manifest."""

    id: uuid.UUID
    protocol_hash: str | None
    implementation_hash: str | None
    dataset_lineage: str | None
    runtime_hash: str | None
    hardware_hash: str | None
    toolchain_hash: str | None
    dependency_hash: str | None
    seed: int | None
    data_order_hash: str | None


def _view(m: ORMEnvironmentManifest) -> EnvManifestView:
    return EnvManifestView(
        id=m.id,
        protocol_hash=m.protocol_hash,
        implementation_hash=m.implementation_hash,
        dataset_lineage=m.dataset_lineage,
        runtime_hash=m.runtime_hash,
        hardware_hash=m.hardware_hash,
        toolchain_hash=m.toolchain_hash,
        dependency_hash=m.dependency_hash,
        seed=m.seed,
        data_order_hash=m.data_order_hash,
    )


def environment_group(v: EnvManifestView) -> str:
    """The independence GROUP key (§8.7.3): protocol + implementation +
    dataset lineage ONLY. A different GPU/backend, seed or data order
    never creates a group. A manifest with no recorded lineage at all is
    the single conservative ``untracked`` group."""
    if v.protocol_hash is None and v.implementation_hash is None and v.dataset_lineage is None:
        return UNTRACKED_GROUP
    return "envgrp:" + canonical_sha256(
        {
            "protocol": v.protocol_hash or "",
            "implementation": v.implementation_hash or "",
            "dataset_lineage": v.dataset_lineage or "",
        }
    )[:16]


def _env_key(v: EnvManifestView) -> tuple[str | int | None, ...]:
    """The "same environment" tuple: the execution fields of §14 minus
    the method/lineage fields (those define the group, not the
    environment)."""
    return (
        v.runtime_hash,
        v.hardware_hash,
        v.toolchain_hash,
        v.dependency_hash,
        v.seed,
        v.data_order_hash,
    )


def classify_pair(a: EnvManifestView, b: EnvManifestView) -> str:
    """The relation between TWO manifests (pure, deterministic).

    ``untracked`` on either side is fail-closed: an unknown lineage can
    never be read as independence (nor as repeatability)."""
    if a.protocol_hash is None and a.implementation_hash is None and a.dataset_lineage is None:
        return UNTRACKED
    if b.protocol_hash is None and b.implementation_hash is None and b.dataset_lineage is None:
        return UNTRACKED
    if environment_group(a) == environment_group(b):
        # same method + same data lineage: the execution fields decide
        return REPEATABILITY if _env_key(a) == _env_key(b) else REPRODUCIBILITY
    # different groups
    method_independent = (a.protocol_hash != b.protocol_hash) or (
        a.implementation_hash != b.implementation_hash
    )
    # "where the claim depends on data, an independent dataset
    # lineage": two known-equal lineages kill independence
    data_dependent = a.dataset_lineage is not None and b.dataset_lineage is not None
    if method_independent and not (data_dependent and a.dataset_lineage == b.dataset_lineage):
        return INDEPENDENT_REPLICATION
    return VARIATION


def strongest_pair_relation(
    v: EnvManifestView, others: list[EnvManifestView]
) -> tuple[str, str]:
    """The member's relation: the STRONGEST pair it has with the rest of
    the set (ties: the lexicographically smallest counterpart id).
    Returns (relation, basis) where basis names the decisive pair."""
    best = (NONE, "")
    for other in sorted(others, key=lambda o: o.id):
        rel = classify_pair(v, other)
        if RELATION_RANK[rel] > RELATION_RANK[best[0]]:
            best = (rel, f"pair:{other.id.hex[:12]}:{rel}")
    if best[0] == NONE:
        return (NONE, "single")
    return best


async def build_environment_independence_snapshot(
    db: AsyncSession,
    *,
    claim_id: uuid.UUID,
    rules_hash: str,
) -> tuple[uuid.UUID | None, dict[uuid.UUID, tuple[str, str]]]:
    """Run the versioned algorithm over the environment manifests
    referenced by the claim's evidence (all relations) and record an
    immutable snapshot (§8.7.3: the assessment fixes the snapshot and
    counts distinct groups, not hashes).

    Returns ``(snapshot_id, {manifest_id: (group_id, relation)})``; the
    id is ``None`` when the claim has no tracked environment (source /
    computation-only evidence). Runs inside the caller's transaction."""
    manifest_ids = {
        row
        for (row,) in (
            await db.execute(
                select(ORMEvidence.environment_manifest_id).where(
                    ORMEvidence.claim_id == claim_id,
                    ORMEvidence.environment_manifest_id.isnot(None),
                )
            )
        ).all()
        if row is not None
    }
    if not manifest_ids:
        return None, {}

    manifests = (
        (
            await db.execute(
                select(ORMEnvironmentManifest).where(
                    ORMEnvironmentManifest.id.in_(sorted(manifest_ids, key=str))
                )
            )
        )
        .scalars()
        .all()
    )
    views = [_view(m) for m in manifests]

    groups: dict[uuid.UUID, str] = {}
    relations: dict[uuid.UUID, str] = {}
    bases: dict[uuid.UUID, str] = {}
    for v in views:
        groups[v.id] = environment_group(v)
        rel, basis = strongest_pair_relation(v, [o for o in views if o.id != v.id])
        relations[v.id] = rel
        bases[v.id] = basis

    snapshot = ORMEnvironmentIndependenceSnapshot(
        id=uuid.uuid4(),
        algorithm_version=ENV_INDEPENDENCE_ALGORITHM_VERSION,
        rules_hash=rules_hash,
    )
    db.add(snapshot)
    # explicit two-step flush: the parent row must exist before the
    # members (the unit-of-work does not guarantee this order for a
    # fresh parent + children batch with a composite PK)
    await db.flush()
    for v in views:
        db.add(
            ORMEnvironmentIndependenceMember(
                snapshot_id=snapshot.id,
                environment_manifest_id=v.id,
                group_id=groups[v.id],
                relation=relations[v.id],
                basis=bases[v.id],
            )
        )
    await db.flush()
    mapping = {v.id: (groups[v.id], relations[v.id]) for v in views}
    return snapshot.id, mapping
