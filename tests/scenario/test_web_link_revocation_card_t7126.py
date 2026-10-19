"""Scenario (DB): карточка утверждения называет отозванную связь (остаток ADR-0039 §6, T7.126 часть в).

Решение пользователя 2026-10-22: «связь отозвана оператором» обязана быть видно на карточке —
строка «связь отозвана оператором: <причина> (отзыв <id>, <дата>)», и отозванная связь не
считается ни уликой, ни группой. Прежнее состояние витрины (ADR-0039 §6, пункт «остаток»):
карточка перечисляла все улики как факты журнала, а оценку брала из головы, которая отзыва
уже не содержала, — то есть показывала связь, которая ничего не подтверждает, без единого
слова о причинах.

Посев — тот же честный, что у T7.124/T7.125: стендовый случай `83e7a1c8` (утверждение про
hh-индекс с тремя записанными связями: две ведут на страницу ключевой ставки, где значения
нет, одна — на страницу со значением) через staging и `apply_claim_staging`; отзыв пишет
`apply_link_revocations`, пересчёт делает `run_reassessment_batch`. Сеть — FakeFetchClient
(§6), страницы — побайтовые нормализованные тексты стендовых страниц. Ни внешнего запроса,
ни LLM-вызова.

Что закреплено:
- т5а: до отзыва отзванных связей нет; «как проверено» утверждает два прочитанных источника.
- т5б: после отзыва и пересчёта карточка называет обе отозванные связи строкой с причиной,
  id записи отзыва и датой; сама строка улики остаётся в журнале и помечена отзывом;
  «как проверено» больше не считает отозванную связь источником подтверждения.
- т5в: «происхождение» (тот же JSON, что читает карточка) несёт те же строки и тот же фильтр;
  группы действующей оценки не содержат источника отозванной связи.
- т5г: отмена отзыва возвращает связь в подтверждение: строка отзыва с карточки уходит
  (запись отзыва остаётся в журнале навсегда, но витрина про отменённый отзыв не утверждает).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.web.knowledge import claim_detail, claim_provenance
from packages.artifacts.store import FilesystemArtifactStore
from packages.memory.dispute import ACTOR_HOSTCTL
from tests.scenario.test_link_audit_t7124 import STATEMENT_83, URL_HR, URL_SBERCIB, FakeFetchClient
from tests.scenario.test_link_revocations_t7125 import (
    CANCEL_REASON,
    REASON,
    _cancel,
    _revocation_rows,
    _revoke,
    _seed,
    _worker,
)

pytestmark = [pytest.mark.scenario]


@pytest.fixture()
def live_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий fetch-путь с НАСТОЯЩИМ нормализатором: подменена только сеть (T7.124)."""

    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)

#: формат строки витрины (решение пользователя): причина + id записи отзыва + дата записи
NOTE_RE = re.compile(
    r"^связь отозвана оператором: (?P<reason>.+) \(отзыв (?P<id>[0-9a-f-]{36}), (?P<date>\d{4}-\d{2}-\d{2})\)$"
)


