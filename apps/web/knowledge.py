"""Knowledge-graph queries for the M7 web (T7.1, §22.1).

Read-only views over claims / assessments / dependencies / evidence /
sources / artifacts. Every head-joined query pins to the EFFECTIVE
config snapshot (``runtime_config_heads`` — the same fail-closed pointer
the runtime uses); candidate/shadow heads are visible per claim but are
never presented as current. No writes: a provenance read must not create
independence snapshots (those are fixed by assessments).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.base import JsonDict

# the effective snapshot pointer (fail-closed: the view is served from
# the same head the runtime reads)
_EFF_SNAP = (
    "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"
)

HEAD_STATES = ("current", "pending", "invalid", "none")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _uuid(value: Any) -> str | None:
    return None if value is None else str(value)


async def list_claims(
    db: AsyncSession, *, limit: int = 50, offset: int = 0, state: str | None = None
) -> JsonDict:
    """Claims with their head state on the effective snapshot."""
    total = (
        await db.execute(
            text("SELECT count(*) FROM claims"),
        )
    ).scalar_one()
    # a dynamic WHERE clause: SQLAlchemy's text() does not convert a
    # nullable bind parameter (asyncpg cannot infer its type from None)
    where = ""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if state is not None:
        where = " AND COALESCE(h.assessment_state, 'none') = :state"
        params["state"] = state
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT c.id, c.statement, c.claim_type, c.freshness_status,
                           c.as_of, c.reverify_after, c.created_at,
                           COALESCE(h.assessment_state, 'none') AS head_state,
                           h.epistemic_status, a.effective_grade, a.confidence
                    FROM claims c
                    LEFT JOIN claim_assessment_heads h
                           ON h.claim_id = c.id
                          AND h.config_snapshot_id = {_EFF_SNAP}
                    LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id
                    WHERE 1 = 1{where}
                    ORDER BY c.created_at DESC, c.id
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {
        "total": int(total),
        "claims": [
            {
                "id": _uuid(r["id"]),
                "statement": r["statement"],
                "claim_type": r["claim_type"],
                "freshness_status": r["freshness_status"],
                "as_of": _iso(r["as_of"]),
                "reverify_after": _iso(r["reverify_after"]),
                "created_at": _iso(r["created_at"]),
                "head_state": r["head_state"],
                "epistemic_status": r["epistemic_status"],
                "effective_grade": r["effective_grade"],
                "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            }
            for r in rows
        ],
    }


async def claim_detail(db: AsyncSession, claim_id: uuid.UUID) -> JsonDict:
    """One claim: body, all heads (effective first), evidence, dependencies."""
    claim = (
        await db.execute(
            text(
                """
                SELECT c.id, c.statement, c.claim_type, c.freshness_status,
                       c.valid_from, c.valid_to, c.as_of, c.observed_at,
                       c.reverify_after, c.dependency_fingerprint, c.topic,
                       c.created_in_session, c.created_at
                FROM claims c WHERE c.id = :id
                """
            ),
            {"id": claim_id},
        )
    ).mappings().first()
    if claim is None:
        raise HTTPException(status_code=404, detail="claim not found")

    heads = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT h.config_snapshot_id, h.assessment_state, h.epistemic_status,
                           h.current_assessment_id, h.prepared_by, h.updated_at,
                           cs.activation_state, cs.activation_mode,
                           a.effective_grade, a.confidence, a.rules_version,
                           a.assessed_scope, a.created_at AS assessment_created_at
                    FROM claim_assessment_heads h
                    LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id
                    LEFT JOIN config_snapshots cs ON cs.id = h.config_snapshot_id
                    WHERE h.claim_id = :id
                    ORDER BY (h.config_snapshot_id = {_EFF_SNAP}) DESC, h.config_snapshot_id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )

    evidence = (
        (
            await db.execute(
                text(
                    """
                    SELECT e.id, e.relation, e.evidence_kind, e.identity_hash,
                           e.scope, e.source_id, e.chunk_id,
                           e.observation_artifact_id, e.environment_manifest_id,
                           e.created_in_session, e.created_at,
                           s.source_type, s.canonical_uri, s.content_hash,
                           ar.sha256 AS artifact_sha256
                    FROM evidence e
                    LEFT JOIN sources s ON s.id = e.source_id
                    LEFT JOIN artifacts ar ON ar.id = e.observation_artifact_id
                    WHERE e.claim_id = :id
                    ORDER BY e.created_at, e.id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )

    deps_out = (
        (
            await db.execute(
                text(
                    """
                    SELECT d.to_claim_id, d.kind, tc.statement
                    FROM claim_dependencies d
                    JOIN claims tc ON tc.id = d.to_claim_id
                    WHERE d.from_claim_id = :id
                    ORDER BY d.id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )
    deps_in = (
        (
            await db.execute(
                text(
                    """
                    SELECT d.from_claim_id, d.kind, fc.statement
                    FROM claim_dependencies d
                    JOIN claims fc ON fc.id = d.from_claim_id
                    WHERE d.to_claim_id = :id
                    ORDER BY d.id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )

    return {
        "id": _uuid(claim["id"]),
        "statement": claim["statement"],
        "claim_type": claim["claim_type"],
        "freshness_status": claim["freshness_status"],
        "valid_from": _iso(claim["valid_from"]),
        "valid_to": _iso(claim["valid_to"]),
        "as_of": _iso(claim["as_of"]),
        "observed_at": _iso(claim["observed_at"]),
        "reverify_after": _iso(claim["reverify_after"]),
        "dependency_fingerprint": claim["dependency_fingerprint"],
        "topic": claim["topic"],
        "created_in_session": _uuid(claim["created_in_session"]),
        "created_at": _iso(claim["created_at"]),
        "heads": [
            {
                "config_snapshot_id": _uuid(h["config_snapshot_id"]),
                "activation_mode": h["activation_mode"],
                "activation_state": h["activation_state"],
                "assessment_state": h["assessment_state"],
                "epistemic_status": h["epistemic_status"],
                "current_assessment_id": _uuid(h["current_assessment_id"]),
                "prepared_by": h["prepared_by"],
                "updated_at": _iso(h["updated_at"]),
                "effective_grade": h["effective_grade"],
                "confidence": (
                    float(h["confidence"]) if h["confidence"] is not None else None
                ),
                "rules_version": h["rules_version"],
                "assessed_scope": h["assessed_scope"],
                "assessment_created_at": _iso(h["assessment_created_at"]),
            }
            for h in heads
        ],
        "evidence": [
            {
                "id": _uuid(e["id"]),
                "relation": e["relation"],
                "evidence_kind": e["evidence_kind"],
                "identity_hash": e["identity_hash"],
                "scope": e["scope"],
                "source_id": _uuid(e["source_id"]),
                "chunk_id": e["chunk_id"],
                "observation_artifact_id": _uuid(e["observation_artifact_id"]),
                "environment_manifest_id": _uuid(e["environment_manifest_id"]),
                "source_type": e["source_type"],
                "source_uri": e["canonical_uri"],
                "source_content_hash": e["content_hash"],
                "artifact_sha256": e["artifact_sha256"],
                "created_at": _iso(e["created_at"]),
            }
            for e in evidence
        ],
        "depends_on": [
            {"claim_id": _uuid(d["to_claim_id"]), "kind": d["kind"], "statement": d["statement"]}
            for d in deps_out
        ],
        "depended_by": [
            {"claim_id": _uuid(d["from_claim_id"]), "kind": d["kind"], "statement": d["statement"]}
            for d in deps_in
        ],
    }


async def claim_provenance(db: AsyncSession, claim_id: uuid.UUID) -> JsonDict:
    """Provenance navigation for one claim: evidence → source (and its
    parent) / artifact, plus the independence groups the CURRENT
    assessment fixed for this claim (read-only: the view never creates
    new snapshots)."""
    claim = (
        await db.execute(
            text("SELECT id FROM claims WHERE id = :id"), {"id": claim_id}
        )
    ).first()
    if claim is None:
        raise HTTPException(status_code=404, detail="claim not found")

    evidence = (
        (
            await db.execute(
                text(
                    """
                    SELECT e.id, e.relation, e.evidence_kind, e.identity_hash,
                           e.source_id, e.chunk_id,
                           e.observation_artifact_id, e.environment_manifest_id,
                           s.source_type, s.canonical_uri, s.content_hash,
                           s.retrieved_at, s.parent_source_id,
                           ps.canonical_uri AS parent_uri,
                           ps.content_hash AS parent_content_hash,
                           ar.sha256 AS artifact_sha256, ar.size AS artifact_size,
                           ar.trust_class, ar.origin AS artifact_origin,
                           em.protocol_hash AS env_protocol_hash
                    FROM evidence e
                    LEFT JOIN sources s ON s.id = e.source_id
                    LEFT JOIN sources ps ON ps.id = s.parent_source_id
                    LEFT JOIN artifacts ar ON ar.id = e.observation_artifact_id
                    LEFT JOIN environment_manifests em ON em.id = e.environment_manifest_id
                    WHERE e.claim_id = :id
                    ORDER BY e.created_at, e.id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )

    # the current assessment (effective snapshot): its fixed independence
    # snapshots + the per-evidence roles
    current = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT a.source_independence_snapshot_id,
                           a.environment_independence_snapshot_id
                    FROM claim_assessment_heads h
                    JOIN claim_assessments a ON a.id = h.current_assessment_id
                    WHERE h.claim_id = :id
                      AND h.config_snapshot_id = {_EFF_SNAP}
                      AND h.assessment_state = 'current'
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .first()
    )

    source_groups: list[dict[str, Any]] = []
    env_groups: list[dict[str, Any]] = []
    roles: list[dict[str, Any]] = []
    if current is not None:
        src_snap = _uuid(current["source_independence_snapshot_id"])
        if src_snap is not None:
            members = (
                (
                    await db.execute(
                        text(
                            """
                            SELECT m.source_id, m.group_id, m.basis,
                                   s.canonical_uri
                            FROM source_independence_members m
                            LEFT JOIN sources s ON s.id = m.source_id
                            WHERE m.snapshot_id = :snap
                            ORDER BY m.source_id
                            """
                        ),
                        {"snap": src_snap},
                    )
                )
                .mappings()
                .all()
            )
            source_groups = [
                {
                    "source_id": _uuid(m["source_id"]),
                    "uri": m["canonical_uri"],
                    "group_id": m["group_id"],
                    "basis": m["basis"],
                }
                for m in members
            ]
        env_snap = _uuid(current["environment_independence_snapshot_id"])
        if env_snap is not None:
            members = (
                (
                    await db.execute(
                        text(
                            """
                            SELECT m.environment_manifest_id, m.group_id,
                                   m.relation, m.basis
                            FROM environment_independence_members m
                            WHERE m.snapshot_id = :snap
                            ORDER BY m.environment_manifest_id
                            """
                        ),
                        {"snap": env_snap},
                    )
                )
                .mappings()
                .all()
            )
            env_groups = [
                {
                    "environment_manifest_id": _uuid(m["environment_manifest_id"]),
                    "group_id": m["group_id"],
                    "relation": m["relation"],
                    "basis": m["basis"],
                }
                for m in members
            ]
        roles_rows = (
            (
                await db.execute(
                    text(
                        f"""
                        SELECT ae.evidence_id, ae.role, e.evidence_kind
                        FROM assessment_evidence ae
                        JOIN claim_assessment_heads h
                               ON h.claim_id = :id
                              AND h.config_snapshot_id = {_EFF_SNAP}
                              AND h.assessment_state = 'current'
                        JOIN evidence e ON e.id = ae.evidence_id
                        WHERE ae.assessment_id = h.current_assessment_id
                        ORDER BY ae.evidence_id, ae.role
                        """
                    ),
                    {"id": claim_id},
                )
            )
            .mappings()
            .all()
        )
        roles = [
            {
                "evidence_id": _uuid(r["evidence_id"]),
                "evidence_kind": r["evidence_kind"],
                "role": r["role"],
            }
            for r in roles_rows
        ]

    return {
        "claim_id": _uuid(claim_id),
        "evidence": [
            {
                "id": _uuid(e["id"]),
                "relation": e["relation"],
                "evidence_kind": e["evidence_kind"],
                "identity_hash": e["identity_hash"],
                "source": (
                    {
                        "id": _uuid(e["source_id"]),
                        "source_type": e["source_type"],
                        "canonical_uri": e["canonical_uri"],
                        "content_hash": e["content_hash"],
                        "retrieved_at": _iso(e["retrieved_at"]),
                        "chunk_id": e["chunk_id"],
                        "parent": (
                            {
                                "id": _uuid(e["parent_source_id"]),
                                "canonical_uri": e["parent_uri"],
                                "content_hash": e["parent_content_hash"],
                            }
                            if e["parent_source_id"] is not None
                            else None
                        ),
                    }
                    if e["source_id"] is not None
                    else None
                ),
                "artifact": (
                    {
                        "id": _uuid(e["observation_artifact_id"]),
                        "sha256": e["artifact_sha256"],
                        "size": int(e["artifact_size"]) if e["artifact_size"] is not None else None,
                        "trust_class": e["trust_class"],
                        "origin": e["artifact_origin"],
                    }
                    if e["observation_artifact_id"] is not None
                    else None
                ),
                "environment_manifest_id": _uuid(e["environment_manifest_id"]),
                "environment_protocol_hash": e["env_protocol_hash"],
            }
            for e in evidence
        ],
        "source_groups": source_groups,
        "environment_groups": env_groups,
        "assessment_evidence_roles": roles,
    }


async def list_dependencies(
    db: AsyncSession, *, claim_id: uuid.UUID | None = None, limit: int = 200
) -> JsonDict:
    """Dependency edges (from depends on to; §8.6), optionally narrowed
    to one claim (either direction)."""
    limit = max(1, min(limit, 1000))
    where = ""
    params: dict[str, Any] = {"limit": limit}
    if claim_id is not None:
        where = " AND (d.from_claim_id = :cid OR d.to_claim_id = :cid)"
        params["cid"] = claim_id
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT d.id, d.from_claim_id, d.to_claim_id, d.kind,
                           fc.statement AS from_statement,
                           tc.statement AS to_statement
                    FROM claim_dependencies d
                    JOIN claims fc ON fc.id = d.from_claim_id
                    JOIN claims tc ON tc.id = d.to_claim_id
                    WHERE 1 = 1{where}
                    ORDER BY d.id
                    LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {
        "dependencies": [
            {
                "id": _uuid(r["id"]),
                "from_claim_id": _uuid(r["from_claim_id"]),
                "to_claim_id": _uuid(r["to_claim_id"]),
                "kind": r["kind"],
                "from_statement": r["from_statement"],
                "to_statement": r["to_statement"],
            }
            for r in rows
        ]
    }
