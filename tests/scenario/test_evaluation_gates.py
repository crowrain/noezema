"""Scenario (DB): T7.7 — §22.2 gate computation.

Seeds a rich domain dataset (sessions, questions, claims,
assessments, heads, evidence, sources, dependencies, reassessment
jobs, alerts) and asserts each of the 11 gates returns the expected
numerator/denominator/outcome. Also covers the empty-DB path (all
sample-based gates → ``insufficient_sample``) and blind-sample
determinism.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.evaluation.gates import compute_gates
from packages.evaluation.service import (
    EvaluationRun,
    create_evaluation_run,
)

pytestmark = [pytest.mark.scenario]


def _uid(seed: str) -> uuid.UUID:
    """Deterministic uuid per seed (stable across runs)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"noezema-eval/{seed}")


async def _mk_run(
    engine: AsyncEngine,
    *,
    thresholds: dict[str, Any] | None = None,
    seed: int = 42,
    size: int = 50,
) -> EvaluationRun:
    """Create an evaluation run row (frozen on the bootstrap snapshot)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        cs = (
            (
                await db.execute(
                    text(
                        "SELECT id FROM config_snapshots "
                        "WHERE activation_mode = 'bootstrap'"
                    )
                )
            )
            .first()
        )[0]
        run = await create_evaluation_run(
            db,
            label="gate-test",
            config_snapshot_id=cs,
            model_fingerprint={"model": "test", "backend": "local"},
            rules_version="rules-v1",
            rules_hash="0" * 64,
            thresholds=thresholds,
            blind_sample_seed=seed,
            blind_sample_size=size,
        )
        assert run is not None
        return run


class _Seeder:
    """Deterministic domain seeding for the gate tests."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.factory = async_sessionmaker(engine, expire_on_commit=False)
        self.cs_id: uuid.UUID | None = None
        self.session_ids: list[uuid.UUID] = []
        self.question_ids: list[uuid.UUID] = []
        self.claim_ids: list[uuid.UUID] = []

    async def _exec(self, sql: str, params: dict[str, Any]) -> None:
        async with self.factory() as db, db.begin():
            await db.execute(text(sql), params)

    async def setup(self) -> None:
        row = (
            await self._scalar(
                "SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap'"
            )
        )
        assert row is not None
        self.cs_id = row

    async def _scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        async with self.factory() as db:
            return (await db.execute(text(sql), params or {})).scalar_one()

    async def add_session(
        self,
        idx: int,
        *,
        state: str = "succeeded",
        question: bool = True,
        qstate: str = "verified",
        abort: bool = False,
        start: datetime | None = None,
    ) -> uuid.UUID:
        sid = _uid(f"session-{idx}")
        qid = _uid(f"question-{idx}")
        self.session_ids.append(sid)
        self.question_ids.append(qid)
        # AFTER the run row's started_at (the window start)
        now = datetime.now(UTC)
        started = start or (now + timedelta(minutes=idx))
        finished = started + timedelta(minutes=10)
        q = (
            "INSERT INTO questions (id, text, origin, state) "
            "VALUES (:id, :t, 'seeded', :q)"
            if question
            else ""
        )
        if question:
            await self._exec(
                q, {"id": qid, "t": f"question {idx}", "q": qstate}
            )
        await self._exec(
            "INSERT INTO sessions (id, state, config_snapshot_id, question_id, "
            "started_at, finished_at, termination_reason, abort_requested_at) "
            "VALUES (:id, :s, :cs, :q, :sa, :fa, :tr, :ab)",
            {
                "id": sid,
                "s": state,
                "cs": self.cs_id,
                "q": qid if question else None,
                "sa": started,
                "fa": finished,
                "tr": "budget" if state in ("succeeded", "succeeded_partial") else None,
                "ab": finished if abort else None,
            },
        )
        return sid

    async def add_claim(
        self,
        idx: int,
        *,
        session: uuid.UUID,
        ctype: str = "computed_result",
        status: str = "supported",
        grade: str = "E2",
        head_state: str = "current",
        scope: dict[str, Any] | None = None,
        evidence_source: bool = True,
        source_exists: bool = True,
        freshness: str = "fresh",
        as_of: datetime | None = None,
        reverify_after: datetime | None = None,
    ) -> uuid.UUID:
        cid = _uid(f"claim-{idx}")
        aid = _uid(f"assessment-{idx}")
        eid = _uid(f"evidence-{idx}")
        srcid = _uid(f"source-{idx}")
        self.claim_ids.append(cid)
        scope_json = scope if scope is not None else {"topic": f"t{idx}"}
        if source_exists:
            await self._exec(
                "INSERT INTO sources (id, source_type, canonical_uri) "
                "VALUES (:id, 'local_corpus', :u)",
                {"id": srcid, "u": f"corpus://doc{idx}"},
            )
        await self._exec(
            "INSERT INTO claims (id, statement, claim_type, freshness_status, "
            "created_in_session, observed_at, as_of, reverify_after) "
            "VALUES (:id, :st, :ct, :f, :s, now(), :ao, :rv)",
            {
                "id": cid,
                "st": f"claim statement {idx}",
                "ct": ctype,
                "f": freshness,
                "s": session,
                "ao": as_of,
                "rv": reverify_after,
            },
        )
        await self._exec(
            "INSERT INTO claim_assessments (id, claim_id, effective_grade, "
            "epistemic_status, rules_version, rules_hash, evidence_set_hash, "
            "assessed_scope, confidence, valid, created_in_session) "
            "VALUES (:id, :c, :g, :es, 'rules-v1', :rh, :eh, CAST(:sc AS jsonb), "
            "0.9, true, :s)",
            {
                "id": aid,
                "c": cid,
                "g": grade,
                "es": status,
                "rh": "0" * 64,
                "eh": "1" * 64,
                "sc": _json(scope_json),
                "s": session,
            },
        )
        if evidence_source:
            await self._exec(
                "INSERT INTO evidence (id, claim_id, relation, evidence_kind, "
                "identity_hash, scope, source_id, chunk_id, created_in_session) "
                "VALUES (:id, :c, 'supports', 'source_assertion', :ih, "
                "CAST(:sc AS jsonb), :src, 'c1', :s)",
                {
                    "id": eid,
                    "c": cid,
                    "ih": f"evid-{idx}",
                    "sc": _json({"scope": f"s{idx}"}),
                    "src": srcid if source_exists else _uid(f"missing-{idx}"),
                    "s": session,
                },
            )
            await self._exec(
                "INSERT INTO assessment_evidence (assessment_id, evidence_id, role) "
                "VALUES (:a, :e, 'support')",
                {"a": aid, "e": eid},
            )
        if head_state == "current":
            await self._exec(
                "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, "
                "assessment_state, current_assessment_id, epistemic_status, "
                "prepared_by) VALUES (:c, :cs, 'current', :a, :es, 'session')",
                {"c": cid, "cs": self.cs_id, "a": aid, "es": status},
            )
        else:
            await self._exec(
                "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, "
                "assessment_state, current_assessment_id, epistemic_status, "
                "prepared_by) VALUES (:c, :cs, :st, NULL, NULL, 'session')",
                {"c": cid, "cs": self.cs_id, "st": head_state},
            )
        return cid

    async def add_second_evidence(
        self, idx: int, session: uuid.UUID
    ) -> None:
        """A second evidence row from a DIFFERENT session (reuse signal)."""
        cid = _uid(f"claim-{idx}")
        eid = _uid(f"evidence-reuse-{idx}")
        aid = _uid(f"assessment-{idx}")
        srcid = _uid(f"source-reuse-{idx}")
        await self._exec(
            "INSERT INTO sources (id, source_type, canonical_uri) "
            "VALUES (:id, 'local_corpus', :u)",
            {"id": srcid, "u": f"corpus://doc-r{idx}"},
        )
        await self._exec(
            "INSERT INTO evidence (id, claim_id, relation, evidence_kind, "
            "identity_hash, scope, source_id, chunk_id, created_in_session) "
            "VALUES (:id, :c, 'supports', 'source_assertion', :ih, "
            "CAST(:sc AS jsonb), :src, 'c1', :s)",
            {
                "id": eid,
                "c": cid,
                "ih": f"reuse-{idx}",
                "sc": _json({"r": 1}),
                "src": srcid,
                "s": session,
            },
        )
        await self._exec(
            "INSERT INTO assessment_evidence (assessment_id, evidence_id, role) "
            "VALUES (:a, :e, 'support')",
            {"a": aid, "e": eid},
        )

    async def add_job(
        self, idx: int, *, within_slo: bool, claim: uuid.UUID
    ) -> None:
        now = datetime.now(UTC)
        enq = now - timedelta(hours=3)
        done = enq + timedelta(seconds=60 if within_slo else 7200)
        await self._exec(
            "INSERT INTO reassessment_jobs (id, claim_id, target_config_snapshot_id, "
            "status, priority, enqueued_at, attempts, max_attempts, completed_at) "
            "VALUES (:id, :c, :cs, 'completed', 0, :e, 1, 78, :d)",
            {
                "id": _uid(f"job-{idx}"),
                "c": claim,
                "cs": self.cs_id,
                "e": enq,
                "d": done,
            },
        )

    async def add_alert(
        self,
        session: uuid.UUID,
        seq: int,
        kind: str,
    ) -> None:
        await self._exec(
            "INSERT INTO audit_events (id, session_id, sequence, type, "
            "payload, public_summary) "
            "VALUES (:id, :s, :q, 'alert_raised', CAST(:p AS jsonb), :ps)",
            {
                "id": _uid(f"alert-{session}-{seq}"),
                "s": session,
                "q": seq,
                "p": _json({"kind": kind}),
                "ps": "test alert",
            },
        )

    async def add_repeat_cycle(self, session: uuid.UUID, qid: uuid.UUID) -> None:
        await self._exec(
            "INSERT INTO audit_events (id, session_id, sequence, type, "
            "payload) VALUES (:id, :s, 7, 'repeat_cycle_detected', CAST(:p AS jsonb))",
            {
                "id": _uid(f"repeat-{session}"),
                "s": session,
                "p": _json({"question_id": str(qid), "strategy": "note"}),
            },
        )

    async def add_dependency(
        self,
        from_idx: int,
        to_idx: int,
        session: uuid.UUID,
        kind: str = "evidential",
    ) -> None:
        await self._exec(
            "INSERT INTO claim_dependencies (id, from_claim_id, to_claim_id, kind, "
            "created_in_session) VALUES (:id, :f, :t, :k, :s)",
            {
                "id": _uid(f"dep-{from_idx}-{to_idx}"),
                "f": _uid(f"claim-{from_idx}"),
                "t": _uid(f"claim-{to_idx}"),
                "k": kind,
                "s": session,
            },
        )


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj)


