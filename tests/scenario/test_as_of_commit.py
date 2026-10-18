"""Scenario (DB): T7.30 (ADR-0016) — the claim row's `as_of` is the
HOST-DERIVED reference date; the model's typed as_of is audit-only.
T7.32 (ADR-0017) — the reverify deadline exists ONLY for a claim about
the PRESENT (the question anchors the date relatively): reverify_after
= the verification moment + the volatility window, never counted from
as_of. A claim about a fixed point (explicit question date / dateless
question with the model's as_of) is immutable → NO deadline (NULL,
freshness evergreen), and reassessment never returns a deadline to it.

The commit boundary (the same code the fenced final transaction runs):
the claim row's `as_of` is derived by `derive_claim_as_of` (one
function, one source of truth — it also returns the ANCHOR of the
branch that won: explicit > relative form → session start (host
clock, UTC) > model's as_of (dateless question fallback)). The
model's value stays in the staging payload and the `claim_created`
audit. The EVAL-4d form (the fixture — NOT the evidence DB): the 19
relative claims are fresh (deadline = commit + 30d) and the 6
explicit-old + 3 dateless fixed-point claims have NO deadline — the
pre-T7.32 due set (8 of 28) is gone by construction: gate 6 counts
only the relative claims, 0 due of them (T7.32/ADR-0017).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import EvidenceKind
from packages.domain.models.sessions import ORMSession
from packages.domain.schemas.evidence import EvidenceRecord
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService
from packages.domain.services.staging import StagingService
from packages.evaluation.gates import compute_gates
from packages.evaluation.service import create_evaluation_run
from packages.memory.reassessment import run_reassessment_batch
from packages.memory.service import MemoryService

pytestmark = [pytest.mark.scenario]

#: the model's EVAL-4d artifact date («модель штампует дату»)
MODEL_AS_OF_ARTIFACT = "2026-06-15T00:00:00+00:00"

#: relative form (T7.18 closed set) — no explicit date, no URL
QUESTION_RELATIVE = "Сколько химических элементов известно на текущую дату?"
#: explicit OLD date — the by-design overdue shape of the corpus
QUESTION_EXPLICIT_OLD = "По состоянию на 15 июня 2026: сколько станций в московском метро?"
#: no date anchor at all (neither explicit nor relative)
QUESTION_NO_DATE = "Когда был запущен Спутник-1?"

SNAP_SUBQUERY = (
    "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"
)


async def _snapshot(engine: AsyncEngine) -> ORMConfigSnapshot:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
        assert snap is not None
        return snap


def _sha(tag: str) -> str:
    return hashlib.sha256(f"noezema-t730/{tag}".encode()).hexdigest()


def _source_record(source_id: str, original_sha256: str) -> Any:
    """A host adapter source_assertion record (the T7.8 payload)."""
    return EvidenceRecord(
        kind=EvidenceKind.SOURCE_ASSERTION,
        identity_hash="i" * 32,
        payload={
            "original_sha256": original_sha256,
            "chunk_id": "chunk-0",
            "url": f"https://example.invalid/{original_sha256[:8]}",
        },
        source_id=source_id,
        chunk_id="chunk-0",
    )


async def _commit_claims(
    engine: AsyncEngine,
    snap: ORMConfigSnapshot,
    *,
    question_text: str,
    claims: list[tuple[str, str | None]],
    session_created_at: datetime | None = None,
    reuse: bool = False,
    sha_tag: str = "",
) -> async_sessionmaker:
    """One session + its commit boundary: staging ops (claim + two
    source_assertion evidence per claim) + the real
    `MemoryService.apply_claim_staging`. ``claims`` is a list of
    (statement, model as_of ISO or None). ``reuse=True``: the same
    claim is proposed again (dedup by statement+type; the content-
    addressed evidence is deduped too). Returns a factory."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        qid = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO questions (id, text, origin, priority, state) "
                "VALUES (:id, :t, 'seeded', 1, 'candidate')"
            ),
            {"id": qid, "t": question_text},
        )
        if session_created_at is not None:
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, "
                    "question_id, started_at, created_at) "
                    "VALUES (:id, 'exploring', :c, :q, now(), :ca)"
                ),
                {
                    "id": sid,
                    "c": str(snap.id),
                    "q": qid,
                    "ca": session_created_at,
                },
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, "
                    "question_id, started_at) VALUES (:id, 'exploring', :c, :q, now())"
                ),
                {"id": sid, "c": str(snap.id), "q": qid},
            )
        session = (
            (await db.execute(select(ORMSession).where(ORMSession.id == sid)))
            .scalars()
            .first()
        )
        assert session is not None
        src_a = str(uuid.uuid4())
        src_b = str(uuid.uuid4())
        await db.execute(
            text(
                "INSERT INTO sources (id, source_type, canonical_uri, "
                "retrieved_at, content_hash) VALUES "
                "(:a, 'external_url', :ua, now(), :ha), "
                "(:b, 'external_url', :ub, now(), :hb)"
            ),
            {
                "a": src_a,
                "ua": "https://alpha.example/page",
                "ha": "a" * 64,
                "b": src_b,
                "ub": "https://beta.example/page",
                "hb": "b" * 64,
            },
        )
        audit = AuditService(db)
        staging = StagingService(HostReserveService.for_snapshot(snap))
        records: list[Any] = []
        for i, (statement, model_as_of) in enumerate(claims):
            payload: dict[str, Any] = {
                "statement": statement,
                "claim_type": "temporal_fact",
                # the model's free-form scope proposal (untrusted;
                # staging/audit only)
                "scope": {
                    "объект": statement[:30],
                    "на дату": (model_as_of or "")[:10],
                },
            }
            if model_as_of is not None:
                payload["as_of"] = model_as_of
            await staging.record(db, audit, session, "claim", payload, proposed_claims=1)
            for j, src in enumerate((src_a, src_b)):
                await staging.record(
                    db,
                    audit,
                    session,
                    "evidence",
                    {
                        "evidence_index": len(records),
                        "claim_index": i,
                        "relation": "supports",
                    },
                    proposed_evidence=1,
                )
                records.append(
                    _source_record(src, _sha(f"{sha_tag}{statement}-{j}"))
                )
        service = MemoryService(snap)
        result = await service.apply_claim_staging(db, audit, session, records)
        assert result.problems == (), f"commit boundary problems: {result.problems}"
        assert result.assessments == len(claims)
        if reuse:
            # dedup by statement+type: the claim is REUSED, the
            # content-addressed evidence is deduped
            assert result.claims_created == 0 and result.claims_reused == len(claims)
            assert result.evidence_added == 0
        else:
            assert result.claims_created == len(claims)
            assert result.evidence_added == 2 * len(claims)
    return factory


