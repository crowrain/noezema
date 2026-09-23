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


async def _seed_session(
    engine: AsyncEngine, question_text: str | None = None
) -> tuple[async_sessionmaker, uuid.UUID]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        if question_text is not None:
            # T7.32 (ADR-0017): the session's QUESTION is the trusted
            # anchor for the claim's reference date and its date
            # anchor (derive_claim_as_of)
            qid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO questions (id, text, origin, priority, state) "
                    "VALUES (:id, :t, 'seeded', 1, 'candidate')"
                ),
                {"id": qid, "t": question_text},
            )
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, "
                    "question_id) "
                    "VALUES (:id, 'committing', "
                    "(SELECT id FROM config_snapshots LIMIT 1), :q)"
                ),
                {"id": sid, "q": qid},
            )
        else:
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
        # T7.32 (ADR-0017): the session has NO question — no date
        # anchor to derive (anchor none, the model's as_of stands or
        # there is no date at all): the claim is about a FIXED point
        # → no reverify deadline → evergreen (the pre-T7.32
        # expectation was FRESH: every claim got a deadline counted
        # from now/as_of — the "formal theorem stale after 90 days"
        # class the ADR removes)
        assert view.freshness is FreshnessStatus.EVERGREEN
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
async def test_commit_deadline_exists_only_for_present_anchored_claims(
    migrated_db: Any,
) -> None:
    """T7.32 (ADR-0017): the reverify deadline exists ONLY for a claim
    about the PRESENT — the session's question anchors the date
    RELATIVELY («на текущую дату»): reverify_after = the verification
    moment + the volatility window (future → fresh). A claim about a
    FIXED point (a DATELESS question keeps the model's as_of — the
    Sputnik-1 case that was DUE FOREVER under the pre-T7.32 rule,
    EVAL-4d/T7.31) has NO deadline at the commit itself:
    reverify_after is NULL, freshness ``evergreen``.

    (The pre-T7.32 version of this test committed the same
    past-as_of claim and expected 'due': reverify_after was counted
    FROM as_of. The expectation is corrected per ADR-0017 — the
    deadline is no longer counted from as_of at all; a fixed-point
    claim is immutable and valid forever, and the relative claim is
    fresh until the verification moment + 30d.)"""
    from datetime import UTC, datetime, timedelta

    _url, engine = migrated_db
    snap = await _snapshot(engine)
    source_id = await _seed_source(engine)

    # session 1: a DATELESS question (the Sputnik-1 shape) — the
    # model's as_of (1957) stands as the reference date; anchor none
    factory, sid_dateless = await _seed_session(
        engine, question_text="Когда был запущен Спутник-1?"
    )
    past_as_of = "1957-10-04T19:28:34+00:00"  # the EVAL-4d Sputnik-1 case
    await _record_staging(
        engine,
        sid_dateless,
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
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    records = [_source_assertion_record(str(source_id), "o" * 64)]
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid_dateless)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)
    assert result.claims_created == 1 and result.assessments == 1
    assert result.problems == ()

    # session 2: a RELATIVE question («на текущую дату») — the session
    # start (host clock) is the reference date; anchor relative → the
    # claim about the present gets the deadline
    recent_as_of = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    factory2, sid_relative = await _seed_session(
        engine, question_text="Какова ключевая ставка ЦБ РФ на текущую дату?"
    )
    await _record_staging(
        engine,
        sid_relative,
        [
            (
                "claim",
                {
                    "statement": "Ставка ЦБ РФ составляет 14 процентов",
                    "claim_type": "temporal_fact",
                    "as_of": recent_as_of,
                    "scope": {},
                },
            ),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    records2 = [_source_assertion_record(str(source_id), "p" * 64)]
    async with factory2() as db, transaction(db):
        session = await db.get(ORMSession, sid_relative)
        assert session is not None
        result2 = await memory.apply_claim_staging(db, AuditService(db), session, records2)
    assert result2.claims_created == 1 and result2.assessments == 1
    assert result2.problems == ()

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
        # the fixed-point claim (dateless question, model's as_of
        # 1957): NO deadline — reverify_after NULL, evergreen (never
        # "due forever" again, ADR-0017)
        sputnik = by_statement["Спутник-1 запущен 4 октября 1957"]
        assert sputnik[2] is None
        assert sputnik[3] == "evergreen"
        # the present claim (relative question): reverify_after = the
        # verification moment + 30d → future → fresh
        rate = by_statement["Ставка ЦБ РФ составляет 14 процентов"]
        assert rate[2] is not None and rate[2] > datetime.now(UTC)
        assert rate[3] == "fresh"
        # and the claim view (read path) agrees with the stored value
        claims = (
            (
                await db.execute(select(ORMClaim).order_by(ORMClaim.statement))
            )
            .scalars()
            .all()
        )
        assert [c.statement for c in claims] == list(by_statement)
        sputnik_view = await memory.claim_view(db, claims[0].id)
        rate_view = await memory.claim_view(db, claims[1].id)
        assert (
            sputnik_view is not None
            and sputnik_view.freshness is FreshnessStatus.EVERGREEN
        )
        assert rate_view is not None and rate_view.freshness is FreshnessStatus.FRESH


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


# ─── T7.34 (ADR-0018): reverify of an existing claim ────────────────────────
#
# The scenario runs through MemoryService (not direct inserts): the anchor
# session CREATES a claim, the follow-up session REVERIFIES it by the
# host-issued id. The reverify record is the fresh assessment row bound
# to the follow-up session — independent of whether any evidence is new
# (a re-fetched identical source reuses the anchor's evidence row by
# identity — the trap ADR-0018 documents).


async def _anchor_comp_claim(
    engine: AsyncEngine, question: str | None = None
) -> tuple[async_sessionmaker, uuid.UUID, uuid.UUID]:
    """Session 1: create a computed_result claim (E2 supported) with one
    computation evidence. Returns (factory, session_id, claim_id)."""
    factory, sid = await _seed_session(engine, question_text=question)
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
        r1 = await memory.apply_claim_staging(db, AuditService(db), session, records)
    assert r1.claims_created == 1 and r1.evidence_added == 1
    async with factory() as db:
        claim = (await db.execute(select(ORMClaim))).scalars().one()
    return factory, sid, claim.id


def _reverify_ops(claim_id: uuid.UUID, ref: str | None = None) -> list[tuple[str, JsonDict]]:
    """Session 2's proposal: a claim op carrying existing_claim_id (a
    restatement, not a new assertion) + the evidence link to it."""
    return [
        (
            "claim",
            {
                "statement": "6*7 равно 42 (перепроверено по источнику)",
                "claim_type": "computed_result",
                "scope": {"expr": "6*7"},
                "existing_claim_id": ref if ref is not None else str(claim_id),
            },
        ),
        ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
    ]


async def _apply_reverify(
    engine: AsyncEngine, snap: ORMConfigSnapshot, ops: list[tuple[str, JsonDict]],
    question: str | None, records: list[Any],
) -> tuple[async_sessionmaker, uuid.UUID, Any]:
    _f, sid = await _seed_session(engine, question_text=question)
    await _record_staging(engine, sid, ops)
    memory = MemoryService(snap)
    async with _f() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)
    return _f, sid, result


@pytest.mark.asyncio
async def test_reverify_same_evidence_record_exists_grade_unchanged(
    migrated_db: Any,
) -> None:
    """The core scenario: anchor creates the claim; the follow-up
    re-verifies the SAME id with the SAME evidence. The evidence must NOT
    duplicate (identity dedup — the invariant is not weakened), the grade
    must NOT change, the reverify RECORD (a fresh assessment row bound to
    the follow-up session) must exist, and no new claim row appears."""
    _url, engine = migrated_db
    _factory, sid, claim_id = await _anchor_comp_claim(engine)
    snap = await _snapshot(engine)
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]
    _f2, sid2, r2 = await _apply_reverify(
        engine, snap, _reverify_ops(claim_id), None, records
    )

    assert r2.claims_created == 0
    assert r2.claims_reused == 0
    assert r2.claims_reverified == 1
    assert r2.evidence_added == 0
    assert r2.evidence_deduped == 1  # the anchor's row is REUSED, not copied
    assert r2.assessments == 1
    assert r2.problems == ()

    async with _f2() as db:
        # exactly one claim and ONE evidence row in the whole DB —
        # the trap: a naive evidence-only record would see one session
        claims = (await db.execute(select(ORMClaim))).scalars().all()
        evidence = (await db.execute(select(ORMEvidence))).scalars().all()
        assert len(claims) == 1 and claims[0].id == claim_id
        assert len(evidence) == 1
        assert evidence[0].created_in_session == sid  # the ANCHOR's session
        # the reverify record: a second assessment, bound to session 2
        assessments = (
            (
                await db.execute(
                    text(
                        "SELECT created_in_session FROM claim_assessments "
                        "WHERE claim_id = :c ORDER BY created_at"
                    ),
                    {"c": claim_id},
                )
            )
            .scalars()
            .all()
        )
        assert assessments == [sid, sid2]
        # grade unchanged: head is current E2 supported
        view = await MemoryService(snap).claim_view(db, claim_id)
        assert view is not None
        assert view.assessment_state is AssessmentState.CURRENT
        assert view.grade is EffectiveGrade.E2
        assert view.epistemic_status is EpistemicStatus.SUPPORTED
        # audit trail: the direct reverify event (same tx as the change)
        n_rev = (
            (
                await db.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE type = 'claim_reverified' "
                        "AND payload->>'claim_id' = :c AND session_id = :s"
                    ),
                    {"c": str(claim_id), "s": sid2},
                )
            )
            .scalar_one()
        )
        assert n_rev == 1


