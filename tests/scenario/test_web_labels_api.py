"""Scenario (DB): аддитивные подписи в Query/Command API (T7.64, ADR-0026).

Проверяет границу этапа: HTML и JS не менялись, JSON API только дополняется.
Для каждого затронутого эндпоинта проверяется (1) прежний набор ключей цел и
значения прежние, (2) новые поля label/hint/action есть и взяты из единого
словаря подписей `apps/web.labels`, (3) там, где данных нет, подпись честная
(запасной путь или «подробности проверки недоступны»), а не выдуманная.

Пороги надёжности ожидаются из действующего снапшота правил (bootstrap), а не
из памяти теста: экран обязан показывать то, что отдаёт конфигурация.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.web import labels as ui_labels
from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.journal import STATE_CHECKING, JournalStore, TransitionRecord
from hostctl.unit_state import publish_unit_state
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository

pytestmark = [pytest.mark.scenario]

EFF = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"
ADMIN_TOKEN = "secret"


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make(
    scratch_url: str,
    host_lib: Path,
    unit_state: Path,
    *,
    admin_token: str | None = ADMIN_TOKEN,
):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = HostStatusAdapter(host_lib_base=host_lib, unit_state_path=unit_state)
    app = create_app(
        engine=engine, factory=factory, host_adapter=adapter, admin_token=admin_token
    )
    return app, engine, factory


def _healthy_host(host_lib: Path, unit_state: Path) -> None:
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})


def _degraded_host(host_lib: Path, unit_state: Path) -> None:
    """Свежий снимок юнитов + незакрытый переход в журнале: Command API и приём
    вопросов обязаны отказаться (fail-closed), GET продолжают отвечать."""
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})
    JournalStore(host_lib).write_record(
        TransitionRecord(
            attempt_id="t1",
            operation="offline_rules",
            candidate_snapshot_id=None,
            base_snapshot_id=None,
            observed_pointer_tuple={},
            state=STATE_CHECKING,
        )
    )


async def _scalar(engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db:
        res = await db.execute(text(sql), params or {})
        return res.first()


async def _seed_claim(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    statement: str,
    *,
    claim_type: str = "computed_result",
    head_state: str = "current",
    grade: str | None = "E2",
    epistemic: str | None = "supported",
) -> None:
    """head_state: 'current' (head + оценка) | 'pending' | 'invalid' | 'none'."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :s, :t, 'fresh')"
            ),
            {"id": claim_id, "s": statement, "t": claim_type},
        )
        if head_state == "current":
            assessment_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO claim_assessments "
                    "(id, claim_id, effective_grade, epistemic_status, rules_version, "
                    " rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                    "VALUES (:a, :c, :g, :e, 'rules-v2', 'h', 'ev', '{\"x\": 1}', 0.9, true)"
                ),
                {"a": assessment_id, "c": claim_id, "g": grade, "e": epistemic},
            )
            await db.execute(
                text(
                    "INSERT INTO claim_assessment_heads "
                    "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                    " epistemic_status, prepared_by) "
                    f"VALUES (:c, {EFF}, 'current', :a, :e, 'rules_activation')"
                ),
                {"c": claim_id, "a": assessment_id, "e": epistemic},
            )
        elif head_state != "none":
            await db.execute(
                text(
                    "INSERT INTO claim_assessment_heads "
                    "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                    " epistemic_status, prepared_by) "
                    f"VALUES (:c, {EFF}, :st, NULL, NULL, 'rules_activation')"
                ),
                {"c": claim_id, "st": head_state},
            )


async def _seed_question(factory, text_value: str) -> uuid.UUID:
    async with factory() as db, db.begin():
        question = await QuestionRepository.create(
            db, ORMQuestion(text=text_value, origin=QuestionOrigin.SEEDED.value)
        )
        return question.id


