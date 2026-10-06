"""Scenario (DB): номер, порядок и краткий итог ответа в `GET /api/v1/questions` (T7.67a).

Здесь проверяются требования пользователя «нумерация и сортировка: последние сверху»:

* `number` — сквозной номер по всей таблице (`created_at ASC, id ASC`), №1 = самый
  первый вопрос узла; он НЕ зависит от состояния вопроса, от `limit` и от порядка
  выдачи, новые вопросы только добавляются в конец нумерации (миграции нет);
* `order=recent` — последние вопросы сверху; порядок по умолчанию остаётся
  порядком очереди (сверено с `question_queue` сервиса приёма вопросов), а
  `position`/`queue_place` описывают очередь даже при сортировке по времени;
* `answer` — краткий итог той же логикой, что карточка: только действующее
  утверждение (голова `current` на действующем снимке); pending/invalid никогда
  не показываются как ответ и не получают бейджа;
* стоимость страницы фиксирована: число SQL-запросов не растёт с числом вопросов
  (нет N+1).

Данные сеются явными `created_at`, поэтому ни одна проверка не зависит от часов.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.unit_state import publish_unit_state
from packages.domain.services.question_intake import question_queue

pytestmark = [pytest.mark.scenario]

ADMIN_TOKEN = "view-token"
_EFF = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"

_T0 = datetime(2025, 3, 1, 12, 0, 0, tzinfo=UTC)


def _ts(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make(scratch_url: str, tmp_path: Path) -> tuple[Any, AsyncEngine]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host_lib = tmp_path / "host"
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(tmp_path / "unit.json", units={"noezema-runtime.target": "active"})
    app = create_app(
        engine=engine,
        factory=factory,
        host_adapter=HostStatusAdapter(host_lib_base=host_lib, unit_state_path=tmp_path / "unit.json"),
        admin_token=ADMIN_TOKEN,
    )
    return app, engine


async def _seed_question(
    engine: AsyncEngine,
    *,
    question_id: uuid.UUID,
    created_at: datetime,
    text_value: str,
    state: str = "candidate",
    priority: int = 0,
    origin: str = "message",
) -> None:
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO questions (id, text, origin, state, priority, created_at) "
                "VALUES (:id, :t, :o, :s, :p, :c)"
            ),
            {"id": question_id, "t": text_value, "o": origin, "s": state, "p": priority, "c": created_at},
        )
        await conn.commit()


async def _snapshot_id(engine: AsyncEngine) -> uuid.UUID:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT {_EFF}"))).scalar_one()


async def _seed_session(
    engine: AsyncEngine,
    *,
    session_id: uuid.UUID,
    question_id: uuid.UUID,
    state: str = "succeeded",
    termination_reason: str | None = None,
) -> None:
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


async def _seed_claim(
    engine: AsyncEngine,
    *,
    claim_id: uuid.UUID,
    session_id: uuid.UUID,
    statement: str,
    head_state: str,
    created_at: datetime,
    grade: str | None = "E2",
    epistemic: str | None = "supported",
) -> None:
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status, created_in_session, created_at) "
                "VALUES (:id, :s, 'computed_result', 'fresh', :c, :ca)"
            ),
            {"id": claim_id, "s": statement, "c": session_id, "ca": created_at},
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


async def _get(app: Any, path: str) -> dict[str, Any]:
    async with _client(app) as client:
        r = await client.get(path)
        assert r.status_code == 200
        return r.json()


# ─── номер: вся таблица, постоянен, не зависит от страницы выдачи ─────────


@pytest.mark.asyncio
async def test_number_is_over_whole_table_and_follows_creation_order(migrated_db, tmp_path: Path) -> None:
    """№1 — самый первый вопрос узла; состояние и приоритет на номер не влияют."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)
    ids = [uuid.uuid4() for _ in range(5)]
    # заведомо «перемешанный» ввод: старший приоритет создан позже, часть answered
    seeds = [
        (ids[0], _ts(0), "первые вопросы", "verified"),
        (ids[1], _ts(1), "второй вопрос", "candidate"),
        (ids[2], _ts(2), "третий решённый", "partially_answered"),
        (ids[3], _ts(3), "четвёртый срочный", "candidate"),
        (ids[4], _ts(4), "пятый в работе", "researching"),
    ]
    for question_id, created_at, text_value, state in seeds:
        await _seed_question(
            engine, question_id=question_id, created_at=created_at, text_value=text_value, state=state,
            priority=(9 if question_id == ids[3] else 0),
        )

    body = await _get(app, "/api/v1/questions")
    numbers = {row["id"]: row["number"] for row in body["questions"]}
    assert numbers == {str(question_id): index + 1 for index, (question_id, *_rest) in enumerate(seeds)}
    assert min(numbers.values()) == 1 and sorted(numbers.values()) == [1, 2, 3, 4, 5]

    # состояние меняется — номер остаётся; новый вопрос получает следующий номер
    async with engine.connect() as conn:
        await conn.execute(
            text("UPDATE questions SET state='partially_answered', priority=0 WHERE id=:i"),
            {"i": ids[3]},
        )
        await conn.execute(text("UPDATE questions SET state='candidate' WHERE id=:i"), {"i": ids[0]})
        await conn.commit()
    new_id = uuid.uuid4()
    await _seed_question(engine, question_id=new_id, created_at=_ts(5), text_value="новейший")

    body = await _get(app, "/api/v1/questions?order=recent")
    numbers2 = {row["id"]: row["number"] for row in body["questions"]}
    assert {k: v for k, v in numbers2.items() if k != str(new_id)} == numbers
    assert numbers2[str(new_id)] == 6

    # limit не сдвигает номера: окно считается до LIMIT
    body = await _get(app, "/api/v1/questions?limit=2&order=recent")
    rows = body["questions"]
    assert [row["number"] for row in rows] == [6, 5]
    await engine.dispose()


