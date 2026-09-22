"""Unit (DB): memory service — lifecycle, dedup, freshness, claim view
(T3.6, T3.7, §8.2, §14.1)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import (
    AssessmentState,
    EffectiveGrade,
    EpistemicStatus,
    FreshnessStatus,
)
from packages.domain.models.memory import ORMClaim, ORMClaimAssessmentHead, ORMEvidence
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService, ReserveLimits
from packages.domain.services.staging import StagingService
from packages.memory.evidence import (
    manifest_content_hash,
    session_environment_fields,
)
from packages.memory.rules_engine import RuleValidationError
from packages.memory.service import MemoryService

pytestmark = [pytest.mark.unit]


async def _seed_session(engine: AsyncEngine) -> tuple[async_sessionmaker, uuid.UUID]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) "
                "VALUES (:id, 'committing', (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid},
        )
    async with factory() as db:
        orm = await db.get(ORMSession, sid)
        assert orm is not None
    return factory, sid


async def _snapshot(engine: AsyncEngine) -> ORMConfigSnapshot:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
        assert snap is not None
        return snap


async def _record_staging(
    engine: AsyncEngine,
    sid: uuid.UUID,
    ops: list[tuple[str, JsonDict]],
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        session = await db.get(ORMSession, sid)
        assert session is not None
        staging = StagingService(HostReserveService(ReserveLimits(32, 16, 64, 4)))
        for op, payload in ops:
            if op == "claim":
                await staging.record(db, AuditService(db), session, "claim", payload, proposed_claims=1)
            else:
                await staging.record(db, AuditService(db), session, "evidence", payload, proposed_evidence=1)


def _comp_record(payload: JsonDict) -> Any:
    from packages.domain.models.enums import EvidenceKind
    from packages.domain.schemas.evidence import EvidenceRecord

    return EvidenceRecord(
        kind=EvidenceKind.COMPUTATION,
        identity_hash="h" * 32,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_apply_claim_and_evidence_creates_current_head(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    await _record_staging(
        engine,
        sid,
        [
            ("claim", {"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]

    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)

    assert result.claims_created == 1
    assert result.evidence_added == 1
    assert result.assessments == 1
    assert result.problems == ()

    async with factory() as db:
        view = await memory.claim_view(db, (await db.execute(select(ORMClaim))).scalars().first().id)
        assert view is not None
        assert view.assessment_state is AssessmentState.CURRENT
        assert view.epistemic_status is EpistemicStatus.SUPPORTED
        assert view.grade is EffectiveGrade.E2
        assert view.freshness is FreshnessStatus.FRESH
        assert len(view.evidence) == 1
        assert view.evidence[0].kind == "computation"


@pytest.mark.asyncio
async def test_same_claim_reused_and_evidence_deduped(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    ops = [
        ("claim", {"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}),
        ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
    ]
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]

    # first apply
    await _record_staging(engine, sid, ops)
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        r1 = await memory.apply_claim_staging(db, AuditService(db), session, records)
    assert r1.claims_created == 1 and r1.evidence_added == 1

    # second session, same claim + same evidence → reused + deduped
    _sid2_factory, sid2 = await _seed_session(engine)
    await _record_staging(engine, sid2, ops)
    async with factory() as db, transaction(db):
        session2 = await db.get(ORMSession, sid2)
        assert session2 is not None
        r2 = await memory.apply_claim_staging(db, AuditService(db), session2, records)
    assert r2.claims_created == 0 and r2.claims_reused == 1
    assert r2.evidence_added == 0 and r2.evidence_deduped == 1

    # exactly one claim and one evidence row in the whole DB
    async with factory() as db:
        claims = (await db.execute(select(ORMClaim))).scalars().all()
        evidence = (await db.execute(select(ORMEvidence))).scalars().all()
        assert len(claims) == 1
        assert len(evidence) == 1


@pytest.mark.asyncio
async def test_head_lifecycle_invariant_current_has_assessment(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    await _record_staging(
        engine, sid, [("claim", {"statement": "no evidence claim", "claim_type": "self_model", "scope": {}})]
    )
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        await memory.apply_claim_staging(db, AuditService(db), session, [])

    # a claim with no evidence is still assessed (hypothesis E0) and the
    # head is current with a non-null assessment + epistemic status
    async with factory() as db:
        heads = (await db.execute(select(ORMClaimAssessmentHead))).scalars().all()
        assert len(heads) == 1
        assert heads[0].assessment_state == "current"
        assert heads[0].current_assessment_id is not None
        assert heads[0].epistemic_status == "hypothesis"


def _source_assertion_record(source_id: str, original_sha256: str) -> Any:
    """A host adapter source_assertion record (T7.8 payload convention)."""
    from packages.domain.models.enums import EvidenceKind
    from packages.domain.schemas.evidence import EvidenceRecord

    return EvidenceRecord(
        kind=EvidenceKind.SOURCE_ASSERTION,
        identity_hash="i" * 32,
        payload={
            "original_sha256": original_sha256,
            "chunk_id": "chunk-0",
            "url": "https://example.com/page",
        },
        source_id=source_id,
        chunk_id="chunk-0",
    )


async def _seed_source(engine: AsyncEngine) -> uuid.UUID:
    """One durable host-verified source row (source provenance, §11.3)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sources (id, source_type, canonical_uri, retrieved_at, "
                "content_hash) VALUES (:id, 'external_url', :uri, now(), :hash)"
            ),
            {"id": str(sid), "uri": "https://example.com/page", "hash": "h" * 64},
        )
    return sid


