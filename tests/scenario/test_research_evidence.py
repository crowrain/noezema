"""Scenario: research.fetch → source_assertion evidence (T7.7, EVAL-3
precondition, ADR-0006 rev).

The full path that was missing from the MVP: a sandboxed session
fetches two independent external pages through the research proxy; each
fetch maps to a source_assertion evidence record with durable source
provenance (sources row); the curator links both to one external_fact
claim via session_staging; the commit boundary (the real MemoryService
apply) recomputes the §14.3 identity and the rules engine — the only
grade producer — grades the claim E3 (supported) from two independent
source groups (different registrable domains), exactly what the §22.2
external/temporal E3 gate requires.

The network is replaced by a deterministic FakeFetchClient (same
contract as FetchClient: FetchResult, no network in CI — the real
FetchClient + SSRF guard are covered by the research-proxy scenario
tests); everything else — proxy registration, store, identity,
independence, assessment — is the real code.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.evidence import observation_to_evidence
from apps.orchestrator.executor import Observation
from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.sessions import ORMSession
from packages.domain.schemas.staging import CuratorProposal
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService
from packages.domain.services.staging import StagingService
from packages.memory.evidence import source_assertion_identity
from packages.memory.independence import registrable_domain
from packages.memory.service import MemoryService

pytestmark = [pytest.mark.scenario]

PAGE_A = (
    "Apollo 11 was the first crewed mission to land on the Moon. "
    "Neil Armstrong and Buzz Aldrin landed the lunar module Eagle on "
    "July 20, 1969, while Michael Collins orbited above."
)
PAGE_B = (
    "The Apollo 11 landing site was in the Sea of Tranquility; the "
    "mission launched on 16 July 1969 from Kennedy Space Center on a "
    "Saturn V rocket."
)

# explicit URL → page map (the content-addressed store dedupes
# identical bytes → the same artifact → the chunk-0 unique violation);
# the registrable domains are chosen per test: alpha.example and
# beta.example are two groups; beta.alpha.example is a SUBDOMAIN of
# alpha.example (one group — the negative control)
PAGES: dict[str, str] = {
    "http://alpha.example/apollo": PAGE_A,
    "http://beta.example/landing": PAGE_B,
    "http://beta.alpha.example/apollo11": PAGE_B,
}


class FakeFetchClient:
    """Deterministic stand-in for FetchClient (no network in CI)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - contract
        pass

    async def fetch(self, url: str) -> FetchResult:
        body = PAGES[url]
        data = f"<html><body><p>{body}</p></body></html>".encode()
        return FetchResult(
            url=url,
            final_url=url,
            content_type="text/html",
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)


def _research_obs(url: str, source_id: str, osha: str, nsha: str, normalized_text: str = "") -> Observation:
    """The observation shape _research_fetch produces (the data fields
    observation_to_evidence consumes; normalized_text — T7.8, the clean
    normalized chunk text the host read back from the store)."""
    return Observation(
        tool="research.fetch",
        ok=True,
        data={
            "url": url,
            "mode": "curated",
            "fenced": True,
            "content": "<<<UNTRUSTED DATA BEGIN>>>\n...\n<<<UNTRUSTED DATA END>>>\n",
            "normalized_text": normalized_text,
            "source_id": source_id,
            "original_sha256": osha,
            "normalized_sha256": nsha,
            "chunk_id": "chunk-0",
        },
    )


def _section(**overrides: Any) -> dict[str, Any]:
    section = {
        "mode": "curated",
        "max_response_bytes": 65536,
        "max_redirects": 3,
        "timeout_seconds": 5,
        "user_agent": "noezema-test/1.0",
        "private_allowlist": [],
        "searxng_url": "http://127.0.0.1:8888",
        "allowed_domains": [],
        "rate_limit_max": 20,
        "rate_limit_window_seconds": 3600,
    }
    section.update(overrides)
    return section


