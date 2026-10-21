"""Scenario (DB): спор по адресу одной страницы в нескольких прочтениях (T7.129 часть 4).

Стенд `.92`: утверждение a7cac406 («По данным hh.ru, hh-индекс в сентябре 2026 года составил
8,5») опирается на три улики одной страницы smart-lab.ru/company/mozgovik/blog/1359553.php —
это три СТРОКИ `sources` с одним адресом (два прочтения http и одно https, разные
`content_hash`). Оператор пытался оспорить эту страницу и получал отказ
`dispute_source_not_found` с detail «(неоднозначно)»: роль обязана была быть одной строкой.
По новому правилу все строки адреса — одна страница, и коррекция заводится на каждую.

Что закреплено тестом:
- на 686d1e8 этот же вызов — отказ «неоднозначно», красность доказана detached worktree;
- после исправления — по одной действующей коррекции merge на каждую строку адреса, каскад
  §11.3 снимает действующую оценку (голова уходит в pending), вопрос заведён один;
- повтор тем же оператором идемпотентен: ни одной новой коррекции, `replayed=True`;
- отмена снимает ВСЕ коррекции этого спора, а не одну (иначе «отозванный» спор продолжал бы
  склеивать остальные версии страницы); строки не удаляются.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from packages.domain.db.uow import transaction
from packages.domain.services.audit import AuditService
from packages.memory.dispute import (
    ACTOR_HOSTCTL,
    REFUSAL_SOURCE_NOT_FOUND,
    DisputeError,
    cancel_dispute,
    dispute_claim,
)
from tests.scenario.test_claim_dispute import (
    PRIMARY_URI,
    _corrections,
    _head,
    _scalar,
    _seed_contested_claim,
    _source_evidence,
)

pytestmark = [pytest.mark.scenario]

REASON = "Страница пересказа прочитана узлом трижды; это одна страница, а не три источника"

# три прочтения ОДНОЙ страницы: та же идентичность адреса (хост+путь+query), разные схемы
# и разные `content_hash` — как у smart-lab 1359553 на стенде
PAGE_HTTP = "http://sbercib.example/economy/pochemu-inflyatsiya-5-6"
PAGE_HTTPS = "https://sbercib.example/economy/pochemu-inflyatsiya-5-6"


async def _extra_reads(
    engine: AsyncEngine, claim_id: uuid.UUID, source_ids: list[uuid.UUID]
) -> None:
    """Два дополнительных прочтения той же страницы: новые строки `sources` и новые улики."""
    from tests.scenario.test_source_graph import _host_source

    for index, uri in enumerate((PAGE_HTTP, PAGE_HTTPS)):
        source_id = await _host_source(engine, uri=uri, content_hash=f"hash-smart-lab-{index}")
        source_ids.append(source_id)
        await _source_evidence(engine, claim_id, source_id, tag=f"extra-{index}")


async def _dispute_direct(scratch_url: str, claim_id: uuid.UUID, *, retelling_uri: str) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db, transaction(db):
            return await dispute_claim(
                db,
                AuditService(db),
                claim_id=claim_id,
                primary_uri=PRIMARY_URI,
                retelling_uri=retelling_uri,
                reason=REASON,
                actor=ACTOR_HOSTCTL,
            )
    finally:
        await engine.dispose()


async def _cancel_direct(scratch_url: str, claim_id: uuid.UUID) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db, transaction(db):
            return await cancel_dispute(db, AuditService(db), claim_id=claim_id, actor=ACTOR_HOSTCTL)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispute_of_a_multi_read_page_writes_one_correction_per_row(
    migrated_db: Any,
) -> None:
    scratch_url, engine = migrated_db
    seeded = await _seed_contested_claim(engine)
    claim_id: uuid.UUID = seeded["claim_id"]
    rows: list[uuid.UUID] = [seeded["retold"]]
    await _extra_reads(engine, claim_id, rows)

    outcome = await _dispute_direct(scratch_url, claim_id, retelling_uri=PAGE_HTTPS)

    assert not outcome.replayed
    assert len(outcome.correction_ids) == 3
    assert outcome.retelling_source_ids == frozenset(rows)
    stored = await _corrections(engine, claim_id)
    valid = [row for row in stored if row[2]]
    assert len(valid) == 3, stored
    assert {row[6] for row in valid} == set(rows)  # from_source_id: каждая версия страницы
    assert {row[7] for row in valid} == {seeded["primary"]}
    head = await _head(engine, claim_id)
    assert head is not None and head[0] == "pending", head  # каскад §11.3 снял оценку
    questions = await _scalar(engine, "SELECT count(*) FROM questions")
    assert questions[0] == 1


@pytest.mark.asyncio
async def test_repeat_of_a_multi_row_dispute_is_a_replay(migrated_db: Any) -> None:
    scratch_url, engine = migrated_db
    seeded = await _seed_contested_claim(engine)
    claim_id: uuid.UUID = seeded["claim_id"]
    rows: list[uuid.UUID] = [seeded["retold"]]
    await _extra_reads(engine, claim_id, rows)

    first = await _dispute_direct(scratch_url, claim_id, retelling_uri=PAGE_HTTPS)
    second = await _dispute_direct(scratch_url, claim_id, retelling_uri=PAGE_HTTP)

    assert second.replayed
    assert set(second.correction_ids) == set(first.correction_ids)
    stored = await _corrections(engine, claim_id)
    assert len([row for row in stored if row[2]]) == 3, stored


@pytest.mark.asyncio
async def test_cancel_removes_every_correction_of_the_page_dispute(migrated_db: Any) -> None:
    scratch_url, engine = migrated_db
    seeded = await _seed_contested_claim(engine)
    claim_id: uuid.UUID = seeded["claim_id"]
    rows: list[uuid.UUID] = [seeded["retold"]]
    await _extra_reads(engine, claim_id, rows)
    await _dispute_direct(scratch_url, claim_id, retelling_uri=PAGE_HTTPS)

    outcome = await _cancel_direct(scratch_url, claim_id)

    assert len(outcome.correction_ids) == 3
    stored = await _corrections(engine, claim_id)
    assert len(stored) == 3 and all(not row[2] for row in stored), stored


@pytest.mark.asyncio
async def test_absent_page_address_keeps_the_old_refusal(migrated_db: Any) -> None:
    """Страницы нет среди улик — прежний честный отказ; строк-двойников он не «размывает»."""
    scratch_url, engine = migrated_db
    seeded = await _seed_contested_claim(engine)
    claim_id: uuid.UUID = seeded["claim_id"]
    with pytest.raises(DisputeError) as exc:
        await _dispute_direct(
            scratch_url, claim_id, retelling_uri="https://absent.example/economy/ino-5-6"
        )
    assert exc.value.code == REFUSAL_SOURCE_NOT_FOUND
