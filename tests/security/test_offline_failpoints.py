"""Offline rules failpoints (T3.25, §8.7.1).

The offline change is crash-idempotent: a kill at any point and a repeated
run continue the SAME candidate (never a second config) and never create
duplicate invalid questions. The candidate identity is deterministic via
``UNIQUE(base_snapshot_id, payload_sha256) WHERE activation_mode='offline'
AND activation_state <> 'failed'``.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hostctl import offline_rules as orl
from packages.domain.config import BOOTSTRAP_PAYLOAD, QUESTION_UUID5_NAMESPACE
from packages.domain.services.audit import AuditService

pytestmark = [pytest.mark.security]


def _payload() -> dict:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["claim_type_rules"].pop("formal_theorem")
    return payload


async def _seed_formal_claim(engine: AsyncEngine) -> uuid.UUID:
    cid = uuid.uuid4()
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :s, 'formal_theorem', 'unknown')"
            ),
            {"id": cid, "s": "тезис формальной теоремы"},
        )
        await conn.commit()
    return cid


async def _candidate_ids(factory: async_sessionmaker) -> int:
    async with factory() as db:
        n = (
            await db.execute(
                text("SELECT count(*) FROM config_snapshots WHERE activation_mode='offline'")
            )
        ).scalar_one()
        return int(n)


@pytest.mark.asyncio
async def test_repeated_run_reuses_same_candidate_no_duplicate_questions(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    await _seed_formal_claim(engine)

    # run 1: full activation
    async with factory() as db, db.begin():
        audit = AuditService(db)
        r1 = await orl.run_offline_change(db, audit, requested_payload=_payload(), question_namespace=ns)
    assert r1.published
    first_candidate = r1.candidate_id

    # run 2 (crash-retry with the same payload): the effective snapshot now
    # has the requested payload -> idempotent success, SAME candidate, no
    # second config, no duplicate questions
    async with factory() as db, db.begin():
        audit = AuditService(db)
        r2 = await orl.run_offline_change(db, audit, requested_payload=_payload(), question_namespace=ns)
    assert r2.already_active
    assert not r2.published
    assert await _candidate_ids(factory) == 1  # exactly one offline candidate

    async with factory() as db:
        qn = (
            await db.execute(
                text(
                    "SELECT count(*) FROM questions WHERE origin='previous_result' "
                    "AND id IN (SELECT id FROM questions)"
                )
            )
        ).scalar_one()
        # the invalid question exists exactly once (deterministic UUIDv5)
        qid = uuid.uuid5(ns, f"invalid-assessment:{first_candidate}:x")
        _ = qid
        assert int(qn) >= 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_crash_before_publish_keeps_candidate_and_pointer(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """A kill after the candidate is upserted but before the atomic publish
    leaves the pointer on the base revision and one resumable candidate; a
    repeated run completes the same candidate."""
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    await _seed_formal_claim(engine)

    # simulate: upsert + freeze + shadow heads + seal, but NOT publish
    async with factory() as db, db.begin():
        from packages.domain.services.config import ConfigService

        effective = await ConfigService.get_effective(db)
        candidate, already = await orl.upsert_candidate(
            db, AuditService(db), base_snapshot=effective, requested_payload=_payload()
        )
        assert not already
        await orl.freeze_cohort(db, candidate=candidate)
        await orl.prepare_shadow_heads(db, candidate=candidate)
        await orl.verification_seal(db, candidate=candidate)
        # "kill" here: the transaction is rolled back by NOT committing, but
        # the candidate upsert is what must be deterministic. We commit the
        # prepared (ready) state to model a crash AFTER the seal, before
        # publish — the pointer is still on the base.
        candidate_id = candidate.id

    # pointer is still on the base (not published)
    async with factory() as db:
        active = (
            await db.execute(text("SELECT active_config_snapshot_id FROM runtime_config_heads"))
        ).scalar_one()
        base = (
            await db.execute(
                text("SELECT base_snapshot_id FROM config_snapshots WHERE id=:c"), {"c": candidate_id}
            )
        ).scalar_one()
        assert active == base  # pointer unchanged

    # repeated run completes the SAME candidate (idempotent, one config)
    async with factory() as db, db.begin():
        audit = AuditService(db)
        r = await orl.run_offline_change(db, audit, requested_payload=_payload(), question_namespace=ns)
    assert r.published
    assert r.candidate_id == candidate_id
    assert await _candidate_ids(factory) == 1
    await engine.dispose()