async def _seed_rich(engine: AsyncEngine) -> _Seeder:
    s = _Seeder(engine)
    await s.setup()
    # 24 sessions: 20 succeeded, 2 succeeded_partial, 1 failed,
    # 1 operator-abort (cancelled + abort_requested)
    for i in range(20):
        await s.add_session(i, state="succeeded")
    await s.add_session(20, state="succeeded_partial", qstate="candidate")
    await s.add_session(21, state="succeeded_partial", qstate="candidate")
    await s.add_session(22, state="failed", qstate="candidate")
    await s.add_session(23, state="cancelled", abort=True, qstate="candidate")
    # 24 claims:
    #  - 16 computed E2 supported (E2+)
    #  - 2 computed E3 supported (E2+)
    #  - 2 computed E1 refuted (not E2+)
    #  - 4 computed hypothesis (excluded from gate 1)
    #  - 20 temporal: 19 E3 supported + 1 E2 supported; freshness:
    #    17 fresh, 2 due, 1 stale
    for i in range(16):
        await s.add_claim(i, session=s.session_ids[i], grade="E2")
    for i in range(16, 18):
        await s.add_claim(i, session=s.session_ids[i], grade="E3")
    for i in range(18, 20):
        await s.add_claim(
            i, session=s.session_ids[i], grade="E1", status="refuted"
        )
    # claims 20..22 are created WITHOUT evidence: their sessions
    # (20, 21, 22) have no outcome (no evidence/revision/question
    # resolution) — gate 3 numerator = 20/23
    for i in range(20, 24):
        await s.add_claim(
            i,
            session=s.session_ids[i],
            grade="E0",
            status="hypothesis",
            evidence_source=i >= 23,
        )
    # T7.27 (ADR-0014): the gate evaluates the §8.6 rule over
    # reverify_after, so the seeded rows are self-consistent with it —
    # the stored freshness_status is the rule's output for the seeded
    # reverify_after (the legacy rows set only the stored column).
    gate_now = datetime.now(UTC)
    for i in range(24, 44):
        grade = "E3" if i < 43 else "E2"
        freshness = "fresh"
        reverify: datetime | None
        if i in (40, 41):
            freshness = "due"
            reverify = gate_now - timedelta(days=30)
        elif i == 42:
            freshness = "stale"
            reverify = gate_now - timedelta(days=60)
        else:
            reverify = gate_now + timedelta(days=30)
        await s.add_claim(
            i,
            session=s.session_ids[i % 24],
            ctype="temporal_fact",
            grade=grade,
            freshness=freshness,
            as_of=reverify - timedelta(days=30),
            reverify_after=reverify,
        )
    # gate 5 (reuse): claims 0..12 get a second evidence from a
    # different session → 13/51 significant claims reused
    for i in range(13):
        await s.add_second_evidence(i, s.session_ids[(i + 2) % 18])
    # gate 7 (SLO): 22 completed jobs, 20 within 3600s, 2 outside
    for i in range(20):
        await s.add_job(i, within_slo=True, claim=s.claim_ids[i])
    for i in range(20, 22):
        await s.add_job(i, within_slo=False, claim=s.claim_ids[i])
    # gate 9 (incidents): 2 idempotency + 1 records_inconsistent
    await s.add_alert(s.session_ids[0], 1, "idempotency_key_conflict")
    await s.add_alert(s.session_ids[1], 1, "idempotency_key_conflict")
    await s.add_alert(s.session_ids[2], 1, "records_inconsistent")
    # gate 4 (near-dup): 2 repeat_cycle_detected
    await s.add_repeat_cycle(s.session_ids[5], s.question_ids[5])
    await s.add_repeat_cycle(s.session_ids[6], s.question_ids[6])
    # gate 8 (pending/invalid ancestor): 2 pending + 1 invalid heads,
    # one current claim (claim 30) depends on a pending claim (44)
    await s.add_claim(44, session=s.session_ids[0], head_state="pending")
    await s.add_claim(45, session=s.session_ids[1], head_state="pending")
    await s.add_claim(46, session=s.session_ids[2], head_state="invalid")
    await s.add_dependency(30, 44, s.session_ids[0])
    # gate 10 (blind provenance): claims 47..51 have a broken source
    # (source row missing) → 5 of 47 blind claims incomplete
    for i in range(47, 52):
        await s.add_claim(
            i, session=s.session_ids[i % 24], source_exists=False
        )
    # gate 11 (blind scope): claims 52..59 have an empty assessed_scope
    for i in range(52, 60):
        await s.add_claim(i, session=s.session_ids[i % 24], scope={})
    return s