async def _read(scratch_url: str, claim_id: uuid.UUID, view: Any) -> Any:
    """Чтение витрины отдельным engine'ом: view только читает (AGENTS: view не создаёт снимков)."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            return await view(db)
    finally:
        await engine.dispose()


async def test_the_card_names_both_revoked_links_with_reason_and_date(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Было: карточка показывала три улики как три подтверждения. Стало: две из них названы
    отозванными (причина, id записи отзыва, дата) и в подтверждение не считаются."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    claim_id = seeded["claim_id"]

    before = await _read(scratch_url, claim_id, lambda db: claim_detail(db, claim_id))
    assert before["revoked_links"] == [], before["revoked_links"]
    assert all(item["link_revocation"] is None for item in before["evidence"]), before["evidence"]
    assert "источников прочитано: 2" in before["verification"], before["verification"]

    result = await _revoke(scratch_url, store, seeded["revokable"], REASON)
    assert len(result.written) == 2, result.written
    await _worker(scratch_url)

    stored = await _revocation_rows(scratch_url)
    assert len(stored) == 2, stored

    detail = await _read(scratch_url, claim_id, lambda db: claim_detail(db, claim_id))
    notes = detail["revoked_links"]
    assert len(notes) == 2, notes
    ids = set()
    for note in notes:
        match = NOTE_RE.match(note)
        assert match is not None, note
        assert match.group("reason") == REASON, note
        ids.add(match.group("id"))
    assert ids == {str(row["id"]) for row in stored}, (ids, stored)

    # журнал не тронут: отозванная связь остаётся строкой улики, но помечена отзывом
    revoked = [item for item in detail["evidence"] if item["link_revocation"] is not None]
    live = [item for item in detail["evidence"] if item["link_revocation"] is None]
    assert len(revoked) == 2 and len(live) == 1, (revoked, live)
    assert {item["source_uri"] for item in revoked} == {URL_SBERCIB}, revoked
    assert [item["source_uri"] for item in live] == [URL_HR], live
    for item in revoked:
        revocation = item["link_revocation"]
        assert revocation["reason"] == REASON, revocation
        assert revocation["actor"] == ACTOR_HOSTCTL, revocation
        # строку составляет сервер, карточка её только печатает: та же строка, что в списке
        assert revocation["note"] in notes, (revocation, notes)

    # отозванная связь не подтверждение: «как проверено» считает только живые улики
    assert "источников прочитано: 2" not in detail["verification"], detail["verification"]
    assert "использован один источник" in detail["verification"], detail["verification"]


async def test_provenance_shows_the_same_lines_and_its_groups_do_not_contain_them(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """«Происхождение» — тот же набор строк об отзыве, что карточка; группы действующей оценки
    источника отозванной связи уже не содержат (их снял пересчёт, витрина ничего не считает сама)."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    claim_id = seeded["claim_id"]

    provenance = await _read(scratch_url, claim_id, lambda db: claim_provenance(db, claim_id))
    assert provenance["revoked_links"] == []
    groups_before = {item["uri"] for item in provenance["source_groups"]}
    assert groups_before == {URL_SBERCIB, URL_HR}, provenance["source_groups"]

    await _revoke(scratch_url, store, seeded["revokable"], REASON)
    await _worker(scratch_url)

    provenance = await _read(scratch_url, claim_id, lambda db: claim_provenance(db, claim_id))
    notes = provenance["revoked_links"]
    assert len(notes) == 2 and all(NOTE_RE.match(note) for note in notes), notes
    assert {item["uri"] for item in provenance["source_groups"]} == {URL_HR}, provenance["source_groups"]
    revoked = [item for item in provenance["evidence"] if item["link_revocation"] is not None]
    assert len(revoked) == 2, provenance["evidence"]
    assert all(item["link_revocation"]["note"] in notes for item in revoked), provenance["evidence"]
    assert "источников прочитано: 2" not in provenance["verification"], provenance["verification"]


async def test_cancelling_the_revocation_returns_the_link_to_the_confirmation(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Отменённый отзыв — вторая append-запись истории: связь снова улика, и карточка перестаёт
    называть её отозванной (строка отзыва в журнале остаётся, витрина про неё не утверждает)."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    claim_id = seeded["claim_id"]

    revoked = await _revoke(scratch_url, store, seeded["revokable"], REASON)
    assert len(revoked.written) == 2, revoked.written
    await _worker(scratch_url)
    detail = await _read(scratch_url, claim_id, lambda db: claim_detail(db, claim_id))
    assert len(detail["revoked_links"]) == 2, detail["revoked_links"]

    ids = [uuid.UUID(str(row["id"])) for row in await _revocation_rows(scratch_url)]
    cancelled = await _cancel(scratch_url, ids, CANCEL_REASON)
    assert len(cancelled.cancelled) == 2, cancelled.cancelled
    await _worker(scratch_url)

    after = await _read(scratch_url, claim_id, lambda db: claim_detail(db, claim_id))
    assert after["revoked_links"] == [], after["revoked_links"]
    assert all(item["link_revocation"] is None for item in after["evidence"]), after["evidence"]
    assert "источников прочитано: 2" in after["verification"], after["verification"]
    # формулировка утверждения та же, что на стенде: витрина не меняла знание
    assert after["statement"] == STATEMENT_83