@pytest.mark.asyncio
async def test_reverify_new_evidence_added_grade_by_rules(migrated_db: Any) -> None:
    """Reverify with a NEW (different-identity) evidence — the ordinary
    rules-engine path: the row is added, the claim is re-assessed, the
    grade comes from the rules (one independence group → E2 stays)."""
    _url, engine = migrated_db
    _factory, _sid, claim_id = await _anchor_comp_claim(engine)
    snap = await _snapshot(engine)
    new_records = [_comp_record({"code": "print(7*6)", "stdout": "42", "exit_code": 0})]
    _f2, _sid2, r2 = await _apply_reverify(
        engine, snap, _reverify_ops(claim_id), None, new_records
    )
    assert r2.claims_reverified == 1
    assert r2.evidence_added == 1
    assert r2.evidence_deduped == 0
    assert r2.problems == ()
    async with _f2() as db:
        n_evidence = (
            (
                await db.execute(
                    text("SELECT count(*) FROM evidence WHERE claim_id = :c"),
                    {"c": claim_id},
                )
            )
            .scalar_one()
        )
        assert n_evidence == 2
        view = await MemoryService(snap).claim_view(db, claim_id)
        assert view is not None
        assert view.grade is EffectiveGrade.E2
        assert view.epistemic_status is EpistemicStatus.SUPPORTED


