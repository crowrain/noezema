"""Scenario (DB): оператор спорит утверждение стенда (T7.81, §11.3, ADR-0031).

Репродюсер стендового случая `.92`: утверждение выглядит как «Проверено» (E3),
потому что две «независимые» группы независимости на деле — первоисточник и его
пересказ. Ни атрибуция, ни переоценка этого не исправляют: оператору нужен вход.

Что закреплено тестом:
- спор = коррекция графа источников `merge` с актором входа оператора,
  основанием (артефакт прочитанной страницы пересказа) и причиной в журнале;
- дальше работает существующий каскад §11.3: головы действующих оценок уходят в
  pending с NULL-парой и заводятся долговременные задачи пересчёта;
- оценку меняет ТОЛЬКО рабочий переоценки + rules engine (insufficient_independence
  → hypothesis), бейдж надёжности исчезает вместе с оценкой, а не по желанию оператора;
- вопрос на перепроверку цитирует statement якоря (ловушка T7.73);
- повтор опознаётся по естественному ключу коррекции и ничего не дублирует;
- отказы честные, подписанные словарём, и whole-way: при отказе не записано ничего;
- отмена обратима (`valid = false` + второй каскад), история знания не удаляется.

Сценарный casus — `external_fact`: у него в действующем снимке правил
`min_independence_groups = 2`, как у стендового `temporal_fact`. Механизм от типа
утверждения не зависит; типы различает rules engine, не инструмент оператора.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.web import labels as ui_labels
from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.unit_state import publish_unit_state
from packages.domain.db.uow import transaction
from packages.domain.services.audit import AuditService
from packages.memory import dispute
from packages.memory.rules_engine import RULES_ENGINE_VERSION
from tests.scenario.test_source_graph import (
    _all,
    _head,
    _host_source,
    _run_worker,
    _scalar,
    _seed_claim_pending,
    _seed_job,
    _source_members,
    _worker_reasons,
)

pytestmark = [pytest.mark.scenario]

ADMIN_TOKEN = "secret-dispute-token"
PRIMARY_URI = "https://rosstat.example/press/inflation-sep-2026"
RETOLD_URI = "https://sbercib.example/economy/pochemu-inflyatsiya-5-6"
STATEMENT = "Инфляция в России в сентябре 2026 года составила около 5,6 процента"
REASON = "Страница банка дословно повторяет абзацы пресс-выпуска и его таблицу"


# ─── harness ──────────────────────────────────────────────────────────────


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make_app(scratch_url: str, host_lib: Path, unit_state: Path):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})
    adapter = HostStatusAdapter(host_lib_base=host_lib, unit_state_path=unit_state)
    app = create_app(
        engine=engine, factory=factory, host_adapter=adapter, admin_token=ADMIN_TOKEN
    )
    return app, engine


def _headers(token: str | None = ADMIN_TOKEN) -> dict[str, str]:
    if token is None:
        return {}
    return {"X-Admin-Token": token}


async def _artifact(engine: AsyncEngine, *, tag: str) -> uuid.UUID:
    """Артефакт прочитанной страницы — проверяемое основание коррекции (§11.3)."""
    aid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO artifacts (id, sha256, size, mime, origin, trust_class) "
        "VALUES (:id, :sha, 4096, 'text/html', 'retrieval', 'external')",
        {"id": aid, "sha": uuid.uuid4().hex + tag},
    )
    return aid


async def _source_evidence(
    engine: AsyncEngine, claim_id: uuid.UUID, source_id: uuid.UUID, *, tag: str
) -> uuid.UUID:
    """Улика `source_assertion` с артефактом прочитанной страницы."""
    artifact_id = await _artifact(engine, tag=tag)
    await _scalar(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        " scope, source_id, chunk_id, observation_artifact_id) "
        "VALUES (:id, :c, 'supports', 'source_assertion', :h, '{\"x\": 1}', :s, :k, :a)",
        {
            "id": uuid.uuid4(),
            "c": claim_id,
            "h": f"disp-{tag}-{uuid.uuid4().hex[:8]}",
            "s": source_id,
            "k": f"c-{tag}",
            "a": artifact_id,
        },
    )
    return artifact_id


async def _seed_contested_claim(
    engine: AsyncEngine,
    *,
    statement: str = STATEMENT,
    claim_type: str = "external_fact",
) -> dict[str, Any]:
    """Утверждение, которое правила уже считают подтверждённым по двум группам.

    Источники — заведомо разные registrable domain (ловушка T7.75): `.example`
    PSL не знает, поэтому домен = весь хост, и группы не сливаются сами.
    """
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, statement, claim_type)
    primary = await _host_source(engine, uri=PRIMARY_URI, content_hash="hash-primary")
    retold = await _host_source(engine, uri=RETOLD_URI, content_hash="hash-retold")
    primary_artifact = await _source_evidence(engine, claim_id, primary, tag="prim")
    retold_artifact = await _source_evidence(engine, claim_id, retold, tag="retl")
    await _seed_job(engine, claim_id, reason="test")

    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head is not None and head[0] == "current", head
    assert head[1] == "supported" and head[3] == "E3", (
        "precondition failed: две независимые группы должны дать E3/supported"
    )
    members = await _source_members(engine, claim_id)
    assert len({m[0] for m in members}) == 2, members
    return {
        "claim_id": claim_id,
        "primary": primary,
        "retold": retold,
        "primary_artifact": primary_artifact,
        "retold_artifact": retold_artifact,
    }


async def _dispute(
    client: httpx.AsyncClient,
    claim_id: uuid.UUID,
    *,
    primary_uri: str = PRIMARY_URI,
    retelling_uri: str = RETOLD_URI,
    reason: str = REASON,
    headers: dict[str, str] | None = None,
):
    return await client.post(
        f"/api/v1/knowledge/claims/{claim_id}/dispute",
        json={
            "primary_uri": primary_uri,
            "retelling_uri": retelling_uri,
            "reason": reason,
        },
        headers=headers if headers is not None else _headers(),
    )


async def _corrections(engine: AsyncEngine, claim_id: uuid.UUID) -> list[Any]:
    return await _all(
        engine,
        "SELECT c.actor, c.kind, c.valid, c.rules_version, c.basis_artifact_id, "
        "       c.reason_audit_event_id, c.from_source_id, c.to_source_id "
        "FROM source_graph_corrections c "
        "WHERE c.from_source_id IN (SELECT source_id FROM evidence WHERE claim_id = :c) "
        "   OR c.to_source_id IN (SELECT source_id FROM evidence WHERE claim_id = :c) "
        "ORDER BY c.created_at, c.id",
        {"c": claim_id},
    )


# ─── спор: что делает и чего не делает ────────────────────────────────────


@pytest.mark.asyncio
async def test_operator_dispute_requeues_the_current_assessment(
    migrated_db: Any, tmp_path: Path
) -> None:
    """Спор оператора = коррекция графа + каскад §11.3 + вопрос; оценку он не назначает."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)
    claim_id = seeded["claim_id"]

    async with _client(app) as client:
        r = await _dispute(client, claim_id)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["replayed"] is False
        assert body["assessments_requeued"] == 1
        assert body["claims_touched"] == 1
        assert body["recheck_jobs_created"] == 1
        assert body["reason"] == REASON
        assert body["actor"] == "operator:web"

    # коррекция графа: актор входа, вид merge, действующая версия правил,
    # основание — артефакт страницы-пересказа, причина — событие журнала
    rows = await _corrections(engine, claim_id)
    assert len(rows) == 1
    actor, kind, valid, rules_version, basis, reason_event, from_id, to_id = rows[0]
    assert actor == "operator:web" and kind == "merge" and valid is True
    assert rules_version == RULES_ENGINE_VERSION
    assert basis == seeded["retold_artifact"]
    assert reason_event is not None
    assert {from_id, to_id} == {seeded["primary"], seeded["retold"]}

    # журнал: принята команда с причиной оператора и завершена с итогом каскада
    audit = await _all(
        engine,
        "SELECT type, actor, payload->>'operator_reason', payload->>'action' "
        "FROM audit_events WHERE type IN ('operator_command_received','operator_command_completed') "
        "ORDER BY sequence",
    )
    assert [a[0] for a in audit] == ["operator_command_received", "operator_command_completed"]
    assert all(a[1] == "operator:web" for a in audit)
    assert audit[0][2] == REASON and audit[0][3] == "claim_dispute"
    reason_row = await _scalar(
        engine,
        "SELECT payload->>'operator_reason' FROM audit_events WHERE id = :id",
        {"id": reason_event},
    )
    assert reason_row[0] == REASON

    # голова действующей оценки снята в pending с NULL-парой (инвариант §3 lifecycle)
    head = await _head(engine, claim_id)
    assert head[0] == "pending" and head[2] is None

    # пересчёт заводит долговременную задачу — её поднимает существующий рабочий
    job = await _scalar(
        engine,
        "SELECT reason, status FROM reassessment_jobs WHERE claim_id = :c "
        "ORDER BY enqueued_at DESC LIMIT 1",
        {"c": claim_id},
    )
    assert job[0] == "source_graph_change" and job[1] == "queued"

    # вопрос на перепроверку цитирует утверждение (иначе FTS не достанет якорь, T7.73)
    question = await _scalar(
        engine,
        "SELECT text, origin, state FROM questions WHERE text LIKE 'Перепроверить утверждение%' "
        "ORDER BY created_at DESC LIMIT 1",
    )
    assert STATEMENT in question[0]
    assert question[1] == "message" and question[2] == "candidate"

    # действующая оценка НЕ спрятана: витрина по-прежнему показывает голову
    async with _client(app) as client:
        detail = await client.get(f"/api/v1/knowledge/claims/{claim_id}")
        assert detail.status_code == 200
        heads = detail.json()["heads"]
        assert any(h["assessment_state"] == "pending" for h in heads)
    await app_engine.dispose()


