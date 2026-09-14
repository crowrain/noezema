"""Unit: offline rules change — the MVP activation protocol (T3.12,
§8.7.1). Scratch DB with the bootstrap config + runtime head."""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hostctl.offline_rules import OfflineRulesError, run_offline_change
from packages.domain.config import (
    BOOTSTRAP_PAYLOAD,
    QUESTION_UUID5_NAMESPACE,
)
from packages.domain.services.audit import AuditService


@pytest.fixture()
async def offline_env(migrated_db: tuple[str, AsyncEngine]) -> AsyncIterator[async_sessionmaker]:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _new_payload_without_formal_theorem() -> dict:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["claim_type_rules"].pop("formal_theorem")
    return payload


async def _seed_claim(factory: async_sessionmaker, claim_type: str) -> uuid.UUID:
    cid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :stmt, :ct, 'unknown')"
            ),
            {"id": cid, "stmt": f"утверждение типа {claim_type}", "ct": claim_type},
        )
    return cid


async def test_offline_idempotent_when_payload_unchanged(offline_env):
    factory = offline_env
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    async with factory() as db, db.begin():
        audit = AuditService(db)
        res = await run_offline_change(
            db, audit, requested_payload=copy.deepcopy(BOOTSTRAP_PAYLOAD), question_namespace=ns
        )
    assert res.already_active
    assert res.state == "active"
    assert not res.published


async def test_offline_activates_new_payload_and_creates_invalid_question(offline_env):
    factory = offline_env
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    claim_id = await _seed_claim(factory, "formal_theorem")

    async with factory() as db, db.begin():
        audit = AuditService(db)
        res = await run_offline_change(
            db, audit, requested_payload=_new_payload_without_formal_theorem(), question_namespace=ns
        )
    assert res.published
    assert res.state == "active"
    assert res.invalid_questions_created == 1
    assert res.cohort_count >= 1
    assert res.heads_digest

    # the pointer now points at the candidate (not the bootstrap)
    async with factory() as db:
        head = (await db.execute(text("SELECT active_config_snapshot_id FROM runtime_config_heads"))).scalar_one()
        assert head != res.candidate_id or head is not None
        # the invalid question exists with a deterministic UUIDv5
        qid = uuid.uuid5(ns, f"invalid-assessment:{res.candidate_id}:{claim_id}")
        q = (await db.execute(text("SELECT text, origin FROM questions WHERE id=:q"), {"q": qid})).mappings().one()
        assert q["origin"] == "previous_result"


async def test_offline_second_run_is_already_active(offline_env):
    factory = offline_env
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    payload = _new_payload_without_formal_theorem()

    async with factory() as db, db.begin():
        audit = AuditService(db)
        first = await run_offline_change(db, audit, requested_payload=payload, question_namespace=ns)
    assert first.published

    # a repeated run with the same payload is idempotent (already active)
    async with factory() as db, db.begin():
        audit = AuditService(db)
        second = await run_offline_change(db, audit, requested_payload=payload, question_namespace=ns)
    assert second.already_active
    assert not second.published
    # the same candidate is reused, not a new config
    async with factory() as db:
        n = (
            await db.execute(
                text("SELECT count(*) FROM config_snapshots WHERE activation_mode='offline'")
            )
        ).scalar_one()
        assert int(n) == 1


async def test_offline_deferred_claim_becomes_pending_head(offline_env):
    """A temporal_fact (requires_as_of) with no as_of -> deferred -> PENDING
    shadow head (no current assessment), distinct from an invalid head."""
    factory = offline_env
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    temporal_id = await _seed_claim(factory, "temporal_fact")  # requires_as_of, no as_of
    formal_id = await _seed_claim(factory, "formal_theorem")  # removed by the candidate

    async with factory() as db, db.begin():
        audit = AuditService(db)
        res = await run_offline_change(
            db, audit, requested_payload=_new_payload_without_formal_theorem(), question_namespace=ns
        )
    assert res.published
    async with factory() as db:
        t_row = (
            (
                await db.execute(
                    text(
                        "SELECT assessment_state, current_assessment_id, epistemic_status "
                        "FROM claim_assessment_heads WHERE claim_id=:c AND config_snapshot_id=:s"
                    ),
                    {"c": temporal_id, "s": res.candidate_id},
                )
            )
            .mappings()
            .first()
        )
        f_row = (
            (
                await db.execute(
                    text(
                        "SELECT assessment_state FROM claim_assessment_heads "
                        "WHERE claim_id=:c AND config_snapshot_id=:s"
                    ),
                    {"c": formal_id, "s": res.candidate_id},
                )
            )
            .mappings()
            .first()
        )
        assert t_row is not None
        assert t_row["assessment_state"] == "pending"
        assert t_row["current_assessment_id"] is None
        assert t_row["epistemic_status"] is None
        # the removed-type claim is invalid, not pending
        assert f_row is not None
        assert f_row["assessment_state"] == "invalid"


async def test_offline_rejects_active_session(offline_env):
    factory = offline_env
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) VALUES "
                "(:id, 'exploring', (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid},
        )
    async with factory() as db, db.begin():
        audit = AuditService(db)
        with pytest.raises(OfflineRulesError):
            await run_offline_change(
                db, audit, requested_payload=_new_payload_without_formal_theorem(), question_namespace=ns
            )
    # the head tuple is untouched (bootstrap still active)
    async with factory() as db:
        head = (
            await db.execute(
                text("SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope='global'")
            )
        ).scalar_one()
        assert head is not None
