"""Scenario (DB): миграция 0026 `evidence_link_revocations` — upgrade/downgrade/upgrade (T7.125).

Закреплено (постановка T7.125, пункт 2):

- ровно один новый долговременный объект (`evidence_link_revocations`) и ровно одна новая
  миграция; `downgrade` снимает таблицу и возвращает прежний CHECK `prepared_by`;
- `upgrade head` -> `downgrade -1` -> `upgrade head` на одноразовой scratch-БД даёт схему,
  побайтно совпадающую с первой (откат не оставляет следов, повторное применение работает);
- CHECK `claim_assessment_heads.prepared_by` после миграции принимает `system:link_revocation`
  (тот же путь расширения, что 0007/0011/0012) и после `downgrade` его отвергает;
- ограничения таблицы работают как заявлено: частичный unique «не больше одного действующего
  отзыва на улику», CHECK парности колонок отмены, CHECK непустой причины, `ON DELETE CASCADE`;
- `audit_events.type` — текст без CHECK (существующая схема): новые типы событий записываются
  без изменения схемы, поэтому второй миграции нет.

Ни одного внешнего запроса, ни одного LLM-вызова. Scratch-БД — клон session-шаблона (T7.55);
alembic вызывается подпроцессом по той же схеме, что `tests/unit/test_fixture_template_clone.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import REPO_ROOT

pytestmark = [pytest.mark.scenario]

TABLE = "evidence_link_revocations"
HEAD_REVISION = "0026_link_revocations"
REVOKATION_ACTOR = "operator:hostctl"


def _alembic(scratch_url: str, *argv: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["NOEZEMA_DATABASE_URL"] = scratch_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *argv],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


async def _rows(scratch_url: str, sql: str, params: dict | None = None) -> list[Any]:
    """Чтение одной строки запроса (отдельное соединение, dispose обязателен)."""
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).mappings().all())
    finally:
        await engine.dispose()


async def _write(scratch_url: str, sql: str, params: dict | None = None) -> list[Any]:
    """Запись в отдельной транзакции: нарушение ограничения поднимается наружу."""
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            if result.returns_rows:
                return list(result.mappings().all())
            return []
    finally:
        await engine.dispose()


async def _seed_pair(scratch_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Утверждение + его источная связь. Посев минимален, но ограничения `evidence` соблюдены:
    `source_assertion` требует источника и окна. Возвращает (claim_id, evidence_id)."""
    claim_id = uuid.uuid4()
    source_id = uuid.uuid4()
    await _write(
        scratch_url,
        "INSERT INTO claims (id, statement, claim_type) VALUES (:id, :s, 'external_fact')",
        {"id": str(claim_id), "s": f"Проверочное утверждение миграции T7.125 {claim_id}."},
    )
    await _write(
        scratch_url,
        "INSERT INTO sources (id, source_type, canonical_uri, metadata) "
        "VALUES (:id, 'external_url', :u, '{}'::jsonb)",
        {"id": str(source_id), "u": f"https://example.test/t7125/{source_id}"},
    )
    await _write(
        scratch_url,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, scope, source_id, chunk_id) "
        "VALUES (:id, :c, 'supports', 'source_assertion', :h, '{}'::jsonb, :s, 'chunk-0')",
        {
            "id": str(uuid.uuid4()),
            "c": str(claim_id),
            "h": f"t7125-{uuid.uuid4()}",
            "s": str(source_id),
        },
    )
    rows = await _rows(scratch_url, "SELECT id FROM evidence WHERE claim_id = :c", {"c": str(claim_id)})
    return claim_id, uuid.UUID(str(rows[0]["id"]))


async def _snapshot_id(scratch_url: str) -> uuid.UUID:
    rows = await _rows(scratch_url, "SELECT id FROM config_snapshots ORDER BY id LIMIT 1")
    assert rows, "миграции не оставили ни одного снимка конфигурации"
    return uuid.UUID(str(rows[0]["id"]))


INSERT_REVOCATION = (
    "INSERT INTO evidence_link_revocations (id, evidence_id, claim_id, reason, basis, actor, rules_version) "
    "VALUES (:id, :e, :c, :r, '{\"method\": \"миграционный тест T7.125\"}'::jsonb, :a, 'rules-v1')"
)


def _revocation_params(evidence_id: uuid.UUID, claim_id: uuid.UUID, reason: str) -> dict[str, str]:
    return {
        "id": str(uuid.uuid4()),
        "e": str(evidence_id),
        "c": str(claim_id),
        "r": reason,
        "a": REVOKATION_ACTOR,
    }


# ─── т8: up / down / up на одноразовой scratch-БД ─────────────────────────────────


