"""Scenario (DB): карточка ответа `GET /api/v1/questions/{id}/answer` (T7.65, ADR-0026).

Эндпоинт только читает уже записанные строки и подписывает их единым словарем.
Здесь проверяются человеческие сценарии, а не алгоритм (он — в
`tests/unit/test_web_answer.py`):

* вопроса нет → 404;
* вопрос в очереди → «Ждёт обработки», ни сессий, ни выводов, ни шагов;
* работа идёт → человеческий этап и признак автообновления;
* ответ получен реальным прогоном (`python.execute`) → бейдж «Проверено» и шаги
  из ленты событий;
* оценка не принята/недействительна → вывод НЕ является ответом и не получает
 нейтральным цветом и без слова «Проверено»;
* неполные данные (нет ленты, нет доказательств, нет головы оценки) → 200 и
  честные пустые блоки, а не падение.

Слово «Проверено» имеет один источник — бейдж надёжности уже вычисленной оценки;
тесты проверяют обе стороны: где принятой оценки нет, этого слова нет
ни в одном тексте ответа.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.web import answer as answer_queries
from apps.web import labels as ui_labels
from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.unit_state import publish_unit_state
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

ADMIN_TOKEN = "answer-token"
_EFF = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"

TOOL_PYTHON = {
    "public_rationale": "Проверить вычисление",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(6*7)"}},
}
COMPLETE = {"public_rationale": "Вопрос отвечен", "decision": {"kind": "complete", "reason": "goal_reached"}}
CURATOR_OK = {
    "summary": "Одно утверждение",
    "claims": [{"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make(scratch_url: str, host_lib: Path, unit_state: Path):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})
    app = create_app(
        engine=engine,
        factory=factory,
        host_adapter=HostStatusAdapter(host_lib_base=host_lib, unit_state_path=unit_state),
        admin_token=ADMIN_TOKEN,
    )
    return app, engine


async def _snapshot_id(engine: AsyncEngine) -> uuid.UUID:
    async with engine.connect() as conn:
        return (
            await conn.execute(text(f"SELECT {_snapshot_select()}"))
        ).scalar_one()


def _snapshot_select() -> str:
    """Действующий снимок правил — из head'а, не из памяти теста."""
    return "active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global'"


async def _seed_question(
    engine: AsyncEngine, *, question_id: uuid.UUID, state: str = "candidate", priority: int = 0
) -> None:
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO questions (id, text, origin, state, priority) "
                "VALUES (:id, :t, 'seeded', :s, :p)"
            ),
            {"id": question_id, "t": f"Вопрос {question_id.hex[:6]}", "s": state, "p": priority},
        )
        await conn.commit()


async def _seed_session(
    engine: AsyncEngine,
    *,
    session_id: uuid.UUID,
    question_id: uuid.UUID | None,
    state: str,
    termination_reason: str | None = None,
) -> uuid.UUID:
    snapshot = await _snapshot_id(engine)
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO sessions (id, state, question_id, config_snapshot_id, "
                "termination_reason, started_at) VALUES (:id, :s, :q, :c, :r, now())"
            ),
            {"id": session_id, "s": state, "q": question_id, "c": snapshot, "r": termination_reason},
        )
        await conn.commit()
    return snapshot


async def _seed_events(engine: AsyncEngine, session_id: uuid.UUID, events: list[tuple[int, str, Any]]) -> None:
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


async def _seed_claim(
    engine: AsyncEngine,
    *,
    claim_id: uuid.UUID,
    session_id: uuid.UUID,
    statement: str,
    head_state: str,
    grade: str | None = "E2",
    epistemic: str | None = "supported",
) -> None:
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status, created_in_session) "
                "VALUES (:id, :s, 'computed_result', 'fresh', :c)"
            ),
            {"id": claim_id, "s": statement, "c": session_id},
        )
        if head_state == "current":
            assessment_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO claim_assessments (id, claim_id, effective_grade, epistemic_status, "
                    "rules_version, rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                    "VALUES (:a, :c, :g, :e, 'rules-v2', 'h', 'ev', '{\"x\": 1}', 0.9, true)"
                ),
                {"a": assessment_id, "c": claim_id, "g": grade, "e": epistemic},
            )
            await conn.execute(
                text(
                    "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
                    "current_assessment_id, epistemic_status, prepared_by) VALUES "
                    f"(:c, {_EFF}, 'current', :a, :e, 'rules_activation')"
                ),
                {"c": claim_id, "a": assessment_id, "e": epistemic},
            )
        elif head_state != "none":
            await conn.execute(
                text(
                    "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
                    "current_assessment_id, epistemic_status, prepared_by) VALUES "
                    f"(:c, {_EFF}, :st, NULL, NULL, 'rules_activation')"
                ),
                {"c": claim_id, "st": head_state},
            )
        await conn.commit()


