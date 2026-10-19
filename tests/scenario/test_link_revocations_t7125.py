"""Scenario (DB): отзыв связи улики — следствие, команды оператора и фильтр ВХОДА (T7.125, ADR-0039).

Что закреплено (номера т1–т9 из постановки T7.125; миграция и её up/down/up — в
`tests/scenario/test_link_revocations_migration_t7125.py`):

- т1: `apply_link_revocations` по двум названным оператором связям (обе «нет значения») пишет
  ровно два отзыва, снимает голову действующей оценки в pending (`prepared_by =
  system:link_revocation`, оба признака current — NULL) и заводит ровно одну durable-задачу
  переоценки; рабочий батч пересчитывает оценку rules engine'ом: E3 supported → E1 hypothesis,
  группа страницы ключевой ставки уходит из участников. Строки `evidence` побайтово прежние,
  журнал прежней оценки (`assessment_evidence`) не тронут, отозванные улики в новой оценке не
  участвуют.
- т2: связь, чья страница называет значение утверждения, отзыву не подлежит: отказ назван
  («значение на странице есть») с исходом той же проверки и заметкой гейта.
- т3: один плохой id в списке отказывает ВСЕМУ вызову: ни одной записи, голова прежняя.
- т4: источник связи под действующей коррекцией оператора (ADR-0031) — отказ с названной
  причиной; решение оператора эта команда не отменяет.
- т5: повтор отзыва идемпотентен: «уже отозвана» со ссылкой на прежнюю запись, записей нет.
- т6: план без `--apply` показывает уже отозванные связи отдельной строкой с причиной и не
  пересчитывает их повторно (пересчёт «стало» совпадает с записанным, групп не меняется).
- т7: CLI-контракт обеих команд: отказы по параметрам и по проверкам, запись отзыва + рабочий
  батч, отмена отзыва + рабочий батч, повтор отмены.
- т8: отзыв последней улики без `--allow-empty` отказывает; с `--allow-empty` рабочий батч по
  §5.9.1 переводит голову в invalid и ставит исследовательский вопрос.
- т9: отозванная связь не входит и в группировщика: `load_source_graph_assembly` (единственный
  сборщик ВХОДА §11.3) больше не даёт её источник участником. Пороги и алгоритм группировки не
  изменены — предикат фильтрует только вход.

Посев «уже записанных» связей — тот же честный, что у T7.124: staging + `apply_claim_staging`
(связи написаны до появления гейта значения). Сеть — FakeFetchClient (§6), прокси, группировка,
правила и staging настоящие, страницы — побайтовые нормализованные тексты стендовых страниц
(tests/fixtures/t7123). Ни одного внешнего запроса, ни одного LLM-вызова.

CliRunner-ловушка T7.59: команда поднимает свой event loop, поэтому тест не берёт engine
фикстуры — каждый read теста открывает свой engine из scratch-URL и `dispose()` его.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from apps.orchestrator.link_audit import (
    REFUSAL_LAST_EVIDENCE,
    REFUSAL_OPERATOR_CORRECTION,
    REFUSAL_UNKNOWN_EVIDENCE,
    REFUSAL_VALUE_PRESENT,
    REVOCATION_JOB_REASON,
    REVOCATION_PREPARED_BY,
    VERDICT_VALUE_ABSENT,
    LinkRevocationError,
    apply_link_revocations,
    cancel_link_revocations,
)
from hostctl.cli import EXIT_USAGE_ERROR, main
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.db.uow import transaction
from packages.domain.models.enums import QuestionOrigin
from packages.domain.services.audit import AuditService
from packages.memory.dispute import ACTOR_HOSTCTL
from packages.memory.reassessment import run_reassessment_batch
from packages.memory.source_graph import load_source_graph_assembly
from tests.scenario.test_link_audit_t7124 import (
    COUNT_TABLES,
    STATEMENT_83,
    URL_HR,
    URL_SBERCIB,
    _assessment_of,
    _audit,
    _group_uris,
    _record,
    _row_of,
    _rows,
    _seed_replay,
    _with_session,
)
from tests.scenario.test_research_evidence import (
    _apply,
    _fetch_sources,
    _section,
    _set_section,
)

pytestmark = [pytest.mark.scenario]

REASON = "на странице «решение ЦБ по ключевой ставке» значения hh-индекса нет"
CANCEL_REASON = "связь проверена повторно: страница называет значение"

#: единственный счётчик таблиц с новой таблицей отзывов + вопросами (т8)
COUNT_TABLES_T7125 = (*COUNT_TABLES, "evidence_link_revocations", "questions", "assessment_evidence")

#: утверждение с единственной связью на hr-ratings: значения 21,7 на этой странице нет (т8)
STATEMENT_LONE = "По данным HR-Ratings, hh-индекс в России в 2026 году составил 21,7."

EVIDENCE_FINGERPRINT_SQL = (
    "SELECT id, claim_id, relation, evidence_kind, identity_hash, source_id, chunk_id, "
    "scope::text AS scope, created_at FROM evidence ORDER BY id"
)


@pytest.fixture()
def live_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий fetch-путь с НАСТОЯЩИМ нормализатором: подменена только сеть (T7.124)."""

    import apps.research_proxy.service as svc
    from tests.scenario.test_link_audit_t7124 import FakeFetchClient

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)


