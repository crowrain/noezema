"""Scenario (DB): T7.19 — the gates count exactly ONE head per claim
after a mid-run online activation (§14.1, §8.7.2).

EVAL-3d post-mortem: the mid-run v2→v3 activation left every claim
with a head on BOTH snapshots (24 claims with a current head on each,
plus 10 claims with a head only on v3 and 1 — the quiesce-race claim
of session 6f45deea — with a head only on the superseded v2). The
pre-fix gates had no snapshot filter: they counted head ROWS (25
current on v2 + 34 on v3 = 59 for 35 claims), inflated every
head-based denominator, distorted the ratios, and the blind-gate
per-claim queries raised ``sqlalchemy.exc.MultipleResultsFound``
(killing the run in ``compute_gates``).

Rule under test (docs/eval/EVAL-3-freeze.md §10): the current
knowledge of a claim is the head of the ACTIVE snapshot, resolved
through the runtime pointer ``runtime_config_heads.
active_config_snapshot_id`` (pointer equality, §14.1 — NOT
``config_snapshots.activation_state``; §8.7.2 «Query/Memory Service
сначала разрешает effective snapshot через runtime pointer и только
затем читает соответствующий head»). A claim without a head on the
active snapshot has no current lifecycle under the effective config
(the query path, ``MemoryService.claim_view``, returns None for it)
and is NOT counted. The fix changes the head bookkeeping only — the
§22.2 thresholds, gate definitions and denominators-by-meaning are
untouched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.evaluation.blind import blind_sample_claim_ids
from packages.evaluation.gates import compute_gates
from packages.evaluation.service import EvaluationRun, create_evaluation_run

pytestmark = [pytest.mark.scenario]

THRESHOLDS: dict[str, Any] = {
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

# The claim set, shared by the activated and the no-activation runs so
# the numerators/denominators are directly comparable. Columns:
# (claim_type, A-side status, A-side grade, B-side status, B-side grade,
#  freshness, has_A_head). idx 0-3: fast path (unchanged rules, same
# values on both sides); idx 4-7: re-evaluated (supported on A,
# hypothesis on B — the old rule would count the A side); idx 8-12:
# the rest, idx 12 created AFTER the flip (head on B only).
CLAIMS: list[tuple[str, str | None, str | None, str, str, str, bool]] = [
    ("temporal_fact", "supported", "E3", "supported", "E3", "due", True),
    ("temporal_fact", "supported", "E3", "supported", "E3", "due", True),
    ("temporal_fact", "supported", "E3", "supported", "E3", "fresh", True),
    ("temporal_fact", "supported", "E3", "supported", "E3", "fresh", True),
    ("temporal_fact", "supported", "E3", "hypothesis", "E1", "fresh", True),
    ("temporal_fact", "supported", "E3", "hypothesis", "E1", "fresh", True),
    ("temporal_fact", "supported", "E3", "hypothesis", "E1", "fresh", True),
    ("temporal_fact", "supported", "E3", "hypothesis", "E1", "fresh", True),
    ("local_observation", "supported", "E2", "supported", "E2", "fresh", True),
    ("local_observation", "supported", "E2", "supported", "E2", "fresh", True),
    ("computed_result", "refuted", "E1", "refuted", "E1", "fresh", True),
    ("external_fact", "hypothesis", "E1", "hypothesis", "E1", "fresh", True),
    ("temporal_fact", None, None, "supported", "E3", "due", False),
]

# Expected gate numbers for this set, counting exactly the B (active)
# head of each claim — identical with and without the activation:
#  g1: B-side supported|refuted = 4 (E3) + 2 (E2) + 1 (refuted E1)
#      + 1 (idx 12, E3) = 8; E2+ = 7
#  g2: B-side external|temporal supported|refuted = 4 (E3) + 1 (E3) = 5,
#      all E3
#  g5: significant (E2+ supported|disputed|refuted on B) = 7; reused
#      (second evidence from another session) = idx 0, 1 → 2
#  g6: temporal claims with a current B head = 8 + 1 = 9; due = 3
#  g8: 13 current heads on B, 0 violations
#  g10/g11: all 13 have a complete provenance path and a declared scope
EXPECTED: dict[str, tuple[int, int]] = {
    "new_supported_refuted_e2": (7, 8),
    "external_temporal_e3": (5, 5),
    "significant_claim_reuse": (2, 7),
    "due_stale_time_sensitive": (3, 9),
    "blind_provenance_path": (13, 13),
    "blind_scope": (13, 13),
}
EXPECTED_G8 = (0, 13)  # numerator, denominator


def _uid(seed: str) -> uuid.UUID:
    """Deterministic uuid per seed (stable across runs)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"noezema-t719/{seed}")


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj)