@pytest.mark.asyncio
async def test_gates_rich_dataset(migrated_db: tuple[str, AsyncEngine]) -> None:
    """All 11 gates on a rich seeded dataset."""
    _scratch, engine = migrated_db
    # the run row is created BEFORE the sessions (its started_at is the
    # window start) — same order as the operational eval-run driver
    thresholds = {
        "new_supported_refuted_e2": 0.80,
        "external_temporal_e3": 1.00,
        "eligible_sessions_with_outcome": 0.60,
        "near_duplicate_questions": 0.15,
        "significant_claim_reuse": 0.25,
        "due_stale_time_sensitive": 0.20,
        "reassessment_slo_seconds": 3600,
        "current_pending_invalid_ancestor": 0,
        "high_severity_incidents": 0,
        "blind_provenance_path": 0.90,
        "blind_scope": 0.80,
    }
    # blind_sample_size=100 > current claims (57) → the sample covers
    # every current claim, so the blind-gate numerators are exact
    run = await _mk_run(engine, thresholds=thresholds, size=100)
    await _seed_rich(engine)
    factory = async_sessionmaker(engine)

    async with factory() as db:
        gates = await compute_gates(db, run=run)

    assert len(gates) == 11
    g1 = gates["new_supported_refuted_e2"]
    # 16 E2 + 2 E3 + 20 temporal + 5 broken + 8 empty-scope = 53
    # supported/refuted new claims; all but the 2 E1 refuted are E2+
    assert g1["denominator"] == 53 and g1["numerator"] == 51
    assert g1["outcome"] == "passed"

    g2 = gates["external_temporal_e3"]
    assert g2["denominator"] == 20 and g2["numerator"] == 19
    assert g2["outcome"] == "failed"  # 0.95 < 1.00

    g3 = gates["eligible_sessions_with_outcome"]
    assert g3["denominator"] == 23 and g3["numerator"] == 20
    assert g3["outcome"] == "passed"

    g4 = gates["near_duplicate_questions"]
    assert g4["denominator"] == 24 and g4["numerator"] == 2
    assert g4["outcome"] == "passed"  # 0.083 <= 0.15

    g5 = gates["significant_claim_reuse"]
    # 13 second-evidence claims + claim 30 (its dependency row carries
    # a second session) = 14
    assert g5["denominator"] == 51 and g5["numerator"] == 14
    assert g5["outcome"] == "passed"  # 0.275 >= 0.25

    g6 = gates["due_stale_time_sensitive"]
    assert g6["denominator"] == 20 and g6["numerator"] == 3
    assert g6["outcome"] == "passed"  # 0.15 <= 0.20

    g7 = gates["reassessment_slo"]
    assert g7["denominator"] == 22 and g7["numerator"] == 20
    assert g7["outcome"] == "failed"  # 0.909 < 1.0

    g8 = gates["current_pending_invalid_ancestor"]
    assert g8["numerator"] == 1
    assert g8["outcome"] == "failed"  # 1 > 0

    g9 = gates["high_severity_incidents"]
    assert g9["numerator"] == 3
    assert g9["outcome"] == "failed"  # 3 > 0

    g10 = gates["blind_provenance_path"]
    # full sample (57 current claims): 5 have a missing source, 3
    # have no linked evidence at all → 49 complete
    assert g10["denominator"] == 57 and g10["numerator"] == 49
    assert g10["outcome"] == "failed"  # 0.859 < 0.90

    g11 = gates["blind_scope"]
    # full sample: 8 have an empty assessed_scope
    assert g11["denominator"] == 57 and g11["numerator"] == 49
    assert g11["outcome"] == "passed"  # 0.86 >= 0.80

    # 95% Wilson CI on every ratio gate (§22.2: интервал публикуется;
    # reporting only — исходы выше не изменились)
    ratio_gates = [g for k, g in gates.items() if k not in (
        "current_pending_invalid_ancestor",
        "high_severity_incidents",
    )]
    for g in ratio_gates:
        ci = g["ci95"]
        assert g["denominator"] > 0
        assert ci["low"] <= g["numerator"] / g["denominator"] <= ci["high"]
    assert g1["ci95"] == {"low": 0.8725, "high": 0.9896}
    assert g6["ci95"] == {"low": 0.0524, "high": 0.3604}