async def _fetch_sources(
    scratch_url: str, store: FilesystemArtifactStore, urls: tuple[str, str]
) -> list[dict[str, str]]:
    """Run the REAL proxy fetch path (proxy registration, store,
    sources/artifact_chunks journal) for two URLs; return the
    envelopes (the network is the faked part)."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    out = []
    try:
        for url in urls:
            env = await service.fetch(url)
            out.append(
                {
                    "url": env["final_url"],
                    "source_id": str(env["source_id"]),
                    "original_sha256": str(env["original_sha256"]),
                    "normalized_sha256": str(env["normalized_sha256"]),
                }
            )
    finally:
        await engine.dispose()
    return out


async def _scalar(scratch_url: str, sql: str, params: dict | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _all(scratch_url: str, sql: str) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql))).mappings().all())
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


async def _apply(
    scratch_url: str, records: list[Any], statement: str
) -> None:
    """The commit boundary: staging ops + the real MemoryService apply
    (the same code the fenced final transaction runs)."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            snap = (
                (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
            )
            assert snap is not None
            sid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, started_at) "
                    "VALUES (:id, 'exploring', :c, now())"
                ),
                {"id": str(sid), "c": str(snap.id)},
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

            proposal = CuratorProposal(
                summary="Research claim",
                claims=[
                    {
                        "statement": statement,
                        "claim_type": "external_fact",
                        "scope": {"event": "apollo11"},
                    }
                ],
                evidence_links=[
                    {"evidence_index": i, "claim_index": 0, "relation": "supports"}
                    for i in range(len(records))
                ],
                new_questions=[],
            )
            assert proposal.validate_against(len(records), questions_max=4) == []
            await staging.record(
                db,
                audit,
                o_session,
                "claim",
                proposal.claims[0].model_dump(mode="json"),
                proposed_claims=1,
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


@pytest.mark.asyncio
async def test_research_fetch_produces_source_assertion_e3(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    # two independent hosts: alpha.example and beta.example are
    # different registrable domains → two independence groups
    urls = ("http://alpha.example/apollo", "http://beta.example/landing")
    assert {registrable_domain(u) for u in urls} == {"alpha.example", "beta.example"}
    sources = await _fetch_sources(scratch_url, store, urls)
    assert len(sources) == 2
    assert sources[0]["source_id"] != sources[1]["source_id"]
    # two distinct pages → two artifacts (the store dedupes by sha)
    assert sources[0]["original_sha256"] != sources[1]["original_sha256"]

    # 1. the host adapter maps each observation to a source_assertion
    #    record with durable provenance and the §14.3 identity
    for src in sources:
        # T7.8 (§6.4): the payload carries the normalized chunk text the
        # assertion is grounded in — the host reads it back from the
        # store; here we pass the page text the host would have read
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
        assert rec is not None, "research.fetch produced no evidence record"
        assert rec.kind.value == "source_assertion"
        assert rec.source_id == src["source_id"]
        assert rec.chunk_id == "chunk-0"
        assert rec.identity_hash == source_assertion_identity(
            src["original_sha256"], "chunk-0", "source_assertion"
        )
        # the assertion text fragment is the source text (pages here are
        # short, so the full text is carried under the budget)
        assert rec.payload["assertion_text"] == PAGES[src["url"]], (
            "payload must carry the normalized text the assertion is grounded in"
        )
        # the fragment is NOT part of the identity: same original hash +
        # chunk + kind, different text → identical identity (dedupe
        # semantics unchanged)
        alt = observation_to_evidence(
            _research_obs(
                src["url"],
                src["source_id"],
                src["original_sha256"],
                src["normalized_sha256"],
                normalized_text="completely different text",
            ),
            {"url": src["url"]},
        )
        assert alt is not None
        assert alt.identity_hash == rec.identity_hash, (
            "assertion_text must not participate in the §14.3 identity"
        )

    # a failed observation and one without a durable source reference
    # produce nothing (no unprovenanced evidence ever)
    assert observation_to_evidence(
        Observation(tool="research.fetch", ok=False, error="refused"), {"url": "x"}
    ) is None
    assert observation_to_evidence(
        Observation(tool="research.fetch", ok=True, data={"url": "x", "content": "y"}),
        {"url": "x"},
    ) is None

    # 2. the commit boundary: the curator links both fetches to one
    #    external_fact claim; the rules engine grades it E3 supported
    records = [
        observation_to_evidence(
            _research_obs(
                s["url"],
                s["source_id"],
                s["original_sha256"],
                s["normalized_sha256"],
                normalized_text=PAGES[s["url"]],
            ),
            {"url": s["url"]},
        )
        for s in sources
    ]
    assert all(r is not None for r in records)
    await _apply(
        scratch_url,
        records,
        "Аполлон-11 совершил первую пилотируемую посадку на Луну 20 июля 1969 года.",
    )

    # 3. the claim is E3 supported — two evidence rows with durable
    #    source provenance, two distinct sources
    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status, "
        "       count(e.source_id) AS src_ev, count(DISTINCT e.source_id) AS srcs "
        "FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "LEFT JOIN evidence e ON e.claim_id = c.id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%' "
        "GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None, "no assessment for the external claim"
    assert row[0] == "E3", f"expected E3, got {row[0]}"
    assert row[1] == "supported"
    assert row[2] == 2, "both evidence rows must carry source provenance"
    assert row[3] == 2, "the two evidence rows must reference two distinct sources"

    # the independence snapshot says the two sources are in DIFFERENT
    # groups (different registrable domains)
    rows = await _all(
        scratch_url,
        "SELECT DISTINCT m.group_id FROM source_independence_members m "
        "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%'",
    )
    assert len(rows) == 2, f"two hosts must land in two groups, got {rows}"


@pytest.mark.asyncio
async def test_same_registrable_domain_is_one_group(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    """Negative control (no fake independence): a subdomain of the same
    registrable domain (beta.alpha.example under alpha.example) is ONE
    independence group — min_independence_groups=2 is NOT met, so the
    claim does not reach E3 supported."""
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    urls = ("http://alpha.example/apollo", "http://beta.alpha.example/apollo11")
    # one registrable domain, two distinct pages
    assert {registrable_domain(u) for u in urls} == {"alpha.example"}
    sources = await _fetch_sources(scratch_url, store, urls)
    assert len(sources) == 2
    assert sources[0]["original_sha256"] != sources[1]["original_sha256"]

    records = [
        observation_to_evidence(
            _research_obs(
                s["url"],
                s["source_id"],
                s["original_sha256"],
                s["normalized_sha256"],
                normalized_text=PAGES[s["url"]],
            ),
            {"url": s["url"]},
        )
        for s in sources
    ]
    assert all(r is not None for r in records)
    await _apply(scratch_url, records, "Аполлон-11 посадка (один домен, два источника).")

    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 посадка (один домен%'",
    )
    assert row is not None
    # one group only → min_independence_groups=2 not met → not E3
    # supported (the rules engine never fakes independence)
    assert not (row[0] == "E3" and row[1] == "supported"), (
        f"same-registrable-domain sources must not grant E3 supported, got {tuple(row)}"
    )


def test_assertion_text_budget_is_explicit_and_identity_ignores_text() -> None:
    """T7.8 (EVAL-3b post-mortem P.1, §6.4): the assertion-text fragment
    is bounded by an explicit budget (SOURCE_ASSERTION_TEXT_BUDGET chars
    of the normalized chunk text), and the fragment does NOT participate
    in the §14.3 identity (identity stays over the original content
    hash + chunk + kind, so dedupe semantics are unchanged)."""
    from apps.orchestrator.evidence import SOURCE_ASSERTION_TEXT_BUDGET

    long_text = "x" * (SOURCE_ASSERTION_TEXT_BUDGET + 500)
    obs = _research_obs("u", "s" * 36, "o" * 64, "n" * 64, normalized_text=long_text)
    rec = observation_to_evidence(obs, {"url": "u"})
    assert rec is not None
    # the fragment is truncated to the explicit budget
    assert rec.payload["assertion_text"] == long_text[:SOURCE_ASSERTION_TEXT_BUDGET]
    assert len(rec.payload["assertion_text"]) == SOURCE_ASSERTION_TEXT_BUDGET

    # the identity ignores the text entirely: two observations with the
    # same original hash + chunk + kind but different text → same identity
    rec2 = observation_to_evidence(
        _research_obs("u", "s" * 36, "o" * 64, "n" * 64, normalized_text="entirely different"),
        {"url": "u"},
    )
    assert rec2 is not None
    assert rec2.identity_hash == rec.identity_hash
    # and both equal the pure §14.3 identity
    assert rec.identity_hash == source_assertion_identity("o" * 64, "chunk-0", "source_assertion")