@pytest.mark.asyncio
async def test_the_badge_moves_only_with_the_rules_recomputation(
    migrated_db: Any, tmp_path: Path
) -> None:
    """Бейдж «Проверено» исчезает потому, что независимость стала одной группой.

    Сам спор бейдж не выключает: витрина показывает уже вычисленную оценку головы,
    а между спором и пересчётом голова `pending` — действующего ответа нет.
    """
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)
    claim_id = seeded["claim_id"]

    async with _client(app) as client:
        row = await _claims_row(client, claim_id)
        assert row["head_state"] == "current" and row["effective_grade"] == "E3", row
        assert row["reliability"]["level"] == ui_reliability_verified(), row

        assert (await _dispute(client, claim_id)).status_code == 201
        between = await _claims_row(client, claim_id)
        assert between["head_state"] == "pending", between
        assert between["reliability"]["level"] != ui_reliability_verified(), between

    await _run_worker(engine)  # ровно тот рабочий, который на стенде запускает maint-юнит

    async with _client(app) as client:
        after = await _claims_row(client, claim_id)
        assert after["head_state"] == "current", after
        assert after["epistemic_status"] == "hypothesis", after
        assert after["reliability"]["level"] != ui_reliability_verified(), after

    reasons = await _worker_reasons(engine, claim_id)
    assert "insufficient_independence" in reasons, reasons

    members = await _source_members(engine, claim_id)
    groups = {m[0] for m in members}
    assert len(groups) == 1, members
    assert all(m[1] == "correction:merge" for m in members), members
    await app_engine.dispose()