@pytest.mark.asyncio
async def test_tie_created_at_gets_distinct_deterministic_numbers(migrated_db, tmp_path: Path) -> None:
    """Совпадающие created_at (ловушка §7) ранжируются по id — детерминированно."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)
    ids = [uuid.uuid4() for _ in range(3)]
    for question_id in ids:
        await _seed_question(
            engine, question_id=question_id, created_at=_T0, text_value=f"вопрос {question_id.hex[:6]}"
        )

    expected_order = sorted(str(question_id) for question_id in ids)  # created_at равны → по id ASC
    first = await _get(app, "/api/v1/questions")
    second = await _get(app, "/api/v1/questions?order=recent")
    numbers_first = {row["id"]: row["number"] for row in first["questions"]}
    numbers_second = {row["id"]: row["number"] for row in second["questions"]}
    assert numbers_first == numbers_second
    assert [numbers_first[qid] for qid in expected_order] == [1, 2, 3]
    await engine.dispose()


# ─── порядок: recent сверху; по умолчанию — прежняя очередь ────────────────


@pytest.mark.asyncio
async def test_recent_order_is_newest_first_and_default_order_is_unchanged(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)
    ids = [uuid.uuid4() for _ in range(4)]
    await _seed_question(
        engine, question_id=ids[0], created_at=_ts(0), text_value="старый решённый", state="partially_answered"
    )
    await _seed_question(engine, question_id=ids[1], created_at=_ts(1), text_value="кандидат обычный")
    await _seed_question(engine, question_id=ids[2], created_at=_ts(2), text_value="кандидат срочный", priority=7)
    await _seed_question(engine, question_id=ids[3], created_at=_ts(3), text_value="новейший кандидат")

    default_rows = (await _get(app, "/api/v1/questions"))["questions"]
    # прежний порядок очереди: кандидаты FIFO-селектора (priority DESC, created_at ASC), затем разобранные
    assert [row["id"] for row in default_rows] == [str(ids[2]), str(ids[1]), str(ids[3]), str(ids[0])]
    assert [row["position"] for row in default_rows] == [1, 2, 3, None]

    recent_rows = (await _get(app, "/api/v1/questions?order=recent"))["questions"]
    assert [row["id"] for row in recent_rows] == [str(ids[3]), str(ids[2]), str(ids[1]), str(ids[0])]
    # позиция очереди не зависит от порядка показа: те же значения, что в дефолтной выдаче
    positions = {row["id"]: row["position"] for row in default_rows}
    assert {row["id"]: row["position"] for row in recent_rows} == positions
    assert [row["number"] for row in recent_rows] == [4, 3, 2, 1]

    bad = None
    async with _client(app) as client:
        bad = await client.get("/api/v1/questions?order=sideways")
    assert bad.status_code == 422
    await engine.dispose()


@pytest.mark.asyncio
async def test_default_view_matches_the_intake_queue_service(migrated_db, tmp_path: Path) -> None:
    """Прежние consuming'ы не сдвинулись: дефолтная выдача = строки `question_queue`."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = [uuid.uuid4() for _ in range(3)]
    await _seed_question(
        engine, question_id=ids[0], created_at=_ts(0), text_value="решённый", state="partially_answered"
    )
    await _seed_question(engine, question_id=ids[1], created_at=_ts(1), text_value="кандидат prio 3", priority=3)
    await _seed_question(engine, question_id=ids[2], created_at=_ts(2), text_value="кандидат prio 1")
    # сессия к ids[0]: ссылка session {id,state} обязана совпасть в обоих выдачах
    await _seed_session(engine, session_id=uuid.uuid4(), question_id=ids[0])

    api_rows = (await _get(app, "/api/v1/questions"))["questions"]
    async with factory() as db:
        legacy_rows = await question_queue(db, limit=100)
    assert len(api_rows) == len(legacy_rows)
    shared_keys = ("id", "text", "origin", "state", "priority", "created_at", "position")
    for api_row, legacy_row in zip(api_rows, legacy_rows, strict=True):
        assert {key: api_row[key] for key in shared_keys} == {key: legacy_row[key] for key in shared_keys}
        if legacy_row["session"] is not None:
            assert api_row["session"]["id"] == legacy_row["session"]["id"]
            assert api_row["session"]["state"] == legacy_row["session"]["state"]
    await engine.dispose()