async def _claim_row(
    factory: async_sessionmaker, statement_prefix: str
) -> tuple[datetime | None, datetime | None, str]:
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT as_of, reverify_after, freshness_status "
                    "FROM claims WHERE statement LIKE :st"
                ),
                {"st": f"{statement_prefix}%"},
            )
        ).all()
    assert len(rows) == 1, f"expected exactly one claim for {statement_prefix!r}"
    return rows[0][0], rows[0][1], rows[0][2]


@pytest.mark.asyncio
async def test_relative_question_as_of_is_session_start(migrated_db: Any) -> None:
    """T7.30 (ADR-0016): a temporal_fact committed on a RELATIVE
    question («на текущую дату») with the model's as_of in the past
    (the EVAL-4d artifact shape) stores `as_of` = the SESSION START
    (sessions.created_at, UTC — the T7.18 anchor, not the commit
    moment), `reverify_after` = +30d from it, and keeps the model's
    date in the staging payload and the claim_created audit."""
    _scratch, engine = migrated_db
    snap = await _snapshot(engine)
    created_at = datetime.now(UTC)
    session_day = created_at.date()
    factory = await _commit_claims(
        engine,
        snap,
        question_text=QUESTION_RELATIVE,
        claims=[("Сколько химических элементов известно", MODEL_AS_OF_ARTIFACT)],
        session_created_at=created_at,
    )
    expected = datetime(session_day.year, session_day.month, session_day.day, tzinfo=UTC)

    as_of, reverify, freshness = await _claim_row(
        factory, "Сколько химических элементов известно"
    )
    # the host-derived reference date: the session START (midnight
    # UTC of the session's date) — NOT the model's 2026-06-15
    assert as_of == expected
    # T7.32 (ADR-0017): a relative claim is about the PRESENT —
    # reverify_after = the VERIFICATION MOMENT (the commit, between
    # the session start and the session start + 1d) + the volatility
    # window (30d) — never counted from as_of → fresh
    assert reverify is not None
    assert as_of + timedelta(days=30) <= reverify < as_of + timedelta(days=31)
    assert freshness == "fresh"

    # the model's date is AUDIT-ONLY: the staging payload (durable,
    # immutable) keeps it
    async with factory() as db:
        staging_as_of = (
            await db.execute(
                text(
                    "SELECT payload->>'as_of' FROM session_staging "
                    "WHERE op = 'claim' LIMIT 1"
                )
            )
        ).scalar_one()
    assert staging_as_of == MODEL_AS_OF_ARTIFACT

    # ...and the claim_created audit carries both values (the model's
    # proposal + the host-derived one stored on the claim row)
    async with factory() as db:
        audit_row = (
            await db.execute(
                text(
                    "SELECT payload->>'as_of', payload->>'assessed_as_of' "
                    "FROM audit_events WHERE type = 'claim_created' "
                    "AND payload->>'statement' LIKE 'Сколько химических элементов%'"
                )
            )
        ).one()
    assert audit_row[0] == MODEL_AS_OF_ARTIFACT
    assert audit_row[1] == expected.isoformat()