@pytest.mark.asyncio
async def test_reverify_counterevidence_disputed_by_rules(migrated_db: Any) -> None:
    """Reverify with counter-evidence — the existing rules: disputed,
    capped at E1 (AGENTS.md §3)."""
    _url, engine = migrated_db
    _factory, _sid, claim_id = await _anchor_comp_claim(engine)
    snap = await _snapshot(engine)
    ops = [
        (
            "claim",
            {
                "statement": "6*7 равно 42 (перепроверено)",
                "claim_type": "computed_result",
                "scope": {"expr": "6*7"},
                "existing_claim_id": str(claim_id),
            },
        ),
        ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "counters"}),
    ]
    counter_records = [_comp_record({"code": "print(6*8)", "stdout": "48", "exit_code": 0})]
    _f2, _sid2, r2 = await _apply_reverify(
        engine, snap, ops, None, counter_records
    )
    assert r2.claims_reverified == 1
    assert r2.evidence_added == 1
    assert r2.problems == ()
    async with _f2() as db:
        view = await MemoryService(snap).claim_view(db, claim_id)
        assert view is not None
        assert view.epistemic_status is EpistemicStatus.DISPUTED
        assert view.grade is EffectiveGrade.E1  # counterevidence → disputed ≤ E1


@pytest.mark.asyncio
async def test_reverify_headless_claim_rejected_fail_closed(migrated_db: Any) -> None:
    """T7.9 condition, fail-closed: a claim WITHOUT a head in the
    session's snapshot (the headless legacy) is not a valid reverify
    target — a clear problem in the commit audit, no new claim, no
    assessment, no silent drop (and no silent fallback to creation)."""
    _url, engine = migrated_db
    factory, _sid, claim_id = await _anchor_comp_claim(engine)
    # strip the head — make it a headless legacy claim
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "DELETE FROM claim_assessment_heads WHERE claim_id = :c"
            ),
            {"c": claim_id},
        )
    snap = await _snapshot(engine)
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]
    _f2, _sid2, r2 = await _apply_reverify(
        engine, snap, _reverify_ops(claim_id), None, records
    )
    assert r2.claims_reverified == 0
    assert r2.claims_created == 0  # NOT a silent fallback to creation
    assert r2.assessments == 0
    # two clear reasons: the reverify reference AND the evidence link to
    # the rejected op (the indices stay aligned with the proposal)
    assert len(r2.problems) == 2
    assert "reverify reference" in r2.problems[0]
    assert "not visible" in r2.problems[0]
    assert "claim op 0 was rejected" in r2.problems[1]
    async with _f2() as db:
        n_claims = (await db.execute(text("SELECT count(*) FROM claims"))).scalar_one()
        assert n_claims == 1  # the headless one, untouched
        n_evidence = (await db.execute(text("SELECT count(*) FROM evidence"))).scalar_one()
        assert n_evidence == 1  # the reverify evidence was NOT attached