# ─── /api/v1/status ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_adds_node_and_recovery_labels_without_replacing_fields(
    migrated_db, tmp_path: Path
) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        before = await client.get("/api/v1/status")
        assert before.status_code == 200
        data = before.json()

    # прежний контракт Query API цел
    for key in ("node_state", "last_error", "config", "session", "counts", "wake", "host"):
        assert key in data, key
    node_state = data["node_state"]
    expected = ui_labels.describe("node_state", node_state)
    assert data["node_state_label"] == expected["label"]
    assert data["node_state_hint"] == expected["hint"]
    assert expected["label"] != node_state  # экран не показывает код состояния

    host = data["host"]
    if "recovery_state" in host:
        recovery = ui_labels.describe("recovery_state", host["recovery_state"])
        assert host["recovery_state_label"] == recovery["label"]
        assert host["recovery_state_action"] == recovery["action"]
    await engine.dispose()


# ─── /api/v1/questions ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_question_queue_rows_keep_their_keys_and_gain_labels(
    migrated_db, tmp_path: Path
) -> None:
    scratch_url, _ = migrated_db
    app, engine, factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    question_id = await _seed_question(factory, "Где зимуют ежи?")

    async with _client(app) as client:
        r = await client.get("/api/v1/questions")
        assert r.status_code == 200
        rows = r.json()["questions"]
        row = next(item for item in rows if item["id"] == str(question_id))

    for key in ("id", "text", "origin", "state", "priority", "created_at", "position", "session"):
        assert key in row, key
    state_entry = ui_labels.describe("question_state", QuestionState.CANDIDATE.value)
    origin_entry = ui_labels.describe("question_origin", QuestionOrigin.SEEDED.value)
    assert row["state_label"] == state_entry["label"]
    assert row["origin_label"] == origin_entry["label"]
    assert row["origin_hint"] == origin_entry["hint"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_post_question_answer_is_annotated_too(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/questions",
            json={"text": "Сколько будет 6*7?", "priority": 4},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert r.status_code == 201
        created = r.json()
        for key in ("id", "text", "origin", "state", "priority", "position", "replayed"):
            assert key in created, key
        assert created["state_label"] == ui_labels.describe(
            "question_state", created["state"]
        )["label"]

        r = await client.get("/api/v1/questions")
        rows = r.json()["questions"]
        by_id = {item["id"]: item for item in rows}
        assert set(by_id) >= {created["id"]}
        assert by_id[created["id"]]["priority"] == created["priority"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_intake_refusal_of_an_unhealthy_host_explains_itself(
    migrated_db, tmp_path: Path
) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _degraded_host(tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/questions",
            json={"text": "Почему небо синее?"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert r.status_code == 423
        body = r.json()
        assert body["rejected"] is True
        assert body["reason"] == "host_not_healthy"  # прежний код причины цел
        refusal = ui_labels.describe_refusal(body["reason"])
        assert body["reason_label"] == refusal["label"]
        assert body["reason_hint"] == refusal["hint"]
        assert body["reason_action"] == refusal["action"]
        assert body["reason_action"]  # оператору всегда говорят, что делать
    await engine.dispose()


# ─── /api/v1/knowledge/claims(+detail) ────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_list_carries_assessment_labels_for_every_head_state(
    migrated_db, tmp_path: Path
) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    supported = uuid.uuid4()
    pending = uuid.uuid4()
    invalid = uuid.uuid4()
    unassessed = uuid.uuid4()
    await _seed_claim(engine, supported, "6*7 равно 42", grade="E2", epistemic="supported")
    await _seed_claim(engine, pending, "Гипотеза о ежах", head_state="pending")
    await _seed_claim(engine, invalid, "Спорное утверждение", head_state="invalid")
    await _seed_claim(engine, unassessed, "Утверждение без оценки", head_state="none")

    async with _client(app) as client:
        r = await client.get("/api/v1/knowledge/claims?q=утверждение&limit=50")
        assert r.status_code == 200
        items = {item["id"]: item for item in r.json()["claims"]}

    assert {str(pending), str(invalid), str(unassessed)} <= set(items)
    for claim_id, head_state in ((pending, "pending"), (invalid, "invalid"), (unassessed, "none")):
        item = items[str(claim_id)]
        for key in ("id", "statement", "claim_type", "head_state", "epistemic_status"):
            assert key in item, key
        assert item["head_state"] == head_state
        assert item["head_label"] == ui_labels.describe("claim_head_state", head_state)["label"]
        # оценки нет → уровня нет: поле остаётся None, а надёжность честная
        assert item.get("grade_label") is None
        assert item["reliability"]["level"] == "unverified"
        assert item["reliability"]["hint"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_supported_claim_is_verified_and_keeps_its_old_fields(
    migrated_db, tmp_path: Path
) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    claim_id = uuid.uuid4()
    await _seed_claim(engine, claim_id, "6*7 равно 42", grade="E2", epistemic="supported")

    async with _client(app) as client:
        r = await client.get(f"/api/v1/knowledge/claims/{claim_id}")
        assert r.status_code == 200
        detail = r.json()

    for key in (
        "id",
        "statement",
        "claim_type",
        "freshness_status",
        "heads",
        "evidence",
        "depends_on",
        "depended_by",
    ):
        assert key in detail, key
    # прежняя оценка живёт в heads: аддитивные поля не переставляют источник
    head = detail["heads"][0]
    assert head["assessment_state"] == "current"
    assert head["effective_grade"] == "E2"
    assert head["epistemic_status"] == "supported"
    assert detail["head_state"] == "current"
    assert detail["grade_label"] == ui_labels.describe("evidence_grade", "E2")["label"]
    assert detail["grade_label"].startswith("E2")
    assert detail["type_label"] == ui_labels.describe("claim_type", "computed_result")["label"]
    assert detail["freshness_label"] == ui_labels.describe("freshness_status", "fresh")["label"]
    reliability = detail["reliability"]
    assert reliability["level"] == "verified"
    assert reliability["color"] == "green"
    assert "E2" in reliability["hint"]
    # evidence нет → «как проверено» говорит об этом прямо (detail подтверждений не скрывает)
    verification = detail["verification"]
    assert isinstance(verification, list) and verification
    assert any("подтверждений нет" in phrase for phrase in verification), verification

    async with _client(app) as client:
        r = await client.get("/api/v1/knowledge/claims?q=42&limit=10")
        item = next(i for i in r.json()["claims"] if i["id"] == str(claim_id))
    # список не обещает «как проверено» — это поле детали
    assert "verification" not in item
    assert item["reliability"]["level"] == "verified"
    await engine.dispose()


@pytest.mark.asyncio
async def test_weak_claim_names_the_threshold_of_its_type_from_the_snapshot(
    migrated_db, tmp_path: Path
) -> None:
    """Подсказка жёлтого уровня берёт порог из действующего снапшота правил."""
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    claim_id = uuid.uuid4()
    await _seed_claim(
        engine,
        claim_id,
        "Кофе бодрит почти всех",
        claim_type="external_fact",
        grade="E1",
        epistemic="hypothesis",
    )
    threshold = BOOTSTRAP_PAYLOAD["claim_type_rules"]["external_fact"]["min_grade_for_supported"]

    async with _client(app) as client:
        r = await client.get(f"/api/v1/knowledge/claims/{claim_id}")
        detail = r.json()

    assert detail["reliability"]["level"] == "weak"
    assert detail["reliability"]["color"] == "yellow"
    assert f"ниже порога {threshold}" in detail["reliability"]["hint"], detail["reliability"]
    # утверждение остаётся hypothesis: представление не повышает статус
    assert detail["heads"][0]["epistemic_status"] == "hypothesis"
    await engine.dispose()


# ─── /api/v1/sessions/{id} + лента ────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_detail_adds_state_label_and_the_five_step_scale(
    migrated_db, tmp_path: Path
) -> None:
    scratch_url, _ = migrated_db
    app, engine, factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    question_id = await _seed_question(factory, "Что делает планировщик?")
    session_id = uuid.uuid4()
    event_id = uuid.uuid4()
    async with factory() as db, db.begin():
        snapshot_id = (
            await db.execute(text("SELECT id FROM config_snapshots LIMIT 1"))
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, question_id, config_snapshot_id) VALUES "
                "(:id, 'exploring', :q, :s)"
            ),
            {"id": session_id, "q": question_id, "s": snapshot_id},
        )
        await db.execute(
            text(
                "INSERT INTO audit_events (id, session_id, sequence, type, public_summary, payload) "
                "VALUES (:id, :s, 1, 'session_state_changed', 'фаза поиска', '{}'::jsonb)"
            ),
            {"id": event_id, "s": session_id},
        )

    async with _client(app) as client:
        r = await client.get(f"/api/v1/sessions/{session_id}")
        assert r.status_code == 200
        detail = r.json()

    for key in ("id", "state", "question_id", "events"):
        assert key in detail, key
    assert detail["state"] == "exploring"  # прежний код состояния цел
    assert detail["state_label"] == ui_labels.describe("session_state", "exploring")["label"]
    stage = detail["stage"]
    assert stage["of"] == ui_labels.STAGE_COUNT == 5
    assert stage["index"] == 3
    assert stage["name"] and "_" not in stage["name"]
    event = detail["events"][0]
    assert event["type"] == "session_state_changed"
    assert event["type_label"] == ui_labels.describe(
        "audit_event_type", "session_state_changed"
    )["label"]

    async with _client(app) as client:
        timeline = (await client.get("/api/v1/timeline?limit=50")).json()["events"]
    assert any(e["type_label"] for e in timeline)
    await engine.dispose()


# ─── Command API ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_refusals_carry_reason_label_hint_and_action(
    migrated_db, tmp_path: Path
) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        # wake_now без подключённого планировщика — прежняя строка причины + подписи
        r = await client.post(
            "/api/v1/commands",
            json={"type": "wake_now", "idempotency_key": "k-wake"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["state"] == "rejected"
        assert body["result"]["reason"] == "orchestrator not attached"
        refusal = ui_labels.describe_refusal("orchestrator not attached")
        assert body["result"]["reason_label"] == refusal["label"]
        assert body["result"]["reason_hint"] == refusal["hint"]
        assert body["result"]["reason_action"] == refusal["action"]
        assert body["type_label"] == ui_labels.describe("command_type", "wake_now")["label"]
        assert body["state_label"] == ui_labels.describe("command_state", "rejected")["label"]

        # resume на незакрытом паузе — отказ с человеческой подписью
        r = await client.post(
            "/api/v1/commands",
            json={"type": "resume", "idempotency_key": "k-resume"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        rejected = r.json()
        assert rejected["result"]["reason"] == "node is not paused"
        assert rejected["result"]["reason_label"] == ui_labels.describe(
            "command_refusal", "node_not_paused"
        )["label"]

        # успешная команда тоже подписана (команда пауза, узел здоров)
        r = await client.post(
            "/api/v1/commands",
            json={"type": "pause", "idempotency_key": "k-pause"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert r.status_code == 202
        paused = r.json()
        assert paused["state"] == "completed"
        assert paused["type_label"] == ui_labels.describe("command_type", "pause")["label"]

        # повтор команды по тому же ключу: подписи есть и на replay-ответе
        r = await client.post(
            "/api/v1/commands",
            json={"type": "pause", "idempotency_key": "k-pause"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        replayed = r.json()
        assert replayed["replayed"] is True
        assert replayed["state_label"] == ui_labels.describe(
            "command_state", replayed["state"]
        )["label"]
    await engine.dispose()


# ─── /api/v1/glossary ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_glossary_is_open_and_covers_every_category(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        r = await client.get("/api/v1/glossary")  # открытая Query-операция, без токена
        assert r.status_code == 200
        body = r.json()

    categories = body["categories"]
    dictionary = ui_labels.glossary()
    assert set(categories) == set(dictionary)
    assert body["category_count"] == len(dictionary)
    assert body["entry_count"] == sum(len(v) for v in dictionary.values())
    assert body["stage_count"] == ui_labels.STAGE_COUNT
    # словарь API и словарь приложения не разъезжаются
    for category, table in categories.items():
        assert set(table) == set(dictionary[category])
        for value, entry in table.items():
            assert set(entry) == {"label", "hint", "action"}
            assert entry["label"] and entry["hint"]
            assert entry == ui_labels.describe(category, value)
    # категории, за полнотой которых следит тест, присутствуют на экране справки
    for category in ui_labels.COMPLETENESS_CATEGORIES:
        assert category in categories, category
    await engine.dispose()
