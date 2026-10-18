"""Scenario (DB + реальный commit): перепроверенное утверждение — ответ нового вопроса (T7.74).

Симптом подставки .92: вопрос «Перепроверь утверждение: …5,59…» отработал — сессия
перепроверила уже записанное утверждение и поставила ему свежую действующую оценку, — но
карточка вопроса и «Мои вопросы» говорили «Ответ не записан». Причина: и карточка, и список
брали только те утверждения, которые сессии этого вопроса ЗАПИСАЛИ (`created_in_session`).

Здесь проверяется связь «вопрос → утверждение», построенная по долговременным записям:

* `created` — claim-строку создала сессия этого вопроса (`claims.created_in_session`);
* `reverified` — эта сессия записала оценку уже существующего утверждения
  (`claim_assessments.claim_id + created_in_session`) и о той же паре есть событие
  `claim_reverified` этой же сессии;
* `reused` — та же оценка без события перепроверки: дедуп-повтор по statement+type (T7.9).
  Отдельного audit-типа у него нет (`AuditEventType` закрыт и не расширяется), поэтому
  различение — по наличию или отсутствию события перепроверки там, где факт оценки этой
  сессией уже доказан долговременной строкой.

Ветка `reused` проверяется реальным commit-контуром (`MemoryService.apply_claim_staging`),
а не подделкой строк. Даты сеются явными `created_at`: ни одна проверка не зависит от часов.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.web import labels as ui_labels
from tests.scenario.test_as_of_commit import _commit_claims, _snapshot
from tests.scenario.test_web_answer_api import (
    _affirms_verification,
    _client,
    _make,
    _visible_texts,
)

pytestmark = [pytest.mark.scenario]

_EFF = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"

_T0 = datetime(2025, 3, 1, 12, 0, 0, tzinfo=UTC)
STATEMENT = "Годовая инфляция по итогам года составила 5,59 процентного пункта"


def _ts(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _label(category: str, value: str) -> str:
    """Подпись из словаря — тест не переписывает фразы, а сверяется со словарём."""
    return ui_labels.describe(category, value)["label"]


async def _scalar(engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).first()


async def _seed_question(
    engine: AsyncEngine,
    *,
    question_id: uuid.UUID,
    text_value: str,
    state: str = "verified",
    created_at: datetime | None = None,
) -> None:
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO questions (id, text, origin, state, priority, created_at) "
                "VALUES (:id, :t, 'seeded', :s, 0, :ca)"
            ),
            {"id": question_id, "t": text_value, "s": state, "ca": created_at or _ts(0)},
        )
        await conn.commit()


async def _seed_session(
    engine: AsyncEngine,
    *,
    session_id: uuid.UUID,
    question_id: uuid.UUID,
    state: str = "succeeded",
    termination_reason: str | None = "goal_reached",
) -> None:
    async with engine.connect() as conn:
        return_snapshot = (await conn.execute(text(f"SELECT {_EFF}"))).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO sessions (id, state, question_id, config_snapshot_id, "
                "termination_reason, started_at) VALUES (:id, :s, :q, :c, :r, now())"
            ),
            {"id": session_id, "s": state, "q": question_id, "c": return_snapshot, "r": termination_reason},
        )
        await conn.commit()


async def _seed_claim(
    engine: AsyncEngine,
    *,
    claim_id: uuid.UUID,
    session_id: uuid.UUID,
    statement: str = STATEMENT,
    created_at: datetime | None = None,
) -> None:
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status, "
                "created_in_session, created_at) VALUES (:id, :s, 'temporal_fact', 'fresh', :c, :ca)"
            ),
            {"id": claim_id, "s": statement, "c": session_id, "ca": created_at or _ts(1)},
        )
        await conn.commit()


async def _seed_assessment(
    engine: AsyncEngine,
    *,
    assessment_id: uuid.UUID,
    claim_id: uuid.UUID,
    grade: str,
    epistemic: str,
    created_in_session: uuid.UUID,
    created_at: datetime,
    head_state: str | None = None,
) -> None:
    """Строка оценки (её пишет rules engine на коммите) и, если нужно, голова lifecycle.

    `head_state='current'` ставит эту оценку действующей; `'pending'`/`'invalid'` — голову
    без действующей оценки (AGENTS §3: оба nullable-поля NULL). Прежняя строка оценки
    остаётся в таблице: она и есть источник «было → стало».
    """
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO claim_assessments (id, claim_id, effective_grade, epistemic_status, "
                "rules_version, rules_hash, evidence_set_hash, assessed_scope, confidence, valid, "
                "created_in_session, created_at) VALUES (:a, :c, :g, :e, 'rules-v2', 'h', 'ev', "
                "'{\"x\": 1}', 0.9, true, :s, :ca)"
            ),
            {
                "a": assessment_id,
                "c": claim_id,
                "g": grade,
                "e": epistemic,
                "s": created_in_session,
                "ca": created_at,
            },
        )
        if head_state == "current":
            await conn.execute(
                text(
                    "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
                    "current_assessment_id, epistemic_status, prepared_by) VALUES "
                    f"(:c, {_EFF}, 'current', :a, :e, 'rules_activation') "
                    "ON CONFLICT (claim_id, config_snapshot_id) DO UPDATE SET "
                    "assessment_state = 'current', current_assessment_id = :a, epistemic_status = :e"
                ),
                {"c": claim_id, "a": assessment_id, "e": epistemic},
            )
        elif head_state in ("pending", "invalid"):
            await conn.execute(
                text(
                    "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
                    "current_assessment_id, epistemic_status, prepared_by) VALUES "
                    f"(:c, {_EFF}, :st, NULL, NULL, 'rules_activation')"
                ),
                {"c": claim_id, "st": head_state},
            )
        await conn.commit()


async def _seed_events(
    engine: AsyncEngine,
    session_id: uuid.UUID,
    events: list[tuple[int, str, dict[str, Any]]],
) -> None:
    """Лента событий с явными `sequence` (хронология — только по ним, AGENTS §7)."""
    async with engine.connect() as conn:
        for sequence, type_, payload in events:
            await conn.execute(
                text(
                    "INSERT INTO audit_events (id, session_id, sequence, type, payload) "
                    "VALUES (:id, :s, :seq, :type, CAST(:payload AS JSONB))"
                ),
                {
                    "id": uuid.uuid4(),
                    "s": session_id,
                    "seq": sequence,
                    "type": type_,
                    "payload": json.dumps(payload),
                },
            )
        await conn.commit()


async def _card(app: Any, question_id: uuid.UUID) -> dict[str, Any]:
    async with _client(app) as client:
        r = await client.get(f"/api/v1/questions/{question_id}/answer")
        assert r.status_code == 200
        return r.json()


async def _queue_rows(app: Any) -> list[dict[str, Any]]:
    async with _client(app) as client:
        r = await client.get("/api/v1/questions")
        assert r.status_code == 200
        return list(r.json()["questions"])


# ─── перепроверка: ответ нового вопроса без нового утверждения ─────────────


@pytest.mark.asyncio
async def test_reverified_claim_is_the_answer_of_the_new_question(
    migrated_db: tuple[str, Any], tmp_path: Path
) -> None:
    """Карточка вопроса перепроверки обязана назвать ответ (прежний код молчал)."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")

    anchor_q, anchor_s = uuid.uuid4(), uuid.uuid4()
    reverify_q, reverify_s = uuid.uuid4(), uuid.uuid4()
    claim_id, old_assessment, new_assessment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await _seed_question(engine, question_id=anchor_q, text_value="Годовая инфляция по итогам года?", created_at=_ts(0))
    await _seed_session(engine, session_id=anchor_s, question_id=anchor_q)
    await _seed_claim(engine, claim_id=claim_id, session_id=anchor_s, created_at=_ts(1))
    await _seed_assessment(
        engine,
        assessment_id=old_assessment,
        claim_id=claim_id,
        grade="E3",
        epistemic="supported",
        created_in_session=anchor_s,
        created_at=_ts(1),
        head_state="current",
    )
    await _seed_events(
        engine,
        anchor_s,
        [(30, "claim_assessed", {"assessment_id": str(old_assessment), "reasons": ["requirements_met"]})],
    )

    await _seed_question(
        engine,
        question_id=reverify_q,
        text_value=f"Перепроверь по независимому источнику: {STATEMENT}?",
        created_at=_ts(5),
    )
    await _seed_session(engine, session_id=reverify_s, question_id=reverify_q)
    # сессия перепроверки НЕ заводила нового claim'а: только новую оценку существующего
    await _seed_assessment(
        engine,
        assessment_id=new_assessment,
        claim_id=claim_id,
        grade="E1",
        epistemic="hypothesis",
        created_in_session=reverify_s,
        created_at=_ts(6),
        head_state="current",
    )
    await _seed_events(
        engine,
        reverify_s,
        [
            (40, "claim_reverified", {"claim_id": str(claim_id), "reference": STATEMENT[:36]}),
            (41, "claim_assessed", {"assessment_id": str(new_assessment), "reasons": ["as_of_missing"]}),
        ],
    )

    card = await _card(app, reverify_q)

    # (а) утверждение видно в карточке вопроса перепроверки, и оно не «создано им»
    assert [item["id"] for item in card["claims"]] == [str(claim_id)]
    claim = card["claims"][0]
    assert claim["relation"] == "reverified"
    assert claim["relation_label"] == _label("claim_relation", "reverified")
    assert claim["relation_hint"]

    # (б) итог вопроса честный: ответ есть, «Ответ не записан» больше не звучит
    assert card["result"]["kind"] == "reverified"
    assert card["result"]["label"] == _label("answer_result", "reverified")
    joined = "\n".join(_visible_texts(card))
    assert "Ответ не записан" not in joined

    # (в) история оценки: было → стало, с причинами обеих оценок из ленты
    history = claim["reverify_history"]
    assert len(history) == 1
    entry = history[0]
    assert entry["from"]["grade_label"] == _label("evidence_grade", "E3")
    assert entry["to"]["grade_label"] == _label("evidence_grade", "E1")
    assert entry["text"] == (
        f"было: {_label('evidence_grade', 'E3')} ({_label('epistemic_status', 'supported')}) → "
        f"стало: {_label('evidence_grade', 'E1')} ({_label('epistemic_status', 'hypothesis')})"
    )
    assert [item["label"] for item in entry["from_reasons"]] == [_label("assessment_reason", "requirements_met")]
    assert [item["label"] for item in entry["reasons"]] == [_label("assessment_reason", "as_of_missing")]

    # (г) шаги рассказывают про ТУ работу, что перепроверяла (а не про сессию-якорь)
    assert card["work"]["session_id"] == str(reverify_s)

    # (д) ни одна новая строка не утверждает проверку: её утверждает только бейдж надёжности
    # (здесь действующая оценка E1 hypothesis — бейдж не утверждающий)
    affirming = [part for part in _visible_texts(card) if _affirms_verification(part)]
    assert affirming == []

    # (е) карточка вопроса-якоря не изменилась: там утверждение по-прежнему «создано им»
    anchor_card = await _card(app, anchor_q)
    assert anchor_card["claims"][0]["relation"] == "created"
    assert anchor_card["result"]["kind"] == "answered"

    # (ж) список «Мои вопросы» говорит то же самое, что карточка
    rows = {row["id"]: row for row in await _queue_rows(app)}
    row = rows[str(reverify_q)]
    assert row["answer"]["kind"] == card["result"]["kind"]
    assert row["answer"]["label"] == card["result"]["label"]
    assert row["answer"]["relation_label"] == claim["relation_label"]
    assert row["answer"]["statement"] == STATEMENT

    await app_engine.dispose()


