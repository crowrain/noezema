"""Scenario (DB): T7.7 (EVAL-3) — the blind sample for MANUAL review.

§22.2 defines the blind sample as a manual procedure: the rendered
sample must be EXACTLY the one the blind gates measure (same seeded,
stratified selection), and it must carry the claim detail plus the
evidence detail (kind/relation/scope, source URL / artifact id, cited
fragment from the content-addressed store) a reviewer needs to judge
whether the claim follows from its evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.artifacts.store import FilesystemArtifactStore
from packages.evaluation.blind import (
    blind_sample_claim_ids,
    blind_sample_details,
    render_blind_sample,
)
from packages.evaluation.gates import compute_gates
from tests.scenario.test_evaluation_gates import _mk_run, _Seeder, _uid

pytestmark = [pytest.mark.scenario]

FRAGMENT = (
    "The United Nations has 193 member states. " * 10
)


async def _seed(engine: AsyncEngine) -> _Seeder:
    s = _Seeder(engine)
    await s.setup()
    sid = await s.add_session(0, state="succeeded")
    sid2 = await s.add_session(1, state="succeeded")
    # claim 0: computed E2 supported, one source evidence (fragment
    # target); claim 1: temporal E3 supported; claim 2: external
    # disputed with NO linked evidence (structural failure rendering)
    await s.add_claim(0, session=sid)
    await s.add_claim(1, session=sid2, ctype="temporal_fact", grade="E3")
    await s.add_claim(
        2,
        session=sid,
        ctype="external_fact",
        status="disputed",
        grade="E1",
        evidence_source=False,
    )
    return s


@pytest.mark.asyncio
async def test_blind_sample_details_and_render(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    _scratch, engine = migrated_db
    run = await _mk_run(engine, seed=7, size=100)
    seeder = await _seed(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # the content-addressed store: claim 0's source points at an
    # artifact whose normalized text is the cited fragment
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    sha = store.put(
        FRAGMENT.encode("utf-8"),
        origin="research_proxy",
        trust_class="external",
        mime="text/plain",
    )
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO artifacts (id, sha256, size, mime, origin, "
                "trust_class) VALUES (:id, :s, :size, 'text/plain', "
                "'research_proxy', 'external')"
            ),
            {"id": str(_uid("artifact-0")), "s": sha, "size": len(FRAGMENT.encode("utf-8"))},
        )
        src0 = _uid("source-0")
        await db.execute(
            text(
                "UPDATE sources SET content_hash = :h, metadata = CAST(:m AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "h": sha,
                "m": '{"normalized_sha256": "' + sha + '"}',
                "id": src0,
            },
        )

    async with factory() as db:
        # same selection, twice (deterministic for the run row)
        sample1 = await blind_sample_claim_ids(db, run)
        sample2 = await blind_sample_claim_ids(db, run)
        assert sample1 == sample2
        # ...and exactly the one the blind gates measure
        gates = await compute_gates(db, run=run)
        assert gates["blind_provenance_path"]["denominator"] == len(sample1)
        assert {str(c) for c in sample1} <= {str(c) for c in seeder.claim_ids}

        entries = await blind_sample_details(
            db, run, store=store, fragment_chars=60
        )
        by_type = {e["claim_type"]: e for e in entries}
        assert len(entries) == 3
        e0 = by_type["computed_result"]
        assert e0["statement"] == "claim statement 0"
        assert e0["epistemic_status"] == "supported"
        assert e0["effective_grade"] == "E2"
        assert e0["assessed_scope"] == {"topic": "t0"}
        ev = e0["evidence"]
        assert len(ev) == 1
        assert ev[0]["kind"] == "source_assertion"
        assert ev[0]["relation"] == "supports"
        assert ev[0]["source_uri"] == "corpus://doc0"
        assert ev[0]["source_content_sha256"] == sha
        assert ev[0]["fragment"] is not None
        assert ev[0]["fragment"].startswith(FRAGMENT[:60])
        assert "chars more" in ev[0]["fragment"]  # truncated
        e2 = by_type["external_fact"]
        assert e2["evidence"] == []  # structural failure, no rows

        report = render_blind_sample(run, entries)
    assert "РУЧНАЯ" in report
    assert "claim statement 0" in report
    assert "corpus://doc0" in report
    assert FRAGMENT[:40] in report  # the fragment is quoted
    assert "**НЕТ**" in report  # claim 2 has no linked evidence
    assert "Ограничение метода" in report


@pytest.mark.asyncio
async def test_blind_sample_without_store(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    _scratch, engine = migrated_db
    run = await _mk_run(engine, seed=7, size=100)
    await _seed(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        entries = await blind_sample_details(db, run, store=None)
        assert len(entries) == 3
        e0 = next(e for e in entries if e["claim_type"] == "computed_result")
        assert e0["evidence"][0]["fragment"] is None  # store unavailable
        report = render_blind_sample(run, entries)
    assert "недоступен" in report
