"""Scenario (DB): первоисточник спора — производитель, которому хост уже приписал улику
(T7.114, ADR-0031 дополнение, решение пользователя).

Репродюсер стендового случая `.92` (утверждение о безработице 2,1–2,2 %, «E3 · Проверено»):
улика caliber читает выпуск Росстата (запись значения `value_attribution` уже ведёт к
источнику-производителю Росстата), а пересказ finmarket значения не несёт — его запись
значения честно не собрана. Факт «finmarket пересказывает Росстат» прежде отказывал
`dispute_source_not_found`: первоисточник искали только среди самих улик, а Росстат —
РОДИТЕЛЬ улики caliber, не улика. По новому правилу производитель — допустимый
первоисточник спора: хост уже записал факт его производности в истории знания.

Что закреплено тестом:
- первоисточник принимается и через запись значения (т1), и через страничный указатель
  `sources.parent_source_id` (т2); коррекция merge пишется пересказ → производитель;
- оценка падает только через каскад + rules engine: группы → одна, insufficient_independence,
  E1/hypothesis (закреплена фактическая ветка правил, как в прежнем сценарии T7.81);
- адреса нет ни среди улик, ни среди родителей — прежний честный отказ (т3), whole-way;
- пересказом производитель быть не может — отказ (т4);
- повтор идемпотентен, отмена возвращает прежнюю оценку и две группы (т5);
- «уже сгруппированы» для родителя проверяется по снимку независимости действующей оценки:
  группа производителя = группа улик, которым он записан родителем (т6).

Форма спора витрины перечисляет производителей отдельно (т7 — read-only список
provenance), развод ролей закреплён в разметке страницы (т8).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.web.knowledge import claim_provenance
from hostctl.cli import EXIT_USAGE_ERROR, main
from packages.memory.scope import ValueAttribution
from tests.scenario.test_claim_dispute import (
    _artifact,
    _corrections,
    _head,
    _run_worker,
    _scalar,
    _seed_claim_pending,
    _seed_job,
    _source_evidence,
    _source_members,
    _worker_reasons,
)
from tests.scenario.test_source_graph import _host_source

pytestmark = [pytest.mark.scenario]

PRIMARY_URI = "https://rosstat.example/press/unemployment-sep-2026"
CALIBER_URI = "https://caliber.example/economics/without-rabochikh"
FINMARKET_URI = "https://finmarket.example/news/6727171"
ABSENT_URI = "https://absent.example/press"
STATEMENT = "Уровень безработицы в России в сентябре 2026 года составил 2,1–2,2 процента по данным Росстата"
REASON = "Страница-пересказ дословно повторяет абзацы пресс-выпуска производителя и его таблицу"


def _text(result: Any) -> str:
    parts = [result.output]
    with contextlib.suppress(ValueError, AttributeError):
        parts.append(result.stderr)
    return "\n".join(parts)


def _with_engine(url: str, fn: Callable[[AsyncEngine], Any]) -> Any:
    """Один engine на операцию (ловушка T7.59 для CliRunner-тестов)."""

    async def _main() -> Any:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


def _dispute(runner: CliRunner, url: str, claim: uuid.UUID, primary_uri: str, retelling_uri: str):
    return runner.invoke(
        main,
        [
            "claim-dispute",
            "--claim",
            str(claim),
            "--primary",
            primary_uri,
            "--retelling",
            retelling_uri,
            "--reason",
            REASON,
        ],
        env={"NOEZEMA_DATABASE_URL": url},
    )


async def _value_record_evidence(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    source_id: uuid.UUID,
    parent_id: uuid.UUID,
    *,
    tag: str,
) -> uuid.UUID:
    """Улика с уже записанным решением хоста `value_attribution` (та же форма записи,
    что пишет `packages/memory/scope.py`; метод здесь — любая прежняя версия)."""
    record = ValueAttribution(
        primary_key="rosstat",
        primary_name="Росстат",
        primary_uri=PRIMARY_URI,
        parent_source_id=str(parent_id),
        method="host-value-attribution-v7",
        basis_fragment="по данным Росстата",
    )
    artifact_id = await _artifact(engine, tag=tag)
    await _scalar(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        " scope, source_id, chunk_id, observation_artifact_id) "
        "VALUES (:id, :c, 'supports', 'source_assertion', :h, CAST(:scope AS jsonb), :s, :k, :art)",
        {
            "id": uuid.uuid4(),
            "c": claim_id,
            "h": f"val-{tag}-{uuid.uuid4().hex[:8]}",
            # scope покрытия — прежняя («{"x": 1}», как в сиденьях T7.81); запись
            # значения добавлена сверху, а не заменила её
            "scope": json.dumps({"x": 1, "value_attribution": record.as_scope()}),
            "s": source_id,
            "k": f"c-{tag}",
            "art": artifact_id,
        },
    )
    return artifact_id


async def _seed_case(engine: AsyncEngine, *, form: str) -> dict[str, Any]:
    """Утверждение `external_fact` (тот же механизм, что у стендового `temporal_fact`)
    с двумя уликами: caliber и finmarket; производитель Росстата — НЕ улика.

    form="value":  родитель записан в улике caliber (`value_attribution`);
    form="pointer": родитель — страничный указатель источника caliber;
    form="both":   оба пересказа уже приписаны производителю (случай «уже сгруппированы»).
    """
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, STATEMENT, "external_fact")
    primary_id = await _host_source(engine, uri=PRIMARY_URI)
    caliber_id = await _host_source(
        engine, uri=CALIBER_URI, parent=primary_id if form == "pointer" else None
    )
    finmarket_id = await _host_source(engine, uri=FINMARKET_URI)

    if form == "pointer":
        caliber_art = await _source_evidence(engine, claim_id, caliber_id, tag=f"cal-{form}")
    else:
        caliber_art = await _value_record_evidence(
            engine, claim_id, caliber_id, primary_id, tag=f"cal-{form}"
        )
    if form == "both":
        finmarket_art = await _value_record_evidence(
            engine, claim_id, finmarket_id, primary_id, tag=f"fin-{form}"
        )
    else:
        finmarket_art = await _source_evidence(
            engine, claim_id, finmarket_id, tag=f"fin-{form}"
        )

    await _seed_job(engine, claim_id, reason="parent-seed")
    await _run_worker(engine)
    return {
        "claim_id": claim_id,
        "primary": primary_id,
        "caliber": caliber_id,
        "caliber_artifact": caliber_art,
        "finmarket": finmarket_id,
        "finmarket_artifact": finmarket_art,
    }


def _assert_preconditions(url: str, claim_id: uuid.UUID) -> None:
    """До спора: две группы независимости, действующая E3/`supported` (витрина «Проверено»)."""
    head = _with_engine(url, lambda engine: _head(engine, claim_id))
    members = _with_engine(url, lambda engine: _source_members(engine, claim_id))
    reasons = _with_engine(url, lambda engine: _worker_reasons(engine, claim_id))
    assert head[0] == "current" and head[1] == "supported" and head[3] == "E3", (
        head,
        members,
        reasons,
    )
    assert len({m[0] for m in members}) == 2, (head, members)


# ─── т1: запись значения → производитель спорится как первоисточник ───────


def test_value_record_parent_becomes_disputable_primary(migrated_db: Any) -> None:
    scratch_url, _fixture_engine = migrated_db
    seeded = _with_engine(scratch_url, lambda engine: _seed_case(engine, form="value"))
    claim_id = seeded["claim_id"]
    _assert_preconditions(scratch_url, claim_id)

    result = _dispute(CliRunner(), scratch_url, claim_id, PRIMARY_URI, FINMARKET_URI)
    assert result.exit_code == 0, _text(result)
    assert "оспорено" in _text(result), _text(result)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert len(rows) == 1, rows
    actor, kind, valid, _, basis, reason_event, from_id, to_id = rows[0]
    assert (actor, kind, valid) == ("operator:hostctl", "merge", True)
    # основание коррекции — артефакт прочтения ПЕРЕСКАЗА (правило не изменилось)
    assert basis == seeded["finmarket_artifact"] and reason_event is not None
    assert (from_id, to_id) == (seeded["finmarket"], seeded["primary"])

    # оценку меняет только каскад + рабочий переоценки: группы стало меньше → E1/hypothesis
    _with_engine(scratch_url, lambda engine: _run_worker(engine))
    head = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    assert head[0] == "current" and head[1] == "hypothesis" and head[3] == "E1", head
    members = _with_engine(scratch_url, lambda engine: _source_members(engine, claim_id))
    assert len({m[0] for m in members}) == 1, members
    assert any(basis.startswith("parent:") for _, basis in members), members
    assert any(basis == "correction:merge" for _, basis in members), members
    reasons = _with_engine(scratch_url, lambda engine: _worker_reasons(engine, claim_id))
    assert "insufficient_independence" in reasons, reasons


# ─── т2: то же через страничный указатель sources.parent_source_id ────────


def test_page_pointer_parent_becomes_disputable_primary(migrated_db: Any) -> None:
    scratch_url, _fixture_engine = migrated_db
    seeded = _with_engine(scratch_url, lambda engine: _seed_case(engine, form="pointer"))
    claim_id = seeded["claim_id"]
    _assert_preconditions(scratch_url, claim_id)

    result = _dispute(CliRunner(), scratch_url, claim_id, PRIMARY_URI, FINMARKET_URI)
    assert result.exit_code == 0, _text(result)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert len(rows) == 1, rows
    assert (rows[0][6], rows[0][7]) == (seeded["finmarket"], seeded["primary"])

    _with_engine(scratch_url, lambda engine: _run_worker(engine))
    head = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    assert head[0] == "current" and head[1] == "hypothesis" and head[3] == "E1", head
    members = _with_engine(scratch_url, lambda engine: _source_members(engine, claim_id))
    assert len({m[0] for m in members}) == 1, members


# ─── т3: адреса нет ни среди улик, ни среди родителей → прежний отказ ─────


def test_address_outside_evidence_and_parents_is_refused(migrated_db: Any) -> None:
    """Отказ не смягчается наличием родителя: контрольный спор с родителем проходит,
    но адреса, которого нет ни среди улик, ни среди родителей, — по-прежнему честный
    отказ whole-way."""
    scratch_url, _fixture_engine = migrated_db
    seeded = _with_engine(scratch_url, lambda engine: _seed_case(engine, form="value"))
    claim_id = seeded["claim_id"]
    _assert_preconditions(scratch_url, claim_id)
    runner = CliRunner()

    # контроль: производитель спорится (иначе отказ ниже ничего не доказывает)
    control = _dispute(runner, scratch_url, claim_id, PRIMARY_URI, FINMARKET_URI)
    assert control.exit_code == 0, _text(control)
    cancelled = runner.invoke(
        main,
        ["claim-dispute-cancel", "--claim", str(claim_id), "--reason", "контроль снят"],
        env={"NOEZEMA_DATABASE_URL": scratch_url},
    )
    assert cancelled.exit_code == 0, _text(cancelled)
    _with_engine(scratch_url, lambda engine: _run_worker(engine))

    result = _dispute(CliRunner(), scratch_url, claim_id, ABSENT_URI, FINMARKET_URI)
    assert result.exit_code == EXIT_USAGE_ERROR, _text(result)
    assert "отказ (dispute_source_not_found)" in _text(result), _text(result)
    assert "Traceback" not in _text(result)

    # whole-way: при отказе не записано ни одной ДЕЙСТВУЮЩЕЙ коррекции
    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert not any(r[2] for r in rows), rows
    head = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    assert head[0] == "current" and head[3] == "E3", head


# ─── т4: пересказом нельзя назвать производителя ──────────────────────────


def test_parent_cannot_be_named_a_retelling(migrated_db: Any) -> None:
    """Контроль из т3 тот же: производитель — первоисточник, но не пересказ.
    Перестановка ролей — отказ по прежней причине (пересказ = только улика)."""
    scratch_url, _fixture_engine = migrated_db
    seeded = _with_engine(scratch_url, lambda engine: _seed_case(engine, form="value"))
    claim_id = seeded["claim_id"]
    runner = CliRunner()

    control = _dispute(runner, scratch_url, claim_id, PRIMARY_URI, FINMARKET_URI)
    assert control.exit_code == 0, _text(control)
    cancelled = runner.invoke(
        main,
        ["claim-dispute-cancel", "--claim", str(claim_id), "--reason", "контроль снят"],
        env={"NOEZEMA_DATABASE_URL": scratch_url},
    )
    assert cancelled.exit_code == 0, _text(cancelled)
    _with_engine(scratch_url, lambda engine: _run_worker(engine))

    result = _dispute(CliRunner(), scratch_url, claim_id, CALIBER_URI, PRIMARY_URI)
    assert result.exit_code == EXIT_USAGE_ERROR, _text(result)
    assert "отказ (dispute_source_not_found)" in _text(result), _text(result)
    assert "retelling:" in _text(result), _text(result)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert not any(r[2] for r in rows), rows
    head = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    assert head[0] == "current" and head[3] == "E3", head


# ─── т5: повтор идемпотентен; отмена возвращает прежнюю оценку ────────────


def test_repeat_on_a_parent_is_replay_and_cancel_restores_the_grade(migrated_db: Any) -> None:
    scratch_url, _fixture_engine = migrated_db
    seeded = _with_engine(scratch_url, lambda engine: _seed_case(engine, form="value"))
    claim_id = seeded["claim_id"]
    runner = CliRunner()

    assert _dispute(runner, scratch_url, claim_id, PRIMARY_URI, FINMARKET_URI).exit_code == 0
    again = _dispute(runner, scratch_url, claim_id, PRIMARY_URI, FINMARKET_URI)
    assert again.exit_code == 0, _text(again)
    assert "идемпотентный повтор" in _text(again), _text(again)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert len(rows) == 1, rows
    questions = _with_engine(
        scratch_url,
        lambda engine: _scalar(
            engine, "SELECT count(*) FROM questions WHERE text LIKE 'Перепроверить%'"
        ),
    )
    assert questions[0] == 1, questions

    _with_engine(scratch_url, lambda engine: _run_worker(engine))
    head = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    assert head[1] == "hypothesis", head

    cancelled = runner.invoke(
        main,
        [
            "claim-dispute-cancel",
            "--claim",
            str(claim_id),
            "--reason",
            "первоисточник всё-таки отдельный: разные таблицы и даты",
        ],
        env={"NOEZEMA_DATABASE_URL": scratch_url},
    )
    assert cancelled.exit_code == 0, _text(cancelled)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert len(rows) == 1 and rows[0][2] is False, rows

    _with_engine(scratch_url, lambda engine: _run_worker(engine))
    head = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    assert head[0] == "current" and head[1] == "supported" and head[3] == "E3", head
    members = _with_engine(scratch_url, lambda engine: _source_members(engine, claim_id))
    assert len({m[0] for m in members}) == 2, members


# ─── т6: «уже сгруппированы» для родителя читается по снимку оценки ───────


def test_parent_already_grouped_with_the_retelling_is_refused(migrated_db: Any) -> None:
    """Оба пересказа уже приписаны одному производителю → группа в снимке у них уже
    одна: спор не пишет ничего (производитель не входит в участники снимка — его
    группа берётся из улик, которым он записан родителем)."""
    scratch_url, _fixture_engine = migrated_db
    seeded = _with_engine(scratch_url, lambda engine: _seed_case(engine, form="both"))
    claim_id = seeded["claim_id"]

    members = _with_engine(scratch_url, lambda engine: _source_members(engine, claim_id))
    assert len({m[0] for m in members}) == 1, members  # хост уже склеил детей общего родителя

    result = _dispute(CliRunner(), scratch_url, claim_id, PRIMARY_URI, FINMARKET_URI)
    assert result.exit_code == EXIT_USAGE_ERROR, _text(result)
    assert "отказ (dispute_sources_already_grouped)" in _text(result), _text(result)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert rows == [], rows


# ─── т7/т8: витрина перечисляет производителей форме спора ────────────────


def test_provenance_lists_producers_for_the_dispute_form(migrated_db: Any) -> None:
    scratch_url, _fixture_engine = migrated_db
    seeded = _with_engine(scratch_url, lambda engine: _seed_case(engine, form="value"))

    async def _read(engine: AsyncEngine) -> dict[str, Any]:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            return await claim_provenance(db, seeded["claim_id"])

    provenance = _with_engine(scratch_url, _read)
    uris = [p["canonical_uri"] for p in provenance["dispute_producers"]]
    assert PRIMARY_URI in uris, provenance["dispute_producers"]
    # производитель — не улика: в списке улик его адреса нет
    evidence_uris = [e["source"]["canonical_uri"] for e in provenance["evidence"] if e["source"]]
    assert PRIMARY_URI not in evidence_uris


def test_dispute_form_separates_roles_in_markup() -> None:
    """Форма перечисляет первоисточники шире, чем пересказы: развод закреплён в разметке страницы."""
    from apps.web.api import _CLAIM_HTML

    assert "primaryUris(p)" in _CLAIM_HTML and "dispute_producers" in _CLAIM_HTML
    assert 'пересказ <select id="d-retelling">\'+opts' in _CLAIM_HTML