@pytest.mark.asyncio
async def test_explicit_question_date_wins_and_has_no_deadline(migrated_db: Any) -> None:
    """T7.30 (ADR-0016) + T7.32 (ADR-0017): a question with an
    EXPLICIT old date stores `as_of` = that date (the model's as_of —
    in the future of the question's date — does not shift it). Under
    ADR-0017 the explicitly-dated claim is about a FIXED point — a
    fixed moment is immutable, later events cannot spoil it — so it
    has NO reverify deadline: `reverify_after` is NULL, freshness
    ``evergreen``. (The pre-T7.32 expectation was 'due': reverify was
    counted from as_of, so the claim was overdue forever — the root of
    the EVAL-4d/T7.31 «просрочен навсегда» class.)"""
    _scratch, engine = migrated_db
    snap = await _snapshot(engine)
    factory = await _commit_claims(
        engine,
        snap,
        question_text=QUESTION_EXPLICIT_OLD,
        claims=[("Сколько станций в московском метро", "2026-08-13T00:00:00+00:00")],
    )

    as_of, reverify, freshness = await _claim_row(factory, "Сколько станций")
    assert as_of == datetime(2026, 6, 15, tzinfo=UTC)  # the explicit question date
    assert reverify is None  # no deadline by construction (ADR-0017)
    assert freshness == "evergreen"


@pytest.mark.asyncio
async def test_dateless_question_keeps_model_as_of_and_has_no_deadline(
    migrated_db: Any,
) -> None:
    """T7.30 (ADR-0016) + T7.32 (ADR-0017): a question with NO date
    anchor (the Sputnik-1 shape) keeps the model's typed as_of as-is
    — the event date; the claim is about a FIXED point → NO reverify
    deadline (NULL, evergreen). (The pre-T7.32 expectation was
    reverify_after = the model's date + 30d = 1957-11-03, due forever
    — claim `de9855eb` in EVAL-4d; the class is gone per ADR-0017.)"""
    _scratch, engine = migrated_db
    snap = await _snapshot(engine)
    factory = await _commit_claims(
        engine,
        snap,
        question_text=QUESTION_NO_DATE,
        claims=[("Спутник-1 запущен 4 октября 1957", "1957-10-04T19:28:34+00:00")],
    )

    as_of, reverify, freshness = await _claim_row(factory, "Спутник-1 запущен")
    assert as_of == datetime(1957, 10, 4, 19, 28, 34, tzinfo=UTC)  # unchanged
    assert reverify is None  # no deadline by construction (ADR-0017)
    assert freshness == "evergreen"