def _visible_texts(payload: Any) -> list[str]:
    """Все человеческие строки ответа (label/hint/action/verification/honesty/steps)."""
    texts: list[str] = []

    def walk(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            for name, value in node.items():
                walk(value, name)
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, str) and key in {"label", "hint", "action", "text", "verification", "honesty"}:
            texts.append(node)

    walk(payload)
    return texts


def _affirms_verification(text: str) -> bool:
    """Текст утверждает, что вывод проверен (отрицание «не проверено» — не утверждение)."""
    return re.search(r"(?<!не )\bпроверен", text.lower()) is not None


# ─── 404 и пустая очередь ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_question_answers_404(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        r = await client.get(f"/api/v1/questions/{uuid.uuid4()}/answer")
        assert r.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_queued_question_promises_nothing_it_does_not_have(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid = uuid.uuid4()
    await _seed_question(engine, question_id=qid, state="candidate")
    async with _client(app) as client:
        r = await client.get(f"/api/v1/questions/{qid}/answer")
        assert r.status_code == 200
        card = r.json()

    result = card["result"]
    expected = ui_labels.describe("answer_result", "waiting")
    assert result["kind"] == "waiting"
    assert result["label"] == expected["label"] and result["action"] == expected["action"]
    assert result["active"] is False  # страницу обновлять незачем: работа ещё не началась
    assert card["sessions"] == [] and card["claims"] == [] and card["steps"] == []
    assert card["work"]["session_id"] is None
    # честное замечание допустимо и без работы: внешних чтений в данных нет,
    # но про закрытую сеть мы сказать не можем — снимок правил не выбирался
    assert ui_labels.describe("honesty_note", "no_external_sources")["label"] in card["honesty"]
    assert not any("сеть закрыта" in note for note in card["honesty"])
    affirming = [text for text in _visible_texts(card) if _affirms_verification(text)]
    assert not affirming, f"обещание проверки там, где оценки нет: {affirming}"
    await engine.dispose()


# ─── работа идёт ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_running_session_shows_the_human_stage_and_needs_refresh(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid = uuid.uuid4()
    sid = uuid.uuid4()
    await _seed_question(engine, question_id=qid, state="selected")
    await _seed_session(engine, session_id=sid, question_id=qid, state="exploring")
    await _seed_events(
        engine,
        sid,
        [
            (1, "session_started", {}),
            (2, "question_selected", {"question_id": str(qid)}),
            (3, "context_packed", {"total_tokens": 900}),
            (4, "action_started", {"tool": "workspace.read", "action_id": "a1"}),
        ],
    )
    async with _client(app) as client:
        r = await client.get(f"/api/v1/questions/{qid}/answer")
        assert r.status_code == 200
        card = r.json()

    result = card["result"]
    assert result["kind"] == "in_progress"
    assert result["label"] == ui_labels.describe("answer_result", "in_progress")["label"]
    assert result["active"] is True  # по этому флагу страница обновляется, код состояния не нужен
    session = card["sessions"][0]
    stage = ui_labels.session_stage("exploring")
    assert session["stage"] == stage
    assert stage["of"] == ui_labels.STAGE_COUNT and stage["index"] == 3
    assert session["state_label"] == ui_labels.describe("session_state", "exploring")["label"]
    # необработанное действие не становится шагом рассказа
    steps = [step["text"] for step in card["steps"]]
    assert ui_labels.describe("action_tool", "workspace.read")["label"] not in " ".join(steps)
    assert steps == [
        ui_labels.describe("answer_step", "question_selected")["label"],
        ui_labels.describe("answer_step", "context_prepared")["label"],
    ]
    await engine.dispose()


# ─── ответ получен реальным прогоном ───────────────────────────────────────


@pytest.mark.asyncio
async def test_answer_from_a_real_session_is_verified_and_tells_the_steps(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Сквозной путь (как `test_dev_stand_flow`): вопрос → wake_now → готовый ответ."""
    scratch_url, _ = migrated_db
    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "workspace"),
    )
    host_lib = tmp_path / "host"
    unit_state = host_lib / "unit.json"
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})
    app = create_app(
        engine=engine,
        factory=factory,
        orchestrator=orchestrator,
        host_adapter=HostStatusAdapter(host_lib_base=host_lib, unit_state_path=unit_state),
        admin_token=ADMIN_TOKEN,
    )
    try:
        async with _client(app) as client:
            r = await client.post(
                "/api/v1/questions",
                json={"text": "Сколько будет 6*7?", "priority": 9},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r.status_code == 201
            qid = r.json()["id"]

            r = await client.post(
                "/api/v1/commands",
                json={"type": "wake_now", "idempotency_key": "answer-wake-1"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r.status_code == 202
            # синхронизация по событиям узла, не по wall-clock (AGENTS §7)
            for _ in range(200):
                status = (await client.get("/api/v1/status")).json()
                if status["node_state"] == "idle" and status["session"] is None:
                    break
                await asyncio.sleep(0.05)

            card = (await client.get(f"/api/v1/questions/{qid}/answer")).json()
            page = await client.get(f"/answer/{qid}")
            assert page.status_code == 200
    finally:
        await gateway.close()
        await engine.dispose()

    result = card["result"]
    assert result["kind"] == "answered" and result["label"] == "Ответ есть"
    assert result["active"] is False

    claims = card["claims"]
    assert len(claims) == 1
    claim = claims[0]
    assert claim["statement"] == "6*7 равно 42"
    reliability = claim["reliability"]
    # бейдж — перевод уже вычисленной оценки rules engine
    assert reliability["level"] == "verified" and reliability["label"] == "Проверено"
    assert reliability["color"] in {"green", "yellow", "gray", "orange", "red"}  # токен, не hex
    assert claim["active"] is True
    assert claim["verification"], "у проверенного вывода должна быть строка о подтверждении"
    assert any("вычислен" in line for line in claim["verification"])
    # заголовок этой строки тоже зависит от оценки, а не от фантазии страницы
    assert claim["verification_lead"] == ui_labels.describe("verification_lead", "verified")["label"]

    steps = [step["text"] for step in card["steps"]]
    assert 3 <= len(steps) <= answer_queries.MAX_STEPS
    assert steps[0] == ui_labels.describe("answer_step", "question_selected")["label"]
    assert any(text.startswith(ui_labels.describe("action_tool", "python.execute")["label"]) for text in steps)
    assert steps[-1] == ui_labels.describe("answer_step", "recorded")["label"]
    for index, step in enumerate(card["steps"], start=1):
        assert step["n"] == index

    honesty = card["honesty"]
    assert ui_labels.describe("honesty_note", "computation_only")["label"] in honesty
    # исполнитель (подставка или песочник) в этих строках не различается (T7.64 §7 п.9)
    assert not any("изолирован" in note for note in honesty)

    affirming = [text for text in _visible_texts(card) if _affirms_verification(text)]
    # единственное утверждение о проверке во всей карточке — это бейдж оценки
    assert affirming == ["Проверено"]


# ─── недействующие выводы не являются ответом ─────────────────────────────


@pytest.mark.asyncio
async def test_pending_and_invalid_conclusions_are_not_the_answer(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid = uuid.uuid4()
    sid = uuid.uuid4()
    await _seed_question(engine, question_id=qid, state="candidate")
    await _seed_session(
        engine, session_id=sid, question_id=qid, state="succeeded", termination_reason="goal_reached"
    )
    await _seed_claim(
        engine, claim_id=uuid.uuid4(), session_id=sid, statement="Оценка ещё не принята", head_state="pending"
    )
    await _seed_claim(
        engine, claim_id=uuid.uuid4(), session_id=sid, statement="Оценка отозвана", head_state="invalid"
    )
    async with _client(app) as client:
        card = (await client.get(f"/api/v1/questions/{qid}/answer")).json()

    assert card["claims"] == []
    others = card["other_claims"]
    assert len(others) == 2 and all(item["active"] is False for item in others)
    states = {item["head_state"] for item in others}
    assert states == {"pending", "invalid"}
    for item in others:
        expected = ui_labels.describe("claim_head_state", item["head_state"])["label"]
        assert item["head_label"] == expected
        assert item["reliability"]["label"] != "Проверено"
        assert item["reliability"]["level"] != "verified"
        # цвет бейджа не должен выглядеть как «действует»: gray/orange/red, но не green
        assert item["reliability"]["color"] != "green"
        # и заголовок строки подтверждения не обещает проверку, которой нет
        assert item["verification_lead"] == ui_labels.describe("verification_lead", "unconfirmed")["label"]

    result = card["result"]
    assert result["kind"] == "no_answer"
    assert result["label"] == ui_labels.describe("answer_result", "no_answer")["label"]
    assert result["action"]  # человеку предлагается, что делать дальше
    affirming = [text for text in _visible_texts(card) if _affirms_verification(text)]
    assert not affirming, f"обещание проверки там, где оценки нет: {affirming}"
    await engine.dispose()


@pytest.mark.asyncio
async def test_current_assessment_is_the_only_source_of_a_verified_word(
    migrated_db, tmp_path: Path
) -> None:
    """Краснота предыдущей проверки: та же карточка с принятой оценкой обязана сказать «Проверено»."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid = uuid.uuid4()
    sid = uuid.uuid4()
    await _seed_question(engine, question_id=qid, state="verified")
    await _seed_session(engine, session_id=sid, question_id=qid, state="succeeded", termination_reason="goal_reached")
    await _seed_claim(
        engine, claim_id=uuid.uuid4(), session_id=sid, statement="Вывод с принятой оценкой", head_state="current"
    )
    async with _client(app) as client:
        card = (await client.get(f"/api/v1/questions/{qid}/answer")).json()

    claim = card["claims"][0]
    assert claim["reliability"]["label"] == "Проверено"
    assert claim["reliability"]["level"] == "verified"
    assert claim["verification_lead"] == ui_labels.describe("verification_lead", "verified")["label"]
    await engine.dispose()


# ─── неполные данные ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_records_still_produce_an_honest_card(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid = uuid.uuid4()
    sid = uuid.uuid4()
    await _seed_question(engine, question_id=qid, state="candidate")
    await _seed_session(
        engine, session_id=sid, question_id=qid, state="failed", termination_reason="attempt_aborted"
    )
    # лента есть, но почти пустая: ни выбора вопроса, ни исходов действий
    await _seed_events(engine, sid, [(1, "session_started", None)])
    await _seed_claim(
        engine, claim_id=uuid.uuid4(), session_id=sid, statement="Утверждение без оценки", head_state="none"
    )
    async with _client(app) as client:
        r = await client.get(f"/api/v1/questions/{qid}/answer")
        assert r.status_code == 200
        card = r.json()

    assert card["steps"] == []  # ничего не выдумывается: событий-оснований нет
    assert card["claims"] == []
    assert len(card["other_claims"]) == 1
    claim = card["other_claims"][0]
    assert claim["head_state"] == "none" and claim["grade_label"] is None
    assert claim["verification"]  # честная строка вместо пустоты
    result = card["result"]
    assert result["kind"] == "failed"
    assert result["label"] == ui_labels.describe("answer_result", "failed")["label"]
    # последняя сессия названа подписями словаря, а не кодами
    assert "Последняя сессия:" in result["hint"]
    assert ui_labels.describe("session_state", "failed")["label"] in result["hint"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_unfinished_and_failed_actions_are_reported_but_never_called_steps(
    migrated_db, tmp_path: Path
) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid = uuid.uuid4()
    sid = uuid.uuid4()
    await _seed_question(engine, question_id=qid, state="candidate")
    await _seed_session(
        engine, session_id=sid, question_id=qid, state="succeeded", termination_reason="goal_reached"
    )
    await _seed_events(
        engine,
        sid,
        [
            (1, "question_selected", {}),
            (2, "action_started", {"tool": "python.execute", "action_id": "a1"}),
            (3, "action_completed", {"action_id": "a1", "ok": False}),
            (4, "action_started", {"tool": "memory.search", "action_id": "a2"}),
            (5, "action_failed", {"action_id": "a3"}),
        ],
    )
    async with _client(app) as client:
        card = (await client.get(f"/api/v1/questions/{qid}/answer")).json()

    steps = [step["text"] for step in card["steps"]]
    tool_labels = [
        ui_labels.describe("action_tool", tool)["label"] for tool in ("python.execute", "memory.search")
    ]
    assert not any(label in " ".join(steps) for label in tool_labels)
    failed_note = ui_labels.describe("honesty_note", "failed_steps")["label"]
    assert any(note.startswith(failed_note) for note in card["honesty"])
    await engine.dispose()