@pytest.mark.asyncio
async def test_reverify_that_changed_nothing_does_not_invent_a_history(
    migrated_db: tuple[str, Any], tmp_path: Path
) -> None:
    """Перепроверка с той же оценкой — ответ, но «было → стало» появляется только при изменении."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    anchor_q, anchor_s = uuid.uuid4(), uuid.uuid4()
    reverify_q, reverify_s = uuid.uuid4(), uuid.uuid4()
    claim_id, old_assessment, new_assessment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await _seed_question(engine, question_id=anchor_q, text_value="Годовая инфляция по итогам года?", created_at=_ts(0))
    await _seed_session(engine, session_id=anchor_s, question_id=anchor_q)
    await _seed_claim(engine, claim_id=claim_id, session_id=anchor_s, created_at=_ts(1))
    await _seed_assessment(
        engine,
        assessment_id=old_assessment,
        claim_id=claim_id,
        grade="E3",
        epistemic="supported",
        created_in_session=anchor_s,
        created_at=_ts(1),
        head_state="current",
    )
    await _seed_question(engine, question_id=reverify_q, text_value=f"Перепроверь: {STATEMENT}?", created_at=_ts(5))
    await _seed_session(engine, session_id=reverify_s, question_id=reverify_q)
    await _seed_assessment(
        engine,
        assessment_id=new_assessment,
        claim_id=claim_id,
        grade="E3",
        epistemic="supported",
        created_in_session=reverify_s,
        created_at=_ts(6),
        head_state="current",
    )
    await _seed_events(engine, reverify_s, [(40, "claim_reverified", {"claim_id": str(claim_id)})])

    card = await _card(app, reverify_q)
    assert card["claims"][0]["relation"] == "reverified"
    assert card["result"]["kind"] == "reverified"
    # оценка не изменилась → карточка этого не утверждает
    assert card["claims"][0]["reverify_history"] == []

    await app_engine.dispose()


@pytest.mark.asyncio
async def test_partially_finished_reverify_is_called_partial(
    migrated_db: tuple[str, Any], tmp_path: Path
) -> None:
    """Частичность берётся из состояния вопроса/сессии, а не из фантазии витрины."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    anchor_q, anchor_s = uuid.uuid4(), uuid.uuid4()
    reverify_q, reverify_s = uuid.uuid4(), uuid.uuid4()
    claim_id, old_assessment, new_assessment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await _seed_question(engine, question_id=anchor_q, text_value="Годовая инфляция по итогам года?", created_at=_ts(0))
    await _seed_session(engine, session_id=anchor_s, question_id=anchor_q)
    await _seed_claim(engine, claim_id=claim_id, session_id=anchor_s, created_at=_ts(1))
    await _seed_assessment(
        engine,
        assessment_id=old_assessment,
        claim_id=claim_id,
        grade="E3",
        epistemic="supported",
        created_in_session=anchor_s,
        created_at=_ts(1),
        head_state="current",
    )
    await _seed_question(
        engine,
        question_id=reverify_q,
        text_value=f"Перепроверь: {STATEMENT}?",
        state="partially_answered",
        created_at=_ts(5),
    )
    await _seed_session(engine, session_id=reverify_s, question_id=reverify_q)
    await _seed_assessment(
        engine,
        assessment_id=new_assessment,
        claim_id=claim_id,
        grade="E2",
        epistemic="supported",
        created_in_session=reverify_s,
        created_at=_ts(6),
        head_state="current",
    )
    await _seed_events(engine, reverify_s, [(40, "claim_reverified", {"claim_id": str(claim_id)})])

    card = await _card(app, reverify_q)
    assert card["result"]["kind"] == "partially_reverified"
    assert card["result"]["label"] == _label("answer_result", "partially_reverified")
    row = {item["id"]: item for item in await _queue_rows(app)}[str(reverify_q)]
    assert row["answer"]["kind"] == "partially_reverified"
    assert row["answer"]["label"] == card["result"]["label"]

    await app_engine.dispose()


