"""Scenario (DB): read-only предпросмотр смены групп независимости и применение
существующим каскадом (T7.117 часть б, `noezemactl independence-preview`, ADR-0029 §22).

Состояние стенда смоделировано честно: утверждение оценено ДО появления записи значения на
третьей улике (переатрибуция прогона 1 записала строгое решение на interfax — её запись
называет того же родителя, что и её указатель, и ничего не меняет; запись на ленте посажена
INSERT-посевом T7.107/T7.116 без каскада, как на стенде она была записана раньше, чем
сработало новое правило приоритета). Действующая оценка головы при этом — прежняя: две
группы, E3 «supported».

Закреплено:
1. предпросмотр перечисляет ровно изменённое утверждение (группы было→стало, смена родителя
   улики ленты, оценка было→стало) и НЕ пишет ни одной строки (счётники ключевых таблиц
   до и после идентичны); ни одного нового AuditEventType;
2. `--apply` идёт существующим каскадом §11.3 (`apply_source_graph_change`: головы в pending,
   задачи переоценки) и рабочим `run_reassessment_batch`; повторный предпросмотр после
   обработки — пуст; повторный apply на пустом предпросмотре — no-op (ноль задач);
3. cli-контракт T7.59: коды возврата, печатные строки отчёта, отсутствие URL → 2;
4. сборка входа (тест на чистых помощниках): разные родители записей одной улики —
   эффективного нет и работает страничный указатель (консерватизм T7.78 не сломан);
   родитель по записи становится узлом графа и склеивает детей, даже когда ни один
   страничный указатель на него не ссылается; записанного родителя без строки источника
   узлом графа не делает (прежнее поведение загрузчика).

CliRunner-ловушка T7.59: команда поднимает свой event loop, поэтому тест не берёт engine
фикстуры — каждый read теста открывает свой engine из scratch-URL и `dispose()` его.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hostctl.cli import EXIT_USAGE_ERROR, main
from packages.memory.independence import group_source_graph
from packages.memory.independence_preview import (
    IndependencePreviewReport,
    apply_independence_preview,
    preview_independence_changes,
)
from packages.memory.source_graph import grouping_arguments, load_source_graph_assembly
from tests.scenario.test_research_evidence import _scalar
from tests.scenario.test_value_over_pointer_priority_t7117 import (
    STATEMENT_CBR,
    URL_CBR,
    URL_IFX,
    URL_LENTA,
    FakeFetchClient,
    _group_count,
    _pointer_of,
    _seed_claim,
)

pytestmark = [pytest.mark.scenario]


@pytest.fixture()
def live_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий fetch-путь с НАСТОЯЩИМ детектором (тот же fixture, что в части а)."""

    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)

VALUE_SCHEMA = "host-value-attribution-v1"
CURRENT_VALUE_METHOD = "host-value-attribution-v18"

COUNT_TABLES = (
    "sources",
    "evidence",
    "claims",
    "claim_assessments",
    "claim_assessment_heads",
    "reassessment_jobs",
    "audit_events",
    "source_independence_snapshots",
)


async def _counts(scratch_url: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNT_TABLES:
        row = await _scalar(scratch_url, f"SELECT count(*) AS n FROM {table}")
        assert row is not None
        counts[table] = int(row[0])
    return counts


async def _plant_source(scratch_url: str, uri: str, tag: str) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata) "
                    "VALUES (:id, 'external_url', :uri, :ch, '{}')"
                ),
                {"id": str(uuid.uuid4()), "uri": uri, "ch": f"t7117-hash-{tag}"},
            )
            row = await conn.execute(
                text("SELECT id::text AS id FROM sources WHERE canonical_uri = :uri"), {"uri": uri}
            )
    finally:
        await engine.dispose()
    return uuid.UUID(str(row.scalar_one()))


