"""Scenario: host-derived scope coverage (T7.17, §3.7, §8.7, §11.2).

The defect (T7.15): with a free-form curator the claim and the evidence
got DIFFERENT free-form scope keys for the same subject and date
(``регион`` vs ``область``, ``на дату`` vs ``as_of``); the key-by-key
coverage never matched and external/temporal claims stalled at E1
unless the question dictated the scope object verbatim — a hint the
frozen corpus does not contain.

The fix: the scope the rules engine checks is derived by the TRUSTED
HOST — the claim scope from the session's QUESTION (reference date +
named sources) and the claim's typed as_of, each evidence scope from
its PROVENANCE (the sources row's registrable domain + retrieval
time). The model's free-form scope is audited but cannot satisfy or
fail coverage; a scope that truly does not cover (a different subject
or a different date) still fails closed at E1.

The required tests:
1. claim and evidence about the SAME subject and date (the model's
   free-form keys differ in spelling) → the path to E3 is open;
2. scope that truly does not cover (different subject: a source not
   named in the question; different date: a source retrieved before
   the claim's reference date) → E1, the grade is NOT lifted;
3. full session on the fake LLM: the curator returns an arbitrary
   free-form scope, the committed claim is head current / supported /
   E3 from two independent sources.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.evidence import observation_to_evidence
from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.sessions import ORMSession
from packages.domain.schemas.staging import CuratorProposal
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService
from packages.domain.services.staging import StagingService
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.memory.service import MemoryService
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import (
    PAGES,
    FakeFetchClient,
    _fetch_sources,
    _research_obs,
    _section,
)

pytestmark = [pytest.mark.scenario]


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch the research-proxy network layer with
    ``FakeFetchClient`` (same approach as
    ``tests/scenario/test_research_evidence.py``): the only faked part is
    the HTTP client, the rest of the proxy path is real."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)

GAMMA_PAGE = (
    "An unrelated page on gamma.example about a completely different "
    "topic, shared by no question in this test."
)

QUESTION_APOLLO_DATED = (
    "По состоянию на 1 января 2026 года: подтверди первую пилотируемую "
    "посадку на Луну строго по этим двум источникам: "
    "http://alpha.example/apollo и http://beta.example/landing"
)
QUESTION_APOLLO_CURRENT = (
    "Подтверди первую пилотируемую посадку на Луну на текущую дату "
    "строго по этим двум источникам: "
    "http://alpha.example/apollo и http://beta.example/landing"
)

#: T7.18: the corpus key-rate question (relative form «на текущую дату»,
#: two named sources) — the found defect
QUESTION_KEY_RATE_RELATIVE = (
    "Какова ключевая ставка Банка России на текущую дату? Установи это "
    "утверждение строго по этим двум источникам: "
    "http://alpha.example/apollo и http://beta.example/landing"
)


async def _scalar(scratch_url: str, sql: str, params: dict | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _all(scratch_url: str, sql: str, params: dict | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).mappings().all())
    finally:
        await engine.dispose()


async def _fetch_records(
    scratch_url: str, store: FilesystemArtifactStore, urls: tuple[str, ...]
) -> list[Any]:
    """The REAL proxy fetch path for the given URLs (the network is the
    faked part) mapped to source_assertion records."""
    sources = await _fetch_sources(scratch_url, store, urls)
    assert len(sources) == len(urls)
    records = []
    for src in sources:
        rec = observation_to_evidence(
            _research_obs(
                src["url"],
                src["source_id"],
                src["original_sha256"],
                src["normalized_sha256"],
                normalized_text=PAGES[src["url"]],
            ),
            {"url": src["url"]},
        )
        assert rec is not None, f"no evidence record for {src['url']}"
        records.append(rec)
    return records


async def _apply_claim(
    scratch_url: str,
    records: list[Any],
    *,
    statement: str,
    question_text: str,
    scope: dict[str, Any],
    claim_type: str = "external_fact",
    as_of: str | None = None,
    session_created_at: datetime | None = None,
) -> None:
    """The commit boundary with a QUESTION attached to the session
    (the trusted scope anchor): staging ops + the real MemoryService
    apply (the same code the fenced final transaction runs).
    ``session_created_at`` (T7.25): the session's START on the host's
    clock — the relative-date anchor (T7.18); None = the real now()."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            snap = (
                (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
            )
            assert snap is not None
            qid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO questions (id, text, origin, priority, state) "
                    "VALUES (:id, :t, 'seeded', 1, 'candidate')"
                ),
                {"id": str(qid), "t": question_text},
            )
            sid = uuid.uuid4()
            if session_created_at is not None:
                await db.execute(
                    text(
                        "INSERT INTO sessions (id, state, config_snapshot_id, "
                        "question_id, started_at, created_at) "
                        "VALUES (:id, 'exploring', :c, :q, now(), :ca)"
                    ),
                    {
                        "id": str(sid),
                        "c": str(snap.id),
                        "q": str(qid),
                        "ca": session_created_at,
                    },
                )
            else:
                await db.execute(
                    text(
                        "INSERT INTO sessions (id, state, config_snapshot_id, question_id, "
                        "started_at) VALUES (:id, 'exploring', :c, :q, now())"
                    ),
                    {"id": str(sid), "c": str(snap.id), "q": str(qid)},
                )
            o_session = (
                (await db.execute(select(ORMSession).where(ORMSession.id == sid)))
                .scalars()
                .first()
            )
            assert o_session is not None
            audit = AuditService(db)
            staging = StagingService(HostReserveService.for_snapshot(snap))
            service = MemoryService(snap)

            claim_payload: dict[str, Any] = {
                "statement": statement,
                "claim_type": claim_type,
                # the model's free-form scope proposal (untrusted for
                # coverage; audited and stored in the staging payload)
                "scope": scope,
            }
            if as_of is not None:
                claim_payload["as_of"] = as_of
            proposal = CuratorProposal(
                summary="Research claim",
                claims=[claim_payload],
                evidence_links=[
                    {"evidence_index": i, "claim_index": 0, "relation": "supports"}
                    for i in range(len(records))
                ],
                new_questions=[],
            )
            assert proposal.validate_against(len(records), questions_max=4) == []
            await staging.record(
                db, audit, o_session, "claim", claim_payload, proposed_claims=1
            )
            for link in proposal.evidence_links:
                await staging.record(
                    db,
                    audit,
                    o_session,
                    "evidence",
                    link.model_dump(mode="json"),
                    proposed_evidence=1,
                )
            result = await service.apply_claim_staging(db, audit, o_session, records)
            assert result.problems == (), f"commit boundary problems: {result.problems}"
            assert result.claims_created == 1
            assert result.evidence_added == len(records)
            assert result.assessments == 1
    finally:
        await engine.dispose()


