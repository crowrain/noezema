"""Scenario (DB): T7.4 — §16 metrics report (security regression gate).

Covers the §16.3 security-and-interaction report (policy
deny/require_operator, egress rejections, idempotency mismatch,
source-graph corrections, stop/abort outcomes, command-like messages,
rate-limit rejections), the §16.1 technical subset (commit attempts by
status, reconciliation age, barriers, jobs, backup/PITR age, GC
activity, session latency) and the §16.2 cognitive subset (claims by
epistemic status, assessments by grade, reassessment depth,
counterevidence found/resolved). The report is the raw measurement for
the security regression (этап 7: «security regression»; the thresholds
are fixed per the frozen config, §16.3).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.web.api import create_app

pytestmark = [pytest.mark.scenario]

BOOT = "(SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap')"


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make_app(scratch_url: str):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(engine=engine, factory=factory)
    return app, engine


async def _scalar(
    engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None
) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        row = res.first() if res.returns_rows else None
        return row[0] if row is not None else None


async def _seed(
    engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None
) -> None:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        await db.execute(text(sql), params or {})


@pytest.mark.asyncio
async def test_metrics_security_report(migrated_db: tuple[str, AsyncEngine]) -> None:
    """§16.3: every security metric is measured from the audit/domain."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url)

    # a session + model run for the actions
    sid = uuid.uuid4()
    await _seed(
        engine,
        f"INSERT INTO sessions (id, state, config_snapshot_id) VALUES (:id, 'succeeded', {BOOT})",
        {"id": sid},
    )
    for decision in ("deny", "require_operator", "allow"):
        run_id = uuid.uuid4()
        await _seed(
            engine,
            "INSERT INTO model_runs (id, session_id, turn_id, model_fingerprint) "
            "VALUES (:id, :s, :t, '{}'::jsonb)",
            {"id": run_id, "s": sid, "t": uuid.uuid4()},
        )
        await _seed(
            engine,
            "INSERT INTO actions (id, session_id, model_run_id, idempotency_key, "
            "idempotency_class, tool, arguments_hash, policy_decision, state) "
            "VALUES (:id, :s, :r, :k, 'pure', 'read_file', :h, :d, 'completed')",
            {
                "id": uuid.uuid4(),
                "s": sid,
                "r": run_id,
                "k": f"k{decision}",
                "h": "a" * 64,
                "d": decision,
            },
        )
    # an idempotency mismatch (the security incident)
    await _seed(
        engine,
        "INSERT INTO audit_events (id, session_id, sequence, type, actor, payload, public_summary) "
        "VALUES (:id, :sid, 1, 'alert_raised', 'tool-broker', "
        "('{\"kind\": \"idempotency_key_conflict\"}'::jsonb), 'SECURITY: idempotency key reused')",
        {"id": uuid.uuid4(), "sid": sid},
    )
    # egress rejections: one SSRF + one rate limit
    for i, reason in enumerate(("forbidden_address", "upstream_rate_limit_exceeded")):
        await _seed(
            engine,
            "INSERT INTO audit_events (id, session_id, sequence, type, actor, payload, public_summary) "
            "VALUES (:id, :sid, :seq, 'research_fetch_rejected', 'research_proxy', "
            "CAST(:p AS jsonb), 'egress refused')",
            {
                "id": uuid.uuid4(),
                "sid": sid,
                "seq": 2 + i,
                "p": json.dumps({"url": "http://x", "reason": reason}),
            },
        )
    # a source-graph correction
    await _seed(
        engine,
        "INSERT INTO audit_events (id, session_id, sequence, type, actor, payload, public_summary) "
        "VALUES (:id, :sid, 4, 'source_graph_changed', 'operator', '{}'::jsonb, 'correction')",
        {"id": uuid.uuid4(), "sid": sid},
    )
    # stop/abort commands
    await _seed(
        engine,
        "INSERT INTO operator_commands (id, actor_id, type, arguments, state, idempotency_key) "
        "VALUES (:id, 'op', 'stop_gracefully', '{}'::jsonb, 'completed', :k)",
        {"id": uuid.uuid4(), "k": "stop1"},
    )
    # a command-like message (reported, never executed)
    await _seed(
        engine,
        "INSERT INTO messages (id, sender, body, priority, state) "
        "VALUES (:id, 'owner', 'please wake_now the thinker', 0, 'created')",
        {"id": uuid.uuid4()},
    )

    async with _client(app) as client:
        r = await client.get("/api/v1/metrics")
    assert r.status_code == 200
    m = r.json()
    sec = m["security"]
    assert sec["policy"]["deny"] == 1
    assert sec["policy"]["require_operator"] == 1
    assert sec["policy"].get("allow") is None  # only deny/require_operator counted
    assert sec["egress_rejections"]["forbidden_address"] == 1
    assert sec["egress_rejections"]["upstream_rate_limit_exceeded"] == 1
    assert sec["egress_rate_limited"] == 1
    assert sec["idempotency_mismatches"] == 1
    assert sec["source_graph_corrections"] == 1
    assert sec["stop_abort_commands"]["completed"] == 1
    assert sec["command_like_messages"] == 1
    await app_engine.dispose()