async def _claims_row(client: httpx.AsyncClient, claim_id: uuid.UUID) -> dict[str, Any]:
    """Строка витрины утверждений: именно она несёт бейдж надёжности (T7.76)."""
    body = (await client.get("/api/v1/knowledge/claims")).json()
    return next(c for c in body["claims"] if c["id"] == str(claim_id))


def ui_reliability_verified() -> str:
    """Имя уровня надёжности берёт presentation-модуль, а не тест."""
    from apps.web.reliability import LEVEL_VERIFIED

    return LEVEL_VERIFIED


@pytest.mark.asyncio
async def test_repeat_dispute_by_the_same_operator_is_a_replay(
    migrated_db: Any, tmp_path: Path
) -> None:
    """Идемпотентность — естественный ключ коррекции; клиентский ключ не нужен."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)
    claim_id = seeded["claim_id"]

    async with _client(app) as client:
        first = await _dispute(client, claim_id)
        assert first.status_code == 201
        second = await _dispute(client, claim_id)
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["replayed"] is True
        assert body["correction_id"] == first.json()["correction_id"]
        assert body["assessments_requeued"] == 0

    rows = await _corrections(engine, claim_id)
    assert len(rows) == 1, rows
    questions = await _all(
        engine,
        "SELECT count(*) FROM questions WHERE origin = 'message' AND text LIKE 'Перепроверить%'",
    )
    assert questions[0][0] == 1, questions
    jobs = await _all(
        engine,
        "SELECT count(*) FROM reassessment_jobs WHERE claim_id = :c AND reason = 'source_graph_change'",
        {"c": claim_id},
    )
    assert jobs[0][0] == 1, jobs
    await app_engine.dispose()


@pytest.mark.asyncio
async def test_every_claim_that_used_those_sources_is_requeued(
    migrated_db: Any, tmp_path: Path
) -> None:
    """Каскад существующего механизма: пересчёт получают все утверждения, которые
    опирались на эти страницы. Знание не удаляется — головы становятся pending."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)
    first_claim = seeded["claim_id"]

    # второе утверждение опирается на те же две страницы (пересказ использован ещё раз)
    second_claim = uuid.uuid4()
    await _seed_claim_pending(
        engine, second_claim, "Ставку налога объяснили той же инфляцией 5,6", "external_fact"
    )
    await _source_evidence(engine, second_claim, seeded["retold"], tag="second-retold")
    await _source_evidence(engine, second_claim, seeded["primary"], tag="second-primary")
    await _seed_job(engine, second_claim, reason="test")
    await _run_worker(engine)
    assert (await _head(engine, second_claim))[0] == "current"

    # и третье — только связано ребром зависимости, без этих источников: его каскад
    # §11.3 не трогает (это честная граница выбранного механизма, см. STATUS.md)
    dependent = uuid.uuid4()
    await _seed_claim_pending(engine, dependent, "Реальные доходы выросли", "external_fact")
    other = await _host_source(engine, uri="https://rosstat.example/income-sep-2026")
    await _source_evidence(engine, dependent, other, tag="income")
    await _seed_job(engine, dependent, reason="test")
    await _run_worker(engine)
    await _scalar(
        engine,
        "INSERT INTO claim_dependencies (id, from_claim_id, to_claim_id, kind) "
        "VALUES (:id, :f, :t, 'evidential')",
        {"id": uuid.uuid4(), "f": dependent, "t": first_claim},
    )

    async with _client(app) as client:
        r = await _dispute(client, first_claim)
        assert r.status_code == 201
        body = r.json()
        assert body["claims_touched"] == 2, body
        assert body["assessments_requeued"] == 2, body

    for claim_id in (first_claim, second_claim):
        head = await _head(engine, claim_id)
        assert head[0] == "pending" and head[2] is None
    untouched = await _head(engine, dependent)
    assert untouched[0] == "current", untouched
    await app_engine.dispose()