@pytest.mark.asyncio
async def test_rules_rejected_claim_aborts_apply_no_headless_claim(migrated_db: Any) -> None:
    """T7.9 invariant (EVAL-3b post-mortem P.2, §14.1): a claim whose
    assessment the rules engine rejects is NOT committed. The fail-closed
    action at the commit boundary is to abort the whole apply (the fenced
    transaction rolls back) — the DB contains no claim, no evidence and
    no head, never a headless claim."""
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)
    source_id = await _seed_source(engine)

    # The exact EVAL-3b case: a local_observation claim supported by
    # source_assertion evidence — a kind the claim-type rule disallows.
    await _record_staging(
        engine,
        sid,
        [
            (
                "claim",
                {
                    "statement": "the page says 14%",
                    "claim_type": "local_observation",
                    "scope": {"url": "https://example.com/page"},
                },
            ),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    records = [_source_assertion_record(str(source_id), "o" * 64)]

    memory = MemoryService(snap)
    with pytest.raises(RuleValidationError):
        async with factory() as db, transaction(db):
            session = await db.get(ORMSession, sid)
            assert session is not None
            await memory.apply_claim_staging(db, AuditService(db), session, records)

    async with factory() as db:
        claims = (await db.execute(select(ORMClaim))).scalars().all()
        heads = (await db.execute(select(ORMClaimAssessmentHead))).scalars().all()
        evidence = (await db.execute(select(ORMEvidence))).scalars().all()
        assert claims == []
        assert heads == []
        assert evidence == []


@pytest.mark.asyncio
async def test_headless_claim_is_never_reused_by_dedup(migrated_db: Any) -> None:
    """T7.9 (EVAL-3b post-mortem P.2, §14.1): a legacy headless claim
    (created but never assessed — the poison of the old
    problems-and-continue path) must NOT be reused by dedup: the apply
    creates a fresh claim and assesses it, so the committed claim has a
    head. A claim WITH a head is still reused (control)."""
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)
    source_id = await _seed_source(engine)

    # A legacy headless claim: same statement/type as the upcoming
    # proposal, but no claim_assessment_heads row (never assessed).
    legacy_id = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, search_statements) "
                "VALUES (:id, 'external fact X', 'external_fact', '[]')"
            ),
            {"id": str(legacy_id)},
        )

    await _record_staging(
        engine,
        sid,
        [
            (
                "claim",
                {
                    "statement": "external fact X",
                    "claim_type": "external_fact",
                    "scope": {"event": "x"},
                },
            ),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    records = [_source_assertion_record(str(source_id), "o" * 64)]

    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)

    # the headless legacy claim was NOT reused — a fresh claim was made
    assert result.claims_created == 1
    assert result.claims_reused == 0
    assert result.assessments == 1

    async with factory() as db:
        claims = (await db.execute(select(ORMClaim))).scalars().all()
        assert len(claims) == 2
        fresh = [c for c in claims if c.id != legacy_id]
        assert len(fresh) == 1
        heads = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead).where(
                        ORMClaimAssessmentHead.claim_id == fresh[0].id,
                        ORMClaimAssessmentHead.config_snapshot_id == snap.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(heads) == 1
        assert heads[0].assessment_state == "current"
        assert heads[0].current_assessment_id is not None
        # the legacy headless claim still has no head (it was not healed
        # by reuse — it is invisible garbage, not dedup input)
        legacy_heads = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead).where(
                        ORMClaimAssessmentHead.claim_id == legacy_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert legacy_heads == []

    # control: the fresh claim now HAS a head — a repeat apply from a
    # new session reuses it (the legacy headless one stays excluded)
    _sid2_factory, sid2 = await _seed_session(engine)
    await _record_staging(
        engine,
        sid2,
        [
            (
                "claim",
                {
                    "statement": "external fact X",
                    "claim_type": "external_fact",
                    "scope": {"event": "x"},
                },
            ),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    async with factory() as db, transaction(db):
        session2 = await db.get(ORMSession, sid2)
        assert session2 is not None
        r2 = await memory.apply_claim_staging(db, AuditService(db), session2, records)
    assert r2.claims_created == 0
    assert r2.claims_reused == 1


@pytest.mark.asyncio
async def test_validate_claim_proposal_bounces_rules_rejected_claim(migrated_db: Any) -> None:
    """T7.9 (EVAL-3b post-mortem P.2): the pre-commit check of the
    curator's proposal — the rules engine's role/kind validation runs
    before any staging op is recorded. The EVAL-3b case (a
    local_observation claim supported by source_assertion) is bounced;
    an admissible proposal (external_fact + source_assertion) passes;
    an unknown claim type is bounced."""
    _url, engine = migrated_db
    snap = await _snapshot(engine)
    memory = MemoryService(snap)

    record = _source_assertion_record("1" * 36, "o" * 64)
    link = [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}]

    # the EVAL-3b case: local_observation does not allow source_assertion
    problems = memory.validate_claim_proposal(
        [{"statement": "S", "claim_type": "local_observation", "scope": {}}],
        link,
        [record],
    )
    assert len(problems) == 1
    assert "not allowed" in problems[0]

    # admissible: external_fact allows source_assertion
    assert (
        memory.validate_claim_proposal(
            [{"statement": "S", "claim_type": "external_fact", "scope": {}}],
            link,
            [record],
        )
        == []
    )

    # unknown claim type is bounced
    problems = memory.validate_claim_proposal(
        [{"statement": "S", "claim_type": "bogus_type", "scope": {}}], [], []
    )
    assert len(problems) == 1
    assert "unknown claim_type" in problems[0]


@pytest.mark.asyncio
async def test_env_manifest_hash_is_content_addressed(migrated_db: Any) -> None:
    # §8.7.3 (T4.6): the manifest is content-addressed over the FULL
    # field set — the session identity is NOT part of it (two sessions
    # in the same environment share one manifest)
    a = manifest_content_hash(
        session_environment_fields(protocol_hash="p1", tool_schema_hash="t1", seed=42)
    )
    b = manifest_content_hash(
        session_environment_fields(protocol_hash="p1", tool_schema_hash="t1", seed=42)
    )
    c = manifest_content_hash(
        session_environment_fields(protocol_hash="p2", tool_schema_hash="t1", seed=42)
    )
    assert a == b
    assert a != c


@pytest.mark.asyncio
async def test_commit_stores_due_when_reverify_deadline_already_passed(migrated_db: Any) -> None:
    """T7.27 (EVAL-4d, §8.6/T3.7): a claim committed with an as_of in
    the past derives reverify_after in the past — the stored
    freshness_status must be 'due' at the commit itself, not the
    unconditional 'fresh' (which kept the claim "fresh" until an
    activation flip triggered a reassessment — a process that in
    EVAL-4d never ran, so the gate saw 0/28 due on 22/28 overdue).

    Control: the same claim type with a recent as_of (deadline still
    in the future) commits as 'fresh'."""
    from datetime import UTC, datetime, timedelta

    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)
    source_id = await _seed_source(engine)

    past_as_of = "1957-10-04T19:28:34+00:00"  # the EVAL-4d Sputnik-1 case
    recent_as_of = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await _record_staging(
        engine,
        sid,
        [
            (
                "claim",
                {
                    "statement": "Спутник-1 запущен 4 октября 1957",
                    "claim_type": "temporal_fact",
                    "as_of": past_as_of,
                    "scope": {},
                },
            ),
            (
                "claim",
                {
                    "statement": "Ставка ЦБ РФ на текущую дату",
                    "claim_type": "temporal_fact",
                    "as_of": recent_as_of,
                    "scope": {},
                },
            ),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
            ("evidence", {"evidence_index": 1, "claim_index": 1, "relation": "supports"}),
        ],
    )
    records = [
        _source_assertion_record(str(source_id), "o" * 64),
        _source_assertion_record(str(source_id), "p" * 64),
    ]

    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)

    assert result.claims_created == 2
    assert result.assessments == 2
    assert result.problems == ()

    async with factory() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT statement, as_of, reverify_after, freshness_status "
                        "FROM claims ORDER BY statement"
                    )
                )
            )
            .all()
        )
        assert len(rows) == 2
        by_statement = {r[0]: r for r in rows}
        # the past-as_of claim: reverify_after = as_of + 30d (past) and
        # the stored status is DUE — the rule, not a write-time constant
        past = by_statement["Спутник-1 запущен 4 октября 1957"]
        assert past[2] is not None and past[2] < datetime.now(UTC)
        assert past[2] == past[1] + timedelta(days=30)
        assert past[3] == "due"
        # control: recent as_of → deadline in the future → fresh
        recent = by_statement["Ставка ЦБ РФ на текущую дату"]
        assert recent[2] is not None and recent[2] > datetime.now(UTC)
        assert recent[3] == "fresh"
        # and the claim view (read path) agrees with the stored value
        claims = (
            (
                await db.execute(select(ORMClaim).order_by(ORMClaim.statement))
            )
            .scalars()
            .all()
        )
        assert [c.statement for c in claims] == list(by_statement)
        past_view = await memory.claim_view(db, claims[0].id)
        recent_view = await memory.claim_view(db, claims[1].id)
        assert past_view is not None and past_view.freshness is FreshnessStatus.DUE
        assert recent_view is not None and recent_view.freshness is FreshnessStatus.FRESH