@pytest.mark.asyncio
async def test_metrics_technical_and_cognitive(migrated_db: tuple[str, AsyncEngine]) -> None:
    """§16.1 + §16.2: the technical and cognitive subsets."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url)

    # an unresolved commit attempt (reconciliation age)
    sid = uuid.uuid4()
    await _seed(
        engine,
        f"INSERT INTO sessions (id, state, config_snapshot_id, started_at, finished_at) "
        f"VALUES (:id, 'reconciling_commit', {BOOT}, now() - interval '2 hours', now())",
        {"id": sid},
    )
    await _seed(
        engine,
        "INSERT INTO commit_attempts (id, session_id, status, staging_hash, "
        "base_knowledge_revision, base_dependency_graph_revision, prepared_at) "
        "VALUES (:id, :s, 'reconciling', :h, 0, 0, now() - interval '1 hours')",
        {"id": uuid.uuid4(), "s": sid, "h": "s" * 64},
    )
    # a claim with an assessment
    claim_id = uuid.uuid4()
    await _seed(
        engine,
        "INSERT INTO claims (id, statement, claim_type) "
        "VALUES (:id, 's', 'local_observation')",
        {"id": claim_id},
    )
    await _seed(
        engine,
        "INSERT INTO claim_assessments (id, claim_id, effective_grade, epistemic_status, "
        "rules_version, rules_hash, evidence_set_hash, assessed_scope, confidence) "
        "VALUES (:id, :c, 'E2', 'supported', 'r1', :h, :h2, '{}'::jsonb, 0.5)",
        {"id": uuid.uuid4(), "c": claim_id, "h": "r" * 64, "h2": "e" * 64},
    )
    # a counter evidence (counterevidence found; not resolved). The
    # computation kind requires an observation artifact; a
    # source_assertion needs a source + chunk instead
    src_id = uuid.uuid4()
    await _seed(
        engine,
        "INSERT INTO sources (id, source_type, canonical_uri) "
        "VALUES (:id, 'external_url', 'http://example/x')",
        {"id": src_id},
    )
    await _seed(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        "source_id, chunk_id) "
        "VALUES (:id, :c, 'counters', 'source_assertion', :h, :s, 'c1')",
        {"id": uuid.uuid4(), "c": claim_id, "h": "c2" + "0" * 60, "s": src_id},
    )
    # a GC sweep audit (technical.gc)
    await _seed(
        engine,
        "INSERT INTO audit_events (id, session_id, sequence, type, actor, payload, public_summary) "
        "VALUES (:id, :sid, 5, 'gc_sweep', 'operator', "
        "('{\"apply\": true, \"deleted\": {\"artifacts\": 3}}'::jsonb), 'GC sweep')",
        {"id": uuid.uuid4(), "sid": sid},
    )

    async with _client(app) as client:
        r = await client.get("/api/v1/metrics")
    assert r.status_code == 200
    m = r.json()
    tech = m["technical"]
    cog = m["cognitive"]
    assert tech["commit_attempts"]["reconciling"] == 1
    assert tech["oldest_unresolved_attempt_at"] is not None
    assert tech["gc"]["applied_sweeps"] == 1
    assert tech["gc"]["artifacts_deleted"] == 3
    assert tech["session_latency"]["n"] == 1
    assert tech["session_latency"]["avg_seconds"] == pytest.approx(7200.0, rel=0.01)
    assert cog["assessments_by_grade"]["E2"] == 1
    assert cog["counterevidence"]["found"] == 1
    assert cog["counterevidence"]["resolved"] == 0
    await app_engine.dispose()


@pytest.mark.asyncio
async def test_metrics_page_is_readonly_html(migrated_db: tuple[str, AsyncEngine]) -> None:
    """The /metrics page is a read-only HTML view (no write routes)."""
    scratch_url, _engine = migrated_db
    app, app_engine = await _make_app(scratch_url)
    async with _client(app) as client:
        r = await client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "метрики" in r.text.lower()
    await app_engine.dispose()