# ─── отказы ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refusals_are_labeled_and_write_nothing(migrated_db: Any, tmp_path: Path) -> None:
    """Отказ whole-way: ни коррекции, ни журнала, ни вопроса — и причина подписана."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)
    claim_id = seeded["claim_id"]

    cases = [
        # утверждения нет
        (uuid.uuid4(), PRIMARY_URI, RETOLD_URI, 404, "dispute_claim_not_found"),
        # адрес не среди источников этого утверждения
        (claim_id, PRIMARY_URI, "https://rian.example/retelling", 409, "dispute_source_not_found"),
        (claim_id, "https://unknown.example/page", RETOLD_URI, 409, "dispute_source_not_found"),
        # один и тот же источник нельзя объявить пересказом себя (схема не важна)
        (
            claim_id,
            RETOLD_URI,
            "http://www.sbercib.example/economy/pochemu-inflyatsiya-5-6/",
            409,
            "dispute_same_source",
        ),
    ]

    async with _client(app) as client:
        for target, primary_uri, retelling_uri, status, code in cases:
            r = await _dispute(client, target, primary_uri=primary_uri, retelling_uri=retelling_uri)
            assert r.status_code == status, (status, r.text)
            body = r.json()
            assert body["rejected"] is True
            assert body["reason"] == code
            entry = ui_labels.describe_refusal(code)
            assert body["reason_label"] == entry["label"]
            assert body["reason_hint"] == entry["hint"]
            assert body["reason_action"] == entry["action"]
        # причина обязательна: слишком короткая — честный 422 валидации, не молчание
        short = await _dispute(client, claim_id, reason="см")
        assert short.status_code == 422

    assert await _corrections(engine, claim_id) == []
    audit = await _all(
        engine,
        "SELECT count(*) FROM audit_events WHERE type IN "
        "('operator_command_received','operator_command_completed','source_graph_changed')",
    )
    assert audit[0][0] == 0, audit
    questions = await _all(engine, "SELECT count(*) FROM questions")
    assert questions[0][0] == 0, questions
    head = await _head(engine, claim_id)
    assert head[0] == "current" and head[3] == "E3", head
    await app_engine.dispose()


@pytest.mark.asyncio
async def test_a_second_operator_cannot_stack_a_second_merge_on_the_same_pair(
    migrated_db: Any, tmp_path: Path
) -> None:
    """Спор другого входа (CLI на стенде) про уже склеенную пару — отказ whole-way.

    Тот же актор и та же пара получают replay существующей коррекции (см. тест
    идемпотентности). Другой актор не может нарастить вторую склейку поверх первой:
    группы уже одна, сначала нужно снять прежний спор. Отказ ничего не пишет.
    """
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)
    claim_id = seeded["claim_id"]

    async with _client(app) as client:
        assert (await _dispute(client, claim_id)).status_code == 201
    await _run_worker(engine)
    assert len({m[0] for m in await _source_members(engine, claim_id)}) == 1
    audit_before = await _all(
        engine, "SELECT count(*) FROM audit_events WHERE actor = :a", {"a": dispute.ACTOR_HOSTCTL}
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    with pytest.raises(dispute.DisputeError) as excinfo:
        async with factory() as db, transaction(db):
            await dispute.dispute_claim(
                db,
                AuditService(db),
                claim_id=claim_id,
                primary_uri=PRIMARY_URI,
                retelling_uri=RETOLD_URI,
                reason="тот же пересказ, видит другой оператор",
                actor=dispute.ACTOR_HOSTCTL,
            )
    assert excinfo.value.code == dispute.REFUSAL_ALREADY_GROUPED
    entry = ui_labels.describe_refusal(dispute.REFUSAL_ALREADY_GROUPED)
    assert entry["hint"]  # отказ обязан быть объясним человеку

    rows = await _corrections(engine, claim_id)
    assert len(rows) == 1 and rows[0][2] is True, rows
    audit_after = await _all(
        engine, "SELECT count(*) FROM audit_events WHERE actor = :a", {"a": dispute.ACTOR_HOSTCTL}
    )
    assert audit_after[0][0] == audit_before[0][0], (audit_before, audit_after)
    head = await _head(engine, claim_id)
    assert head[0] == "current" and head[1] == "hypothesis", head
    await app_engine.dispose()


# ─── отмена спора ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_undoes_the_dispute_and_keeps_the_history(
    migrated_db: Any, tmp_path: Path
) -> None:
    """Отмена обратима: `valid = false`, второй каскад, пересчёт возвращает E3.

    История не стирается: коррекция остаётся строкой, прежние оценки — строками
    утверждения (invalid-оценки видны в происхождении), вопрос остаётся в очереди.
    """
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)
    claim_id = seeded["claim_id"]
    first_assessments = await _all(
        engine, "SELECT id, effective_grade FROM claim_assessments WHERE claim_id = :c", {"c": claim_id}
    )

    async with _client(app) as client:
        assert (await _dispute(client, claim_id)).status_code == 201
    await _run_worker(engine)
    after_dispute = await _head(engine, claim_id)
    assert after_dispute[1] == "hypothesis", after_dispute

    async with _client(app) as client:
        r = await client.post(
            f"/api/v1/knowledge/claims/{claim_id}/dispute/cancel",
            json={"reason": "первоисточник всё-таки отдельный: разные таблицы и даты"},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cancelled"] is True and body["actor"] == "operator:web"
        assert body["claims_touched"] == 1 and body["assessments_requeued"] == 1

    rows = await _corrections(engine, claim_id)
    assert len(rows) == 1 and rows[0][2] is False, rows  # строка цела, стала недействующей

    head = await _head(engine, claim_id)
    assert head[0] == "pending" and head[2] is None

    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[0] == "current" and head[1] == "supported" and head[3] == "E3", head
    members = await _source_members(engine, claim_id)
    assert len({m[0] for m in members}) == 2, members

    # прежнее знание не удалено: все оценки, что были, остались в таблице
    now_assessments = await _all(
        engine, "SELECT id FROM claim_assessments WHERE claim_id = :c", {"c": claim_id}
    )
    assert {a[0] for a in first_assessments} <= {a[0] for a in now_assessments}

    audit = await _all(
        engine,
        "SELECT payload->>'action' FROM audit_events "
        "WHERE type = 'operator_command_completed' ORDER BY sequence",
    )
    assert [a[0] for a in audit] == ["claim_dispute", "claim_dispute_cancel"], audit

    async with _client(app) as client:
        provenance = (await client.get(f"/api/v1/knowledge/claims/{claim_id}/provenance")).json()
        corrections = provenance["operator_corrections"]
        assert len(corrections) == 1, corrections
        row = corrections[0]
        assert row["state_label"] == ui_labels.describe("dispute_state", "withdrawn")["label"]
        assert row["kind_hint"] == ui_labels.describe("graph_correction_kind", "merge")["hint"]
        assert row["actor_label"] == ui_labels.describe("dispute_actor", "operator:web")["label"]
        # прежняя причина спора остаётся видимой: решение оператора не стирается
        assert row["reason"] == REASON, row
        assert row["withdraw_reason"] and "разные таблицы и даты" in row["withdraw_reason"], row
        assert (
            row["withdrawn_by_label"]
            == ui_labels.describe("dispute_actor", "operator:web")["label"]
        ), row
        assert row["withdrawn_at"], row
    await app_engine.dispose()


@pytest.mark.asyncio
async def test_cancel_of_a_claim_without_a_dispute_is_refused(migrated_db: Any, tmp_path: Path) -> None:
    """Снимать нечего — честный отказ с подписью, никаких записей."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)

    async with _client(app) as client:
        r = await client.post(
            f"/api/v1/knowledge/claims/{seeded['claim_id']}/dispute/cancel",
            json={},
            headers=_headers(),
        )
        assert r.status_code == 404, r.text
        body = r.json()
        assert body["reason"] == "dispute_correction_not_found"
        assert body["reason_hint"] == ui_labels.describe_refusal("dispute_correction_not_found")["hint"]

    assert await _corrections(engine, seeded["claim_id"]) == []
    assert (await _head(engine, seeded["claim_id"]))[0] == "current"
    audit = await _all(
        engine,
        "SELECT count(*) FROM audit_events WHERE type IN "
        "('operator_command_received','operator_command_completed')",
    )
    assert audit[0][0] == 0, audit
    await app_engine.dispose()


