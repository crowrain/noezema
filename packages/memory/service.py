"""Memory service (T3.6, T3.7, §8.2, §14.1).

Applies the session's claim/evidence staging at the commit boundary
(synchronously, inside the fenced final transaction) and resolves the
claim views: the lifecycle is read from ``claim_assessment_heads`` via
the effective config snapshot (``runtime_config_heads``), never from the
mutable claims row.

Only the rules engine assigns grade/confidence (§3.7). pending/invalid
heads are not current evidence and not dependencies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.canonical import canonical_sha256
from packages.domain.models.artifacts import ORMArtifact, ORMStagingOp
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot, ORMRuntimeConfigHead
from packages.domain.models.enums import (
    AssessmentState,
    AuditEventType,
    EffectiveGrade,
    EpistemicStatus,
    FreshnessStatus,
)
from packages.domain.models.memory import (
    ORMAssessmentEvidence,
    ORMClaim,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
    ORMClaimDependency,
    ORMEnvironmentManifest,
    ORMEvidence,
)
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.memory.env_independence import (
    UNTRACKED_GROUP,
    build_environment_independence_snapshot,
)
from packages.memory.evidence import (
    RULES_ENGINE_VERSION,
    computation_identity,
    local_observation_identity,
    manifest_content_hash,
    observation_artifact_hash,
    rules_hash,
    session_environment_fields,
    source_assertion_identity,
)
from packages.memory.rules_engine import (
    ClaimTypeRule,
    EvaluatedEvidence,
    RuleValidationError,
    evaluate,
    reverify_after,
)
from packages.memory.source_graph import build_source_independence_snapshot

GLOBAL_SCOPE = "global"


def _dependency_dict(value: Any) -> JsonDict | None:
    """A JSONB element is native Python at runtime; guard non-dict values
    (a malformed staging payload must reject the edge, not raise)."""
    return value if isinstance(value, dict) else None


def find_evidential_cycles(
    existing: list[tuple[uuid.UUID, uuid.UUID]],
    new: list[tuple[uuid.UUID, uuid.UUID]],
) -> set[int]:
    """Indices of ``new`` edges that would create a cycle in the
    evidential DAG (T4.1, §8.6: cycles are forbidden for evidential
    dependencies; the check runs at the commit boundary).

    A new edge ``a -> b`` closes a cycle iff ``a`` is reachable from
    ``b`` in (existing edges ∪ new edges). Pure and corpus-scale small,
    so it is an in-memory DFS over one pre-fetched edge set."""
    graph: dict[uuid.UUID, list[uuid.UUID]] = {}
    for src, dst in existing:
        graph.setdefault(src, []).append(dst)
    for src, dst in new:
        graph.setdefault(src, []).append(dst)
    rejected: set[int] = set()
    for i, (src, dst) in enumerate(new):
        stack = [dst]
        visited = {dst}
        cyclic = False
        while stack:
            node = stack.pop()
            for nxt in graph.get(node, ()):
                if nxt == src:
                    cyclic = True
                    break
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
            if cyclic:
                break
        if cyclic:
            rejected.add(i)
    return rejected


@dataclass(frozen=True)
class MemoryApplyResult:
    claims_created: int = 0
    claims_reused: int = 0
    evidence_added: int = 0
    evidence_deduped: int = 0
    assessments: int = 0
    dependencies_added: int = 0
    #: evidential subset — the graph revision is bumped only when these
    #: actually change (T4.1, §8.6)
    dependencies_evidential_added: int = 0
    dependencies_rejected: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimViewEvidence:
    evidence_id: uuid.UUID
    kind: str
    relation: str
    role: str
    identity_hash: str


@dataclass(frozen=True)
class ClaimView:
    """The resolved view of one claim under the effective config."""

    claim_id: uuid.UUID
    statement: str
    claim_type: str
    assessment_state: AssessmentState
    epistemic_status: EpistemicStatus | None
    grade: EffectiveGrade | None
    confidence: float | None
    freshness: FreshnessStatus
    valid_from: datetime | None
    valid_to: datetime | None
    as_of: datetime | None
    reverify_after: datetime | None
    evidence: list[ClaimViewEvidence] = field(default_factory=list)
    counterevidence: list[ClaimViewEvidence] = field(default_factory=list)


class MemoryService:
    def __init__(self, snapshot: ORMConfigSnapshot) -> None:
        self.snapshot = snapshot
        self._rules: dict[str, ClaimTypeRule] = {
            str(claim_type): ClaimTypeRule.from_payload(str(claim_type), dict(payload))
            for claim_type, payload in snapshot.claim_type_rules.items()
        }
        self._rules_hash = rules_hash(dict(snapshot.claim_type_rules))

    # ── helpers ───────────────────────────────────────────────────────────

    def rule(self, claim_type: str) -> ClaimTypeRule:
        try:
            return self._rules[claim_type]
        except KeyError as exc:
            raise RuleValidationError(f"unknown claim_type {claim_type!r}") from exc

    def freshness_status(self, claim: ORMClaim, now: datetime) -> FreshnessStatus:
        """Expiry changes ONLY the freshness status (T3.7, §8.6) — never
        the grade or confidence. valid_to=NULL means an open end."""
        if claim.reverify_after is None:
            return FreshnessStatus.UNKNOWN
        if now < claim.reverify_after:
            return FreshnessStatus.FRESH
        return FreshnessStatus.DUE

    # ── commit-boundary apply ─────────────────────────────────────────────

    async def apply_claim_staging(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        evidence_records: list[Any],
        *,
        model_fingerprint: JsonDict | None = None,
        tool_schema_hash: str | None = None,
    ) -> MemoryApplyResult:
        """Apply the session's recorded claim/evidence staging ops inside
        the caller's (fenced) transaction. ``evidence_records`` are the
        in-memory observations of the session, referenced by index from
        the evidence ops — the trusted host recomputes every identity
        here; the M1 in-memory hash is not trusted."""
        rows = (
            (
                await db.execute(
                    select(ORMStagingOp)
                    .where(
                        ORMStagingOp.session_id == session.id,
                        ORMStagingOp.op.in_(("claim", "evidence")),
                        ORMStagingOp.state == "recorded",
                    )
                    .order_by(ORMStagingOp.created_at, ORMStagingOp.id)
                )
            )
            .scalars()
            .all()
        )
        claim_ops = [r for r in rows if r.op == "claim"]
        evidence_ops = [r for r in rows if r.op == "evidence"]
        if not claim_ops:
            return MemoryApplyResult()

        now = datetime.now(UTC)
        problems: list[str] = []
        counters = {"created": 0, "reused": 0, "added": 0, "deduped": 0, "assessments": 0}

        # §8.7.3 (T4.6): the FULL §14 environment manifest — content-
        # addressed over the field set. The protocol is the session's
        # prompt set (the effective config's prompts section); the seed
        # is the LLM sampling seed (part of the execution environment,
        # never of the independence group key).
        protocol_hash = canonical_sha256(dict(self.snapshot.prompts or {}))
        sampling = ((self.snapshot.model or {}).get("sampling")) or {}
        seed = int(sampling["seed"]) if sampling.get("seed") is not None else None
        env_fields = session_environment_fields(
            protocol_hash=protocol_hash,
            tool_schema_hash=tool_schema_hash or "",
            seed=seed,
        )
        env_hash = manifest_content_hash(env_fields)
        env = await self._environment_manifest(db, env_hash, env_fields)

        # 1. claims (exact statement+type dedup against the corpus)
        claims: list[ORMClaim] = []
        claim_scopes: dict[uuid.UUID, JsonDict] = {}
        claim_deps: list[tuple[ORMClaim, list[Any]]] = []
        for row in claim_ops:
            payload = dict(row.payload)
            statement = str(payload.get("statement", ""))[:2000]
            claim_type = str(payload.get("claim_type", ""))
            if not statement or claim_type not in self._rules:
                problems.append(f"claim staging rejected: bad statement/type ({row.id})")
                continue
            scope = dict(payload.get("scope") or {})
            as_of_raw = payload.get("as_of")
            deps = payload.get("dependencies")
            deps_list: list[Any] = list(deps) if isinstance(deps, list) else []
            # cross-lingual search index (ADR-0006 rev): host-trusted —
            # stripped, truncated, list-of-str only
            raw_search = payload.get("search_statements")
            search_statements: list[str] = []
            if isinstance(raw_search, list):
                search_statements = [
                    str(s).strip()[:300] for s in raw_search if str(s).strip()
                ][:2]
            existing_claim = (
                (
                    await db.execute(
                        select(ORMClaim).where(
                            ORMClaim.statement == statement, ORMClaim.claim_type == claim_type
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing_claim is not None:
                claims.append(existing_claim)
                claim_scopes[existing_claim.id] = scope
                counters["reused"] += 1
                claim_deps.append((existing_claim, deps_list))
            else:
                claim = ORMClaim(
                    id=uuid.uuid4(),
                    statement=statement,
                    search_statements=search_statements,
                    claim_type=claim_type,
                    as_of=datetime.fromisoformat(as_of_raw) if as_of_raw else None,
                    observed_at=now,
                    created_in_session=session.id,
                )
                db.add(claim)
                await db.flush()
                claims.append(claim)
                claim_scopes[claim.id] = scope
                counters["created"] += 1
                claim_deps.append((claim, deps_list))
                await audit.record(
                    AuditEventType.CLAIM_CREATED,
                    session_id=session.id,
                    payload={
                        "claim_id": str(claim.id),
                        "claim_type": claim_type,
                        "statement": statement[:500],
                        "scope": scope,
                    },
                    public_summary=f"claim: {statement[:120]}",
                )

        # 1b. claim dependencies (T4.1, §8.6): the DAG cycle check runs
        # at the commit boundary; a cyclic/invalid edge is rejected with
        # audit, the claim itself still commits (conservative, no
        # invariant is broken)
        counters["deps"] = 0
        deps_rejected: list[str] = []
        dep_edges: list[tuple[ORMClaim, JsonDict]] = []
        for claim, deps_list in claim_deps:
            for d in deps_list:
                dep = _dependency_dict(d)
                if dep is None:
                    deps_rejected.append(f"{claim.id}: unparseable dependency")
                    continue
                dep_edges.append((claim, dep))
        if dep_edges:
            proposed: list[tuple[uuid.UUID, uuid.UUID, str]] = []
            for claim, d in dep_edges:
                kind = str(d.get("kind", "evidential"))
                if kind not in ("evidential", "research"):
                    deps_rejected.append(f"{claim.id}: bad dependency kind {kind!r}")
                    continue
                raw = str(d.get("claim_id", ""))
                try:
                    to_id = uuid.UUID(raw)
                except ValueError:
                    deps_rejected.append(f"{claim.id}: bad dependency claim_id {raw!r}")
                    continue
                if to_id == claim.id:
                    deps_rejected.append(f"{claim.id}: self-dependency")
                    continue
                target = await db.get(ORMClaim, to_id)
                if target is None:
                    deps_rejected.append(f"{claim.id}: dependency target {to_id} missing")
                    continue
                if kind == "evidential":
                    # §8.6: pending/invalid claims are not acting
                    # dependencies — the evidential edge to a
                    # non-current target is refused (research edges
                    # carry the explicit marker and are allowed)
                    ok = (
                        (
                            await db.execute(
                                text(
                                    "SELECT 1 FROM claim_assessment_heads h "
                                    "WHERE h.claim_id = :c "
                                    "AND h.config_snapshot_id = :s "
                                    "AND h.assessment_state = 'current'"
                                ),
                                {"c": to_id, "s": session.config_snapshot_id},
                            )
                        )
                        .first()
                        is not None
                    )
                    if not ok:
                        deps_rejected.append(
                            f"{claim.id}: evidential dependency on non-current claim {to_id}"
                        )
                        continue
                proposed.append((claim.id, to_id, kind))

            evidential = [(f, t) for (f, t, k) in proposed if k == "evidential"]
            if evidential:
                existing_rows = (
                    await db.execute(
                        text(
                            "SELECT from_claim_id, to_claim_id FROM claim_dependencies "
                            "WHERE kind = 'evidential'"
                        )
                    )
                ).all()
                existing: list[tuple[uuid.UUID, uuid.UUID]] = []
                for r in existing_rows:
                    f = r[0] if isinstance(r[0], uuid.UUID) else uuid.UUID(str(r[0]))
                    t = r[1] if isinstance(r[1], uuid.UUID) else uuid.UUID(str(r[1]))
                    existing.append((f, t))
                cyclic = find_evidential_cycles(existing, evidential)
                if cyclic:
                    # drop exactly the cyclic evidential edges
                    drop: set[tuple[uuid.UUID, uuid.UUID]] = set()
                    ev_idx = 0
                    for (f, t, k) in proposed:
                        if k == "evidential":
                            if ev_idx in cyclic:
                                drop.add((f, t))
                                deps_rejected.append(
                                    f"{f}: evidential edge to {t} rejected (would create a cycle)"
                                )
                            ev_idx += 1
                    proposed = [e for e in proposed if e[:2] not in drop]

            for f, t, k in proposed:
                exists = (
                    (
                        await db.execute(
                            select(ORMClaimDependency).where(
                                ORMClaimDependency.from_claim_id == f,
                                ORMClaimDependency.to_claim_id == t,
                                ORMClaimDependency.kind == k,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if exists is not None:
                    continue  # idempotent: the edge already exists
                db.add(
                    ORMClaimDependency(
                        id=uuid.uuid4(),
                        from_claim_id=f,
                        to_claim_id=t,
                        kind=k,
                        created_in_session=session.id,
                    )
                )
                counters["deps"] += 1
                if k == "evidential":
                    counters["deps_evidential"] = counters.get("deps_evidential", 0) + 1
            if deps_rejected:
                await audit.record(
                    AuditEventType.DEPENDENCY_EDGE_REJECTED,
                    session_id=session.id,
                    payload={"edges": deps_rejected[:20]},
                    public_summary=f"{len(deps_rejected)} dependency edge(s) rejected",
                )

        # 2. evidence (identity recomputed by the trusted host)
        linked: dict[uuid.UUID, list[ORMEvidence]] = {c.id: [] for c in claims}
        for row in evidence_ops:
            payload = dict(row.payload)
            index = int(payload.get("evidence_index", -1))
            claim_index = int(payload.get("claim_index", -1))
            relation = str(payload.get("relation", "supports"))
            if index < 0 or index >= len(evidence_records):
                problems.append(f"evidence staging rejected: index {index} out of range ({row.id})")
                continue
            if claim_index < 0 or claim_index >= len(claims):
                problems.append(f"evidence staging rejected: claim {claim_index} out of range ({row.id})")
                continue
            if relation not in ("supports", "counters"):
                problems.append(f"evidence staging rejected: bad relation {relation!r} ({row.id})")
                continue
            record = evidence_records[index]
            claim = claims[claim_index]
            kind = str(record.kind.value)
            identity, artifact_id = await self._identity_for(db, record, kind, env_hash)
            if artifact_id is None and kind in (
                "computation",
                "formal_check",
                "experiment_run",
                "local_observation",
            ):
                problems.append(f"evidence staging rejected: no artifact for {kind} ({row.id})")
                continue
            existing_ev = (
                (
                    await db.execute(
                        select(ORMEvidence).where(
                            ORMEvidence.claim_id == claim.id,
                            ORMEvidence.evidence_kind == kind,
                            ORMEvidence.identity_hash == identity,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing_ev is not None:
                ev: ORMEvidence = existing_ev
                counters["deduped"] += 1
            else:
                # source provenance (source_assertion / quote_integrity):
                # the host-verified sources row the assertion was read
                # from — drives the source-independence groups (§11.3)
                rec_source_id: uuid.UUID | None = None
                if kind in ("source_assertion", "quote_integrity"):
                    raw_sid = getattr(record, "source_id", None)
                    if raw_sid:
                        try:
                            rec_source_id = uuid.UUID(str(raw_sid))
                        except ValueError:
                            rec_source_id = None
                    if rec_source_id is None:
                        problems.append(
                            f"evidence staging rejected: no source provenance for {kind} ({row.id})"
                        )
                        continue
                ev = ORMEvidence(
                    id=uuid.uuid4(),
                    claim_id=claim.id,
                    relation=relation,
                    evidence_kind=kind,
                    identity_hash=identity,
                    scope=claim_scopes.get(claim.id, {}),
                    source_id=rec_source_id,
                    chunk_id=getattr(record, "chunk_id", None),
                    observation_artifact_id=artifact_id,
                    environment_manifest_id=env.id
                    if kind in ("experiment_run", "local_observation")
                    else None,
                    created_in_session=session.id,
                )
                db.add(ev)
                counters["added"] += 1
            if ev not in linked[claim.id]:
                linked[claim.id].append(ev)

        # 3. assessment per claim (rules engine is the only producer, §3.7)
        for claim in claims:
            all_evidence = list(
                (
                    await db.execute(select(ORMEvidence).where(ORMEvidence.claim_id == claim.id))
                )
                .scalars()
                .all()
            )
            try:
                ok = await self._assess(
                    db, audit, session, claim, claim_scopes.get(claim.id, {}), all_evidence, now
                )
            except RuleValidationError as exc:
                problems.append(f"assessment rejected for {claim.id}: {exc}")
                continue
            if ok:
                counters["assessments"] += 1

        return MemoryApplyResult(
            claims_created=counters["created"],
            claims_reused=counters["reused"],
            evidence_added=counters["added"],
            evidence_deduped=counters["deduped"],
            assessments=counters["assessments"],
            dependencies_added=counters["deps"],
            dependencies_evidential_added=counters.get("deps_evidential", 0),
            dependencies_rejected=tuple(deps_rejected),
            problems=tuple(problems),
        )

    async def _identity_for(
        self, db: AsyncSession, record: Any, kind: str, env_hash: str
    ) -> tuple[str, uuid.UUID | None]:
        """Recompute (identity, artifact_id) for one observation record.
        The identity hash is content/provenance (§14.3) — for computation
        it folds in the artifact's sha256, the exact input and the tool
        fingerprint."""
        payload = dict(record.payload or {})
        artifact = await self._ensure_artifact(db, payload)
        if kind == "computation":
            result_text = str(payload.get("stdout", ""))
            inputs = str(payload.get("code", ""))
            identity = computation_identity(result_text, inputs, artifact.sha256)
        elif kind in ("local_observation", "experiment_run"):
            content = str(payload.get("content", payload.get("entries", "")))
            identity = local_observation_identity(content, env_hash)
        elif kind in ("source_assertion", "quote_integrity"):
            # §14.3: provenance identity over the ORIGINAL source content
            # hash + chunk + kind — stable across re-fetches, independent
            # of the (budget-truncated) context copy in the payload
            identity = source_assertion_identity(
                str(payload.get("original_sha256", "")),
                str(payload.get("chunk_id", "chunk-0")),
                kind,
            )
        else:
            identity = observation_artifact_hash(payload)
        return identity, artifact.id

    async def _ensure_artifact(self, db: AsyncSession, payload: JsonDict) -> ORMArtifact:
        """The observation artifact (content-addressed registry row)."""
        sha = observation_artifact_hash(payload)
        existing = (
            (await db.execute(select(ORMArtifact).where(ORMArtifact.sha256 == sha)))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
        artifact = ORMArtifact(
            id=uuid.uuid4(),
            sha256=sha,
            size=len(canonical_sha256(payload)),
            mime="application/noezema+json",
            origin="session_workspace",
            trust_class="session_workspace",
        )
        db.add(artifact)
        await db.flush()
        return artifact

    async def _environment_manifest(
        self, db: AsyncSession, env_hash: str, fields: JsonDict
    ) -> ORMEnvironmentManifest:
        """The environment manifest, content-addressed by its FULL §14
        field set (T4.6): the same environment is ONE row regardless of
        how many sessions ran in it."""
        existing = (
            (
                await db.execute(
                    select(ORMEnvironmentManifest).where(
                        ORMEnvironmentManifest.manifest_hash == env_hash
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
        env = ORMEnvironmentManifest(
            id=uuid.uuid4(),
            protocol_hash=fields["protocol_hash"],
            implementation_hash=fields["implementation_hash"],
            code_lineage=fields["code_lineage"],
            dataset_hash=fields["dataset_hash"],
            dataset_lineage=fields["dataset_lineage"],
            toolchain_hash=fields["toolchain_hash"],
            dependency_hash=fields["dependency_hash"],
            runtime_hash=fields["runtime_hash"],
            hardware_hash=fields["hardware_hash"],
            seed=fields["seed"],
            data_order_hash=fields["data_order_hash"],
            normalizer_version=fields["normalizer_version"],
            manifest_hash=env_hash,
        )
        db.add(env)
        await db.flush()
        return env

    async def _assess(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        claim: ORMClaim,
        claim_scope: JsonDict,
        all_evidence: list[ORMEvidence],
        now: datetime,
    ) -> bool:
        """One deterministic assessment + head upsert (same transaction)."""
        rule = self.rule(claim.claim_type)
        # §8.7.3 (T4.6): the versioned environment-independence
        # snapshot over the claim's environment manifests — the
        # assessment fixes it and counts distinct groups, not hashes.
        # Evidence without a tracked environment is the conservative
        # untracked group, never a fake-independent one.
        env_snapshot_id, env_mapping = await build_environment_independence_snapshot(
            db, claim_id=claim.id, rules_hash=self._rules_hash
        )
        # §11.3 (T4.7): the versioned source-independence snapshot over
        # the claim's sources — source-based evidence gets the SOURCE
        # group (the provenance of the data), execution evidence gets the
        # environment group, neither → the conservative untracked group.
        src_snapshot_id, src_mapping = await build_source_independence_snapshot(
            db, claim_id=claim.id
        )
        # §8.7.4 (T4.8): a counters evidence with a VALID resolution
        # does not cap the grade (counterevidence_unresolved == false)
        resolved_counter_ids = {
            row[0]
            for row in (
                await db.execute(
                    text(
                        "SELECT evidence_id FROM counterevidence_resolutions "
                        "WHERE valid AND evidence_id = ANY(:ids)"
                    ),
                    {"ids": [e.id for e in all_evidence]},
                )
            ).all()
        }
        group_by_manifest = {mid: g for mid, (g, _r) in env_mapping.items()}
        relation_by_manifest = {mid: r for mid, (_g, r) in env_mapping.items()}
        evs = [
            EvaluatedEvidence(
                identity_hash=e.identity_hash,
                kind=e.evidence_kind,
                relation=e.relation,
                scope=dict(e.scope or {}),
                independence_group=(
                    src_mapping[e.source_id][0]
                    if e.source_id is not None and e.source_id in src_mapping
                    else (
                        group_by_manifest.get(e.environment_manifest_id, UNTRACKED_GROUP)
                        if e.environment_manifest_id is not None
                        else UNTRACKED_GROUP
                    )
                ),
                env_relation=(
                    relation_by_manifest.get(e.environment_manifest_id, "none")
                    if e.environment_manifest_id is not None
                    else "none"
                ),
                resolved=e.id in resolved_counter_ids,
            )
            for e in all_evidence
        ]
        result = evaluate(
            claim.claim_type,
            rule,
            claim_scope=claim_scope,
            evidences=evs,
            has_as_of=claim.as_of is not None,
        )

        head = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead).where(
                        ORMClaimAssessmentHead.claim_id == claim.id,
                        ORMClaimAssessmentHead.config_snapshot_id == self.snapshot.id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if head is not None and head.current_assessment_id is not None:
            prev = await db.get(ORMClaimAssessment, head.current_assessment_id)
            if prev is not None and prev.valid:
                prev.valid = False
                prev.invalidation_reason = "superseded"

        assessment = ORMClaimAssessment(
            id=uuid.uuid4(),
            claim_id=claim.id,
            effective_grade=result.grade.value,
            epistemic_status=result.epistemic_status.value,
            rules_version=RULES_ENGINE_VERSION,
            rules_hash=self._rules_hash,
            environment_independence_snapshot_id=env_snapshot_id,
            source_independence_snapshot_id=src_snapshot_id,
            evidence_set_hash=_evidence_set_hash(all_evidence),
            assessed_scope=dict(claim_scope),
            confidence=result.confidence,
            valid=True,
            created_in_session=session.id,
        )
        db.add(assessment)
        await db.flush()

        for e in all_evidence:
            db.add(
                ORMAssessmentEvidence(
                    assessment_id=assessment.id,
                    evidence_id=e.id,
                    role="counter" if e.relation == "counters" else "support",
                )
            )

        if head is None:
            head = ORMClaimAssessmentHead(
                claim_id=claim.id,
                config_snapshot_id=self.snapshot.id,
                assessment_state=AssessmentState.CURRENT.value,
                current_assessment_id=assessment.id,
                epistemic_status=result.epistemic_status.value,
                prepared_by="session",
            )
            db.add(head)
        else:
            head.assessment_state = AssessmentState.CURRENT.value
            head.current_assessment_id = assessment.id
            head.epistemic_status = result.epistemic_status.value
            head.prepared_by = "session"

        # freshness: reverify_after is derived, not a mutable clock
        claim.reverify_after = reverify_after(result, claim.as_of, now)
        claim.freshness_status = FreshnessStatus.FRESH.value

        await db.flush()
        await audit.record(
            AuditEventType.CLAIM_ASSESSED,
            session_id=session.id,
            payload={
                "claim_id": str(claim.id),
                "assessment_id": str(assessment.id),
                "grade": result.grade.value,
                "epistemic_status": result.epistemic_status.value,
                "confidence": result.confidence,
                "reasons": list(result.reasons),
                "rules_hash": self._rules_hash,
                "environment_independence_snapshot_id": (
                    str(env_snapshot_id) if env_snapshot_id is not None else None
                ),
                "source_independence_snapshot_id": (
                    str(src_snapshot_id) if src_snapshot_id is not None else None
                ),
            },
            public_summary=f"claim assessed: {result.grade.value}/{result.epistemic_status.value}",
        )
        return True

    # ── query path (§14.1: pointer equality, not activation_state) ───────

    async def claim_view(self, db: AsyncSession, claim_id: uuid.UUID) -> ClaimView | None:
        """Resolve one claim through the effective config snapshot."""
        claim = await db.get(ORMClaim, claim_id)
        if claim is None:
            return None
        head_row = (
            (
                await db.execute(
                    select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == GLOBAL_SCOPE)
                )
            )
            .scalars()
            .first()
        )
        if head_row is None:
            return None
        head = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead).where(
                        ORMClaimAssessmentHead.claim_id == claim_id,
                        ORMClaimAssessmentHead.config_snapshot_id == head_row.active_config_snapshot_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if head is None:
            return None
        assessment = None
        if head.current_assessment_id is not None:
            assessment = await db.get(ORMClaimAssessment, head.current_assessment_id)
        evs: list[ORMEvidence] = []
        roles: dict[uuid.UUID, str] = {}
        if assessment is not None:
            links = (
                (
                    await db.execute(
                        select(ORMAssessmentEvidence).where(
                            ORMAssessmentEvidence.assessment_id == assessment.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for link in links:
                ev = await db.get(ORMEvidence, link.evidence_id)
                if ev is not None:
                    evs.append(ev)
                    roles[ev.id] = link.role
        evidence = [
            ClaimViewEvidence(e.id, e.evidence_kind, e.relation, roles.get(e.id, "support"), e.identity_hash)
            for e in evs
            if e.relation == "supports"
        ]
        counterevidence = [
            ClaimViewEvidence(e.id, e.evidence_kind, e.relation, roles.get(e.id, "counter"), e.identity_hash)
            for e in evs
            if e.relation == "counters"
        ]
        return ClaimView(
            claim_id=claim.id,
            statement=claim.statement,
            claim_type=claim.claim_type,
            assessment_state=AssessmentState(head.assessment_state),
            epistemic_status=EpistemicStatus(head.epistemic_status) if head.epistemic_status else None,
            grade=EffectiveGrade(assessment.effective_grade) if assessment else None,
            confidence=assessment.confidence if assessment else None,
            freshness=self.freshness_status(claim, datetime.now(UTC)),
            valid_from=claim.valid_from,
            valid_to=claim.valid_to,
            as_of=claim.as_of,
            reverify_after=claim.reverify_after,
            evidence=evidence,
            counterevidence=counterevidence,
        )

    async def pending_invalid_claims(self, db: AsyncSession, limit: int = 100) -> list[ORMClaim]:
        """Claims whose effective head is pending/invalid — never current
        evidence, never a dependency (§8.6)."""
        head_row = (
            (
                await db.execute(
                    select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == GLOBAL_SCOPE)
                )
            )
            .scalars()
            .first()
        )
        if head_row is None:
            return []
        rows = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead)
                    .where(
                        ORMClaimAssessmentHead.config_snapshot_id == head_row.active_config_snapshot_id,
                        ORMClaimAssessmentHead.assessment_state.in_(
                            (AssessmentState.PENDING.value, AssessmentState.INVALID.value)
                        ),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        claims = []
        for head in rows:
            claim = await db.get(ORMClaim, head.claim_id)
            if claim is not None:
                claims.append(claim)
        return claims


def _evidence_set_hash(evidence: list[ORMEvidence]) -> str:
    return canonical_sha256(
        sorted(f"{e.evidence_kind}:{e.relation}:{e.identity_hash}" for e in evidence)
    )