# ─── очередь в колонке ответа: queue_place только у тех, кто ждёт ──────────


@pytest.mark.asyncio
async def test_queue_place_marks_only_questions_waiting_for_processing(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)
    waiting_id = uuid.uuid4()
    worked_candidate_id = uuid.uuid4()  # кандидат, у которого уже есть действующее утверждение
    await _seed_question(engine, question_id=waiting_id, created_at=_ts(0), text_value="ждёт очереди")
    await _seed_question(
        engine, question_id=worked_candidate_id, created_at=_ts(1),
        text_value="уже отвечен, снова в очереди", state="candidate",
    )
    session_id = uuid.uuid4()
    await _seed_session(engine, session_id=session_id, question_id=worked_candidate_id)
    await _seed_claim(
        engine, claim_id=uuid.uuid4(), session_id=session_id,
        statement="6*7 равно 42", head_state="current", created_at=_ts(9),
    )

    rows = (await _get(app, "/api/v1/questions?order=recent"))["questions"]
    by_id = {row["id"]: row for row in rows}
    waiting = by_id[str(waiting_id)]
    worked = by_id[str(worked_candidate_id)]
    assert waiting["answer"]["kind"] == "waiting"
    assert waiting["queue_place"] == waiting["position"] == 1
    # у кандидата с ответом очередь не «обещается»: итог — ответ, а не место в очереди
    assert worked["answer"]["kind"] == "answered" and worked["queue_place"] is None
    await engine.dispose()


# ─── краткий итог ответа: только действующее утверждение ──────────────────


@pytest.mark.asyncio
async def test_summary_shows_only_current_claim_never_pending_or_invalid(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)
    current_id, pending_id, invalid_id, long_id = (uuid.uuid4() for _ in range(4))
    questions = {
        "current": uuid.uuid4(),
        "pending": uuid.uuid4(),
        "invalid": uuid.uuid4(),
        "long": uuid.uuid4(),
    }
    for index, (name, question_id) in enumerate(questions.items()):
        await _seed_question(
            engine, question_id=question_id, created_at=_ts(index), text_value=name, state="partially_answered"
        )
    sessions: dict[str, uuid.UUID] = {}
    for name, question_id in questions.items():
        sessions[name] = uuid.uuid4()
        await _seed_session(engine, session_id=sessions[name], question_id=question_id)

    await _seed_claim(engine, claim_id=current_id, session_id=sessions["current"],
                      statement="первое утверждение", head_state="current", created_at=_ts(20))
    await _seed_claim(engine, claim_id=uuid.uuid4(), session_id=sessions["current"],
                      statement="второе утверждение позже", head_state="current", created_at=_ts(21))
    await _seed_claim(
        engine, claim_id=pending_id, session_id=sessions["pending"], statement="оценка ещё не принята",
        head_state="pending", created_at=_ts(22), grade=None, epistemic=None,
    )
    await _seed_claim(
        engine, claim_id=invalid_id, session_id=sessions["invalid"], statement="вывод недействителен",
        head_state="invalid", created_at=_ts(23), grade=None, epistemic=None,
    )
    long_statement = "д" * 140 + " " + "е" * 220
    await _seed_claim(engine, claim_id=long_id, session_id=sessions["long"],
                      statement=long_statement, head_state="current", created_at=_ts(24))

    rows = (await _get(app, "/api/v1/questions?order=recent"))["questions"]
    by_text = {row["text"]: row for row in rows}

    answered = by_text["current"]["answer"]
    assert answered["kind"] == "answered"
    assert answered["statement"] == "первое утверждение"  # первое по (created_at, id) — как список claims карточки
    assert {"level", "label", "color"} <= set(answered["reliability"] or {})

    for name in ("pending", "invalid"):
        summary = by_text[name]["answer"]
        assert summary["kind"] == "no_answer", f"{name}: вывод с недействующей головой не ответ"
        assert summary["statement"] is None and summary["reliability"] is None

    long_summary = by_text["long"]["answer"]
    short_statement = long_summary["statement"]
    assert len(short_statement) == 160 and short_statement.endswith("…")
    assert long_statement.startswith(short_statement[:-1])

    # бейдж краткого итога — ровно тот же перевод, что у claims[0] карточки ответа
    qid = str(questions["current"])
    card = await _get(app, f"/api/v1/questions/{qid}/answer")
    assert by_text["current"]["answer"]["reliability"] == card["claims"][0]["reliability"]
    assert by_text["current"]["answer"]["label"] == card["result"]["label"]
    # карточка не обрезает утверждение — обрезка принадлежит колонке «Ответ» главной страницы
    long_card = await _get(app, f"/api/v1/questions/{questions['long']}/answer")
    assert long_card["claims"][0]["statement"] == long_statement
    await engine.dispose()