async def _mk_run(
    engine: AsyncEngine, *, cs_id: uuid.UUID, seed: int = 42, size: int = 50
) -> EvaluationRun:
    """Create the run row frozen on ``cs_id`` (the pre-activation
    snapshot — as the operational driver does)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        run = await create_evaluation_run(
            db,
            label="t719",
            config_snapshot_id=cs_id,
            model_fingerprint={"model": "test"},
            rules_version="rules-v1",
            rules_hash="0" * 64,
            thresholds=dict(THRESHOLDS),
            blind_sample_seed=seed,
            blind_sample_size=size,
        )
        assert run is not None
        return run


class _ActivationSeeder:
    """Bootstrap (A) + one online candidate (B); the flip moves the
    runtime pointer to B (§8.7.2 atomic flip — in the test a direct
    pointer update, the flip mechanics are covered by
    tests/scenario/test_online_activation.py)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.factory = async_sessionmaker(engine, expire_on_commit=False)
        self.a_id: uuid.UUID | None = None
        self.b_id: uuid.UUID | None = None

    async def _exec(self, sql: str, params: dict[str, Any]) -> None:
        async with self.factory() as db, db.begin():
            await db.execute(text(sql), params)

    async def _scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        async with self.factory() as db:
            return (await db.execute(text(sql), params or {})).scalar_one()

    async def setup(self) -> None:
        self.a_id = await self._scalar(
            "SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap'"
        )
        assert self.a_id is not None
        self.b_id = _uid("snapshot-b")
        await self._exec(
            "INSERT INTO config_snapshots (id, base_snapshot_id, payload_sha256, "
            " sha256, activation_mode, activation_state, model, embeddings, "
            " prompts, policy, curiosity, token_budgets, session_limits, "
            " activation_limits, claim_type_rules) "
            "VALUES (:id, :base, :ps, :s, 'online', 'active', "
            "'{}', '{}', '{}', '{}', '{}', '{}', '{}', '{}', '{}')",
            {"id": self.b_id, "base": self.a_id, "ps": "b" * 64, "s": "c" * 64},
        )

    async def flip(self) -> None:
        """The atomic flip: the runtime pointer now resolves to B."""
        await self._exec(
            "UPDATE runtime_config_heads SET active_config_snapshot_id = :b "
            "WHERE scope = 'global'",
            {"b": self.b_id},
        )

    async def add_session(self, idx: int) -> uuid.UUID:
        sid = _uid(f"session-{idx}")
        qid = _uid(f"question-{idx}")
        # AFTER the run row's started_at (the window start)
        started = datetime.now(UTC) + timedelta(minutes=idx + 1)
        await self._exec(
            "INSERT INTO questions (id, text, origin, state) "
            "VALUES (:id, :t, 'seeded', 'verified')",
            {"id": qid, "t": f"q {idx}"},
        )
        await self._exec(
            "INSERT INTO sessions (id, state, config_snapshot_id, question_id, "
            "started_at, finished_at, termination_reason) "
            "VALUES (:id, 'succeeded', :cs, :q, :sa, :fa, 'budget')",
            {
                "id": sid,
                "cs": self.a_id,
                "q": qid,
                "sa": started,
                "fa": started + timedelta(minutes=5),
            },
        )
        return sid

    async def add_claim(
        self,
        idx: int,
        snap_id: uuid.UUID,
        *,
        ctype: str = "temporal_fact",
        status: str = "supported",
        grade: str = "E3",
        freshness: str = "fresh",
        scope: dict[str, Any] | None = None,
        evidence: bool = True,
        session: uuid.UUID,
        side: str = "a",
        claim_row: bool = True,
        reverify_after: datetime | None = None,
    ) -> uuid.UUID:
        """The claim row is shared per idx (one claim; ``claim_row``
        for the first side only); the assessment / evidence / source
        rows are per (claim, snapshot) side — the same claim gets a
        separate assessment per head, exactly like the online
        activation's shadow heads + reassessment.

        T7.27 (ADR-0014): the gate evaluates the §8.6 rule over
        reverify_after, so the seeded rows stay self-consistent — when
        ``reverify_after`` is not given it is derived from the stored
        ``freshness`` (past for due/stale, future for fresh)."""
        cid = _uid(f"claim-{idx}")
        aid = _uid(f"assessment-{side}-{idx}")
        eid = _uid(f"evidence-{side}-{idx}")
        srcid = _uid(f"source-{side}-{idx}")
        if reverify_after is None:
            now = datetime.now(UTC)
            reverify_after = (
                now - timedelta(days=30)
                if freshness in ("due", "stale")
                else now + timedelta(days=30)
            )
        if evidence:
            await self._exec(
                "INSERT INTO sources (id, source_type, canonical_uri) "
                "VALUES (:id, 'local_corpus', :u)",
                {"id": srcid, "u": f"corpus://doc{idx}"},
            )
        if claim_row:
            await self._exec(
                "INSERT INTO claims (id, statement, claim_type, freshness_status, "
                "created_in_session, observed_at, reverify_after) "
                "VALUES (:id, :st, :ct, :f, :s, now(), :rv)",
                {"id": cid, "st": f"statement {idx}", "ct": ctype, "f": freshness, "s": session,
                 "rv": reverify_after},
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
                "sc": _json(scope if scope is not None else {"topic": f"t{idx}"}),
                "s": session,
            },
        )
        if evidence:
            await self._exec(
                "INSERT INTO evidence (id, claim_id, relation, evidence_kind, "
                "identity_hash, scope, source_id, chunk_id, created_in_session) "
                "VALUES (:id, :c, 'supports', 'source_assertion', :ih, "
                "CAST(:sc AS jsonb), :src, 'c1', :s)",
                {
                    "id": eid,
                    "c": cid,
                    "ih": f"evid-{side}-{idx}",
                    "sc": _json({"scope": f"s{idx}"}),
                    "src": srcid,
                    "s": session,
                },
            )
            await self._exec(
                "INSERT INTO assessment_evidence (assessment_id, evidence_id, role) "
                "VALUES (:a, :e, 'support')",
                {"a": aid, "e": eid},
            )
        await self._exec(
            "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, "
            "assessment_state, current_assessment_id, epistemic_status, "
            "prepared_by) VALUES (:c, :cs, 'current', :a, :es, 'session')",
            {"c": cid, "cs": snap_id, "a": aid, "es": status},
        )
        return cid

    async def add_second_evidence(self, idx: int, session: uuid.UUID) -> None:
        """A second evidence row from a DIFFERENT session (reuse signal)."""
        cid = _uid(f"claim-{idx}")
        eid = _uid(f"evidence-reuse-{idx}")
        srcid = _uid(f"source-reuse-{idx}")
        await self._exec(
            "INSERT INTO sources (id, source_type, canonical_uri) "
            "VALUES (:id, 'local_corpus', :u)",
            {"id": srcid, "u": f"corpus://docr{idx}"},
        )
        await self._exec(
            "INSERT INTO evidence (id, claim_id, relation, evidence_kind, "
            "identity_hash, scope, source_id, chunk_id, created_in_session) "
            "VALUES (:id, :c, 'supports', 'source_assertion', :ih, "
            "CAST(:sc AS jsonb), :src, 'c2', :s)",
            {
                "id": eid,
                "c": cid,
                "ih": f"reuse-{idx}",
                "sc": _json({"r": 1}),
                "src": srcid,
                "s": session,
            },
        )