@pytest.mark.asyncio
async def test_session_crossing_midnight_anchors_on_session_start(migrated_db: Any) -> None:
    """T7.30 (ADR-0016, the T7.18 anchor): a session that starts at
    23:50 UTC of day N and COMMITS after midnight (day N+1) keeps the
    reference date = the SESSION START's date (day N) — the commit
    moment does not re-anchor it (the same rule the scope uses)."""
    _scratch, engine = migrated_db
    snap = await _snapshot(engine)
    now = datetime.now(UTC)
    day_n = (now - timedelta(days=1)).date()
    day_n1 = now.date()
    assert day_n1 == day_n + timedelta(days=1)
    start = datetime(day_n.year, day_n.month, day_n.day, 23, 50, tzinfo=UTC)
    factory = await _commit_claims(
        engine,
        snap,
        question_text=QUESTION_RELATIVE,
        claims=[("Сколько букв в русском алфавите", MODEL_AS_OF_ARTIFACT)],
        session_created_at=start,
    )

    as_of, reverify, freshness = await _claim_row(factory, "Сколько букв")
    # the anchor is the session start's date — NOT the commit day
    assert as_of == datetime(day_n.year, day_n.month, day_n.day, tzinfo=UTC)
    assert as_of != datetime(day_n1.year, day_n1.month, day_n1.day, tzinfo=UTC)
    # T7.32 (ADR-0017): the deadline is the commit moment (day N+1,
    # right after the session start `start`) + 30d — within a day of
    # start + 30d — never counted from as_of (as_of + 30d = day N + 30d
    # would be the pre-T7.32 value)
    assert reverify is not None
    assert start + timedelta(days=30) <= reverify < start + timedelta(days=31)
    assert reverify != as_of + timedelta(days=30)  # never counted from as_of
    assert freshness == "fresh"  # commit + 30d is still in the future


@pytest.mark.asyncio
async def test_reassessment_keeps_host_as_of(migrated_db: Any) -> None:
    """T7.30 (ADR-0016): reassessment re-derives `reverify_after` from
    the STORED `claims.as_of` (the host date) — it never returns the
    model's date (which after T7.30 exists only in staging/audit). A
    relative claim committed with the model's as_of = 2026-06-15 keeps
    reverify_after = session date + 30d after the worker run."""
    _scratch, engine = migrated_db
    snap = await _snapshot(engine)
    created_at = datetime.now(UTC)
    session_day = created_at.date()
    expected = datetime(session_day.year, session_day.month, session_day.day, tzinfo=UTC)
    factory = await _commit_claims(
        engine,
        snap,
        question_text=QUESTION_RELATIVE,
        claims=[("Сколько государств-членов в ООН", MODEL_AS_OF_ARTIFACT)],
        session_created_at=created_at,
    )

    # move the head to pending (as a rules/config change would) so the
    # worker actually re-evaluates, and queue a reassessment job
    cid: Any
    async with factory() as db, db.begin():
        cid = (
            await db.execute(
                text(
                    "SELECT id FROM claims WHERE statement LIKE "
                    "'Сколько государств-членов в ООН%'"
                )
            )
        ).one()[0]
        await db.execute(
            text(
                "UPDATE claim_assessment_heads SET assessment_state = 'pending', "
                "current_assessment_id = NULL, epistemic_status = NULL "
                f"WHERE claim_id = :c AND config_snapshot_id = {SNAP_SUBQUERY}"
            ),
            {"c": cid},
        )
        await db.execute(
            text(
                "INSERT INTO reassessment_jobs (id, claim_id, "
                "target_config_snapshot_id, status, reason, priority) "
                f"VALUES (:id, :c, {SNAP_SUBQUERY}, 'queued', 'test', 0)"
            ),
            {"id": str(uuid.uuid4()), "c": cid},
        )

    async with factory() as db:
        out = await run_reassessment_batch(db)
    assert out.completed == 1 and out.blocked == 0

    as_of, reverify, freshness = await _claim_row(factory, "Сколько государств-членов")
    # the host date is kept (the model's 2026-06-15 → reverify
    # 2026-07-15 did NOT come back); T7.32 (ADR-0017): the worker
    # refreshes the deadline to its OWN verification moment + 30d —
    # between the session date + 30d and the session date + 31d
    assert as_of == expected
    assert reverify is not None
    assert expected + timedelta(days=30) <= reverify < expected + timedelta(days=31)
    assert reverify != datetime(2026, 7, 15, tzinfo=UTC)
    assert freshness == "fresh"