async def test_migration_upgrades_downgrades_and_upgrades_again(migrated_db: tuple[str, Any]) -> None:
    """`upgrade head` (применён фикстурой) -> `downgrade -1` -> `upgrade head`: схема после
    повторного применения идентична первой; откат снимает таблицу целиком и ничего не оставляет."""

    scratch_url, _engine = migrated_db

    async def _fingerprint() -> dict[str, Any]:
            return {
                "revision": [
                    row["version_num"]
                    for row in await _rows(scratch_url, "SELECT version_num FROM alembic_version")
                ],
                "columns": [
                    dict(row)
                    for row in await _rows(
                        scratch_url,
                        "SELECT column_name, ordinal_position, data_type, is_nullable "
                        "FROM information_schema.columns WHERE table_schema='public' "
                        "AND table_name = :t ORDER BY ordinal_position",
                        {"t": TABLE},
                    )
                ],
                "indexes": [
                    row["indexname"]
                    for row in await _rows(
                        scratch_url,
                        "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename = :t "
                        "ORDER BY indexname",
                        {"t": TABLE},
                    )
                ],
                # только именованные CHECK: служебные not_null содержат oid таблицы
                # и потому меняются между применением и повторным применением той же миграции
                "checks": [
                    row["conname"]
                    for row in await _rows(
                        scratch_url,
                        "SELECT con.conname FROM pg_constraint con JOIN pg_class cls ON cls.oid = con.conrelid "
                        "JOIN pg_namespace ns ON ns.oid = cls.relnamespace "
                        "WHERE ns.nspname = 'public' AND cls.relname = :t AND con.contype = 'c' "
                        "AND con.conname NOT LIKE '%not_null' ORDER BY con.conname",
                        {"t": TABLE},
                    )
                ],
            }

    first = await _fingerprint()
    assert first["revision"] == [HEAD_REVISION], first["revision"]
    assert {column["column_name"] for column in first["columns"]} == {
        "id",
        "evidence_id",
        "claim_id",
        "reason",
        "basis",
        "actor",
        "rules_version",
        "created_at",
        "cancelled_at",
        "cancelled_by",
        "cancel_reason",
    }, first["columns"]
    assert "uq_link_revocations_active" in first["indexes"], first["indexes"]
    assert first["checks"] == [
        "evidence_link_revocations_check",
        "evidence_link_revocations_reason_check",
    ], first["checks"]

    down = _alembic(scratch_url, "downgrade", "-1")
    assert down.returncode == 0, down.stdout + down.stderr
    gone = await _fingerprint()
    assert gone["revision"] != [HEAD_REVISION], gone["revision"]
    assert gone["columns"] == [] and gone["indexes"] == [] and gone["checks"] == [], gone

    up = _alembic(scratch_url, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    again = await _fingerprint()
    assert again == first, (again, first)


async def test_prepared_by_check_accepts_system_link_revocation_only_after_migration(
    migrated_db: tuple[str, Any],
) -> None:
    """CHECK `prepared_by` расширен самой миграцией (тот же путь, что 0007/0011/0012): после
    `upgrade` значение `system:link_revocation` принимается, после `downgrade` — отвергается.

    Ограничение отката названо явно, потому что оно реальное: `downgrade` возвращает прежний CHECK
    и Postgres проверяет его по существующим строкам, поэтому голова, подготовленная отзывом
    связи, переживает откат только вместе с собой — миграция её не правит. Тест сначала показывает
    этот отказ, затем снимает голову и проходит откат дальше.
    """

    scratch_url, _engine = migrated_db
    snapshot_id = await _snapshot_id(scratch_url)

    async def _claim(statement: str) -> uuid.UUID:
        claim_id = uuid.uuid4()
        await _write(
            scratch_url,
            "INSERT INTO claims (id, statement, claim_type) VALUES (:id, :s, 'external_fact')",
            {"id": str(claim_id), "s": statement},
        )
        return claim_id

    insert_head = (
        "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, prepared_by) "
        "VALUES (:c, :s, 'pending', :p)"
    )

    live_claim = await _claim("Проверочное утверждение CHECK prepared_by.")
    written = await _write(
        scratch_url,
        insert_head + " RETURNING prepared_by",
        {"c": str(live_claim), "s": str(snapshot_id), "p": "system:link_revocation"},
    )
    assert written[0]["prepared_by"] == "system:link_revocation", written

    blocked = _alembic(scratch_url, "downgrade", "-1")
    assert blocked.returncode != 0, "откат при живой голове, подготовленной отзывом, не проходит молча"
    assert "claim_assessment_heads_prepared_by_check" in blocked.stdout + blocked.stderr, blocked.stderr
    version = (await _rows(scratch_url, "SELECT version_num FROM alembic_version"))[0]["version_num"]
    assert version == HEAD_REVISION, version

    removed = await _write(scratch_url, "DELETE FROM claim_assessment_heads RETURNING claim_id")
    assert len(removed) >= 1, removed

    down = _alembic(scratch_url, "downgrade", "-1")
    assert down.returncode == 0, down.stdout + down.stderr
    definition = (
        await _rows(
            scratch_url,
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conname = 'claim_assessment_heads_prepared_by_check'",
        )
    )[0]["def"]
    assert "system:link_revocation" not in definition, definition

    plain_claim = await _claim("Проверочное утверждение CHECK prepared_by после отката.")
    with pytest.raises(IntegrityError):
        await _write(
            scratch_url,
            insert_head,
            {"c": str(plain_claim), "s": str(snapshot_id), "p": "system:link_revocation"},
        )

    up = _alembic(scratch_url, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    again = await _write(
        scratch_url,
        insert_head + " RETURNING prepared_by",
        {"c": str(plain_claim), "s": str(snapshot_id), "p": "system:link_revocation"},
    )
    assert again[0]["prepared_by"] == "system:link_revocation", again

    # расширение не должно терять ни одного прежнего значения: CHECK до отката и после
    # повторного применения сравниваются множествами, а не «наличием нового значения»
    after = (
        await _rows(
            scratch_url,
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conname = 'claim_assessment_heads_prepared_by_check'",
        )
    )[0]["def"]
    old_values = set(re.findall(r"'([^']+)'", definition))
    new_values = set(re.findall(r"'([^']+)'", after))
    assert old_values <= new_values, (old_values - new_values)
    assert new_values - old_values == {"system:link_revocation"}, (new_values, old_values)
    for accepted in sorted(old_values):
        claim_for_value = await _claim(f"Проверочное утверждение prepared_by {accepted}.")
        row = await _write(
            scratch_url,
            insert_head + " RETURNING prepared_by",
            {"c": str(claim_for_value), "s": str(snapshot_id), "p": accepted},
        )
        assert row[0]["prepared_by"] == accepted, row


# ─── ограничения новой таблицы ────────────────────────────────────────────────────


async def test_partial_unique_allows_one_active_revocation_and_reopen_after_cancel(
    migrated_db: tuple[str, Any],
) -> None:
    """Не больше одного ДЕЙСТВУЮЩЕГО отзыва на улику (частичный unique по образцу 0012): второй
    активный — нарушение; после отмены отзыв разрешён снова (связь можно отозвать вторично),
    история при этом не правится: прежняя строка остаётся с колонками отмены."""

    scratch_url, _engine = migrated_db
    claim_id, evidence_id = await _seed_pair(scratch_url)

    await _write(scratch_url, INSERT_REVOCATION, _revocation_params(evidence_id, claim_id, "значения на странице нет"))
    with pytest.raises(IntegrityError):
        await _write(
            scratch_url,
            INSERT_REVOCATION,
            _revocation_params(evidence_id, claim_id, "вторая попытка того же отзыва"),
        )

    first = (await _rows(scratch_url, f"SELECT id FROM {TABLE} WHERE evidence_id = :e", {"e": str(evidence_id)}))[0]
    await _write(
        scratch_url,
        f"UPDATE {TABLE} SET cancelled_at = now(), cancelled_by = :a, cancel_reason = :r WHERE id = :id",
        {"id": str(first["id"]), "a": REVOKATION_ACTOR, "r": "связь возвращена в оценку"},
    )
    await _write(
        scratch_url,
        INSERT_REVOCATION,
        _revocation_params(evidence_id, claim_id, "повторный отзыв после отмены"),
    )
    rows = await _rows(scratch_url, f"SELECT cancelled_at FROM {TABLE} WHERE evidence_id = :e", {"e": str(evidence_id)})
    assert len(rows) == 2, rows
    assert sum(1 for row in rows if row["cancelled_at"] is None) == 1, rows


async def test_cancel_columns_and_reason_constraints_reject_invalid_rows(
    migrated_db: tuple[str, Any],
) -> None:
    """Колонки отмены живут только вместе (CHECK парности): `cancelled_at` без `cancelled_by` и
    `cancel_reason` — нарушение, как и причина отмены без отметки времени; причина отзыва обязана
    быть непустой: отзыв «без причины» записан быть не может."""

    scratch_url, _engine = migrated_db
    claim_id, evidence_id = await _seed_pair(scratch_url)
    revocation_id = uuid.uuid4()
    await _write(
        scratch_url,
        INSERT_REVOCATION,
        {**_revocation_params(evidence_id, claim_id, "значения на странице нет"), "id": str(revocation_id)},
    )

    with pytest.raises(IntegrityError):
        await _write(scratch_url, f"UPDATE {TABLE} SET cancelled_at = now() WHERE id = :id", {"id": str(revocation_id)})
    with pytest.raises(IntegrityError):
        await _write(
            scratch_url,
            f"UPDATE {TABLE} SET cancel_reason = :r WHERE id = :id",
            {"id": str(revocation_id), "r": "причина есть, отметки времени нет"},
        )

    with pytest.raises(IntegrityError):
        await _write(scratch_url, INSERT_REVOCATION, _revocation_params(evidence_id, claim_id, "   "))

    await _write(
        scratch_url,
        f"UPDATE {TABLE} SET cancelled_at = now(), cancelled_by = :a, cancel_reason = :r WHERE id = :id",
        {"id": str(revocation_id), "a": REVOKATION_ACTOR, "r": "связь возвращена в оценку"},
    )
    cancelled = (
        await _rows(
            scratch_url,
            f"SELECT cancelled_by, cancel_reason FROM {TABLE} WHERE id = :id",
            {"id": str(revocation_id)},
        )
    )[0]
    assert cancelled["cancelled_by"] == REVOKATION_ACTOR and cancelled["cancel_reason"], cancelled

    with pytest.raises(IntegrityError):
        await _write(
            scratch_url,
            f"UPDATE {TABLE} SET cancelled_at = now(), cancelled_by = :a, cancel_reason = '  ' WHERE id = :id",
            {"id": str(revocation_id), "a": REVOKATION_ACTOR},
        )


async def test_revocation_rows_follow_the_link_and_the_claim_on_delete(
    migrated_db: tuple[str, Any],
) -> None:
    """`ON DELETE CASCADE` по улике и по утверждению: строка отзыва — производная от связи,
    а не самостоятельный журнал. Каскад задевает ровно свою связь: чужие отзывы остаются.
    Сама миграция при этом ни одной строки `evidence` не удаляет и не обновляет."""

    scratch_url, _engine = migrated_db
    pairs = [await _seed_pair(scratch_url) for _ in range(3)]
    for claim_id, evidence_id in pairs:
        await _write(
            scratch_url,
            INSERT_REVOCATION,
            _revocation_params(evidence_id, claim_id, "значения на странице нет"),
        )
    assert len(await _rows(scratch_url, f"SELECT id FROM {TABLE}")) == 3

    await _write(scratch_url, "DELETE FROM evidence WHERE id = :e", {"e": str(pairs[0][1])})
    left = await _rows(scratch_url, f"SELECT evidence_id FROM {TABLE}")
    assert {uuid.UUID(str(row["evidence_id"])) for row in left} == {pairs[1][1], pairs[2][1]}, left

    await _write(scratch_url, "DELETE FROM claims WHERE id = :c", {"c": str(pairs[1][0])})
    left = await _rows(scratch_url, f"SELECT evidence_id FROM {TABLE}")
    assert {uuid.UUID(str(row["evidence_id"])) for row in left} == {pairs[2][1]}, left

    journal = await _rows(
        scratch_url, "SELECT relation, evidence_kind FROM evidence WHERE id = :e", {"e": str(pairs[2][1])}
    )
    assert len(journal) == 1 and journal[0]["relation"] == "supports", journal


async def test_new_audit_types_need_no_migration(migrated_db: tuple[str, Any]) -> None:
    """`audit_events.type` — текст без CHECK (существующая схема): новые типы отзывов записываются
    без изменения схемы, поэтому второй миграции нет и CHECK таблицы событий не появляется."""

    scratch_url, _engine = migrated_db
    checks = await _rows(
        scratch_url,
        "SELECT conname FROM pg_constraint WHERE conrelid = 'audit_events'::regclass AND contype = 'c'",
    )
    assert [row["conname"] for row in checks] == [], checks

    for event_type in ("link_revocation_created", "link_revocation_cancelled"):
        await _write(
            scratch_url,
            "INSERT INTO audit_events (id, sequence, type, actor, payload) "
            "VALUES (:id, :q, :t, :a, '{}'::jsonb)",
            {
                "id": str(uuid.uuid4()),
                "q": 900_001,
                "t": event_type,
                "a": REVOKATION_ACTOR,
            },
        )
    written = await _rows(
        scratch_url,
        "SELECT type FROM audit_events WHERE type IN ('link_revocation_created', 'link_revocation_cancelled') "
        "ORDER BY type",
    )
    assert [row["type"] for row in written] == [
        "link_revocation_cancelled",
        "link_revocation_created",
    ], written