def _assert_expected(gates: dict[str, dict[str, Any]]) -> None:
    for name, (num, den) in EXPECTED.items():
        g = gates[name]
        assert (g["numerator"], g["denominator"]) == (num, den), name
    g8 = gates["current_pending_invalid_ancestor"]
    assert (g8["numerator"], g8["denominator"]) == EXPECTED_G8


async def _seed_claims(
    s: _ActivationSeeder,
    sessions: list[uuid.UUID],
    *,
    a_side: bool,
) -> None:
    """The shared claim set: on B (always) + on A only when ``a_side``
    (the activated case — the A-side heads are the pre-activation
    state, exactly one per (claim, snapshot) pair)."""
    for i, (ct, ast, ag, bst, bg, fr, has_a) in enumerate(CLAIMS):
        if a_side and has_a:
            assert ast is not None and ag is not None
            await s.add_claim(
                i,
                s.a_id,
                ctype=ct,
                status=ast,
                grade=ag,
                freshness=fr,
                session=sessions[i],
                side="a",
            )
        await s.add_claim(
            i,
            s.b_id,
            ctype=ct,
            status=bst,
            grade=bg,
            freshness=fr,
            session=sessions[i],
            side="b",
            claim_row=not (a_side and has_a),
        )


@pytest.mark.asyncio
async def test_gates_mid_run_activation_count_each_claim_once(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """The run with the mid-run activation: compute_gates does not
    raise and counts every claim exactly once (the active-snapshot
    head), while the blind sample contains each claim at most once."""
    _scratch, engine = migrated_db
    s = _ActivationSeeder(engine)
    await s.setup()
    run = await _mk_run(engine, cs_id=s.a_id)
    sessions = [await s.add_session(i) for i in range(13)]
    await _seed_claims(s, sessions, a_side=True)
    await s.flip()
    await s.add_second_evidence(0, sessions[5])
    await s.add_second_evidence(1, sessions[6])
    factory = async_sessionmaker(engine)
    async with factory() as db:
        gates = await compute_gates(db, run=run)
        sample = await blind_sample_claim_ids(db, run)
    _assert_expected(gates)
    assert gates["eligible_sessions_with_outcome"]["denominator"] == 13
    assert gates["near_duplicate_questions"]["numerator"] == 0
    # exactly one entry per claim — no snapshot duplicates
    assert len(sample) == 13
    assert len(set(sample)) == 13
    # the re-evaluated claims (idx 4-7) are hypothesis on B — NOT
    # counted as supported (the old rule would count their A heads)
    assert set(sample) == {_uid(f"claim-{i}") for i in range(13)}


@pytest.mark.asyncio
async def test_gates_no_activation_same_numbers(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """The same claim set WITHOUT the activation (heads on the active
    snapshot only, same values as the B side of the activated case):
    identical numerators and denominators — the snapshot filter
    changes nothing for a run without mid-run activation."""
    _scratch, engine = migrated_db
    s = _ActivationSeeder(engine)
    await s.setup()
    run = await _mk_run(engine, cs_id=s.a_id)
    sessions = [await s.add_session(i) for i in range(13)]
    # no A-side heads at all, no flip: the B-side values live on the
    # ACTIVE snapshot (B) directly
    for i, (ct, _ast, _ag, bst, bg, fr, _has_a) in enumerate(CLAIMS):
        await s.add_claim(
            i,
            s.b_id,
            ctype=ct,
            status=bst,
            grade=bg,
            freshness=fr,
            session=sessions[i],
            side="b",
        )
    await s.flip()
    await s.add_second_evidence(0, sessions[5])
    await s.add_second_evidence(1, sessions[6])
    factory = async_sessionmaker(engine)
    async with factory() as db:
        gates = await compute_gates(db, run=run)
        sample = await blind_sample_claim_ids(db, run)
    _assert_expected(gates)
    assert len(sample) == 13
    assert len(set(sample)) == 13


@pytest.mark.asyncio
async def test_gates_superseded_only_head_not_counted(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """Regression (T7.19, п.1): a claim with a current head ONLY on
    the superseded snapshot and no head on the active snapshot (the
    EVAL-3d quiesce-race data state — claim 8bbbb06a, session
    6f45deea) has no current lifecycle under the effective config
    (§14.1: current lifecycle is resolved through the runtime
    pointer; MemoryService.claim_view returns None for it) and is
    NOT counted in any gate nor in the blind sample."""
    _scratch, engine = migrated_db
    s = _ActivationSeeder(engine)
    await s.setup()
    run = await _mk_run(engine, cs_id=s.a_id)
    sessions = [await s.add_session(i) for i in range(14)]
    await _seed_claims(s, sessions, a_side=True)
    # the superseded-only claim: current supported/E3 temporal/due on
    # A, NOTHING on B — full provenance on its A head (if the gate
    # read the wrong head, it would be counted)
    await s.add_claim(
        13,
        s.a_id,
        ctype="temporal_fact",
        status="supported",
        grade="E3",
        freshness="due",
        session=sessions[13],
        side="a",
    )
    await s.flip()
    await s.add_second_evidence(0, sessions[5])
    await s.add_second_evidence(1, sessions[6])
    factory = async_sessionmaker(engine)
    async with factory() as db:
        gates = await compute_gates(db, run=run)
        sample = await blind_sample_claim_ids(db, run)
    # identical to the plain activated case: the claim is not counted
    _assert_expected(gates)
    assert _uid("claim-13") not in sample
    assert len(sample) == 13
    # and the gate-8 denominator is the current heads of the ACTIVE
    # snapshot only (13, not 14)
    assert gates["current_pending_invalid_ancestor"]["denominator"] == 13


@pytest.mark.asyncio
async def test_blind_gates_use_active_head_with_duplicate_heads(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """blind_provenance_path / blind_scope do not raise with a claim
    holding two current heads and read the ACTIVE head (not the
    superseded one): idx 14 is complete on A but broken on B (no
    linked evidence, empty scope) → counted incomplete; idx 15 is
    broken on A but complete on B → counted complete."""
    _scratch, engine = migrated_db
    s = _ActivationSeeder(engine)
    await s.setup()
    run = await _mk_run(engine, cs_id=s.a_id)
    sessions = [await s.add_session(i) for i in range(15)]
    await _seed_claims(s, sessions, a_side=True)
    # idx 14: A side complete, B side broken (supported, no evidence,
    # empty assessed scope)
    await s.add_claim(14, s.a_id, status="supported", grade="E3", session=sessions[14], side="a")
    await s.add_claim(
        14,
        s.b_id,
        status="supported",
        grade="E3",
        scope={},
        evidence=False,
        session=sessions[14],
        side="b",
        claim_row=False,
    )
    # idx 15: A side broken, B side complete
    await s.add_claim(15, s.a_id, status="supported", grade="E3", evidence=False, session=sessions[14], side="a")
    await s.add_claim(15, s.b_id, status="supported", grade="E3", session=sessions[14], side="b", claim_row=False)
    await s.flip()
    factory = async_sessionmaker(engine)
    async with factory() as db:
        gates = await compute_gates(db, run=run)
        sample = await blind_sample_claim_ids(db, run)
    assert len(sample) == 15
    assert len(set(sample)) == 15
    # 15 claims in the full sample; idx 14's ACTIVE head is broken
    # (idx 15's active head is complete — the broken A head is ignored)
    assert (
        gates["blind_provenance_path"]["numerator"],
        gates["blind_provenance_path"]["denominator"],
    ) == (14, 15)
    assert (gates["blind_scope"]["numerator"], gates["blind_scope"]["denominator"]) == (14, 15)
