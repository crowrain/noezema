"""Scenario (DB): карточка берёт причину оценки из ТОГО события, которое её записало (T7.87).

Ловушка разобрана в ADR-0035 §3 и STATUS T7.86: после пересчёта рабочим переоценки
(`packages/memory/reassessment.py`) голова указывает на новую оценку, а её причины лежат в
событии `reassessment_job_completed` — не в `claim_assessed`. Прежняя выборка причин читала
только `claim_assessed`, поэтому карточка становилась без объяснения ровно там, где понижение
уровня и нужно объяснить (на стенде .92 это `9266248e…`: E1/0.15 hypothesis,
`insufficient_independence`).

Здесь проверяется честность витрины: она называет причину, записанную правилами, из той же
ленты, где она лежит. Ни новой колонки, ни нового типа события, ни пересчёта оценки — только
выборка уже записанного (ADR-0026).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.web import labels as ui_labels
from tests.scenario.test_web_answer_api import _client, _make, _seed_claim, _seed_question, _seed_session

pytestmark = [pytest.mark.scenario]

REASONS_WORKER = ["insufficient_independence"]
STATEMENT = "Годовая инфляция в России по итогам 2025 года составила 5,59%"


async def _seed_worker_assessment(
    engine: AsyncEngine,
    *,
    claim_id: uuid.UUID,
    grade: str,
    epistemic: str,
    confidence: float,
) -> uuid.UUID:
    """Оценка, записанная рабочим переоценки: `created_in_session IS NULL`, голова —
    `prepared_by = 'reassessment_worker'` (ровно та форма, что пишет
    `packages/memory/reassessment.py`)."""
    assessment_id = uuid.uuid4()
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO claim_assessments (id, claim_id, effective_grade, epistemic_status, "
                "rules_version, rules_hash, evidence_set_hash, assessed_scope, confidence, valid, "
                "created_in_session) VALUES (:a, :c, :g, :e, 'rules-v2', 'h', 'ev', '{}', :f, true, NULL)"
            ),
            {"a": assessment_id, "c": claim_id, "g": grade, "e": epistemic, "f": confidence},
        )
        await conn.execute(
            text(
                "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
                "current_assessment_id, epistemic_status, prepared_by) VALUES "
                "(:c, (SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global'), "
                "'current', :a, :e, 'reassessment_worker')"
            ),
            {"c": claim_id, "a": assessment_id, "e": epistemic},
        )
        await conn.commit()
    return assessment_id


async def _seed_worker_completed(
    engine: AsyncEngine,
    *,
    claim_id: uuid.UUID,
    assessment_id: uuid.UUID,
    grade: str,
    epistemic: str,
    confidence: float,
    reasons: list[str],
    sequence: int = 120,
) -> None:
    """Событие `reassessment_job_completed` в той форме, в какой его пишет рабочий:
    без строки сессии (его у рабочего нет) и с полным набором payload-ключей."""
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_events (id, session_id, sequence, type, actor, public_summary, payload) "
                "VALUES (:id, NULL, :seq, 'reassessment_job_completed', 'memory.reassessment', :summary, "
                "CAST(:payload AS JSONB))"
            ),
            {
                "id": uuid.uuid4(),
                "seq": sequence,
                "summary": f"reassessment {claim_id}: {grade}/{epistemic}",
                "payload": _json(
                    {
                        "job_id": str(uuid.uuid4()),
                        "claim_id": str(claim_id),
                        "assessment_id": str(assessment_id),
                        "attempt": 1,
                        "grade": grade,
                        "epistemic_status": epistemic,
                        "confidence": confidence,
                        "reasons": reasons,
                    }
                ),
            },
        )
        await conn.commit()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_worker_reassessment_explains_the_card(migrated_db, tmp_path: Path) -> None:
    """Оценку записал рабочий переоценки — карточка обязана назвать её причину.

    Единственная запись ленты об этой оценке — `reassessment_job_completed`. Прежде
    такая карточка была объяснений лишена (`grade_reasons` пуст)."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid, sid, claim_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_question(engine, question_id=qid, state="verified")
    await _seed_session(
        engine, session_id=sid, question_id=qid, state="succeeded", termination_reason="goal_reached"
    )
    # утверждение создала сессия этого вопроса, а оценку пересчитал рабочий
    assert (
        await _seed_claim(
            engine,
            claim_id=claim_id,
            session_id=sid,
            statement=STATEMENT,
            head_state="none",
        )
        is None
    )
    assessment_id = await _seed_worker_assessment(
        engine, claim_id=claim_id, grade="E1", epistemic="hypothesis", confidence=0.15
    )
    await _seed_worker_completed(
        engine,
        claim_id=claim_id,
        assessment_id=assessment_id,
        grade="E1",
        epistemic="hypothesis",
        confidence=0.15,
        reasons=REASONS_WORKER,
    )

    async with _client(app) as client:
        card = (await client.get(f"/api/v1/questions/{qid}/answer")).json()

    claim = card["claims"][0]
    entry = ui_labels.describe("assessment_reason", "insufficient_independence")
    assert [item["code"] for item in claim["grade_reasons"]] == REASONS_WORKER, claim["grade_reasons"]
    assert claim["grade_reasons"][0]["label"] == entry["label"]
    assert claim["grade_reasons"][0]["hint"] == entry["hint"]
    # человеческое название причины доходит до экрана, код наружу не выходит
    assert entry["label"] in " ".join(_visible_lines(card)), claim
    await engine.dispose()