@pytest.mark.asyncio
async def test_reassessment_does_not_return_deadline_to_fixed_point_claim(
    migrated_db: Any,
) -> None:
    """T7.32 (ADR-0017): reassessment NEVER returns a deadline to a
    claim about a fixed point. A claim committed on an EXPLICIT old
    date question has reverify_after = NULL (evergreen — the
    fixed moment is immutable); after the head is invalidated and the
    worker re-evaluates it, the deadline is STILL NULL (the worker
    applies the same anchor rule from the persisted scope's
    ``date_anchor``) — no as_of-based deadline comes back."""
    _scratch, engine = migrated_db
    snap = await _snapshot(engine)
    factory = await _commit_claims(
        engine,
        snap,
        question_text=QUESTION_EXPLICIT_OLD,
        claims=[("Сколько станций в московском метро", "2026-08-13T00:00:00+00:00")],
    )
    # the fixed-point state BEFORE the worker: NULL deadline, evergreen
    as_of, reverify, freshness = await _claim_row(factory, "Сколько станций")
    assert as_of == datetime(2026, 6, 15, tzinfo=UTC)
    assert reverify is None and freshness == "evergreen"

    # move the head to pending (as a rules/config change would) so the
    # worker actually re-evaluates, and queue a reassessment job
    cid: Any
    async with factory() as db, db.begin():
        cid = (
            await db.execute(
                text(
                    "SELECT id FROM claims WHERE statement LIKE "
                    "'Сколько станций%'"
                )
            )
        ).one()[0]
        await db.execute(
            text(
                "UPDATE claim_assessment_heads SET assessment_state = 'pending', "
                "current_assessment_id = NULL, epistemic_status = NULL "
                f"WHERE claim_id = :c AND config_snapshot_id = {SNAP_SUBQUERY}"
            ),
            {"c": cid},
        )
        await db.execute(
            text(
                "INSERT INTO reassessment_jobs (id, claim_id, "
                "target_config_snapshot_id, status, reason, priority) "
                f"VALUES (:id, :c, {SNAP_SUBQUERY}, 'queued', 'test', 0)"
            ),
            {"id": str(uuid.uuid4()), "c": cid},
        )

    async with factory() as db:
        out = await run_reassessment_batch(db)
    assert out.completed == 1 and out.blocked == 0

    # the worker re-evaluated the head (current again) but did NOT
    # return a deadline to the fixed-point claim
    as_of, reverify, freshness = await _claim_row(factory, "Сколько станций")
    assert as_of == datetime(2026, 6, 15, tzinfo=UTC)
    assert reverify is None
    assert freshness == "evergreen"