@pytest.mark.asyncio
async def test_pending_head_after_a_reverify_is_still_not_an_answer(
    migrated_db: tuple[str, Any], tmp_path: Path
) -> None:
    """Оценка не принята — ответа нет, даже если эта сессия к утверждению прикоснулась."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    anchor_q, anchor_s = uuid.uuid4(), uuid.uuid4()
    reverify_q, reverify_s = uuid.uuid4(), uuid.uuid4()
    claim_id, old_assessment, new_assessment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await _seed_question(engine, question_id=anchor_q, text_value="Годовая инфляция по итогам года?", created_at=_ts(0))
    await _seed_session(engine, session_id=anchor_s, question_id=anchor_q)
    await _seed_claim(engine, claim_id=claim_id, session_id=anchor_s, created_at=_ts(1))
    await _seed_assessment(
        engine,
        assessment_id=old_assessment,
        claim_id=claim_id,
        grade="E3",
        epistemic="supported",
        created_in_session=anchor_s,
        created_at=_ts(1),
        head_state="pending",
    )
    await _seed_question(engine, question_id=reverify_q, text_value=f"Перепроверь: {STATEMENT}?", created_at=_ts(5))
    await _seed_session(engine, session_id=reverify_s, question_id=reverify_q)
    await _seed_assessment(
        engine,
        assessment_id=new_assessment,
        claim_id=claim_id,
        grade="E2",
        epistemic="deferred",
        created_in_session=reverify_s,
        created_at=_ts(6),
    )
    await _seed_events(engine, reverify_s, [(40, "claim_reverified", {"claim_id": str(claim_id)})])

    card = await _card(app, reverify_q)
    assert card["claims"] == []
    assert [item["id"] for item in card["other_claims"]] == [str(claim_id)]
    assert card["result"]["kind"] == "no_answer"
    assert card["result"]["label"] == _label("answer_result", "no_answer")
    # ни одна строка (включая подписанное отношение) не утверждает проверку
    assert [part for part in _visible_texts(card) if _affirms_verification(part)] == []

    row = {item["id"]: item for item in await _queue_rows(app)}[str(reverify_q)]
    assert row["answer"]["statement"] is None and row["answer"]["reliability"] is None

    await app_engine.dispose()


# ─── дедуп-повтор: реальный commit-контур, без события перепроверки ────────


@pytest.mark.asyncio
async def test_dedup_reuse_is_an_answer_of_the_new_question(
    migrated_db: tuple[str, Any], tmp_path: Path
) -> None:
    """Тот же statement+type вторично: нового claim'а нет, события `claim_reverified` нет —
    и всё равно это ответ вопроса (`reused`). Ветка построена реальным MemoryService.apply."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    snap = await _snapshot(engine)

    first_text = f"{STATEMENT}?"
    second_text = f"Подтверди ещё раз: {STATEMENT}?"
    await _commit_claims(engine, snap, question_text=first_text, claims=[(STATEMENT, None)])
    await _commit_claims(
        engine, snap, question_text=second_text, claims=[(STATEMENT, None)], reuse=True
    )

    # новое утверждение не заводилось (дедуп по statement+type) и события перепроверки нет
    claim_count = await _scalar(engine, "SELECT count(*) FROM claims")
    assert claim_count is not None and claim_count[0] == 1
    reverify_events = await _scalar(
        engine, "SELECT count(*) FROM audit_events WHERE type = 'claim_reverified'"
    )
    assert reverify_events is not None and reverify_events[0] == 0

    second_q = await _scalar(engine, "SELECT id FROM questions WHERE text = :t", {"t": second_text})
    assert second_q is not None
    question_id = second_q[0] if isinstance(second_q[0], uuid.UUID) else uuid.UUID(str(second_q[0]))

    card = await _card(app, question_id)
    assert [item["relation"] for item in card["claims"]] == ["reused"]
    assert card["claims"][0]["relation_label"] == _label("claim_relation", "reused")
    assert card["result"]["kind"] == "reused"
    assert card["result"]["label"] == _label("answer_result", "reused")
    # утверждение принадлежит работе другого вопроса — шаги берёт сессия этого вопроса
    work = await _scalar(
        engine, "SELECT id FROM sessions WHERE question_id = :q ORDER BY created_at ASC LIMIT 1", {"q": question_id}
    )
    assert work is not None and card["work"]["session_id"] == str(work[0])

    row = {item["id"]: item for item in await _queue_rows(app)}[str(question_id)]
    assert row["answer"]["kind"] == "reused"
    assert row["answer"]["label"] == card["result"]["label"]
    assert row["answer"]["relation_label"] == _label("claim_relation", "reused")

    await app_engine.dispose()