@pytest.mark.asyncio
async def test_gates_empty_db(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Empty domain: sample gates → insufficient_sample."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine)  # default thresholds (SLO null)
    factory = async_sessionmaker(engine)
    async with factory() as db:
        gates = await compute_gates(db, run=run)
    for name in (
        "new_supported_refuted_e2",
        "external_temporal_e3",
        "eligible_sessions_with_outcome",
        "near_duplicate_questions",
        "significant_claim_reuse",
        "due_stale_time_sensitive",
        "reassessment_slo",
        "current_pending_invalid_ancestor",
        "blind_provenance_path",
        "blind_scope",
    ):
        assert gates[name]["outcome"] == "insufficient_sample", name
    # the zero-count incident gate passes vacuously (no incidents)
    assert gates["high_severity_incidents"]["numerator"] == 0
    assert gates["high_severity_incidents"]["outcome"] == "passed"


@pytest.mark.asyncio
async def test_gates_due_stale_counts_expired_without_reassessment_or_flip(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """T7.27 (EVAL-4d, ADR-0014): the gate measures the §8.6/T3.7 rule
    at the computation instant, from ``reverify_after`` — not the
    stored ``freshness_status`` column.

    The data state is exactly what EVAL-4d left behind: every current
    temporal claim stores ``freshness_status='fresh'`` because the
    v4→v5 flip never happened and no reassessment was ever triggered.
    The gate must still count the expired claims — with NO
    reassessment job in the queue and NO activation flip. A claim
    stored 'due' whose deadline has not passed is NOT counted: the
    gate follows the rule, never the display cache."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine)  # default thresholds: due_stale 0.20
    now = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
    s = _Seeder(engine)
    await s.setup()
    # 17 within the deadline + 3 EXPIRED (the stored column says
    # 'fresh' on all of them — the EVAL-4d state) + 1 stored 'due'
    # but not yet expired + 1 without a deadline (unknown)
    for i in range(17):
        await s.add_claim(
            i, session=None, ctype="temporal_fact",
            as_of=now - timedelta(days=20), reverify_after=now + timedelta(days=10),
        )
    for i in range(17, 20):
        await s.add_claim(
            i, session=None, ctype="temporal_fact",
            as_of=now - timedelta(days=50), reverify_after=now - timedelta(days=20),
        )
    await s.add_claim(
        20, session=None, ctype="temporal_fact", freshness="due",
        as_of=now - timedelta(days=20), reverify_after=now + timedelta(days=10),
    )
    await s.add_claim(
        21, session=None, ctype="temporal_fact", freshness="unknown",
        as_of=None, reverify_after=None,
    )
    # the state under test: no reassessment jobs at all, no flip
    factory = async_sessionmaker(engine)
    async with factory() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM reassessment_jobs"))
        ).scalar_one() == 0
        head = (
            await db.execute(
                text(
                    "SELECT activating_config_snapshot_id FROM "
                    "runtime_config_heads WHERE scope = 'global'"
                )
            )
        ).scalar_one()
        assert head is None
        gates = await compute_gates(db, run=run, now=now)

    g6 = gates["due_stale_time_sensitive"]
    # 3 expired of 22 current temporal claims — counted from
    # reverify_after alone, 0.136 <= 0.20
    assert g6["denominator"] == 22 and g6["numerator"] == 3
    assert g6["ratio"] == 0.1364
    assert g6["outcome"] == "passed"


@pytest.mark.asyncio
async def test_gates_due_stale_eval4d_shape(migrated_db: tuple[str, AsyncEngine]) -> None:
    """T7.27 regression, the EVAL-4d form on synthetic data: 28 current
    ``temporal_fact`` claims, 22 with ``reverify_after`` already in the
    past — all 28 stored ``freshness_status='fresh'`` (no flip, no
    reassessment). The legacy gate reported 0/28 ``passed``; the
    rule-based gate must report 22/28 (0.7857 >= 0.20) → ``failed``.
    The threshold (0.20) and direction (at most) are unchanged."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine)
    now = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
    s = _Seeder(engine)
    await s.setup()
    # the 22 overdue ones (EVAL-4d: 16 model as_of-artifacts + 6
    # by-design old-as_of questions) and the 6 within the deadline
    for i in range(22):
        await s.add_claim(
            i, session=None, ctype="temporal_fact",
            as_of=now - timedelta(days=30 + i),
            reverify_after=now - timedelta(days=100 - i),
        )
    for i in range(22, 28):
        await s.add_claim(
            i, session=None, ctype="temporal_fact",
            as_of=now - timedelta(days=10),
            reverify_after=now + timedelta(days=20),
        )
    factory = async_sessionmaker(engine)
    async with factory() as db:
        gates = await compute_gates(db, run=run, now=now)

    g6 = gates["due_stale_time_sensitive"]
    assert g6["denominator"] == 28 and g6["numerator"] == 22
    assert g6["ratio"] == 0.7857
    assert g6["threshold"] == 0.20
    assert g6["outcome"] == "failed"
    assert "ci95" in g6  # §22.2: the interval is published with the gate


@pytest.mark.asyncio
async def test_blind_sample_deterministic(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Same run row → same blind sample (seeded, stratified)."""
    from packages.evaluation.blind import blind_sample_claim_ids

    _scratch, engine = migrated_db
    seeder = await _seed_rich(engine)
    run = await _mk_run(engine, seed=7)
    factory = async_sessionmaker(engine)
    async with factory() as db:
        s1 = await blind_sample_claim_ids(db, run)
        s2 = await blind_sample_claim_ids(db, run)
    assert s1 == s2
    # only claims with a CURRENT head enter the sample: the pending/
    # invalid heads (claims 44..46) are excluded
    assert len(s1) == min(50, len(seeder.claim_ids) - 3)


# ---------------------------------------------------------------------------
# T7.28 (ADR-0015): boundary tests — EVERY ratio gate exactly at its
# §22.2 threshold and just off it. The spec wording (ARCHITECTURE.md:
# 2602–2611) fixes the comparison direction per gate:
#   «≥ X%» (at_least)  → exactly at the threshold PASSES;
#   «≤ X%» (at_most)   → exactly at the threshold PASSES;
#   «< X%» (below, due_stale_time_sensitive) → exactly at the threshold
#   FAILS (strict: 6/30 = 0.200 → failed, 6/31 = 0.1935 → passed).
# The Wilson-95 interval is pinned in every assertion: it is
# reporting-only (§22.2) and must not change with the direction fix.
# ---------------------------------------------------------------------------


async def _gates(
    engine: AsyncEngine, run: EvaluationRun, now: datetime | None = None
) -> dict[str, dict[str, Any]]:
    factory = async_sessionmaker(engine)
    async with factory() as db:
        return await compute_gates(db, run=run, now=now)


@pytest.mark.asyncio
async def test_gate1_new_e2_exactly_at_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g1 (at_least, spec «≥80%»): 16/20 = 0.80 exactly → passed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"new_supported_refuted_e2": 0.80})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(i, state="succeeded")
        await s.add_claim(i, session=s.session_ids[i], grade="E2" if i < 16 else "E1")
    g = (await _gates(engine, run))["new_supported_refuted_e2"]
    assert (g["numerator"], g["denominator"]) == (16, 20)
    assert g["ratio"] == 0.8
    assert g["outcome"] == "passed"
    assert g["ci95"] == {"low": 0.584, "high": 0.9193}


@pytest.mark.asyncio
async def test_gate1_new_e2_below_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g1 (at_least): 15/20 = 0.75 < 0.80 → failed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"new_supported_refuted_e2": 0.80})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(i, state="succeeded")
        await s.add_claim(i, session=s.session_ids[i], grade="E2" if i < 15 else "E1")
    g = (await _gates(engine, run))["new_supported_refuted_e2"]
    assert (g["numerator"], g["denominator"]) == (15, 20)
    assert g["ratio"] == 0.75
    assert g["outcome"] == "failed"
    assert g["ci95"] == {"low": 0.5313, "high": 0.8881}


@pytest.mark.asyncio
async def test_gate2_external_e3_exactly_at_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g2 (at_least, spec «каждый … выполняет E3» = 100%): 20/20 =
    1.00 exactly → passed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"external_temporal_e3": 1.00})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(i, state="succeeded")
        await s.add_claim(i, session=s.session_ids[i], ctype="external_fact", grade="E3")
    g = (await _gates(engine, run))["external_temporal_e3"]
    assert (g["numerator"], g["denominator"]) == (20, 20)
    assert g["ratio"] == 1.0
    assert g["outcome"] == "passed"
    assert g["ci95"] == {"low": 0.8389, "high": 1.0}


@pytest.mark.asyncio
async def test_gate2_external_e3_below_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g2 (at_least): 19/20 = 0.95 < 1.00 → failed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"external_temporal_e3": 1.00})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(i, state="succeeded")
        await s.add_claim(
            i, session=s.session_ids[i], ctype="external_fact",
            grade="E3" if i < 19 else "E2",
        )
    g = (await _gates(engine, run))["external_temporal_e3"]
    assert (g["numerator"], g["denominator"]) == (19, 20)
    assert g["ratio"] == 0.95
    assert g["outcome"] == "failed"
    assert g["ci95"] == {"low": 0.7639, "high": 0.9911}


@pytest.mark.asyncio
async def test_gate3_sessions_exactly_at_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g3 (at_least, spec «≥60%»): 12/20 = 0.60 exactly → passed.
    Outcome = the question moved out of candidate (no evidence/revision)."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"eligible_sessions_with_outcome": 0.60})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(
            i, state="succeeded", qstate="verified" if i < 12 else "candidate"
        )
    g = (await _gates(engine, run))["eligible_sessions_with_outcome"]
    assert (g["numerator"], g["denominator"]) == (12, 20)
    assert g["ratio"] == 0.6
    assert g["outcome"] == "passed"
    assert g["ci95"] == {"low": 0.3866, "high": 0.7812}


@pytest.mark.asyncio
async def test_gate3_sessions_below_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g3 (at_least): 11/20 = 0.55 < 0.60 → failed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"eligible_sessions_with_outcome": 0.60})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(
            i, state="succeeded", qstate="verified" if i < 11 else "candidate"
        )
    g = (await _gates(engine, run))["eligible_sessions_with_outcome"]
    assert (g["numerator"], g["denominator"]) == (11, 20)
    assert g["ratio"] == 0.55
    assert g["outcome"] == "failed"
    assert g["ci95"] == {"low": 0.3421, "high": 0.7418}


@pytest.mark.asyncio
async def test_gate4_near_dup_exactly_at_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g4 (at_most, spec «≤15%»): 3/20 = 0.15 exactly → passed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"near_duplicate_questions": 0.15})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(i, state="succeeded")
    for i in range(3):
        await s.add_repeat_cycle(s.session_ids[i], s.question_ids[i])
    g = (await _gates(engine, run))["near_duplicate_questions"]
    assert (g["numerator"], g["denominator"]) == (3, 20)
    assert g["ratio"] == 0.15
    assert g["outcome"] == "passed"
    assert g["ci95"] == {"low": 0.0524, "high": 0.3604}


@pytest.mark.asyncio
async def test_gate4_near_dup_above_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g4 (at_most): 4/20 = 0.20 > 0.15 → failed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"near_duplicate_questions": 0.15})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(i, state="succeeded")
    for i in range(4):
        await s.add_repeat_cycle(s.session_ids[i], s.question_ids[i])
    g = (await _gates(engine, run))["near_duplicate_questions"]
    assert (g["numerator"], g["denominator"]) == (4, 20)
    assert g["ratio"] == 0.2
    assert g["outcome"] == "failed"
    assert g["ci95"] == {"low": 0.0807, "high": 0.416}


@pytest.mark.asyncio
async def test_gate5_reuse_exactly_at_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g5 (at_least, spec «≥25%»): 5/20 = 0.25 exactly → passed.
    Reused = referenced by ≥2 distinct sessions (own evidence + one
    second-evidence row from a different session)."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"significant_claim_reuse": 0.25})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(i, state="succeeded")
        await s.add_claim(i, session=s.session_ids[i], grade="E2")
    for i in range(5):
        await s.add_second_evidence(i, s.session_ids[i + 1])
    g = (await _gates(engine, run))["significant_claim_reuse"]
    assert (g["numerator"], g["denominator"]) == (5, 20)
    assert g["ratio"] == 0.25
    assert g["outcome"] == "passed"
    assert g["ci95"] == {"low": 0.1119, "high": 0.4687}


@pytest.mark.asyncio
async def test_gate5_reuse_below_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g5 (at_least): 4/20 = 0.20 < 0.25 → failed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"significant_claim_reuse": 0.25})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_session(i, state="succeeded")
        await s.add_claim(i, session=s.session_ids[i], grade="E2")
    for i in range(4):
        await s.add_second_evidence(i, s.session_ids[i + 1])
    g = (await _gates(engine, run))["significant_claim_reuse"]
    assert (g["numerator"], g["denominator"]) == (4, 20)
    assert g["ratio"] == 0.2
    assert g["outcome"] == "failed"
    assert g["ci95"] == {"low": 0.0807, "high": 0.416}


@pytest.mark.asyncio
async def test_gate6_due_stale_exactly_at_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g6 (below, spec «<20%» — STRICT, ADR-0015): 6/30 = 0.200 exactly
    → failed. Under the pre-T7.28 direction (at_most, `ratio <=
    threshold`) this data reported passed — the boundary outcome
    changed, the threshold (0.20) did not."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"due_stale_time_sensitive": 0.20})
    now = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
    s = _Seeder(engine)
    await s.setup()
    for i in range(30):
        # the 6 by-design overdue (reverify_after in the past) and the
        # 24 within the deadline
        await s.add_claim(
            i, session=None, ctype="temporal_fact",
            as_of=now - timedelta(days=31) if i < 6 else now - timedelta(days=10),
            reverify_after=now - timedelta(days=1) if i < 6 else now + timedelta(days=20),
        )
    g = (await _gates(engine, run, now=now))["due_stale_time_sensitive"]
    assert (g["numerator"], g["denominator"]) == (6, 30)
    assert g["ratio"] == 0.2
    assert g["threshold"] == 0.20
    assert g["outcome"] == "failed"  # 0.200 is NOT < 0.20
    assert g["ci95"] == {"low": 0.0951, "high": 0.3731}


@pytest.mark.asyncio
async def test_gate6_due_stale_just_below_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g6 (below): 6/31 = 0.1935 < 0.20 → passed — one more temporal
    claim in the denominator is enough (the corpus-v3 pre-registration
    N ≥ 31, EVAL-4-freeze §3.1)."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"due_stale_time_sensitive": 0.20})
    now = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
    s = _Seeder(engine)
    await s.setup()
    for i in range(31):
        await s.add_claim(
            i, session=None, ctype="temporal_fact",
            as_of=now - timedelta(days=31) if i < 6 else now - timedelta(days=10),
            reverify_after=now - timedelta(days=1) if i < 6 else now + timedelta(days=20),
        )
    g = (await _gates(engine, run, now=now))["due_stale_time_sensitive"]
    assert (g["numerator"], g["denominator"]) == (6, 31)
    assert g["ratio"] == 0.1935
    assert g["threshold"] == 0.20
    assert g["outcome"] == "passed"  # 0.1935 < 0.20
    assert g["ci95"] == {"low": 0.0919, "high": 0.3628}


@pytest.mark.asyncio
async def test_gate7_slo_exactly_at_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g7 (at_least, spec «укладываются в SLO» = all completed jobs,
    threshold 1.0): 20/20 = 1.00 exactly → passed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"reassessment_slo_seconds": 3600})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_claim(i, session=None)
        await s.add_job(i, within_slo=True, claim=s.claim_ids[i])
    g = (await _gates(engine, run))["reassessment_slo"]
    assert (g["numerator"], g["denominator"]) == (20, 20)
    assert g["ratio"] == 1.0
    assert g["outcome"] == "passed"
    assert g["slo_seconds"] == 3600.0
    assert g["ci95"] == {"low": 0.8389, "high": 1.0}


@pytest.mark.asyncio
async def test_gate7_slo_below_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g7 (at_least): 19/20 = 0.95 < 1.00 (one job outside the SLO) →
    failed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"reassessment_slo_seconds": 3600})
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_claim(i, session=None)
        await s.add_job(i, within_slo=i < 19, claim=s.claim_ids[i])
    g = (await _gates(engine, run))["reassessment_slo"]
    assert (g["numerator"], g["denominator"]) == (19, 20)
    assert g["ratio"] == 0.95
    assert g["outcome"] == "failed"
    assert g["ci95"] == {"low": 0.7639, "high": 0.9911}


@pytest.mark.asyncio
async def test_gate10_blind_provenance_exactly_at_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g10 (at_least, spec «≥90%»): 18/20 = 0.90 exactly → passed
    (2 sampled claims with a missing source row)."""
    _scratch, engine = migrated_db
    run = await _mk_run(
        engine, thresholds={"blind_provenance_path": 0.90}, size=100
    )
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_claim(i, session=None, source_exists=i < 18)
    g = (await _gates(engine, run))["blind_provenance_path"]
    assert (g["numerator"], g["denominator"]) == (18, 20)
    assert g["ratio"] == 0.9
    assert g["outcome"] == "passed"
    assert g["ci95"] == {"low": 0.699, "high": 0.9721}


@pytest.mark.asyncio
async def test_gate10_blind_provenance_below_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g10 (at_least): 17/20 = 0.85 < 0.90 → failed."""
    _scratch, engine = migrated_db
    run = await _mk_run(
        engine, thresholds={"blind_provenance_path": 0.90}, size=100
    )
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_claim(i, session=None, source_exists=i < 17)
    g = (await _gates(engine, run))["blind_provenance_path"]
    assert (g["numerator"], g["denominator"]) == (17, 20)
    assert g["ratio"] == 0.85
    assert g["outcome"] == "failed"
    assert g["ci95"] == {"low": 0.6396, "high": 0.9476}


@pytest.mark.asyncio
async def test_gate11_blind_scope_exactly_at_threshold_passes(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g11 (at_least, spec «≥80%»): 16/20 = 0.80 exactly → passed
    (4 sampled claims with an empty assessed_scope)."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"blind_scope": 0.80}, size=100)
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_claim(i, session=None, scope={} if i >= 16 else None)
    g = (await _gates(engine, run))["blind_scope"]
    assert (g["numerator"], g["denominator"]) == (16, 20)
    assert g["ratio"] == 0.8
    assert g["outcome"] == "passed"
    assert g["ci95"] == {"low": 0.584, "high": 0.9193}


@pytest.mark.asyncio
async def test_gate11_blind_scope_below_threshold_fails(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """g11 (at_least): 15/20 = 0.75 < 0.80 → failed."""
    _scratch, engine = migrated_db
    run = await _mk_run(engine, thresholds={"blind_scope": 0.80}, size=100)
    s = _Seeder(engine)
    await s.setup()
    for i in range(20):
        await s.add_claim(i, session=None, scope={} if i >= 15 else None)
    g = (await _gates(engine, run))["blind_scope"]
    assert (g["numerator"], g["denominator"]) == (15, 20)
    assert g["ratio"] == 0.75
    assert g["outcome"] == "failed"
    assert g["ci95"] == {"low": 0.5313, "high": 0.8881}