@pytest.mark.asyncio
async def test_reused_claim_as_of_rederived_from_current_question(migrated_db: Any) -> None:
    """T7.30 (ADR-0016, dedup): a claim REUSED by a later session gets
    its `as_of` re-derived from the CURRENT session's question — the
    same re-derivation the scope gets (the claim is asserted for the
    date THIS question anchors), so reverify_after never rests on a
    stale value. A relative claim created on day N, reused on day N+1
    (same relative question, the model proposes ANOTHER past date),
    ends with as_of = day N+1."""
    _scratch, engine = migrated_db
    snap = await _snapshot(engine)
    now = datetime.now(UTC)
    day_n = (now - timedelta(days=1)).date()
    day_n1 = now.date()
    assert day_n1 == day_n + timedelta(days=1)
    start_n = datetime(day_n.year, day_n.month, day_n.day, 12, 0, tzinfo=UTC)
    start_n1 = datetime(day_n1.year, day_n1.month, day_n1.day, 12, 0, tzinfo=UTC)

    # session 1 (day N): the claim is created (relative question)
    await _commit_claims(
        engine,
        snap,
        question_text=QUESTION_RELATIVE,
        claims=[("Сколько букв в русском алфавите", MODEL_AS_OF_ARTIFACT)],
        session_created_at=start_n,
        sha_tag="dayN/",
    )
    # session 2 (day N+1): the SAME claim (dedup by statement+type),
    # the model proposes yet another past date — it must not matter
    factory = await _commit_claims(
        engine,
        snap,
        question_text=QUESTION_RELATIVE,
        claims=[("Сколько букв в русском алфавите", "2026-08-13T00:00:00+00:00")],
        session_created_at=start_n1,
        reuse=True,
        sha_tag="dayN/",  # the same content → the evidence dedups
    )

    # exactly one claim row in the whole DB
    async with factory() as db:
        n = (
            await db.execute(
                text("SELECT count(*) FROM claims WHERE statement LIKE 'Сколько букв%'")
            )
        ).scalar_one()
    assert n == 1

    as_of, reverify, _freshness = await _claim_row(factory, "Сколько букв")
    # re-derived from the CURRENT session's question: day N+1
    assert as_of == datetime(day_n1.year, day_n1.month, day_n1.day, tzinfo=UTC)
    # T7.32 (ADR-0017): the deadline is the SECOND commit's moment +
    # 30d (the relative claim is re-asserted for the current
    # session's date) — between as_of + 30d and as_of + 31d
    assert reverify is not None
    assert as_of + timedelta(days=30) <= reverify < as_of + timedelta(days=31)