# ─── стоимость списка не растёт от перепроверок (нет N+1) ──────────────────


@pytest.mark.asyncio
async def test_the_queue_page_does_not_add_queries_for_reverified_answers(
    migrated_db: tuple[str, Any], tmp_path: Path
) -> None:
    """Тот же фиксированный бюджет SELECT'ов, когда ответы — перепроверки (окно + IN-выборка)."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")

    statements: list[str] = []

    @event.listens_for(app_engine.sync_engine, "before_cursor_execute")
    def _capture(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, many: Any) -> None:
        statements.append(stmt.strip())

    def _select_count() -> int:
        return sum(1 for stmt in statements if stmt.upper().startswith("SELECT"))

    anchor_q, anchor_s, claim_id, assessment_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_question(engine, question_id=anchor_q, text_value="Годовая инфляция по итогам года?", created_at=_ts(0))
    await _seed_session(engine, session_id=anchor_s, question_id=anchor_q)
    await _seed_claim(engine, claim_id=claim_id, session_id=anchor_s, created_at=_ts(1))
    await _seed_assessment(
        engine,
        assessment_id=assessment_id,
        claim_id=claim_id,
        grade="E3",
        epistemic="supported",
        created_in_session=anchor_s,
        created_at=_ts(1),
        head_state="current",
    )

    try:
        statements.clear()
        await _queue_rows(app)
        created_only = _select_count()

        # три вопроса перепроверяют то же утверждение: по сессии и по событию на каждый
        for index in range(3):
            question_id, session_id, new_assessment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await _seed_question(
                engine,
                question_id=question_id,
                text_value=f"Перепроверь ({index}): {STATEMENT}?",
                created_at=_ts(10 + index),
            )
            await _seed_session(engine, session_id=session_id, question_id=question_id)
            await _seed_assessment(
                engine,
                assessment_id=new_assessment,
                claim_id=claim_id,
                grade="E3",
                epistemic="supported",
                created_in_session=session_id,
                created_at=_ts(20 + index),
                head_state="current",
            )
            await _seed_events(engine, session_id, [(50, "claim_reverified", {"claim_id": str(claim_id)})])

        statements.clear()
        rows = await _queue_rows(app)
        with_reverifies = _select_count()
    finally:
        event.remove(app_engine.sync_engine, "before_cursor_execute", _capture)

    answers = {row["answer"]["kind"] for row in rows}
    assert answers == {"answered", "reverified"}
    assert with_reverifies == created_only, "перепроверки добавили запросы к списку (N+1)"
    assert with_reverifies <= 4

    await app_engine.dispose()