@pytest.mark.asyncio
async def test_old_assessment_reasons_do_not_masquerade_as_current(
    migrated_db, tmp_path: Path
) -> None:
    """Причины текущей оценки не подменяются причиной прежней оценки того же утверждения,
    даже когда прежняя записана в ленте раньше, а текущая — в другом событии."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid, sid, claim_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_question(engine, question_id=qid, state="verified")
    await _seed_session(
        engine, session_id=sid, question_id=qid, state="succeeded", termination_reason="goal_reached"
    )
    assert (
        await _seed_claim(
            engine, claim_id=claim_id, session_id=sid, statement=STATEMENT, head_state="none"
        )
        is None
    )
    assessment_id = await _seed_worker_assessment(
        engine, claim_id=claim_id, grade="E1", epistemic="hypothesis", confidence=0.15
    )
    old_assessment = uuid.uuid4()
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO claim_assessments (id, claim_id, effective_grade, epistemic_status, "
                "rules_version, rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                "VALUES (:a, :c, 'E3', 'supported', 'rules-v2', 'h', 'ev', '{}', 0.75, true)"
            ),
            {"a": old_assessment, "c": claim_id},
        )
        await conn.commit()
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_events (id, session_id, sequence, type, payload) VALUES "
                "(:id, :s, 60, 'claim_assessed', CAST(:payload AS JSONB))"
            ),
            {
                "id": uuid.uuid4(),
                "s": sid,
                "payload": _json(
                    {
                        "claim_id": str(claim_id),
                        "assessment_id": str(old_assessment),
                        "grade": "E3",
                        "epistemic_status": "supported",
                        "reasons": ["requirements_met"],
                    }
                ),
            },
        )
        await conn.commit()
    await _seed_worker_completed(
        engine,
        claim_id=claim_id,
        assessment_id=assessment_id,
        grade="E1",
        epistemic="hypothesis",
        confidence=0.15,
        reasons=REASONS_WORKER,
        sequence=70,
    )

    async with _client(app) as client:
        card = (await client.get(f"/api/v1/questions/{qid}/answer")).json()

    claim = card["claims"][0]
    assert [item["code"] for item in claim["grade_reasons"]] == REASONS_WORKER, claim["grade_reasons"]
    await engine.dispose()


def _visible_lines(payload: Any) -> list[str]:
    texts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            texts.append(node)

    walk(payload)
    return texts