async def _plant_record(
    scratch_url: str,
    *,
    claim_id: uuid.UUID,
    source_id: uuid.UUID,
    tag: str,
    parent_id: uuid.UUID,
    pairing: str = "value_and_primary_in_fragment",
) -> None:
    """Посев улики с записью о происхождении значения (INSERT вместо UPDATE, T7.107/T7.116):
    ровно та форма scope, которую пишет переатрибуция; каскада при этом НЕ происходит —
    так на стенде запись оказалась раньше переоценки."""

    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
                    "scope, source_id, chunk_id) "
                    "VALUES (:id, :c, 'supports', 'source_assertion', :h, CAST(:sc AS JSONB), :s, :k)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "c": str(claim_id),
                    "h": f"t7117-identity-{tag}",
                    "sc": json.dumps(
                        {
                            "scope_schema": "host-scope-v1",
                            "value_attribution": {
                                "schema": VALUE_SCHEMA,
                                "primary_key": "cbr",
                                "primary_name": "Банк России",
                                "primary_uri": "https://cbr.ru/",
                                "parent_source_id": str(parent_id),
                                "method": CURRENT_VALUE_METHOD,
                                "basis_fragment": "планированного Банком России коридора в 6-7 процентов",
                                "pairing": pairing,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    "s": str(source_id),
                    "k": f"t7117-chunk-{tag}",
                },
            )
    finally:
        await engine.dispose()


async def _with_session(scratch_url: str, fn: Callable[[Any], Any]) -> Any:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            return await fn(db)
    finally:
        await engine.dispose()


async def _seed_stale_state(scratch_url: str, store: Any) -> dict[str, uuid.UUID]:
    """Стендовое состояние: оценка головы построена ДО записи значения на ленте."""

    from tests.scenario.test_value_over_pointer_priority_t7117 import _run_backfill

    claim_id, src = await _seed_claim(scratch_url, store)
    apply1 = await _run_backfill(scratch_url, store, dry_run=False)
    assert apply1.changed_evidence >= 1
    cbr_id = src[URL_CBR]
    lenta_id = src[URL_LENTA]
    await _plant_record(scratch_url, claim_id=claim_id, source_id=lenta_id, tag="lenta", parent_id=cbr_id)
    return {"claim": claim_id, "cbr": cbr_id, "ifx": src[URL_IFX], "lenta": lenta_id}


# ─── т5а: предпросмотр перечисляет ровно изменённое утверждение и ничего не пишет ──


@pytest.mark.asyncio
async def test_preview_lists_the_changed_claim_and_writes_nothing(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    from packages.artifacts.store import FilesystemArtifactStore

    ids = await _seed_stale_state(scratch_url, FilesystemArtifactStore(tmp_path / "artifacts"))
    before = await _counts(scratch_url)
    assert await _group_count(scratch_url) == 2  # голова ещё прежняя: две группы E3

    report: IndependencePreviewReport = await _with_session(
        scratch_url, lambda db: preview_independence_changes(db)
    )
    assert report.config_snapshot_id is not None
    assert report.claims_checked == 1
    assert len(report.rows) == 1, report.rows
    row = report.rows[0]
    assert row.claim_id == ids["claim"]
    assert row.statement == STATEMENT_CBR

    # было: две группы записанного снимка; стало: одна — по новому правилу
    assert len(row.old_groups) == 2 and len(row.new_groups) == 1, (row.old_groups, row.new_groups)
    lenta_uri = next(uri for g in row.new_groups for uri in g if "lenta" in uri)
    assert any(lenta_uri in g for g in row.old_groups)

    # какая улика сменила родителя: лента (указатель Минфин → запись ЦБ).
    # interfax выпадает честно: её указатель и запись называют один источник (т6).
    assert len(row.parent_switches) == 1, row.parent_switches
    switch = row.parent_switches[0]
    assert "lenta" in switch.evidence_uri and "interfax" not in switch.evidence_uri
    # родитель по записи — прочитанная страница ЦБ; указатель вёл к Минфин-якорю (т2 формы)
    assert "cbr.ru" in switch.record_parent_uri
    assert switch.pointer_parent_uri is not None and switch.record_parent_uri != switch.pointer_parent_uri

    # оценка без записи: было — записанная голова, стало — пересчёт чистыми правилами
    assert (row.persisted_grade, row.persisted_status) == ("E3", "supported")
    assert row.evaluation_computed is True
    assert (row.new_grade, row.new_status) == ("E1", "hypothesis")
    assert row.assessment_changed is True

    # read-only: ни одной новой строки, ни одного аудит-события
    after = await _counts(scratch_url)
    assert after == before, (before, after)


# ─── т5б: применение существующим каскадом; повторный предпросмотр пуст ─────────────


@pytest.mark.asyncio
async def test_preview_apply_uses_existing_cascade_and_is_idempotent(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    from packages.artifacts.store import FilesystemArtifactStore
    from packages.domain.services.audit import AuditService
    from packages.memory.reassessment import run_reassessment_batch

    ids = await _seed_stale_state(scratch_url, FilesystemArtifactStore(tmp_path / "artifacts"))
    report = await _with_session(scratch_url, lambda db: preview_independence_changes(db))
    assert len(report.rows) == 1

    from packages.domain.db.uow import transaction

    async def _apply(db: Any) -> Any:
        async with transaction(db):
            return await apply_independence_preview(db, AuditService(db), rows=report.rows)

    cascade = await _with_session(scratch_url, _apply)
    assert cascade.jobs_created >= 1 and cascade.invalidated >= 1
    assert str(ids["claim"])  # каскад идёт по утверждению из предпросмотра

    outcome = await _with_session(scratch_url, lambda db: run_reassessment_batch(db))
    assert outcome.completed >= 1

    again = await _with_session(scratch_url, lambda db: preview_independence_changes(db))
    assert again.rows == (), again.rows

    # повторный apply на пустом предпросмотре — no-op
    async def _apply_empty(db: Any) -> Any:
        async with transaction(db):
            return await apply_independence_preview(db, AuditService(db), rows=())

    empty_cascade = await _with_session(scratch_url, _apply_empty)
    assert (empty_cascade.affected_claims, empty_cascade.invalidated, empty_cascade.jobs_created) == (0, 0, 0)

    # голова снова current, оценка перестроена по новому правилу
    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status FROM claim_assessment_heads h "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.assessment_state = 'current' AND h.claim_id = :c",
        {"c": str(ids["claim"])},
    )
    assert row is not None and (str(row[0]), str(row[1])) == ("E1", "hypothesis")
    assert await _group_count(scratch_url) == 1


# ─── т5в: cli-контракт T7.59 (CliRunner, свой event loop у команды) ────────────────


def _with_engine(url: str, fn: Callable[[Any], Any]) -> Any:
    async def _main() -> Any:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


def _seed_state_sync(url: str, store: Any) -> dict[str, uuid.UUID]:
    return asyncio.run(_seed_stale_state(url, store))


def test_cli_independence_preview_is_read_only_then_apply_requeues_and_converges(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    from packages.artifacts.store import FilesystemArtifactStore

    _seed_state_sync(scratch_url, FilesystemArtifactStore(tmp_path / "artifacts"))
    before = _with_engine(scratch_url, lambda engine: _counts(scratch_url))

    runner = CliRunner()
    env = {"NOEZEMA_DATABASE_URL": scratch_url}

    result = runner.invoke(main, ["independence-preview"], env=env)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "independence-preview: только чтение" in out
    assert "утверждений с изменениями: 1" in out
    assert "группы было (2)" in out and "группы стало (1)" in out
    assert "родитель по указателю" in out and "по записи" in out
    assert "оценка было: E3 supported" in out and "стало: E1 hypothesis" in out
    assert "проверено утверждений: 1" in out
    # предпросмотр ничего не дописал
    after_preview = _with_engine(scratch_url, lambda engine: _counts(scratch_url))
    assert after_preview == before

    applied = runner.invoke(main, ["independence-preview", "--apply"], env=env)
    assert applied.exit_code == 0, applied.output
    assert "каскад §11.3:" in applied.output and "задач переоценки создано: " in applied.output
    assert "рабочий переоценки:" in applied.output
    assert "повторный предпросмотр после обработки: пусто" in applied.output

    third = runner.invoke(main, ["independence-preview"], env=env)
    assert third.exit_code == 0 and "изменений нет" in third.output
    assert "утверждений с изменениями: 0" in third.output

    reapply = runner.invoke(main, ["independence-preview", "--apply"], env=env)
    assert reapply.exit_code == 0 and "(применять нечего: предпросмотр пуст)" in reapply.output


def test_cli_independence_preview_without_database_url_exits_two(migrated_db: tuple[str, Any]) -> None:
    result = CliRunner().invoke(main, ["independence-preview"], env={})
    assert result.exit_code == EXIT_USAGE_ERROR


# ─── т2/т3 + включение родителя по записи в узлы графа (чистые помощники сборки) ───


@pytest.mark.asyncio
async def test_conflicting_records_fall_back_to_pointer_and_recorded_parent_becomes_node(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    from packages.artifacts.store import FilesystemArtifactStore

    claim_id, src = await _seed_claim(scratch_url, FilesystemArtifactStore(tmp_path / "artifacts"))
    lenta_id = src[URL_LENTA]
    pointer_parent = await _pointer_of(scratch_url, lenta_id)
    assert pointer_parent is not None  # страница ленты помечена Минфин-якорем

    # две улики одного источника с РАЗНЫМИ родителями записей → эффективного нет (T7.78)
    ghost_1, ghost_2 = uuid.uuid4(), uuid.uuid4()
    await _plant_record(
        scratch_url, claim_id=claim_id, source_id=lenta_id, tag="p1", parent_id=ghost_1
    )
    await _plant_record(
        scratch_url, claim_id=claim_id, source_id=lenta_id, tag="p2", parent_id=ghost_2
    )
    # tass: единственный родитель по записи на РЕАЛЬНОЙ строке-якоре, указателя нет
    anchor_id = await _plant_source(scratch_url, "https://cbr.example/press/doklad-2026", "anchor")
    tass_uri = "https://tass.example/inflyaciya"
    tass_id = await _plant_source(scratch_url, tass_uri, "tass")
    await _plant_record(
        scratch_url, claim_id=claim_id, source_id=tass_id, tag="tass", parent_id=anchor_id
    )
    # второй ребёнок того же записанного родителя: склейка возможна только если родитель — узел
    ved_id = await _plant_source(scratch_url, "https://vedomosti.example/inflyaciya-2026", "ved")
    await _plant_record(
        scratch_url, claim_id=claim_id, source_id=ved_id, tag="ved", parent_id=anchor_id
    )

    before = await _counts(scratch_url)

    async def _assemble(db: Any) -> Any:
        assembly = await load_source_graph_assembly(db, claim_id=claim_id)
        assert assembly is not None
        return assembly

    assembly = await _with_session(scratch_url, _assemble)

    # эффективного родителя у ленты нет (конфликт записей) — указатель прежний (т2)
    assert str(lenta_id) not in {str(k) for k in assembly.effective_parent}
    # tass: единственный записанный родитель — эффективный; он включён в узлы графа,
    # хотя ни один страничный указатель на него не ссылается (проверка части а)
    assert str(assembly.effective_parent.get(tass_id) or "") == str(anchor_id)
    node_ids = {str(n.source_id) for n in assembly.nodes}
    assert str(anchor_id) in node_ids and str(tass_id) in node_ids
    # родителей-призраков (нет строки источника) загрузчик узлом не делает — прежнее поведение
    assert not node_ids & {str(ghost_1), str(ghost_2)}

    inputs, edges, corrections = grouping_arguments(assembly)
    chosen = {str(inp.source_id): inp.parent_source_id for inp in inputs}
    assert chosen[str(lenta_id)] is not None and str(chosen[str(lenta_id)]) == pointer_parent, (
        "конфликт записей → страничный указатель"
    )
    assert chosen[str(tass_id)] is not None and str(chosen[str(tass_id)]) == str(anchor_id)
    assert chosen.get(str(src[URL_CBR])) is None, "собственная публикация ЦБ без изменений (т3)"

    result = group_source_graph(inputs, edges, corrections)
    # два ребёнка записанного родителя — в одной группе, хотя домены у них разные
    assert result.groups[tass_id] == result.groups[ved_id]
    assert result.bases[tass_id] == f"parent:{str(anchor_id)[:12]}"
    assert result.bases[ved_id] == f"parent:{str(anchor_id)[:12]}"
    assert result.groups[lenta_id] != result.groups[src[URL_CBR]]

    after = await _counts(scratch_url)
    assert after == before  # чистые помощники ничего не пишут
