"""Scenario (DB): M7 web — knowledge graph (T7.1, §22.1).

Covers: the claims list with head state on the EFFECTIVE snapshot, the
claim detail (heads across snapshots, evidence, dependencies both
directions), the provenance navigation (evidence → source → parent
source / artifact, the independence groups the current assessment fixed,
per-evidence roles), the dependency edge view, and the HTML pages.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.web.api import create_app

pytestmark = [pytest.mark.scenario]

EFF = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make_app(scratch_url: str):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(engine=engine, factory=factory)
    return app, engine


async def _scalar(engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        return res.first() if res.returns_rows else None


async def _seed_claim(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    statement: str,
    *,
    claim_type: str = "computed_result",
    state: str = "current",
    grade: str = "E2",
    epistemic: str = "supported",
    scope: str = '{"x": 1}',
) -> None:
    """state: 'current' (head + assessment) | 'pending' | 'invalid'
    (head without assessment) | 'none' (claim without any head)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :s, :t, 'fresh')"
            ),
            {"id": claim_id, "s": statement, "t": claim_type},
        )
        if state == "current":
            aid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO claim_assessments "
                    "(id, claim_id, effective_grade, epistemic_status, rules_version, "
                    " rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                    "VALUES (:a, :c, :g, :e, 'rules-v1', 'h', 'ev', :sc, 0.9, true)"
                ),
                {"a": aid, "c": claim_id, "g": grade, "e": epistemic, "sc": scope},
            )
            await db.execute(
                text(
                    "INSERT INTO claim_assessment_heads "
                    "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                    " epistemic_status, prepared_by) "
                    f"VALUES (:c, {EFF}, 'current', :a, :e, 'rules_activation')"
                ),
                {"c": claim_id, "a": aid, "e": epistemic},
            )
        elif state != "none":
            await db.execute(
                text(
                    "INSERT INTO claim_assessment_heads "
                    "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                    " epistemic_status, prepared_by) "
                    f"VALUES (:c, {EFF}, :st, NULL, NULL, 'rules_activation')"
                ),
                {"c": claim_id, "st": state},
            )


async def _seed_source(
    engine: AsyncEngine,
    *,
    uri: str,
    content_hash: str,
    parent: uuid.UUID | None = None,
) -> uuid.UUID:
    sid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata, "
        " parent_source_id) VALUES (:id, 'external_url', :uri, :ch, '{}', :p)",
        {"id": sid, "uri": uri, "ch": content_hash, "p": parent},
    )
    return sid


async def _seed_source_evidence(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    tag: str,
) -> None:
    await _scalar(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        " scope, source_id, chunk_id) "
        "VALUES (:id, :c, 'supports', 'source_assertion', :h, '{\"x\": 1}', :s, :k)",
        {"id": uuid.uuid4(), "c": claim_id, "h": f"src-{tag}", "s": source_id, "k": f"chunk-{tag}"},
    )


async def _seed_computation_evidence(
    engine: AsyncEngine, claim_id: uuid.UUID, *, tag: str
) -> uuid.UUID:
    art = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO artifacts (id, sha256, size, trust_class) "
        "VALUES (:a, :sha, 12, 'session_workspace')",
        {"a": art, "sha": f"comp-{tag}-artifact"},
    )
    await _scalar(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        " scope, observation_artifact_id) "
        "VALUES (:id, :c, 'supports', 'computation', :h, '{\"x\": 1}', :a)",
        {"id": uuid.uuid4(), "c": claim_id, "h": f"comp-{tag}", "a": art},
    )
    return art


async def _seed_edge(
    engine: AsyncEngine, frm: uuid.UUID, to: uuid.UUID, kind: str = "evidential"
) -> None:
    await _scalar(
        engine,
        "INSERT INTO claim_dependencies (id, from_claim_id, to_claim_id, kind) "
        "VALUES (:i, :f, :t, :k)",
        {"i": uuid.uuid4(), "f": frm, "t": to, "k": kind},
    )