@pytest.mark.asyncio
async def test_apply_claim_staging_uses_recording_order_not_uuid_order(migrated_db: Any) -> None:
    """T7.24 (EVAL-4 abort 2026-09-21 — the root cause).

    The commit boundary must read the recorded staging ops in RECORDING
    (proposal) order. The EVAL-4 shape: two claim ops recorded in the
    order [local_observation, computed_result] (the evidence links refer
    to that order via ``claim_index``), but with the UUID ``id`` of the
    SECOND claim SMALLER than the first — and, as always inside the long
    phase-1 transaction, one shared ``created_at``. The legacy ordering
    ``(created_at, id)`` then puts the computed_result claim FIRST, so
    ``claim_index=0`` (the local_observation evidence) is linked to the
    computed_result claim → ``RuleValidationError`` in the fenced final
    transaction → session left committing with a prepared attempt.
    With the durable ``seq`` the apply pairs each claim with the
    evidence the curator proposed: both claims commit with a head."""
    import json
    from datetime import UTC, datetime

    from packages.domain.canonical import canonical_sha256
    from packages.domain.models.enums import EvidenceKind
    from packages.domain.schemas.evidence import EvidenceRecord

    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    big_id = uuid.UUID("f0000000-0000-4000-8000-000000000001")
    small_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
    rows = [
        # seq = the proposal order; id = the scrambled UUID order
        (
            big_id,
            0,
            "claim",
            {
                "statement": "Файл notes/reading.md содержит три непустые строки.",
                "claim_type": "local_observation",
                "scope": {},
            },
        ),
        (
            small_id,
            1,
            "claim",
            {
                "statement": "Количество непустых строк в файле notes/reading.md равно 3.",
                "claim_type": "computed_result",
                "scope": {},
            },
        ),
        (uuid.uuid4(), 2, "evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        (uuid.uuid4(), 3, "evidence", {"evidence_index": 1, "claim_index": 1, "relation": "supports"}),
    ]
    phase1_start = datetime.now(UTC)
    async with factory() as db, db.begin():
        for row_id, seq, op, payload in rows:
            await db.execute(
                text(
                    "INSERT INTO session_staging "
                    "(id, session_id, op, payload, payload_hash, schema_version, seq, created_at) "
                    "VALUES (:id, :s, :op, CAST(:p AS jsonb), :h, 1, :seq, :ts)"
                ),
                {
                    "id": row_id,
                    "s": sid,
                    "op": op,
                    "p": json.dumps(payload, ensure_ascii=False),
                    "h": canonical_sha256(payload),
                    "seq": seq,
                    "ts": phase1_start,  # constant now() of the phase-1 txn
                },
            )

    records = [
        # index 0: the workspace.read observation (local_observation)
        EvidenceRecord(
            kind=EvidenceKind.LOCAL_OBSERVATION,
            identity_hash="l" * 32,
            payload={"path": "notes/reading.md", "content": "The Little Prince\nDune\nThe Master and Margarita\n"},
        ),
        # index 1: the python.execute observation (computation)
        _comp_record({"code": "print(3)", "stdout": "3", "exit_code": 0}),
    ]

    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)

    assert result.claims_created == 2
    assert result.assessments == 2
    assert result.problems == ()

    async with factory() as db:
        by_statement = {
            r.statement: r for r in (await db.execute(select(ORMClaim))).scalars().all()
        }
        obs_claim = by_statement["Файл notes/reading.md содержит три непустые строки."]
        comp_claim = by_statement["Количество непустых строк в файле notes/reading.md равно 3."]
        # each claim got exactly the evidence the curator PROPOSED
        obs_kinds = set(
            (
                await db.execute(
                    text("SELECT evidence_kind FROM evidence WHERE claim_id = :c"),
                    {"c": str(obs_claim.id)},
                )
            )
            .scalars()
            .all()
        )
        comp_kinds = set(
            (
                await db.execute(
                    text("SELECT evidence_kind FROM evidence WHERE claim_id = :c"),
                    {"c": str(comp_claim.id)},
                )
            )
            .scalars()
            .all()
        )
        assert obs_kinds == {"local_observation"}
        assert comp_kinds == {"computation"}
        # both claims have a head under this session's snapshot
        heads = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead).where(
                        ORMClaimAssessmentHead.config_snapshot_id == snap.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {h.claim_id for h in heads} == {obs_claim.id, comp_claim.id}
        assert all(h.current_assessment_id is not None for h in heads)