# ─── происхождение вопроса и отсутствие ответа ────────────────────────────


@pytest.mark.asyncio
async def test_operator_and_system_questions_are_distinguishable(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)
    mine_id, system_id = uuid.uuid4(), uuid.uuid4()
    await _seed_question(engine, question_id=mine_id, created_at=_ts(0), text_value="мой вопрос", origin="message")
    await _seed_question(
        engine, question_id=system_id, created_at=_ts(1), text_value="предложен системой", origin="model_proposal"
    )
    rows = (await _get(app, "/api/v1/questions"))["questions"]
    by_id = {row["id"]: row for row in rows}
    assert by_id[str(mine_id)]["created_by_operator"] is True
    assert by_id[str(system_id)]["created_by_operator"] is False
    # человеческая подпись происхождения уже есть (T7.64) — страница не выдумывает её сама
    assert by_id[str(system_id)]["origin_label"]
    await engine.dispose()


# ─── карточка ответа и список называют один и тот же номер ────────────────


@pytest.mark.asyncio
async def test_answer_card_number_matches_the_queue_number(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)
    ids = [uuid.uuid4() for _ in range(3)]
    for index, question_id in enumerate(ids):
        await _seed_question(engine, question_id=question_id, created_at=_ts(index), text_value=f"вопрос {index}")

    rows = (await _get(app, "/api/v1/questions?order=recent"))["questions"]
    numbers = {row["id"]: row["number"] for row in rows}
    assert sorted(numbers.values()) == [1, 2, 3]
    for question_id in ids:
        card = await _get(app, f"/api/v1/questions/{question_id}/answer")
        assert card["question"]["number"] == numbers[str(question_id)]
    await engine.dispose()


# ─── нет N+1: фиксированное число запросов на страницу ─────────────────────


@pytest.mark.asyncio
async def test_page_costs_a_fixed_number_of_queries(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path)

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, many: Any) -> None:
        statements.append(stmt.strip())

    def _select_count() -> int:
        return sum(1 for stmt in statements if stmt.upper().startswith("SELECT"))

    try:
        statements.clear()
        await _get(app, "/api/v1/questions")  # пустая таблица
        empty_selects = _select_count()

        for index in range(5):
            await _seed_question(engine, question_id=uuid.uuid4(), created_at=_ts(index), text_value=f"вопрос {index}")
        statements.clear()
        await _get(app, "/api/v1/questions")
        one_selects = _select_count()

        statements.clear()
        await _get(app, "/api/v1/questions?order=recent&limit=5")
        recent_selects = _select_count()

        # ещё пять вопросов не добавляют ни одного запроса (окна и IN-выборки считаются пакетно)
        for index in range(5, 10):
            await _seed_question(engine, question_id=uuid.uuid4(), created_at=_ts(index), text_value=f"вопрос {index}")
        statements.clear()
        await _get(app, "/api/v1/questions?order=recent&limit=10")
        ten_selects = _select_count()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert empty_selects == 1
    assert one_selects == recent_selects == ten_selects
    assert one_selects <= 4  # вопросы + сессии + действующие утверждения + пороги типов
    await engine.dispose()