@pytest.mark.asyncio
async def test_reverify_unknown_claim_rejected_fail_closed(migrated_db: Any) -> None:
    """An id that exists in no corpus is rejected fail-closed — with a
    clear reason, and without creating a claim from the restatement."""
    _url, engine = migrated_db
    _factory, _sid, claim_id = await _anchor_comp_claim(engine)
    snap = await _snapshot(engine)
    unknown = uuid.uuid4()
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]
    _f2, _sid2, r2 = await _apply_reverify(
        engine, snap, _reverify_ops(claim_id, ref=str(unknown)), None, records
    )
    assert r2.claims_reverified == 0
    assert r2.claims_created == 0
    assert r2.assessments == 0
    assert len(r2.problems) >= 1
    assert "not visible" in r2.problems[0]
    async with _f2() as db:
        n_claims = (await db.execute(text("SELECT count(*) FROM claims"))).scalar_one()
        assert n_claims == 1


@pytest.mark.asyncio
async def test_reverify_unique_prefix_resolves(migrated_db: Any) -> None:
    """The observed model failure (EVAL-4d pack 7): a truncated UUID. A
    prefix unique among the session-visible claims resolves (the host
    re-checks the T7.9 condition on the resolved id)."""
    _url, engine = migrated_db
    _factory, _sid, claim_id = await _anchor_comp_claim(engine)
    snap = await _snapshot(engine)
    prefix = str(claim_id).replace("-", "")[:16]
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]
    _f2, _sid2, r2 = await _apply_reverify(
        engine, snap, _reverify_ops(claim_id, ref=prefix), None, records
    )
    assert r2.claims_reverified == 1
    assert r2.problems == ()
    async with _f2() as db:
        n_claims = (await db.execute(text("SELECT count(*) FROM claims"))).scalar_one()
        assert n_claims == 1
        # the audit event records the raw reference AND the resolved id
        row = (
            (
                await db.execute(
                    text(
                        "SELECT payload->>'reference', payload->>'resolved' "
                        "FROM audit_events WHERE type = 'claim_reverified'"
                    )
                )
            )
            .first()
        )
        assert row is not None
        assert row[0] == prefix
        assert row[1] == str(claim_id).lower()