@pytest.mark.asyncio
async def test_eval4d_shape_fixed_points_have_no_deadline_gate_0_of_20_passed(
    migrated_db: Any,
) -> None:
    """T7.30 (ADR-0016) + T7.32 (ADR-0017), the EVAL-4d form in a
    FIXTURE (not the evidence DB — `noezema-eval4d` stays SELECT-only):
    29 current temporal_fact claims committed through the real commit
    path — 20 on RELATIVE questions (15 with the model's as_of in the
    past — the EVAL-4d artifact clusters 2026-06-15/2026-08-13 — and
    5 with a recent model's as_of), 6 on an EXPLICIT OLD date, 3 on a
    DATELESS question (model's as_of: Sputnik 1957, population
    2026-07-01, the line opening 2026-09-05).

    Under ADR-0017 the pre-T7.32 due set (8 of 28: 6 explicit-old +
    2 dateless — "overdue forever" by construction) is GONE: the
    6 explicit-old and 3 dateless fixed-point claims have NO deadline
    (reverify_after NULL, evergreen) and are OUT of the gate's
    denominator (they cannot become due — counting them would dilute
    the ratio with composition). The 20 relative claims are fresh
    (deadline = commit moment + 30d) → gate 6 = 0/20 = 0.0 → passed.
    (The pre-T7.32 version of this test asserted 8/28 = 0.2857
    failed — the T7.29 recalculation; the expectation is corrected
    per ADR-0017.)"""
    _scratch, engine = migrated_db
    snap = await _snapshot(engine)
    now = datetime.now(UTC)

    # the run row FIRST (the window starts now; the gate itself counts
    # the current temporal claims WITH a deadline of the effective
    # snapshot)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        run = await create_evaluation_run(
            db,
            label="t732-gate-test",
            config_snapshot_id=snap.id,
            model_fingerprint={"model": "test", "backend": "local"},
            rules_version="rules-v2",
            rules_hash="0" * 64,
            thresholds=None,
            blind_sample_seed=42,
            blind_sample_size=50,
        )
        assert run is not None

    # 20 relative + 6 explicit-old + 3 dateless = 29 (the EVAL-4d
    # shape, +1 relative so the post-ADR-0017 denominator stays at the
    # MIN_SAMPLE = 20 boundary)
    relative = [
        (
            f"Факт {i} на текущую дату",
            # the 15 artifact ones: the EVAL-4d artifact clusters
            MODEL_AS_OF_ARTIFACT if i % 2 == 0 else "2026-08-13T00:00:00+00:00",
        )
        for i in range(15)
    ] + [
        # the 5 with a recent model's as_of (the EVAL-4d 2026-09-07..21
        # dates) — under ADR-0017 the model's as_of is audit-only for
        # relative questions
        (f"Факт {i} на текущую дату", d)
        for i, d in enumerate(
            (
                "2026-09-07T00:00:00+00:00",
                "2026-09-15T00:00:00+00:00",
                "2026-09-17T00:00:00+00:00",
                "2026-09-18T00:00:00+00:00",
                "2026-09-21T21:00:00+00:00",
            ),
            start=15,
        )
    ]
    explicit = [(f"Станций метро факт {i}", "2026-08-13T00:00:00+00:00") for i in range(6)]
    dateless = [
        ("Спутник-1 запущен 4 октября 1957", "1957-10-04T19:28:34+00:00"),
        ("Численность населения Земли 8,2 миллиарда", "2026-07-01T00:00:00+00:00"),
        ("Сколько станций открыто на Рублёво-Архангельской", "2026-09-05T00:00:00+00:00"),
    ]
    # the 20 relative claims go in TWO sessions (the staging reserve
    # caps max_new_claims_per_session at 16); the gate counts claims
    await _commit_claims(
        engine, snap, question_text=QUESTION_RELATIVE, claims=relative[:10]
    )
    await _commit_claims(
        engine, snap, question_text=QUESTION_RELATIVE, claims=relative[10:]
    )
    await _commit_claims(
        engine, snap, question_text=QUESTION_EXPLICIT_OLD, claims=explicit
    )
    await _commit_claims(engine, snap, question_text=QUESTION_NO_DATE, claims=dateless)

    async with factory() as db:
        gates = await compute_gates(db, run=run, now=now)

    g6 = gates["due_stale_time_sensitive"]
    # ADR-0017: the denominator counts ONLY the claims with a deadline
    # (the 20 relative) — the 6 explicit-old + 3 dateless fixed-point
    # claims are out (no deadline, can never be due)
    assert g6["denominator"] == 20
    # all 20 relative are fresh (deadline = commit + 30d, in the
    # future) → 0 due
    assert g6["numerator"] == 0
    assert g6["ratio"] == 0.0
    assert g6["threshold"] == 0.20
    assert g6["outcome"] == "passed"
    assert "ci95" in g6  # §22.2: the interval is published with the gate

    # sanity 1: the model's artifact date is GONE from the claim rows
    # of the relative questions (audit-only) — all 20 carry the
    # session date (midnight UTC of today), a deadline, and are fresh
    async with factory() as db:
        fresh, total = (
            await db.execute(
                text(
                    "SELECT count(*) FILTER (WHERE c.as_of = "
                    "date_trunc('day', now())::timestamptz "
                    "AND c.freshness_status = 'fresh' "
                    "AND c.reverify_after IS NOT NULL), count(*) "
                    "FROM claims c "
                    "JOIN claim_assessment_heads h ON h.claim_id = c.id "
                    f"AND h.config_snapshot_id = {SNAP_SUBQUERY} "
                    "AND h.assessment_state = 'current' "
                    "WHERE c.claim_type = 'temporal_fact' "
                    "AND c.statement LIKE 'Факт %'"
                )
            )
        ).one()
    assert (fresh, total) == (20, 20)
    # sanity 2: all 9 fixed-point claims (6 explicit + 3 dateless)
    # have NO deadline and are evergreen
    async with factory() as db:
        fixed_no_deadline, fixed_total = (
            await db.execute(
                text(
                    "SELECT count(*) FILTER (WHERE c.reverify_after IS NULL "
                    "AND c.freshness_status = 'evergreen'), count(*) "
                    "FROM claims c "
                    "JOIN claim_assessment_heads h ON h.claim_id = c.id "
                    f"AND h.config_snapshot_id = {SNAP_SUBQUERY} "
                    "AND h.assessment_state = 'current' "
                    "WHERE c.claim_type = 'temporal_fact' "
                    "AND c.statement NOT LIKE 'Факт %'"
                )
            )
        ).one()
    assert (fixed_no_deadline, fixed_total) == (9, 9)