# ─── граница Command API и честные подписи ────────────────────────────────


@pytest.mark.asyncio
async def test_dispute_requires_admin_token_and_a_healthy_host(migrated_db: Any, tmp_path: Path) -> None:
    """Спор — Command API: без токена и при нездоровом узле отказа не миновать."""
    scratch_url, engine = migrated_db
    host_lib = tmp_path / "host"
    unit_state = tmp_path / "unit.json"
    app, app_engine = await _make_app(scratch_url, host_lib, unit_state)
    seeded = await _seed_contested_claim(engine)

    async with _client(app) as client:
        no_token = await _dispute(client, seeded["claim_id"], headers={})
        assert no_token.status_code == 401, no_token.text

    # незакрытый переход в журнале хоста → Command API fail-closed (423), GET живёт
    from hostctl.journal import STATE_CHECKING, JournalStore, TransitionRecord

    JournalStore(host_lib).write_record(
        TransitionRecord(
            attempt_id="t-dispute",
            operation="offline_rules",
            candidate_snapshot_id=None,
            base_snapshot_id=None,
            observed_pointer_tuple={},
            state=STATE_CHECKING,
        )
    )
    async with _client(app) as client:
        degraded = await _dispute(client, seeded["claim_id"])
        assert degraded.status_code == 423, degraded.text
        body = degraded.json()
        assert body["reason"] == "host_not_healthy"
        assert body["reason_hint"] == ui_labels.describe_refusal("host_not_healthy")["hint"]
        provenance = await client.get(f"/api/v1/knowledge/claims/{seeded['claim_id']}/provenance")
        assert provenance.status_code == 200  # Query API продолжает отдавать данные

    assert await _corrections(engine, seeded["claim_id"]) == []
    await app_engine.dispose()