# ─── посев и чтение ───────────────────────────────────────────────────────────────


async def _claim_id(scratch_url: str, statement: str) -> uuid.UUID:
    rows = await _rows(scratch_url, "SELECT id FROM claims WHERE statement = :s", {"s": statement})
    assert rows, f"утверждения {statement!r} в журнале нет"
    return uuid.UUID(str(rows[0]["id"]))


async def _link_ids(scratch_url: str, statement: str) -> dict[str, list[uuid.UUID]]:
    """id записанных связей утверждения по адресу страницы (det. порядок по id улики)."""
    rows = await _rows(
        scratch_url,
        "SELECT e.id AS id, s.canonical_uri AS uri FROM evidence e "
        "JOIN sources s ON s.id = e.source_id JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = :s ORDER BY e.id",
        {"s": statement},
    )
    out: dict[str, list[uuid.UUID]] = {}
    for row in rows:
        out.setdefault(str(row["uri"]), []).append(uuid.UUID(str(row["id"])))
    return out


async def _evidence_fingerprint(scratch_url: str) -> list[tuple[Any, ...]]:
    rows = await _rows(scratch_url, EVIDENCE_FINGERPRINT_SQL)
    return [tuple(str(value) for value in row.values()) for row in rows]


async def _counts(scratch_url: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNT_TABLES_T7125:
        rows = await _rows(scratch_url, f"SELECT count(*) AS n FROM {table}")
        counts[table] = int(rows[0]["n"])
    return counts


async def _head(scratch_url: str, statement: str) -> dict[str, Any]:
    rows = await _rows(
        scratch_url,
        "SELECT h.assessment_state, h.current_assessment_id, h.epistemic_status, h.prepared_by "
        "FROM claim_assessment_heads h JOIN claims c ON c.id = h.claim_id WHERE c.statement = :s",
        {"s": statement},
    )
    assert len(rows) == 1, rows
    return dict(rows[0])


async def _revocation_rows(scratch_url: str) -> list[dict[str, Any]]:
    rows = await _rows(
        scratch_url,
        "SELECT id, evidence_id, claim_id, reason, actor, rules_version, basis, cancelled_at, "
        "cancelled_by, cancel_reason FROM evidence_link_revocations ORDER BY evidence_id",
    )
    return [dict(row) for row in rows]


async def _audit_events(scratch_url: str, event_type: str) -> list[dict[str, Any]]:
    rows = await _rows(
        scratch_url,
        "SELECT type, actor, public_summary, payload FROM audit_events WHERE type = :t ORDER BY sequence",
        {"t": event_type},
    )
    return [dict(row) for row in rows]


async def _assessment_evidence_ids(scratch_url: str, assessment_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await _rows(
        scratch_url,
        "SELECT evidence_id FROM assessment_evidence WHERE assessment_id = :a",
        {"a": str(assessment_id)},
    )
    return {uuid.UUID(str(row["evidence_id"])) for row in rows}


async def _revoke(
    scratch_url: str,
    store: FilesystemArtifactStore,
    evidence_ids: list[uuid.UUID],
    reason: str,
    *,
    allow_empty: bool = False,
) -> Any:
    """Один вызов отзыва в одной транзакции — ровно так же, как его делает команда."""

    async def _work(db: Any) -> Any:
        async with transaction(db):
            return await apply_link_revocations(
                db,
                AuditService(db),
                store,
                evidence_ids=evidence_ids,
                reason=reason,
                actor=ACTOR_HOSTCTL,
                allow_empty=allow_empty,
            )

    return await _with_session(scratch_url, _work)


async def _cancel(scratch_url: str, revocation_ids: list[uuid.UUID], reason: str) -> Any:
    async def _work(db: Any) -> Any:
        async with transaction(db):
            return await cancel_link_revocations(
                db,
                AuditService(db),
                revocation_ids=revocation_ids,
                reason=reason,
                actor=ACTOR_HOSTCTL,
            )

    return await _with_session(scratch_url, _work)


async def _worker(scratch_url: str) -> Any:
    return await _with_session(scratch_url, lambda db: run_reassessment_batch(db))


async def _seed(scratch_url: str, store: FilesystemArtifactStore) -> dict[str, Any]:
    await _seed_replay(scratch_url, store)
    sbercib = (await _link_ids(scratch_url, STATEMENT_83))[URL_SBERCIB]
    hr = (await _link_ids(scratch_url, STATEMENT_83))[URL_HR]
    return {
        "claim_id": await _claim_id(scratch_url, STATEMENT_83),
        "sbercib_ids": sbercib,
        "hr_ids": hr,
        "revokable": [*sbercib],
    }


# ─── т1: отзыв названных связей + рабочий батч ────────────────────────────────────


async def test_apply_revokes_listed_links_and_worker_recomputes_without_them(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Было на стенде: E3 supported на двух группах независимости. Отозваны ровно две связи к
    странице ключевой ставки (обе «нет значения»), связь на hr-ratings не тронута. Рабочий батч
    пересчитывает оценку: E1 hypothesis, одна группа. Журнал улик не тронут нигде."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    claim_id = seeded["claim_id"]

    before = await _counts(scratch_url)
    evidence_before = await _evidence_fingerprint(scratch_url)
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E3", "supported")
    old_assessment = uuid.UUID(str((await _head(scratch_url, STATEMENT_83))["current_assessment_id"]))
    old_evidence = await _assessment_evidence_ids(scratch_url, old_assessment)
    assert len(old_evidence) == 3, old_evidence

    result = await _revoke(scratch_url, store, seeded["revokable"], REASON)

    assert len(result.written) == 2, result.written
    assert {write.evidence_id for write in result.written} == set(seeded["revokable"])
    assert {write.canonical_uri for write in result.written} == {URL_SBERCIB}
    assert {write.verdict for write in result.written} == {VERDICT_VALUE_ABSENT}
    assert all(write.reason == REASON for write in result.written)
    assert result.already_revoked == () and result.heads_to_invalid == ()
    assert result.claims_without_source_links == ()
    assert result.claims_affected == (claim_id,)
    assert (result.heads_invalidated, result.jobs_created) == (1, 1)

    # запись отзыва: строка `evidence_link_revocations` с причиной, актором и basis проверки
    stored = await _revocation_rows(scratch_url)
    assert len(stored) == 2, stored
    for row in stored:
        assert row["reason"] == REASON and row["actor"] == ACTOR_HOSTCTL, row
        assert row["cancelled_at"] is None and row["cancelled_by"] is None, row
        basis = row["basis"]
        assert basis["verdict"] == VERDICT_VALUE_ABSENT, basis
        assert "link-audit --apply" in basis["method"], basis
        assert basis["page_sha256"] and basis["config_snapshot_sha256"], basis
        assert basis["relation"] == "supports" and basis["evidence_kind"] == "source_assertion", basis

    # улики побайтово прежние: отзыв не UPDATE и не DELETE журнала
    assert await _evidence_fingerprint(scratch_url) == evidence_before, "журнал улик изменён"

    # голова снята с действующей оценки (§3 Lifecycle: pending ⇒ оба признака NULL)
    head = await _head(scratch_url, STATEMENT_83)
    assert head["assessment_state"] == "pending", head
    assert head["current_assessment_id"] is None and head["epistemic_status"] is None, head
    assert head["prepared_by"] == REVOCATION_PREPARED_BY, head

    # ровно одна durable-задача переоценки на (утверждение, снимок)
    jobs = await _rows(
        scratch_url,
        "SELECT reason, status FROM reassessment_jobs WHERE claim_id = :c",
        {"c": str(claim_id)},
    )
    assert len(jobs) == 1 and jobs[0]["reason"] == REVOCATION_JOB_REASON, jobs
    assert jobs[0]["status"] == "queued", jobs

    # аудит в той же транзакции, что запись
    events = await _audit_events(scratch_url, "link_revocation_created")
    assert len(events) == 2 and all(event["actor"] == ACTOR_HOSTCTL for event in events), events
    assert all(REASON in str(event["public_summary"]) for event in events), events

    # журнал прежней оценки не переписан: он остаётся историей со всеми тремя уликами
    assert await _assessment_evidence_ids(scratch_url, old_assessment) == old_evidence

    after_apply = await _counts(scratch_url)
    assert after_apply["evidence"] == before["evidence"], (before, after_apply)
    assert after_apply["evidence_link_revocations"] == 2, after_apply

    outcome = await _worker(scratch_url)
    assert (outcome.processed, outcome.completed, outcome.blocked, outcome.deferred) == (1, 1, 0, 0), outcome

    # оценки и группы пересчитаны rules engine'ом без отозванных связей
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E1", "hypothesis")
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_HR}
    head = await _head(scratch_url, STATEMENT_83)
    assert head["assessment_state"] == "current", head
    new_assessment = uuid.UUID(str(head["current_assessment_id"]))
    new_evidence = await _assessment_evidence_ids(scratch_url, new_assessment)
    assert new_assessment != old_assessment
    assert set(seeded["revokable"]).isdisjoint(new_evidence), new_evidence
    assert new_evidence & set(seeded["hr_ids"]) == set(seeded["hr_ids"]), new_evidence

    jobs = await _rows(scratch_url, "SELECT status FROM reassessment_jobs WHERE claim_id = :c", {"c": str(claim_id)})
    assert [row["status"] for row in jobs] == ["completed"], jobs


# ─── т2: связь со значением отзыву не подлежит ────────────────────────────────────


async def test_link_that_carries_the_value_cannot_be_revoked(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Страница hr-ratings называет значение утверждения целым токеном — та же проверка даёт
    «значение есть», и отзыв этой связи отказывает с названным исходом. Ничего не записано."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    before = await _counts(scratch_url)
    evidence_before = await _evidence_fingerprint(scratch_url)

    with pytest.raises(LinkRevocationError) as raised:
        await _revoke(scratch_url, store, seeded["hr_ids"], "почему-то захотелось снять связь")

    refusals = raised.value.refusals
    assert len(refusals) == 1, refusals
    assert refusals[0].code == REFUSAL_VALUE_PRESENT, refusals
    assert refusals[0].canonical_uri == URL_HR, refusals
    assert "значение" in refusals[0].detail and "нет значения" in refusals[0].detail, refusals

    assert await _revocation_rows(scratch_url) == []
    assert await _evidence_fingerprint(scratch_url) == evidence_before
    assert await _counts(scratch_url) == before, (before, await _counts(scratch_url))
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E3", "supported")
    assert (await _head(scratch_url, STATEMENT_83))["prepared_by"] != REVOCATION_PREPARED_BY


# ─── т3: отказ относится ко всему вызову ──────────────────────────────────────────


async def test_one_refused_link_cancels_the_whole_call(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """В списке — и законная связь, и та, что проходит проверку: вызов отказывает целиком,
    законная связь тоже НЕ отозвана (частичного отзыва нет), голова и журнал прежние."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    before = await _counts(scratch_url)
    mixed = [*seeded["revokable"], *seeded["hr_ids"]]

    with pytest.raises(LinkRevocationError) as raised:
        await _revoke(scratch_url, store, mixed, REASON)

    assert "1 из 3" in str(raised.value), str(raised.value)
    assert [refusal.code for refusal in raised.value.refusals] == [REFUSAL_VALUE_PRESENT]
    assert {refusal.evidence_id for refusal in raised.value.refusals} == set(seeded["hr_ids"])

    assert await _revocation_rows(scratch_url) == []
    assert await _counts(scratch_url) == before, (before, await _counts(scratch_url))
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E3", "supported")


async def test_unknown_evidence_id_is_named_and_refuses_the_call(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """id улики, которого в журнале нет, назван отдельным отказом; записей нет ни одной."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    before = await _counts(scratch_url)
    missing = uuid.uuid4()

    with pytest.raises(LinkRevocationError) as raised:
        await _revoke(scratch_url, store, [*seeded["revokable"], missing], REASON)

    codes = {refusal.code for refusal in raised.value.refusals}
    assert codes == {REFUSAL_UNKNOWN_EVIDENCE}, raised.value.refusals
    assert {refusal.evidence_id for refusal in raised.value.refusals} == {missing}
    assert await _counts(scratch_url) == before, (before, await _counts(scratch_url))


# ─── т4: коррекция оператора не отменяется ────────────────────────────────────────


async def test_revocation_under_operator_correction_is_refused(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Действующая коррекция оператора касается источника связи: автоматический отзыв не
    перешагивает решение оператора — отказ с названной причиной, записей нет (ADR-0031)."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    sources = await _rows(
        scratch_url, "SELECT id, canonical_uri FROM sources WHERE canonical_uri IN (:a, :b)",
        {"a": URL_SBERCIB, "b": URL_HR},
    )
    by_uri = {str(row["canonical_uri"]): uuid.UUID(str(row["id"])) for row in sources}
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO source_graph_corrections "
                    "(id, actor, kind, from_source_id, to_source_id, rules_version, valid) "
                    "VALUES (:id, :a, 'merge', :f, :t, 'rules-v1', true)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "a": ACTOR_HOSTCTL,
                    "f": str(by_uri[URL_SBERCIB]),
                    "t": str(by_uri[URL_HR]),
                },
            )
    finally:
        await engine.dispose()

    before = await _counts(scratch_url)
    with pytest.raises(LinkRevocationError) as raised:
        await _revoke(scratch_url, store, seeded["revokable"], REASON)

    codes = {refusal.code for refusal in raised.value.refusals}
    assert codes == {REFUSAL_OPERATOR_CORRECTION}, raised.value.refusals
    assert {refusal.canonical_uri for refusal in raised.value.refusals} == {URL_SBERCIB}
    assert await _revocation_rows(scratch_url) == []
    assert await _counts(scratch_url) == before, (before, await _counts(scratch_url))
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E3", "supported")


# ─── т5: повтор ничего не пишет ───────────────────────────────────────────────────


async def test_repeated_revocation_reports_already_revoked_and_writes_nothing(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Тот же список улик второй раз: записанных отзывов не прибавилось, повтор называет
    существующую запись (id, актор, причина) и не создаёт ни задач, ни голов."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)

    first = await _revoke(scratch_url, store, seeded["revokable"], REASON)
    stored = await _revocation_rows(scratch_url)
    between = await _counts(scratch_url)

    again = await _revoke(scratch_url, store, seeded["revokable"], REASON)
    assert again.written == (), again.written
    assert len(again.already_revoked) == 2, again.already_revoked
    assert {item.evidence_id for item in again.already_revoked} == set(seeded["revokable"])
    assert all(item.reason == REASON and item.actor == ACTOR_HOSTCTL for item in again.already_revoked)
    assert {item.revocation_id for item in again.already_revoked} == {row["id"] for row in stored}
    assert (again.claims_affected, again.heads_invalidated, again.jobs_created) == ((), 0, 0)

    after = await _counts(scratch_url)
    assert after == between, (between, after)
    assert after["evidence_link_revocations"] == 2, after
    assert len(await _audit_events(scratch_url, "link_revocation_created")) == 2
    assert len(first.written) == 2


# ─── т6: план показывает отозванные связи и не пересчитывает их ───────────────────


async def test_plan_names_already_revoked_links_without_recounting_them(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """После отзыва и рабочего батча read-only план по-прежнему перечисляет отозванные связи —
    с id отзыва, актором и причиной — но его пересчёт «стало» совпадает с записанным: групп и
    оценки менять нечего, и ни одной строки план не пишет."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    await _revoke(scratch_url, store, seeded["revokable"], REASON)
    await _worker(scratch_url)

    before = await _counts(scratch_url)
    report = await _audit(scratch_url, store)
    row = _row_of(report, STATEMENT_83)

    assert row.revoked_links and len(row.revoked_links) == 2, row.links
    assert {link.evidence_id for link in row.revoked_links} == set(seeded["revokable"])
    assert all(link.revoked and link.revocation_reason == REASON for link in row.revoked_links)
    assert all(link.revocation_actor == ACTOR_HOSTCTL for link in row.revoked_links)
    assert report.links_revoked == 2, report
    # отозванные связи названы тем же исходом гейта, но их снятие уже учтено записью
    assert {link.verdict for link in row.revoked_links} == {VERDICT_VALUE_ABSENT}
    assert row.groups_changed is False, (row.old_groups, row.new_groups)
    assert row.old_groups == row.new_groups, (row.old_groups, row.new_groups)
    assert (row.persisted_grade, row.persisted_status) == ("E1", "hypothesis")
    assert (row.new_grade, row.new_status) == ("E1", "hypothesis")
    assert row.assessment_changed is False
    # связь со значением по-прежнему вне снимаемых
    assert URL_HR not in row.excluded_source_uris, row.excluded_source_uris

    after = await _counts(scratch_url)
    assert after == before, (before, after)


# ─── т8: отзыв последней улики ────────────────────────────────────────────────────


async def test_revoking_the_last_link_needs_a_separate_decision(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Утверждение с единственной связью («нет значения»): без `--allow-empty` отзыв отказывает,
    потому что улик не останется вовсе. С `--allow-empty` он записан, и рабочий батч по §5.9.1
    переводит голову в invalid и ставит исследовательский вопрос."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())
    envs = await _fetch_sources(scratch_url, store, (URL_SBERCIB, URL_HR))
    await _apply(scratch_url, [_record(envs[1], "chunk-0")], STATEMENT_LONE)
    claim_id = await _claim_id(scratch_url, STATEMENT_LONE)
    lone = (await _link_ids(scratch_url, STATEMENT_LONE))[URL_HR]

    before = await _counts(scratch_url)
    with pytest.raises(LinkRevocationError) as raised:
        await _revoke(scratch_url, store, lone, "значения 21,7 на странице нет")

    assert [refusal.code for refusal in raised.value.refusals] == [REFUSAL_LAST_EVIDENCE]
    assert "--allow-empty" in str(raised.value), str(raised.value)
    assert await _counts(scratch_url) == before, (before, await _counts(scratch_url))

    result = await _revoke(scratch_url, store, lone, "значения 21,7 на странице нет", allow_empty=True)
    assert len(result.written) == 1 and result.heads_to_invalid == (claim_id,)
    assert (result.heads_invalidated, result.jobs_created) == (1, 1)
    assert (await _revocation_rows(scratch_url))[0]["evidence_id"] == lone[0]

    outcome = await _worker(scratch_url)
    assert outcome.processed == 1 and outcome.completed == 1, outcome

    head = await _head(scratch_url, STATEMENT_LONE)
    assert head["assessment_state"] == "invalid", head
    assert head["current_assessment_id"] is None and head["epistemic_status"] is None, head

    questions = await _rows(
        scratch_url,
        "SELECT origin, text FROM questions WHERE origin_config_snapshot_id IS NOT NULL",
    )
    assert len(questions) == 1 and questions[0]["origin"] == QuestionOrigin.INVALID_ASSESSMENT.value, questions
    assert STATEMENT_LONE in str(questions[0]["text"]), questions

    finished = await _rows(
        scratch_url,
        "SELECT status FROM reassessment_jobs WHERE claim_id = :c",
        {"c": str(claim_id)},
    )
    assert [row["status"] for row in finished] == ["completed"], finished
    completions = await _audit_events(scratch_url, "reassessment_job_completed")
    assert len(completions) == 1 and completions[0]["payload"]["outcome"] == "insufficient_data", completions

    # улика осталась в журнале: invalid — не «стёрто»
    rows = await _rows(scratch_url, "SELECT id FROM evidence WHERE claim_id = :c", {"c": str(claim_id)})
    assert [uuid.UUID(str(row["id"])) for row in rows] == lone, rows


# ─── т9: отозванная связь вне входа группировщика ────────────────────────────────


async def test_revoked_link_leaves_the_grouping_input(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """`load_source_graph_assembly` — единственный сборщик ВХОДА §11.3 (его вызывают и рабочий
    каскад, и предпросмотр): после отзыва источник отозванной связи больше не участник.
    Отмена отзыва возвращает участника. Пороги и алгоритм группировки не тронуты."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    claim_id = seeded["claim_id"]
    sources = {
        str(row["canonical_uri"]): uuid.UUID(str(row["id"]))
        for row in await _rows(
            scratch_url, "SELECT id, canonical_uri FROM sources WHERE canonical_uri IN (:a, :b)",
            {"a": URL_SBERCIB, "b": URL_HR},
        )
    }

    async def _members() -> set[uuid.UUID]:
        assembly = await _with_session(
            scratch_url, lambda db: load_source_graph_assembly(db, claim_id=claim_id)
        )
        assert assembly is not None
        return {uuid.UUID(str(sid)) for sid in assembly.member_ids}

    assert await _members() == {sources[URL_SBERCIB], sources[URL_HR]}

    result = await _revoke(scratch_url, store, seeded["revokable"], REASON)
    assert await _members() == {sources[URL_HR]}

    cancelled = await _cancel(scratch_url, [write.revocation_id for write in result.written], CANCEL_REASON)
    assert len(cancelled.cancelled) == 2 and cancelled.claims_affected == (claim_id,)
    # каскад снимает только действующую голову: здесь она уже pending из шага отзыва, а задача
    # ещё не выполнена — второй задачи на (утверждение, снимок) partial unique не заводит
    assert (cancelled.heads_invalidated, cancelled.jobs_created) == (0, 0), cancelled
    assert await _members() == {sources[URL_SBERCIB], sources[URL_HR]}

    outcome = await _worker(scratch_url)
    assert (outcome.processed, outcome.completed) == (1, 1), outcome
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E3", "supported")
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_SBERCIB, URL_HR}

    # ни одной записи об уликах за всю проверку
    rows = await _rows(scratch_url, EVIDENCE_FINGERPRINT_SQL)
    assert len(rows) == 3, rows


# ─── отмена отзыва: связь возвращается в оценку ───────────────────────────────────


async def test_cancel_returns_the_link_and_worker_restores_the_recorded_grade(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Отмена отзыва — вторая append-запись (колонки отмены), история не правится. Рабочий батч
    пересчитывает оценку: E1 hypothesis → E3 supported, обе группы независимости возвращаются."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)

    result = await _revoke(scratch_url, store, seeded["revokable"], REASON)
    await _worker(scratch_url)
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E1", "hypothesis")
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_HR}

    revocation_ids = [write.revocation_id for write in result.written]
    cancel = await _cancel(scratch_url, revocation_ids, CANCEL_REASON)
    assert list(cancel.cancelled) == revocation_ids
    assert cancel.already_cancelled == () and cancel.claims_affected == (seeded["claim_id"],)
    assert (cancel.heads_invalidated, cancel.jobs_created) == (1, 1)

    outcome = await _worker(scratch_url)
    assert (outcome.processed, outcome.completed) == (1, 1), outcome
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E3", "supported")
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_SBERCIB, URL_HR}

    stored = await _revocation_rows(scratch_url)
    assert len(stored) == 2, stored  # строки отзывов остались навсегда
    for row in stored:
        assert row["cancelled_at"] is not None, row
        assert row["cancelled_by"] == ACTOR_HOSTCTL and row["cancel_reason"] == CANCEL_REASON, row
        assert row["reason"] == REASON, row  # прежняя причина отзыва не переписана

    events = await _audit_events(scratch_url, "link_revocation_cancelled")
    assert len(events) == 2 and all(CANCEL_REASON in str(event["public_summary"]) for event in events), events

    again = await _cancel(scratch_url, revocation_ids, CANCEL_REASON)
    assert again.cancelled == () and set(again.already_cancelled) == set(revocation_ids), again
    assert (again.claims_affected, again.jobs_created) == ((), 0), again
    assert len(await _audit_events(scratch_url, "link_revocation_cancelled")) == 2


# ─── т7: CLI обеих команд ─────────────────────────────────────────────────────────


def _text(result: Any) -> str:
    """Вывод команды целиком: отказ CLI печатает в stderr."""
    parts = [result.output]
    with contextlib.suppress(ValueError, AttributeError):
        parts.append(result.stderr)
    return "\n".join(parts)


def _with_db(url: str, fn: Callable[[Any], Any]) -> Any:
    async def _main() -> Any:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


def test_cli_apply_refusals_name_the_check_and_write_nothing(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Отказ по параметрам и отказ по проверкам: код 2, причина названа, ни одной записи нет."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = asyncio.run(_seed(scratch_url, store))
    before = asyncio.run(_counts(scratch_url))
    runner = CliRunner()
    env = {"NOEZEMA_DATABASE_URL": scratch_url, "NOEZEMA_DATA_ROOT": str(tmp_path)}

    without_evidence = runner.invoke(main, ["link-audit", "--apply"], env=env)
    assert without_evidence.exit_code == EXIT_USAGE_ERROR, _text(without_evidence)
    assert "--evidence" in _text(without_evidence)

    without_reason = runner.invoke(
        main, ["link-audit", "--apply", "--evidence", str(seeded["revokable"][0])], env=env
    )
    assert without_reason.exit_code == EXIT_USAGE_ERROR, _text(without_reason)
    assert "--reason" in _text(without_reason)

    evidence_without_apply = runner.invoke(
        main, ["link-audit", "--evidence", str(seeded["revokable"][0])], env=env
    )
    assert evidence_without_apply.exit_code == EXIT_USAGE_ERROR, _text(evidence_without_apply)
    assert "--apply" in _text(evidence_without_apply)

    bad_uuid = runner.invoke(main, ["link-audit", "--apply", "--evidence", "не-uuid"], env=env)
    assert bad_uuid.exit_code == EXIT_USAGE_ERROR and "--evidence" in _text(bad_uuid)

    carries_value = runner.invoke(
        main,
        ["link-audit", "--apply", "--evidence", str(seeded["hr_ids"][0]), "--reason", REASON],
        env=env,
    )
    assert carries_value.exit_code == EXIT_USAGE_ERROR, _text(carries_value)
    assert REFUSAL_VALUE_PRESENT in _text(carries_value)

    unknown = runner.invoke(
        main,
        ["link-audit", "--apply", "--evidence", str(uuid.uuid4()), "--reason", REASON],
        env=env,
    )
    assert unknown.exit_code == EXIT_USAGE_ERROR, _text(unknown)
    assert "ни одной из перечисленных улик" in _text(unknown)

    assert asyncio.run(_counts(scratch_url)) == before, (before, asyncio.run(_counts(scratch_url)))
    assert asyncio.run(_revocation_rows(scratch_url)) == []


def test_cli_apply_writes_revocations_runs_worker_and_marks_the_plan(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """`link-audit --apply`: сначала план (тот же read-only пересчёт), затем запись отзывов +
    каскад + один рабочий батч + состояние после батча. Повтор команды — «уже отозвана»."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = asyncio.run(_seed(scratch_url, store))
    before = asyncio.run(_counts(scratch_url))
    runner = CliRunner()
    env = {"NOEZEMA_DATABASE_URL": scratch_url, "NOEZEMA_DATA_ROOT": str(tmp_path)}

    listed = ",".join(str(evidence_id) for evidence_id in seeded["revokable"])
    applied = runner.invoke(
        main, ["link-audit", "--apply", "--evidence", listed, "--reason", REASON], env=env
    )
    out = _text(applied)
    assert applied.exit_code == 0, out
    assert "link-audit --apply: план перед записью" in out
    assert "записан отзыв" in out and REASON in out
    assert "каскад переоценки: утверждений затронуто: 1; снятых голов: 1; задач переоценки заведено: 1" in out
    assert REVOCATION_JOB_REASON in out and REVOCATION_PREPARED_BY in out
    assert "рабочий переоценки: processed=1 completed=1" in out
    assert "link-audit --apply: состояние после рабочего батча" in out

    assert asyncio.run(_assessment_of(scratch_url, STATEMENT_83)) == ("E1", "hypothesis")
    assert asyncio.run(_group_uris(scratch_url, STATEMENT_83)) == {URL_HR}
    after = asyncio.run(_counts(scratch_url))
    assert after["evidence"] == before["evidence"], (before, after)
    assert after["evidence_link_revocations"] == 2, after

    again = runner.invoke(main, ["link-audit", "--apply", "--evidence", listed, "--reason", REASON], env=env)
    out_again = _text(again)
    assert again.exit_code == 0, out_again
    assert "связь уже отозвана" in out_again and "повтор ничего не пишет" in out_again
    assert asyncio.run(_counts(scratch_url)) == after, (after, asyncio.run(_counts(scratch_url)))

    plan = runner.invoke(main, ["link-audit"], env=env)
    out_plan = _text(plan)
    assert plan.exit_code == 0, out_plan
    assert "связь отозвана оператором" in out_plan and REASON in out_plan
    assert "уже отозвано связей: 2" in out_plan
    assert "запись не выполнена: это план" in out_plan
    assert asyncio.run(_counts(scratch_url)) == after, (after, asyncio.run(_counts(scratch_url)))


def test_cli_cancel_returns_links_and_reassessment_restores_the_grade(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """`link-audit-cancel --revocation <id,…> --reason "<текст>"`: отмена записана, каскад и
    рабочий батч выполнены, оценка восстановлена. Отмена без `--reason` — отказ 2."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = asyncio.run(_seed(scratch_url, store))
    runner = CliRunner()
    env = {"NOEZEMA_DATABASE_URL": scratch_url, "NOEZEMA_DATA_ROOT": str(tmp_path)}

    listed = ",".join(str(evidence_id) for evidence_id in seeded["revokable"])
    applied = runner.invoke(main, ["link-audit", "--apply", "--evidence", listed, "--reason", REASON], env=env)
    assert applied.exit_code == 0, _text(applied)
    stored = asyncio.run(_revocation_rows(scratch_url))
    revocations = ",".join(str(row["id"]) for row in stored)

    without_reason = runner.invoke(main, ["link-audit-cancel", "--revocation", revocations], env=env)
    assert without_reason.exit_code == EXIT_USAGE_ERROR, _text(without_reason)
    assert "--reason" in _text(without_reason)
    assert all(row["cancelled_at"] is None for row in asyncio.run(_revocation_rows(scratch_url)))

    cancelled = runner.invoke(
        main, ["link-audit-cancel", "--revocation", revocations, "--reason", CANCEL_REASON], env=env
    )
    out = _text(cancelled)
    assert cancelled.exit_code == 0, out
    assert "отменён отзыв" in out and CANCEL_REASON in out
    assert "связь снова участвует в оценке" in out
    assert "рабочий переоценки: processed=1 completed=1" in out

    assert asyncio.run(_assessment_of(scratch_url, STATEMENT_83)) == ("E3", "supported")
    assert asyncio.run(_group_uris(scratch_url, STATEMENT_83)) == {URL_SBERCIB, URL_HR}
    stored_after = asyncio.run(_revocation_rows(scratch_url))
    assert len(stored_after) == 2 and all(row["cancelled_at"] is not None for row in stored_after)

    again = runner.invoke(
        main, ["link-audit-cancel", "--revocation", revocations, "--reason", CANCEL_REASON], env=env
    )
    out_again = _text(again)
    assert again.exit_code == 0, out_again
    assert "уже отменён — повтор ничего не пишет" in out_again

    unknown = runner.invoke(
        main, ["link-audit-cancel", "--revocation", str(uuid.uuid4()), "--reason", CANCEL_REASON], env=env
    )
    assert unknown.exit_code == EXIT_USAGE_ERROR and "отзыва связи" in _text(unknown)

    assert asyncio.run(_assessment_of(scratch_url, STATEMENT_83)) == ("E3", "supported")