@pytest.mark.asyncio
async def test_reverify_relative_anchor_shifts_deadline_to_moment(migrated_db: Any) -> None:
    """T7.32/ADR-0017 + ADR-0018: the deadline exists only for a claim
    about the PRESENT (anchor relative) and is the VERIFICATION MOMENT +
    the volatility window. A confirmed reverify is a new verification
    moment — the SAME single rule (reverify_after in _assess) shifts the
    anchor's deadline to the reverify session's moment. The anchor
    session is back-dated so the shift is observable."""
    _url, engine = migrated_db
    factory, sid, claim_id = await _anchor_comp_claim(engine, question="Проверь сейчас")
    async with factory() as db:
        row = (
            (
                await db.execute(
                    text("SELECT reverify_after FROM claims WHERE id = :c"),
                    {"c": claim_id},
                )
            )
            .first()
        )
        assert row is not None and row[0] is not None  # relative → deadline
    # back-date the anchor session by 2 days (its deadline is now in the
    # past relative to the reverify moment)
    async with factory() as db, db.begin():
        await db.execute(
            text("UPDATE sessions SET created_at = created_at - interval '2 days' WHERE id = :s"),
            {"s": sid},
        )
    snap = await _snapshot(engine)
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]
    _f2, sid2, r2 = await _apply_reverify(
        engine, snap, _reverify_ops(claim_id), "Проверь сейчас", records
    )
    assert r2.claims_reverified == 1
    async with _f2() as db:
        moment = (
            (
                await db.execute(
                    text("SELECT created_at FROM sessions WHERE id = :s"),
                    {"s": sid2},
                )
            )
            .scalar_one()
        )
        new_deadline = (
            (
                await db.execute(
                    text("SELECT reverify_after FROM claims WHERE id = :c"),
                    {"c": claim_id},
                )
            )
            .scalar_one()
        )
    assert new_deadline is not None
    # the deadline was recomputed from the REVERIFY moment (computed
    # result: static → +90d), not inherited from the back-dated anchor
    # (whose deadline is 2 days in the past)
    assert new_deadline >= moment
    delta = (new_deadline - moment).total_seconds()
    assert abs(delta - 90 * 86400) < 3600


@pytest.mark.asyncio
async def test_reverify_evergreen_claim_stays_deadline_less(migrated_db: Any) -> None:
    """An evergreen claim (anchor none — no question date) keeps NO
    deadline through the reverify: NULL stays NULL (ADR-0017)."""
    _url, engine = migrated_db
    factory, _sid, claim_id = await _anchor_comp_claim(engine)  # no question
    async with factory() as db:
        deadline = (
            (
                await db.execute(
                    text("SELECT reverify_after FROM claims WHERE id = :c"),
                    {"c": claim_id},
                )
            )
            .scalar_one()
        )
    assert deadline is None  # anchor none → evergreen by construction
    snap = await _snapshot(engine)
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]
    _f2, _sid2, r2 = await _apply_reverify(
        engine, snap, _reverify_ops(claim_id), None, records
    )
    assert r2.claims_reverified == 1
    async with _f2() as db:
        deadline2 = (
            (
                await db.execute(
                    text("SELECT reverify_after FROM claims WHERE id = :c"),
                    {"c": claim_id},
                )
            )
            .scalar_one()
        )
    assert deadline2 is None  # the reverify did not create a deadline