async def _seed_shadow_head(
    engine: AsyncEngine, claim_id: uuid.UUID, candidate_id: uuid.UUID
) -> None:
    """A shadow head on a CANDIDATE snapshot (must never be served as
    current by the views)."""
    await _scalar(
        engine,
        "INSERT INTO claim_assessment_heads "
        "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
        " epistemic_status, prepared_by) "
        "VALUES (:c, :s, 'pending', NULL, NULL, 'rules_activation')",
        {"c": claim_id, "s": candidate_id},
    )


@pytest.mark.asyncio
async def test_knowledge_claims_list(migrated_db: tuple[str, AsyncEngine]) -> None:
    scratch_url, engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        c1 = uuid.uuid5(uuid.NAMESPACE_URL, "kw-1")
        c2 = uuid.uuid5(uuid.NAMESPACE_URL, "kw-2")
        c3 = uuid.uuid5(uuid.NAMESPACE_URL, "kw-3")
        c4 = uuid.uuid5(uuid.NAMESPACE_URL, "kw-4")
        await _seed_claim(engine, c1, "утверждение одно", grade="E3", epistemic="supported")
        await _seed_claim(engine, c2, "утверждение два", state="pending")
        await _seed_claim(engine, c3, "утверждение три", state="invalid")
        await _seed_claim(engine, c4, "утверждение четыре без head", state="none")
        # the last insert wins the created_at tie only via id order — the
        # list must carry the head state of the EFFECTIVE snapshot
        async with _client(app) as client:
            r = await client.get("/api/v1/knowledge/claims")
            assert r.status_code == 200
            data = r.json()
            assert data["total"] == 4
            by_id = {c["id"]: c for c in data["claims"]}
            assert by_id[str(c1)]["head_state"] == "current"
            assert by_id[str(c1)]["effective_grade"] == "E3"
            assert by_id[str(c1)]["epistemic_status"] == "supported"
            assert by_id[str(c2)]["head_state"] == "pending"
            assert by_id[str(c2)]["effective_grade"] is None
            assert by_id[str(c3)]["head_state"] == "invalid"
            assert by_id[str(c4)]["head_state"] == "none"

            r = await client.get("/api/v1/knowledge/claims", params={"state": "current"})
            assert r.status_code == 200
            assert [c["id"] for c in r.json()["claims"]] == [str(c1)]

            # an unknown state value is rejected
            r = await client.get("/api/v1/knowledge/claims", params={"state": "bogus"})
            assert r.status_code == 422
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_claim_detail_with_dependencies(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    scratch_url, engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        root = uuid.uuid5(uuid.NAMESPACE_URL, "kw-root")
        child = uuid.uuid5(uuid.NAMESPACE_URL, "kw-child")
        await _seed_claim(engine, root, "базовый факт", grade="E3")
        await _seed_claim(
            engine, child, "вывод из базового", claim_type="empirical_conjecture",
            grade="E1", epistemic="hypothesis",
        )
        await _seed_edge(engine, child, root, kind="research")
        # a shadow head on a candidate snapshot: visible per claim, never
        # the current one
        candidate = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO config_snapshots (id, base_snapshot_id, payload_sha256, sha256, "
            " activation_mode, activation_state, model, embeddings, prompts, policy, "
            " curiosity, token_budgets, session_limits, activation_limits, claim_type_rules) "
            "VALUES "
            f"(:id, {EFF}, :ps, :s, 'online', 'preparing_heads', "
            "'{}', '{}', '{}', '{}', '{}', '{}', '{}', '{}', '{}')",
            {"id": candidate, "ps": "c" * 64, "s": "d" * 64},
        )
        await _seed_shadow_head(engine, child, candidate)
        # evidence on the root
        src = await _seed_source(engine, uri="https://ex.org/a", content_hash="h1")
        await _seed_source_evidence(engine, root, src, tag="r")

        async with _client(app) as client:
            r = await client.get(f"/api/v1/knowledge/claims/{child}")
            assert r.status_code == 200
            d = r.json()
            assert d["statement"] == "вывод из базового"
            assert d["claim_type"] == "empirical_conjecture"
            # the effective head first, then the candidate shadow head
            assert [h["assessment_state"] for h in d["heads"]] == ["current", "pending"]
            assert d["heads"][0]["effective_grade"] == "E1"
            assert d["heads"][0]["epistemic_status"] == "hypothesis"
            assert d["heads"][1]["activation_state"] == "preparing_heads"
            assert d["depends_on"] == [
                {
                    "claim_id": str(root),
                    "kind": "research",
                    "statement": "базовый факт",
                }
            ]
            assert d["depended_by"] == []

            r = await client.get(f"/api/v1/knowledge/claims/{root}")
            d = r.json()
            assert d["depended_by"][0]["claim_id"] == str(child)
            assert d["depended_by"][0]["kind"] == "research"
            assert len(d["evidence"]) == 1
            assert d["evidence"][0]["evidence_kind"] == "source_assertion"
            assert d["evidence"][0]["source_uri"] == "https://ex.org/a"

            # unknown claim → 404
            r = await client.get(f"/api/v1/knowledge/claims/{uuid.uuid4()}")
            assert r.status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_provenance_navigation(migrated_db: tuple[str, AsyncEngine]) -> None:
    scratch_url, engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        cid = uuid.uuid5(uuid.NAMESPACE_URL, "kw-prov")
        await _seed_claim(engine, cid, "факт с двумя источниками", claim_type="external_fact")
        parent = await _seed_source(engine, uri="https://ex.org/parent", content_hash="hp")
        s1 = await _seed_source(engine, uri="https://ex.org/child1", content_hash="h1", parent=parent)
        s2 = await _seed_source(engine, uri="https://ex.org/child2", content_hash="h2", parent=parent)
        await _seed_source_evidence(engine, cid, s1, tag="p1")
        await _seed_source_evidence(engine, cid, s2, tag="p2")
        art = await _seed_computation_evidence(engine, cid, tag="p3")

        # a current assessment that FIXES a source-independence snapshot
        # over the two sources (different groups) + the evidence roles
        snap = uuid.uuid4()
        aid = (
            await _scalar(
                engine,
                "SELECT id FROM claim_assessments WHERE claim_id = :c ORDER BY created_at DESC, id DESC LIMIT 1",
                {"c": cid},
            )
        )[0]
        await _scalar(
            engine,
            "UPDATE claim_assessments SET source_independence_snapshot_id = :s WHERE id = :a",
            {"s": snap, "a": aid},
        )
        await _scalar(
            engine,
            "INSERT INTO source_independence_snapshots "
            "(id, algorithm_version, thresholds, psl_fingerprint, uri_normalizer_version) "
            "VALUES (:id, 'noezema-source-v1', '{}', 'f', 'v1')",
            {"id": snap},
        )
        await _scalar(
            engine,
            "INSERT INTO source_independence_members (snapshot_id, source_id, group_id, basis) "
            "VALUES (:s, :a, 'source:A', 'canonical_uri'), (:s, :b, 'source:B', 'canonical_uri')",
            {"s": snap, "a": s1, "b": s2},
        )
        ev_ids = list(
            (
                await _scalar(
                    engine,
                    "SELECT array_agg(id ORDER BY created_at, id) "
                    "FROM evidence WHERE claim_id = :c",
                    {"c": cid},
                )
            )[0]
        )
        await _scalar(
            engine,
            "INSERT INTO assessment_evidence (assessment_id, evidence_id, role) VALUES "
            "(:a, :e1, 'support'), (:a, :e2, 'support'), (:a, :e3, 'scope_witness')",
            {"a": aid, "e1": ev_ids[0], "e2": ev_ids[1], "e3": ev_ids[2]},
        )

        async with _client(app) as client:
            r = await client.get(f"/api/v1/knowledge/claims/{cid}/provenance")
            assert r.status_code == 200
            p = r.json()
            assert p["claim_id"] == str(cid)
            assert len(p["evidence"]) == 3
            by_kind = {}
            for e in p["evidence"]:
                by_kind.setdefault(e["evidence_kind"], []).append(e)
            srcs = by_kind["source_assertion"]
            assert {e["source"]["canonical_uri"] for e in srcs} == {
                "https://ex.org/child1",
                "https://ex.org/child2",
            }
            for e in srcs:
                assert e["source"]["parent"]["canonical_uri"] == "https://ex.org/parent"
                assert e["source"]["parent"]["id"] == str(parent)
                assert e["source"]["content_hash"] in ("h1", "h2")
                assert e["source"]["chunk_id"] in ("chunk-p1", "chunk-p2")
            comp = by_kind["computation"][0]
            assert comp["artifact"]["sha256"] == "comp-p3-artifact"
            assert comp["artifact"]["trust_class"] == "session_workspace"
            assert comp["artifact"]["id"] == str(art)

            # the independence groups fixed by the current assessment
            assert sorted(g["group_id"] for g in p["source_groups"]) == [
                "source:A",
                "source:B",
            ]
            uri_by_group = {g["group_id"]: g["uri"] for g in p["source_groups"]}
            assert uri_by_group["source:A"] == "https://ex.org/child1"
            # per-evidence roles of the current assessment
            roles = {(r_["evidence_id"], r_["role"]) for r_ in p["assessment_evidence_roles"]}
            assert (str(ev_ids[0]), "support") in roles
            assert (str(ev_ids[2]), "scope_witness") in roles

            # unknown claim → 404
            r = await client.get(f"/api/v1/knowledge/claims/{uuid.uuid4()}/provenance")
            assert r.status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_dependencies_view(migrated_db: tuple[str, AsyncEngine]) -> None:
    scratch_url, engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        a = uuid.uuid5(uuid.NAMESPACE_URL, "kw-dep-a")
        b = uuid.uuid5(uuid.NAMESPACE_URL, "kw-dep-b")
        c = uuid.uuid5(uuid.NAMESPACE_URL, "kw-dep-c")
        await _seed_claim(engine, a, "зависимость a")
        await _seed_claim(engine, b, "зависимость b")
        await _seed_claim(engine, c, "отдельное c")
        await _seed_edge(engine, a, b)

        async with _client(app) as client:
            r = await client.get("/api/v1/knowledge/dependencies")
            assert r.status_code == 200
            deps = r.json()["dependencies"]
            assert len(deps) == 1
            assert deps[0]["from_claim_id"] == str(a)
            assert deps[0]["to_claim_id"] == str(b)
            assert deps[0]["from_statement"] == "зависимость a"
            assert deps[0]["to_statement"] == "зависимость b"

            # narrowed to one claim: the edge appears for either endpoint
            r = await client.get(
                "/api/v1/knowledge/dependencies", params={"claim_id": str(b)}
            )
            assert len(r.json()["dependencies"]) == 1
            r = await client.get(
                "/api/v1/knowledge/dependencies", params={"claim_id": str(c)}
            )
            assert r.json()["dependencies"] == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_html_pages(migrated_db: tuple[str, AsyncEngine]) -> None:
    scratch_url, engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        cid = uuid.uuid5(uuid.NAMESPACE_URL, "kw-html")
        await _seed_claim(engine, cid, "видимое в html утверждение")
        async with _client(app) as client:
            r = await client.get("/knowledge")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/html")
            assert "NOEZEMA — знание" in r.text

            r = await client.get(f"/claim/{cid}")
            assert r.status_code == 200
            assert str(cid) in r.text

            r = await client.get("/diagnostics")
            assert r.status_code == 200
            assert "NOEZEMA — диагностика" in r.text

            # the main page links to the new views
            r = await client.get("/")
            assert 'href="/knowledge"' in r.text
            assert 'href="/diagnostics"' in r.text
    finally:
        await engine.dispose()