def _assessment(sql_where_statement: str) -> str:
    return (
        "SELECT a.effective_grade, a.epistemic_status "
        "FROM claim_assessments a JOIN claims c ON c.id = a.claim_id "
        f"WHERE c.statement LIKE '{sql_where_statement}'"
    )


# ── 1. same subject and date, free-form model keys → E3 ────────────────


@pytest.mark.asyncio
async def test_freeform_scope_same_subject_and_date_reaches_E3(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    """Required test 1: the curator's claim scope is free-form with
    keys spelled differently from anything canonical (``регион`` /
    ``на дату`` / ``объект``); the claim and the evidence are about
    the SAME subject (the question's two sources) and the SAME date —
    the host-derived scopes cover, and the claim reaches E3 supported
    from two independent source groups."""
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    records = await _fetch_records(
        scratch_url, store, ("http://alpha.example/apollo", "http://beta.example/landing")
    )
    await _apply_claim(
        scratch_url,
        records,
        statement="Аполлон-11 совершил первую пилотируемую посадку на Луну 20 июля 1969 года.",
        question_text=QUESTION_APOLLO_DATED,
        # free-form, different key spellings — the T7.15 shape
        scope={"регион": "Луна", "на дату": "1969-07-20", "объект": "Аполлон-11"},
    )

    row = await _scalar(
        scratch_url,
        _assessment("Аполлон-11 совершил%")
        + " GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None, "no assessment for the external claim"
    assert row[0] == "E3", f"expected E3, got {row[0]}"
    assert row[1] == "supported"

    # head is current (the lifecycle invariant)
    head = await _scalar(
        scratch_url,
        "SELECT h.assessment_state FROM claim_assessment_heads h "
        "JOIN claims c ON c.id = h.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%'",
    )
    assert head is not None and head[0] == "current"

    # the assessed scope is HOST-DERIVED (not the model's dict): the
    # reference date and the two named source domains
    assessed = await _scalar(
        scratch_url,
        "SELECT a.assessed_scope FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%'",
    )
    assert assessed is not None
    scope = dict(assessed[0])
    assert scope["scope_schema"] == "host-scope-v1"
    assert scope["as_of"] == "2026-01-01"
    assert scope["source_domains"] == ["alpha.example", "beta.example"]

    # every evidence row carries the host-derived scope from its
    # PROVENANCE (registrable domain + retrieval time), not a copy of
    # the model's claim scope
    ev_scopes = await _all(
        scratch_url,
        "SELECT e.scope, e.source_id FROM evidence e "
        "JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%' ORDER BY e.scope->>'source_domain'",
    )
    assert len(ev_scopes) == 2
    domains = sorted(r["scope"]["source_domain"] for r in ev_scopes)
    assert domains == ["alpha.example", "beta.example"]
    for r in ev_scopes:
        assert r["scope"]["scope_schema"] == "host-scope-v1"
        assert r["scope"]["as_of"] is not None
    assert ev_scopes[0]["source_id"] != ev_scopes[1]["source_id"]

    # the model's free-form proposal is audited, not assessed
    audit_row = await _scalar(
        scratch_url,
        "SELECT payload->'scope', payload->'assessed_scope' FROM audit_events "
        "WHERE type = 'claim_created' AND payload->>'statement' LIKE 'Аполлон-11 совершил%'",
    )
    assert audit_row is not None
    model_scope, derived_scope = audit_row
    assert model_scope == {"регион": "Луна", "на дату": "1969-07-20", "объект": "Аполлон-11"}
    assert derived_scope["scope_schema"] == "host-scope-v1"


# ── 2. fail-closed: a scope that truly does not cover ──────────────────


@pytest.mark.asyncio
async def test_source_not_named_in_question_stays_E1(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Required test 2 (different subject): one supporting source is
    NOT among the question's named sources — a different subject.
    Everything else meets the E3 rule (two independent groups, two
    source_assertion) — yet the grade is NOT lifted: E1 hypothesis,
    scope_not_covered."""
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())
    monkeypatch.setitem(PAGES, "http://gamma.example/other", GAMMA_PAGE)

    # the question names alpha + beta; the evidence is alpha + GAMMA
    records = await _fetch_records(
        scratch_url, store, ("http://alpha.example/apollo", "http://gamma.example/other")
    )
    await _apply_claim(
        scratch_url,
        records,
        statement="Аполлон-11 совершил первую пилотируемую посадку на Луну 20 июля 1969 года.",
        question_text=QUESTION_APOLLO_DATED,
        scope={"регион": "Луна", "на дату": "1969-07-20", "объект": "Аполлон-11"},
    )

    row = await _scalar(
        scratch_url,
        _assessment("Аполлон-11 совершил%")
        + " GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None
    assert row[0] == "E1", f"the grade must NOT be lifted out of scope, got {row[0]}"
    assert row[1] == "hypothesis"

    reasons = await _scalar(
        scratch_url,
        "SELECT payload->'reasons' FROM audit_events "
        "WHERE type = 'claim_assessed' AND payload->>'grade' = 'E1'",
    )
    assert reasons is not None
    assert "scope_not_covered" in reasons[0]
    # the E3 rule's OTHER requirements ARE met (two independent groups,
    # two source_assertion) — the scope is the reason, not independence
    assert "insufficient_independence" not in reasons[0]
    assert "insufficient_evidence" not in reasons[0]


@pytest.mark.asyncio
async def test_source_retrieved_before_as_of_stays_E1(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    """Required test 2 (different date): both sources are named in the
    question and independent, but they were RETRIEVED before the
    claim's reference date — a source cannot speak about a date it
    predates. E1, scope_not_covered, no grade lift."""
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    records = await _fetch_records(
        scratch_url, store, ("http://alpha.example/apollo", "http://beta.example/landing")
    )
    # the claim's reference date is TODAY (the question says "текущую
    # дату", the model's typed as_of = now) — then the sources turn out
    # to have been retrieved long ago: a different date
    as_of = datetime.now(UTC).isoformat()
    await _update(
        scratch_url,
        "UPDATE sources SET retrieved_at = '2020-01-01T00:00:00Z'",
    )
    await _apply_claim(
        scratch_url,
        records,
        statement="Аполлон-11 совершил первую пилотируемую посадку на Луну 20 июля 1969 года.",
        question_text=QUESTION_APOLLO_CURRENT,
        scope={"регион": "Луна", "на дату": "1969-07-20", "объект": "Аполлон-11"},
        as_of=as_of,
    )

    row = await _scalar(
        scratch_url,
        _assessment("Аполлон-11 совершил%")
        + " GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None
    assert row[0] == "E1", f"the grade must NOT be lifted out of scope, got {row[0]}"
    assert row[1] == "hypothesis"

    reasons = await _scalar(
        scratch_url,
        "SELECT payload->'reasons' FROM audit_events "
        "WHERE type = 'claim_assessed' AND payload->>'grade' = 'E1'",
    )
    assert reasons is not None
    assert "scope_not_covered" in reasons[0]
    # the E3 rule's OTHER requirements ARE met (two independent groups,
    # two source_assertion) — the date is the reason, not independence
    assert "insufficient_independence" not in reasons[0]
    assert "insufficient_evidence" not in reasons[0]


# ── T7.18: relative reference date is host-derived (session date) ──────


@pytest.mark.asyncio
async def test_relative_date_model_as_of_tomorrow_reaches_E3(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    """T7.18 REGRESSION (full commit boundary, the found defect): the
    question asks «на текущую дату», the model's typed as_of =
    TOMORROW, the evidence was retrieved TODAY. Under T7.17 the model's
    as_of became the reference date, so today's evidence was "before"
    it → scope_not_covered → E1. Now the host-derived reference is the
    SESSION's date (today) — the model's tomorrow does not shift it —
    so the evidence covers and the claim reaches E3 supported, head
    current."""
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    records = await _fetch_records(
        scratch_url, store, ("http://alpha.example/apollo", "http://beta.example/landing")
    )
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    await _apply_claim(
        scratch_url,
        records,
        statement="Ключевая ставка Банка России составляет 14,00%.",
        question_text=QUESTION_KEY_RATE_RELATIVE,
        # free-form model scope + an arbitrary (wrong) typed as_of — the
        # T7.17 defect shape
        scope={"объект": "ключевая ставка", "на дату": "2026-09-19"},
        claim_type="temporal_fact",
        as_of=tomorrow,
    )

    row = await _scalar(
        scratch_url,
        _assessment("Ключевая ставка Банка России%")
        + " GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None, "no assessment for the temporal claim"
    assert row[0] == "E3", f"expected E3, got {row[0]}"
    assert row[1] == "supported"

    head = await _scalar(
        scratch_url,
        "SELECT h.assessment_state, h.epistemic_status "
        "FROM claim_assessment_heads h JOIN claims c ON c.id = h.claim_id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert head is not None
    assert head[0] == "current" and head[1] == "supported"

    # the assessed reference is HOST-DERIVED: the session's date
    # (today), NOT the model's tomorrow
    assessed = await _scalar(
        scratch_url,
        "SELECT a.assessed_scope FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert assessed is not None
    scope = dict(assessed[0])
    assert scope["scope_schema"] == "host-scope-v1"
    assert scope["as_of"] == datetime.now(UTC).date().isoformat()
    assert scope["source_domains"] == ["alpha.example", "beta.example"]


# ── T7.25: cross-day reuse of a relative-date claim (decision (a)) ─────


async def _apply_claim_reuse(
    scratch_url: str,
    *,
    statement: str,
    question_text: str,
    scope: dict[str, Any],
    claim_type: str = "temporal_fact",
    as_of: str | None = None,
    session_created_at: datetime,
) -> None:
    """The commit boundary of a REUSE session (T7.25): the model proposes
    the SAME claim (dedup by statement+type) WITHOUT new evidence. The
    host re-derives the claim scope from the session's QUESTION and the
    session's date (T7.18) and re-assesses the claim against its FULL
    existing evidence set (the same code the fenced final transaction
    runs)."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            snap = (
                (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
            )
            assert snap is not None
            qid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO questions (id, text, origin, priority, state) "
                    "VALUES (:id, :t, 'seeded', 1, 'candidate')"
                ),
                {"id": str(qid), "t": question_text},
            )
            sid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, "
                    "question_id, started_at, created_at) "
                    "VALUES (:id, 'exploring', :c, :q, now(), :ca)"
                ),
                {
                    "id": str(sid),
                    "c": str(snap.id),
                    "q": str(qid),
                    "ca": session_created_at,
                },
            )
            o_session = (
                (await db.execute(select(ORMSession).where(ORMSession.id == sid)))
                .scalars()
                .first()
            )
            assert o_session is not None
            audit = AuditService(db)
            staging = StagingService(HostReserveService.for_snapshot(snap))
            service = MemoryService(snap)

            claim_payload: dict[str, Any] = {
                "statement": statement,
                "claim_type": claim_type,
                "scope": scope,
            }
            if as_of is not None:
                claim_payload["as_of"] = as_of
            proposal = CuratorProposal(
                summary="Reuse of a known claim",
                claims=[claim_payload],
                evidence_links=[],
                new_questions=[],
            )
            assert proposal.validate_against(0, questions_max=4) == []
            await staging.record(
                db, audit, o_session, "claim", claim_payload, proposed_claims=1
            )
            result = await service.apply_claim_staging(db, audit, o_session, [])
            assert result.problems == (), f"commit boundary problems: {result.problems}"
            assert result.claims_reused == 1, f"expected a reuse, got {result}"
            assert result.evidence_added == 0
            assert result.assessments == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_relative_date_reuse_next_day_same_evidence_stays_E1(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    """T7.25 (decision (a), ADR-0007 уточнение — NOT a defect): a
    relative-date claim («на текущую дату») is created at 23:50 UTC of
    day N with day-N evidence → E3 supported. Reused at 00:10 UTC of
    day N+1 (a new session, the same question, NO new evidence): the
    host re-derives the claim scope — the reference date is the NEW
    session's date (T7.18). Yesterday's evidence predates the new
    reference date and does not cover it: the SAME fail-closed
    predicate as for an explicit date (a source retrieved before the
    reference date cannot speak about it; the spec's rule is
    ``every(scope_covers_claim) == true``, §8.7). The claim honestly
    drops to E1 hypothesis (scope_not_covered): "the state as of day
    N+1" is NOT supported by day-N evidence. Freshness is untouched —
    §8.2/§8.6: expiry changes freshness, never the grade; the claim is
    simultaneously E1 (scope) and fresh (reverify_after in the future):
    the two mechanisms are independent, and the drop is NOT due/stale."""
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    # day N 23:50 UTC → day N+1 00:10 UTC (yesterday → today, so
    # reverify_after = as_of + 30d stays in the future)
    now = datetime.now(UTC)
    day_n = (now - timedelta(days=1)).date()
    day_n1 = now.date()
    assert day_n1 == day_n + timedelta(days=1)
    t1 = datetime(day_n.year, day_n.month, day_n.day, 23, 50, tzinfo=UTC)
    t2 = datetime(day_n1.year, day_n1.month, day_n1.day, 0, 10, tzinfo=UTC)

    records = await _fetch_records(
        scratch_url, store, ("http://alpha.example/apollo", "http://beta.example/landing")
    )
    # provenance: both sources were fetched at 23:50 of day N
    await _update(scratch_url, f"UPDATE sources SET retrieved_at = '{t1.isoformat()}'")

    # session 1 (day N 23:50): the claim is created, covered, E3
    await _apply_claim(
        scratch_url,
        records,
        statement="Ключевая ставка Банка России составляет 14,00%.",
        question_text=QUESTION_KEY_RATE_RELATIVE,
        scope={"объект": "ключевая ставка", "на дату": day_n.isoformat()},
        claim_type="temporal_fact",
        as_of=t1.isoformat(),
        session_created_at=t1,
    )
    row = await _scalar(
        scratch_url,
        _assessment("Ключевая ставка Банка России%")
        + " GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None, "no assessment for the temporal claim"
    assert row[0] == "E3", f"expected E3 on day N, got {row[0]}"
    assert row[1] == "supported"
    assessed = await _scalar(
        scratch_url,
        "SELECT a.assessed_scope FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert assessed is not None
    assert dict(assessed[0])["as_of"] == day_n.isoformat()

    # session 2 (day N+1 00:10): the SAME claim is reused, no new
    # evidence — the reference date is now day N+1
    await _apply_claim_reuse(
        scratch_url,
        statement="Ключевая ставка Банка России составляет 14,00%.",
        question_text=QUESTION_KEY_RATE_RELATIVE,
        scope={"объект": "ключевая ставка"},
        claim_type="temporal_fact",
        as_of=t2.isoformat(),
        session_created_at=t2,
    )

    # the claim was REUSED (dedup by statement+type): one row, two
    # assessments
    n_claims = await _scalar(
        scratch_url,
        "SELECT count(*) FROM claims WHERE statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert n_claims[0] == 1
    n_assessments = await _scalar(
        scratch_url,
        "SELECT count(*) FROM claim_assessments a JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert n_assessments[0] == 2

    # the new assessment: E1 hypothesis; the re-derived reference is the
    # NEW session's date — yesterday's evidence does not cover it
    row = await _scalar(
        scratch_url,
        "SELECT a.assessed_scope FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%' "
        "AND a.effective_grade = 'E1'",
    )
    assert row is not None, "the reused claim must be assessed E1, not lifted"
    scope = dict(row[0])
    assert scope["scope_schema"] == "host-scope-v1"
    assert scope["as_of"] == day_n1.isoformat()
    assert scope["source_domains"] == ["alpha.example", "beta.example"]

    # head: current / hypothesis — the claim is NOT served as supported
    head = await _scalar(
        scratch_url,
        "SELECT h.assessment_state, h.epistemic_status "
        "FROM claim_assessment_heads h JOIN claims c ON c.id = h.claim_id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert head is not None
    assert head[0] == "current" and head[1] == "hypothesis"

    # the reason is the DATE (scope_not_covered), not independence or
    # counts — the E3 rule's other requirements are met (two
    # independent groups, two source_assertion)
    reasons = await _scalar(
        scratch_url,
        "SELECT payload->'reasons' FROM audit_events "
        "WHERE type = 'claim_assessed' AND payload->>'grade' = 'E1'",
    )
    assert reasons is not None
    assert "scope_not_covered" in reasons[0]
    assert "insufficient_independence" not in reasons[0]
    assert "insufficient_evidence" not in reasons[0]

    # freshness is UNTOUCHED: the claim is simultaneously E1 (scope) and
    # fresh — the coverage failure is not due/stale (§8.2/§8.6)
    fr = await _scalar(
        scratch_url,
        "SELECT c.freshness_status, c.reverify_after > now() FROM claims c "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert fr is not None
    assert fr[0] == "fresh"
    assert fr[1] is True


async def _update(scratch_url: str, sql: str) -> None:
    """A one-shot committed DML on the scratch DB (test plumbing)."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await db.execute(text(sql))
            await db.commit()
    finally:
        await engine.dispose()


async def _set_section(scratch_url: str, section: dict) -> None:
    import json

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await db.execute(
                text("UPDATE config_snapshots SET research_proxy = CAST(:sec AS jsonb)"),
                {"sec": json.dumps(section)},
            )
            await db.commit()
    finally:
        await engine.dispose()


# ── 3. full session on the fake LLM → E3 ───────────────────────────────


@pytest.mark.asyncio
async def test_full_session_freeform_curator_scope_reaches_E3(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """Required test 3: a full session through the real orchestrator
    under the CURATED profile (the fake LLM drives two research.fetch
    calls over two registrable domains); the curator returns an
    ARBITRARY free-form scope. The committed claim must be head
    current / supported / E3 from two independent sources — the
    model's scope keys are irrelevant to the grade."""
    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = "http://127.0.0.1:8888"  # unused: the fetch is faked
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], "research.fetch"]
    result = await _run_online(engine, payload)
    assert result.state == "active"

    question_text = (
        "Подтверди первую пилотируемую посадку на Луну строго по этим "
        "двум источникам: http://alpha.example/apollo и "
        "http://beta.example/landing"
    )
    question_id = await _seed_question(scratch_url, question_text)

    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=service,
    )
    def fetch(url: str) -> dict[str, Any]:
        """One scripted fake-LLM response: the action envelope under
        "content" (the fake server's scripted-response shape)."""
        return {
            "content": {
                "public_rationale": "Источники вопроса",
                "expected_information": "Текст источника",
                "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": url}},
            }
        }

    CURATOR_FREEFORM: dict[str, Any] = {
        "summary": "Первая пилотируемая посадка на Луну",
        "claims": [
            {
                "statement": (
                    "Аполлон-11 совершил первую пилотируемую посадку на "
                    "Луну 20 июля 1969 года"
                ),
                "claim_type": "external_fact",
                # arbitrary free-form scope, arbitrary key spellings —
                # the T7.15 shape the host must not depend on
                "scope": {
                    "объект": "Аполлон-11",
                    "событие": "посадка на Луну",
                    "на дату": "1969-07-20",
                },
            }
        ],
        "evidence_links": [
            {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
            {"evidence_index": 1, "claim_index": 0, "relation": "supports"},
        ],
        "new_questions": [],
    }
    fake_llm.script(
        [
            fetch("http://alpha.example/apollo"),
            fetch("http://beta.example/landing"),
            {
                "content": {
                    "public_rationale": "Данные собраны",
                    "decision": {"kind": "complete", "reason": "goal_reached"},
                }
            },
            {"content": CURATOR_FREEFORM},
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state.value == "succeeded"
    assert outcome.evidence_count == 2

    # the claim: head current, supported, E3 from two independent
    # sources — the model's free-form scope keys did not block it
    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status, "
        "       count(DISTINCT e.source_id) AS srcs "
        "FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "LEFT JOIN evidence e ON e.claim_id = c.id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%' "
        "GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None, "no assessment for the external claim"
    assert row[0] == "E3", f"expected E3, got {row[0]}"
    assert row[1] == "supported"
    assert row[2] == 2, "the two evidence rows must reference two distinct sources"

    head = await _scalar(
        scratch_url,
        "SELECT h.assessment_state, h.epistemic_status "
        "FROM claim_assessment_heads h JOIN claims c ON c.id = h.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%'",
    )
    assert head is not None
    assert head[0] == "current" and head[1] == "supported"

    # the assessed scope is host-derived from the QUESTION (no date is
    # named there — the temporal dimension is absent; the two named
    # source domains are present)
    assessed = await _scalar(
        scratch_url,
        "SELECT a.assessed_scope FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%'",
    )
    assert assessed is not None
    scope = dict(assessed[0])
    assert scope["scope_schema"] == "host-scope-v1"
    assert scope["as_of"] is None
    assert scope["source_domains"] == ["alpha.example", "beta.example"]


@pytest.mark.asyncio
async def test_full_session_relative_date_curator_as_of_reaches_E3(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """T7.18 (full session on the fake LLM through the real
    orchestrator): the question asks «на текущую дату»; the curator
    returns a temporal claim with an ARBITRARY typed as_of (tomorrow)
    and a free-form scope. The host-derived reference = the SESSION's
    date (today) — the model's as_of does not shift it — so the
    committed claim is head current / supported / E3 from two
    independent sources, and the assessed_scope carries the session's
    date, not the model's."""
    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = "http://127.0.0.1:8888"  # unused: the fetch is faked
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], "research.fetch"]
    result = await _run_online(engine, payload)
    assert result.state == "active"

    question_id = await _seed_question(scratch_url, QUESTION_KEY_RATE_RELATIVE)

    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=service,
    )

    def fetch(url: str) -> dict[str, Any]:
        return {
            "content": {
                "public_rationale": "Источники вопроса",
                "expected_information": "Текст источника",
                "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": url}},
            }
        }

    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    CURATOR: dict[str, Any] = {
        "summary": "Ключевая ставка Банка России",
        "claims": [
            {
                "statement": "Ключевая ставка Банка России составляет 14,00%.",
                "claim_type": "temporal_fact",
                # arbitrary (wrong) model as_of + free-form scope — the
                # T7.17 defect shape
                "as_of": tomorrow,
                "scope": {"объект": "ключевая ставка", "на дату": tomorrow[:10]},
            }
        ],
        "evidence_links": [
            {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
            {"evidence_index": 1, "claim_index": 0, "relation": "supports"},
        ],
        "new_questions": [],
    }
    fake_llm.script(
        [
            fetch("http://alpha.example/apollo"),
            fetch("http://beta.example/landing"),
            {
                "content": {
                    "public_rationale": "Данные собраны",
                    "decision": {"kind": "complete", "reason": "goal_reached"},
                }
            },
            {"content": CURATOR},
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state.value == "succeeded"
    assert outcome.evidence_count == 2

    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status, "
        "       count(DISTINCT e.source_id) AS srcs "
        "FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "LEFT JOIN evidence e ON e.claim_id = c.id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%' "
        "GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None, "no assessment for the temporal claim"
    assert row[0] == "E3", f"expected E3, got {row[0]}"
    assert row[1] == "supported"
    assert row[2] == 2, "the two evidence rows must reference two distinct sources"

    head = await _scalar(
        scratch_url,
        "SELECT h.assessment_state, h.epistemic_status "
        "FROM claim_assessment_heads h JOIN claims c ON c.id = h.claim_id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert head is not None
    assert head[0] == "current" and head[1] == "supported"

    # the assessed reference is the SESSION's date (today), NOT the
    # model's arbitrary as_of (tomorrow)
    assessed = await _scalar(
        scratch_url,
        "SELECT a.assessed_scope FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Ключевая ставка Банка России%'",
    )
    assert assessed is not None
    scope = dict(assessed[0])
    assert scope["scope_schema"] == "host-scope-v1"
    assert scope["as_of"] == datetime.now(UTC).date().isoformat()
    assert scope["source_domains"] == ["alpha.example", "beta.example"]


async def _seed_question(scratch_url: str, text_: str) -> Any:
    from packages.domain.db.uow import transaction
    from packages.domain.models.questions import ORMQuestion
    from packages.domain.repositories.questions import QuestionRepository

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(
                db,
                ORMQuestion(text=text_, origin="seeded", priority=1),
            )
            return q.id
    finally:
        await engine.dispose()