@pytest.mark.asyncio
async def test_provenance_shows_the_dispute_as_an_operator_statement(migrated_db: Any, tmp_path: Path) -> None:
    """Человек видит: спор «оспорено оператором», кем, когда и с какой причиной."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    seeded = await _seed_contested_claim(engine)

    async with _client(app) as client:
        empty = (await client.get(f"/api/v1/knowledge/claims/{seeded['claim_id']}/provenance")).json()
        assert empty["operator_corrections"] == []

        r = await _dispute(client, seeded["claim_id"])
        assert r.status_code == 201

        provenance = (await client.get(f"/api/v1/knowledge/claims/{seeded['claim_id']}/provenance")).json()
        corrections = provenance["operator_corrections"]
        assert len(corrections) == 1, corrections
        row = corrections[0]
        assert row["state_label"] == ui_labels.describe("dispute_state", "disputed")["label"]
        assert row["kind_hint"] == ui_labels.describe("graph_correction_kind", "merge")["hint"]
        assert row["actor_label"] == ui_labels.describe("dispute_actor", "operator:web")["label"]
        assert row["reason"] == REASON  # человеческие слова оператора, дословно
        assert row["retelling_uri"] == RETOLD_URI and row["primary_uri"] == PRIMARY_URI
        assert row["rules_version"] == RULES_ENGINE_VERSION
        assert row["created_at"], row

        # бейдж пока прежний: пересчёт ещё не произошёл — витрина не врёт заранее
        detail = (await client.get(f"/api/v1/knowledge/claims/{seeded['claim_id']}")).json()
        assert all(h["assessment_state"] == "pending" for h in detail["heads"])

        page = await client.get(f"/claim/{seeded['claim_id']}")
        assert page.status_code == 200
        assert 'id="d-primary"' in page.text and 'id="d-reason"' in page.text
    await app_engine.dispose()

