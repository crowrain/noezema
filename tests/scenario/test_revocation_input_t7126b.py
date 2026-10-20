"""Scenario T7.126b (дополнение ADR-0039): отзыв связи учтён во ВСЕХ сборках входа оценки.

Разбор стенда `.92` (утверждение `83e7a1c8`, срез — `tests/fixtures/t7126b/stand-83e7a1c8.json`):
предикат «нет действующего отзыва» (`active_link_revocation_exclusion`, T7.125) стоял на ВХОДЕ
группировщика и на ВХОДЕ переоценки, но НЕ стоял на ВХОДЕ оценки предпросмотра независимости
(`preview_independence_changes`) и на ВХОДЕ оценки при коммите сессии (`MemoryService._assess`).
Отозванная связь при этом исключена из групп, поэтому в `evaluate` она получала резервную группу
`UNTRACKED_GROUP` — вторую «независимую» группу — и оценку вверх: E1 hypothesis (0.15) →
E3 supported (0.75), то есть `independence-preview --apply` записал бы ложное «Проверено».

Посев — стендовый случай целиком, по-настоящему: прочитанные страницы (`research.fetch` c настоящим
нормализатором, подменена только сеть), улика `source_assertion`, связь `supports`, указатель
происхождения `sbercib → cbr` (`write_derivative_pointer` — тот же хостовый путь, что у fetch и
переатрибуции), формулировка со стенда, отзыв двух названных связей и рабочий батч.
Прямых UPDATE доменных таблиц нет: посев идёт staging + `apply_claim_staging`.
"""

from __future__ import annotations

import copy
import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.link_audit import audit_recorded_links
from apps.research_proxy.derivative_pointer import write_derivative_pointer
from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.source_attribution import (
    STATUS_DERIVATIVE,
    AttributionDecision,
)
from hostctl import offline_rules as orl
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.canonical import canonical_sha256
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.sessions import ORMSession
from packages.domain.schemas.staging import CuratorProposal
from packages.domain.services.audit import AuditService
from packages.domain.services.config import ConfigService
from packages.domain.services.reserve import HostReserveService
from packages.domain.services.staging import StagingService
from packages.memory.independence_preview import (
    IndependencePreviewReport,
    preview_independence_changes,
)
from packages.memory.service import MemoryService
from tests.scenario.test_link_audit_t7124 import (
    STATEMENT_83,
    _assessment_of,
    _group_uris,
    _rows,
    _with_session,
)
from tests.scenario.test_link_revocations_t7125 import (
    CANCEL_REASON,
    _cancel,
    _claim_id,
    _link_ids,
    _revocation_rows,
    _revoke,
    _worker,
)
from tests.scenario.test_research_evidence import _fetch_sources, _section, _set_section

pytestmark = [pytest.mark.scenario]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: те же адреса страниц, что на стенде (срез `stand-83e7a1c8.json`)
URL_SBERCIB = "https://www.sbercib.ru/publication/reshenie-banka-rossii-po-klyuchevoi-stavke-ot-11-sentyabrya-2026-goda"
URL_HR = "https://hr-ratings.com/analytics/rynok-truda-bolshoj-krizis-v-istorii-rossii"
#: объявленный первоисточник пересказа (форма родителя со стенда: sbercib → cbr)
URL_CBR_ANCHOR = "https://cbr.ru/analytics/dkp/dinamic/CPD_2026-9/"
#: третья живая страница (вымышленный сервис) — нужна, чтобы проверить: НОВАЯ улика этой же
#: транзакции остаётся во входе оценки и даёт вторую группу
URL_TARVEN = "https://tarven.io/analytics/hh-index-2026"

REASON = "на странице «решение ЦБ по ключевой ставке» значения hh-индекса 9,6 нет"

#: числа оценки со стенда (external_fact: min_independence_groups = 2)
E1_HYPOTHESIS = ("E1", "hypothesis")
E3_SUPPORTED = ("E3", "supported")
CONF_ONE_GROUP = 0.15
CONF_TWO_GROUPS = 0.75


def _fixture_lines(name: str) -> list[str]:
    return (REPO_ROOT / "tests/fixtures/t7126b" / name).read_text(encoding="utf-8").splitlines()


#: побайтовые копии стендовых страниц + вымышленная третья страница со значением
PAGES: dict[str, list[str]] = {
    URL_SBERCIB: _fixture_lines("sbercib-key-rate.normalized.txt"),
    URL_HR: _fixture_lines("hr-ratings-hh-index.normalized.txt"),
    URL_TARVEN: _fixture_lines("tarven-hh-index-2026.normalized.txt"),
}


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в T7.124): сети нет."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> Any:
        paragraphs = "".join(f"<p>{line}</p>" for line in PAGES[url] if line.strip())
        data = f"<html><body>{paragraphs}</body></html>".encode()
        return FetchResult(
            url=url,
            final_url=url,
            content_type="text/html",
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


@pytest.fixture()
def live_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий fetch-путь с НАСТОЯЩИМ нормализатором: подменена только сеть."""

    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)


# ─── посев и чтение ───────────────────────────────────────────────────────────────


def _record(env: dict[str, str], chunk: str) -> Any:
    """Улика `source_assertion` прочитанной страницы (форма наблюдения T7.124)."""

    from apps.orchestrator.evidence import observation_to_evidence
    from apps.orchestrator.executor import Observation

    obs = Observation(
        tool="research.fetch",
        ok=True,
        data={
            "url": env["url"],
            "mode": "curated",
            "fenced": True,
            "content": "<<<UNTRUSTED DATA BEGIN>>>\n...\n<<<UNTRUSTED DATA END>>>\n",
            "normalized_text": "\n".join(PAGES[env["url"]]),
            "source_id": env["source_id"],
            "original_sha256": env["original_sha256"],
            "normalized_sha256": env["normalized_sha256"],
            "chunk_id": chunk,
        },
    )
    rec = observation_to_evidence(obs, {"url": env["url"]})
    assert rec is not None, "research.fetch не дал улики"
    return rec


async def _mark_derivative(scratch_url: str, source_id: str) -> str:
    """Указатель происхождения прочитанной страницы — ХОСТОВЫМ путём (`write_derivative_pointer`,
    тот же, что вызывает fetch и переатрибуция): пересказ SberCIB получает объявленный
    первоисточник Банка России. Строку источника это не склеивает и ни о чём не лжёт."""

    async def _work(db: Any) -> str:
        parent = await write_derivative_pointer(
            db,
            source_id,
            AttributionDecision(
                status=STATUS_DERIVATIVE,
                primary_key="cbr",
                primary_name="Банк России",
                parent_uri=URL_CBR_ANCHOR,
                basis_fragment="По данным Банка России",
            ),
        )
        assert parent is not None
        await db.commit()
        return str(parent)

    return await _with_session(scratch_url, _work)


async def _commit(
    scratch_url: str, records: list[Any], statement: str
) -> Any:
    """Коммит сессии: staging-операции + настоящий `apply_claim_staging` (тот же commit-boundary
    код, что финальная транзакция). Утверждение с той же формулировкой переиспользуется."""

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
            assert snap is not None
            sid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, started_at) "
                    "VALUES (:id, 'exploring', :c, now())"
                ),
                {"id": str(sid), "c": str(snap.id)},
            )
            o_session = (
                (await db.execute(select(ORMSession).where(ORMSession.id == sid))).scalars().first()
            )
            assert o_session is not None
            audit = AuditService(db)
            staging = StagingService(HostReserveService.for_snapshot(snap))
            service = MemoryService(snap)
            proposal = CuratorProposal(
                summary="Перепроверка hh-индекса",
                claims=[
                    {
                        "statement": statement,
                        "claim_type": "external_fact",
                        "scope": {"event": "hh-index-2026"},
                    }
                ],
                evidence_links=[
                    {"evidence_index": i, "claim_index": 0, "relation": "supports"}
                    for i in range(len(records))
                ],
                new_questions=[],
            )
            assert proposal.validate_against(len(records), questions_max=4) == []
            await staging.record(
                db, audit, o_session, "claim", proposal.claims[0].model_dump(mode="json"), proposed_claims=1
            )
            for link in proposal.evidence_links:
                await staging.record(
                    db, audit, o_session, "evidence", link.model_dump(mode="json"), proposed_evidence=1
                )
            result = await service.apply_claim_staging(db, audit, o_session, records)
            assert result.problems == (), f"commit boundary problems: {result.problems}"
            return result
    finally:
        await engine.dispose()


async def _seed(scratch_url: str, store: FilesystemArtifactStore) -> dict[str, Any]:
    """Стендовое состояние до отзыва: три записанные связи утверждения `83e7a1c8` (две на страницу
    SberCIB без значения, одна на страницу HR-Ratings со значением), оценка E3 supported (0.75),
    участники групп — обе страницы; у страницы SberCIB — объявленный первоисточник Банка России."""

    await _set_section(scratch_url, _section())
    envs = await _fetch_sources(scratch_url, store, (URL_SBERCIB, URL_HR))
    anchor_id = await _mark_derivative(scratch_url, envs[0]["source_id"])
    # форма родителя со стенда (пересказ → объявленный первоисточник Банка России): якорь
    # создаёт сам хостовый путь `resolve_primary_source` (home_uri словаря первоисточников)
    anchor_rows = await _rows(
        scratch_url,
        "SELECT canonical_uri AS u, content_hash AS h FROM sources WHERE id = :i",
        {"i": anchor_id},
    )
    assert len(anchor_rows) == 1 and anchor_rows[0]["h"] is None, anchor_rows
    assert anchor_rows[0]["u"] == "https://cbr.ru/"
    parent_rows = await _rows(
        scratch_url,
        "SELECT parent_source_id AS p FROM sources WHERE id = :i",
        {"i": envs[0]["source_id"]},
    )
    assert str(parent_rows[0]["p"]) == anchor_id, parent_rows
    records = [
        _record(envs[0], "chunk-0"),
        _record(envs[0], "chunk-1"),
        _record(envs[1], "chunk-0"),
    ]
    await _commit(scratch_url, records, STATEMENT_83)

    claim_id = await _claim_id(scratch_url, STATEMENT_83)
    links = await _link_ids(scratch_url, STATEMENT_83)
    assert await _assessment_of(scratch_url, STATEMENT_83) == E3_SUPPORTED
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_HR, URL_SBERCIB}
    return {
        "claim_id": claim_id,
        "sbercib_ids": links[URL_SBERCIB],
        "hr_ids": links[URL_HR],
        "envs": {"sbercib": envs[0], "hr": envs[1]},
        "anchor_id": anchor_id,
    }


async def _revoke_and_reassess(
    scratch_url: str, store: FilesystemArtifactStore, seeded: dict[str, Any]
) -> list[uuid.UUID]:
    """Отзыв двух названных связей + рабочий батч: голова E1 hypothesis (0.15), группа одна."""

    await _revoke(scratch_url, store, list(seeded["sbercib_ids"]), REASON)
    await _worker(scratch_url)
    assert await _assessment_of(scratch_url, STATEMENT_83) == E1_HYPOTHESIS
    assert await _confidence(scratch_url, STATEMENT_83) == pytest.approx(CONF_ONE_GROUP)
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_HR}
    return [uuid.UUID(str(row["id"])) for row in await _revocation_rows(scratch_url)]


async def _confidence(scratch_url: str, statement: str) -> float:
    row = await _rows(
        scratch_url,
        "SELECT a.confidence AS c FROM claim_assessment_heads h "
        "JOIN claims c ON c.id = h.claim_id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.assessment_state = 'current' AND c.statement = :s",
        {"s": statement},
    )
    assert len(row) == 1, row
    return float(row[0]["c"])


async def _preview(scratch_url: str) -> IndependencePreviewReport:
    return await _with_session(scratch_url, lambda db: preview_independence_changes(db))


def _preview_empty(report: IndependencePreviewReport) -> None:
    """Предпросмотр называет только разошедшиеся утверждения — здесь их быть не должно."""

    assert report.rows == (), report
    assert report.groups_changed_count == 0
    assert report.assessment_changed_count == 0


async def _assessment_evidence(scratch_url: str, statement: str) -> set[uuid.UUID]:
    """id улик, записанных как участие ДЕЙСТВУЮЩЕЙ оценки (`assessment_evidence`)."""

    rows = await _rows(
        scratch_url,
        "SELECT ae.evidence_id AS id FROM assessment_evidence ae "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = ae.assessment_id "
        "JOIN claims c ON c.id = h.claim_id "
        "WHERE h.assessment_state = 'current' AND c.statement = :s",
        {"s": statement},
    )
    return {uuid.UUID(str(r["id"])) for r in rows}


async def _evidence_count(scratch_url: str, statement: str) -> int:
    rows = await _rows(
        scratch_url,
        "SELECT count(*) AS n FROM evidence e JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = :s",
        {"s": statement},
    )
    return int(rows[0]["n"])


# ─── т1: предпросмотр независимости (а) — отзыв не рисует подъём оценки ───────────


async def test_preview_does_not_raise_the_grade_of_a_revoked_link(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Стендовый дефект: после отзыва и рабочего батча `independence-preview` обязан НЕ
    показывать смены оценки. Предпросмотр называет только утверждения, у которых группы или
    оценка разошлись с записанной, — значит таких строк быть не должно вовсе.

    На прежнем коде строка была: группы не изменились (участники те же), но оценка «было:
    E1 hypothesis (0.15) → стало: E3 supported (0.75)» — ровно то, что showed стендовый вывод
    `independence-preview` по `83e7a1c8` (`tests/fixtures/t7126b/`). Причина: отозванная связь
    отсутствовала в группах, но входила в `evaluate` и получала вторую «независимую» группу
    `UNTRACKED_GROUP`. `--apply` записал бы этот ложный подъём (вопрос «Проверено»)."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    await _revoke_and_reassess(scratch_url, store, seeded)

    report = await _preview(scratch_url)
    assert report.claims_checked == 1, report
    _preview_empty(report)


# ─── т2: отменённый отзыв возвращает связь в оценку предпросмотра ─────────────────


async def test_preview_restores_the_grade_after_the_revocation_is_cancelled(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Отменённый отзыв не исключает ничего: связь возвращается и в группы, и в оценку.

    На прежнем коде этот тест зелёный (отзыв уже был снят, и прежний дефект — «все улики
    утверждения» — там не проявляется): он закрепляет вторую половину правила ADR-0039 и краснеет,
    если фильтр входа начать применять к ОТМЕНЁННЫМ отзывам — предпросмотр тогда показал бы
    ложное понижение E3 → E1, а рабочий батч разошёлся бы с предпросмотром."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    revocation_ids = await _revoke_and_reassess(scratch_url, store, seeded)

    await _cancel(scratch_url, revocation_ids, CANCEL_REASON)
    # каскад отвёл голову в pending: предпросмотр current-головы не видит (и не выдаёт ложных
    # строк), оценка вернётся рабочим батчем
    pending = await _preview(scratch_url)
    assert pending.claims_checked == 0, pending
    await _worker(scratch_url)

    assert await _assessment_of(scratch_url, STATEMENT_83) == E3_SUPPORTED
    assert await _confidence(scratch_url, STATEMENT_83) == pytest.approx(CONF_TWO_GROUPS)
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_HR, URL_SBERCIB}
    # записанное участие оценки снова включает обе связи
    assert len(await _assessment_evidence(scratch_url, STATEMENT_83)) == 3

    # предпросмотр называет только разошедшиеся утверждения: их нет — «стало» совпало с
    # записанным E3 supported (0.75), отменённый отзыв ничего не исключил
    after = await _preview(scratch_url)
    assert after.claims_checked == 1, after
    _preview_empty(after)
    # история не правится: обе строки отзыва остались, с колонками отмены
    history = await _revocation_rows(scratch_url)
    assert len(history) == 2
    assert all(r["cancelled_at"] is not None and r["cancel_reason"] == CANCEL_REASON for r in history)


# ─── т3 (б): коммит сессии — отозванная связь не входит в оценку, новая — входит ──


async def test_commit_assessment_excludes_revoked_link_and_counts_new_one(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Живой путь коммита (`apply_claim_staging` → `_assess`): переутверждение того же утверждения
    новой сессией. Отозванная связь не участвует ни в группах, ни в `evaluate`, ни в
    `assessment_evidence`; улика, записанная ЭТОЙ ЖЕ транзакцией, остаётся во входе и даёт вторую
    группу (E3 supported 0.75). Строки `evidence` отозванных связей не удалены."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    await _revoke_and_reassess(scratch_url, store, seeded)

    envs = await _fetch_sources(scratch_url, store, (URL_HR, URL_TARVEN))
    before = await _evidence_count(scratch_url, STATEMENT_83)
    result = await _commit(
        scratch_url, [_record(envs[0], "chunk-0"), _record(envs[1], "chunk-0")], STATEMENT_83
    )
    assert result.claims_reused == 1 and result.claims_created == 0
    # улица hr уже записана (тот же identity_hash) — новая строка одна
    assert result.evidence_added == 1
    assert result.evidence_deduped == 1
    assert result.assessments == 1

    assert await _assessment_of(scratch_url, STATEMENT_83) == E3_SUPPORTED
    assert await _confidence(scratch_url, STATEMENT_83) == pytest.approx(CONF_TWO_GROUPS)
    # две живые группы: hr и tarven; отозванный sbercib — не участник
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_HR, URL_TARVEN}

    # участие действующей оценки ровно из двух улик (на прежнем коде — из четырёх:
    # отозванные входили в оценку и в её записанное участие)
    live_ids = await _assessment_evidence(scratch_url, STATEMENT_83)
    assert len(live_ids) == 2, live_ids
    revoked_ids = set(seeded["sbercib_ids"])
    assert revoked_ids & live_ids == set(), (revoked_ids, live_ids)

    after = await _evidence_count(scratch_url, STATEMENT_83)
    assert after == before + 1, (before, after)  # отозванные строки живы в журнале


# ─── т4 (г): повторное предложение той же отозванной связи отзыв не обходит ──────


async def test_reproposing_the_same_identity_hash_does_not_bypass_revocation(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Новая сессия предлагает ТЕ ЖЕ связи (тот же `identity_hash`): строка `evidence` не
    создаётся заново (дедуп T7.78), действующий отзыв остаётся на ней, и оценка не поднимается —
    иначе переутверждение обходил бы решение оператора. После отмены отзыва те же предложения
    возвращают E3 (дедуп и отзыв не спорят)."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    revocation_ids = await _revoke_and_reassess(scratch_url, store, seeded)

    envs = await _fetch_sources(scratch_url, store, (URL_SBERCIB, URL_HR))
    before = await _evidence_count(scratch_url, STATEMENT_83)
    result = await _commit(
        scratch_url,
        [_record(envs[0], "chunk-0"), _record(envs[0], "chunk-1"), _record(envs[1], "chunk-0")],
        STATEMENT_83,
    )
    assert result.evidence_added == 0, result
    assert result.evidence_deduped == 3, result
    assert await _evidence_count(scratch_url, STATEMENT_83) == before
    assert {str(r["id"]) for r in await _revocation_rows(scratch_url)} == {
        str(rid) for rid in revocation_ids
    }
    assert all(r["cancelled_at"] is None for r in await _revocation_rows(scratch_url))

    # отзыв жив: одна группа, оценка не поднялась (на прежнем коде — E3 supported 0.75)
    assert await _assessment_of(scratch_url, STATEMENT_83) == E1_HYPOTHESIS
    assert await _confidence(scratch_url, STATEMENT_83) == pytest.approx(CONF_ONE_GROUP)
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_HR}
    assert len(await _assessment_evidence(scratch_url, STATEMENT_83)) == 1

    # отмена отзыва возвращает связи в оценку — те же предложения дают E3
    await _cancel(scratch_url, revocation_ids, CANCEL_REASON)
    await _worker(scratch_url)
    assert await _assessment_of(scratch_url, STATEMENT_83) == E3_SUPPORTED
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_HR, URL_SBERCIB}


# ─── т5 (в): offline-оценка кандидата правил — тот же вход ────────────────────────


async def test_offline_shadow_assessment_input_excludes_revoked_links(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Теневые головы кандидата правил (`hostctl/offline_rules.py`) считают утверждение по тому же
    входу: `evidence_set_hash` тени = множество ЖИВЫХ улик, отозванные в него не входят (и тени не
    повышают оценку утверждения с отозванными связями)."""

    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    await _revoke_and_reassess(scratch_url, store, seeded)

    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    # кандидат обязан отличаться от effective-снапшота; правила те же, что у base
    payload["session_limits"] = {
        **dict(payload.get("session_limits") or {}),
        "max_explorer_steps": int((payload.get("session_limits") or {}).get("max_explorer_steps", 10)) + 1,
    }

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        audit = AuditService(db)
        base = await ConfigService.get_effective(db)
        candidate, already = await orl.upsert_candidate(
            db, audit, base_snapshot=base, requested_payload=payload
        )
        assert already is False
        await orl.freeze_cohort(db, candidate=candidate)
        prepared = await orl.prepare_shadow_heads(db, candidate=candidate)
        assert prepared >= 1
        shadow_id = candidate.id
        claim_id = seeded["claim_id"]

    rows = await _rows(
        scratch_url,
        "SELECT a.evidence_set_hash AS h, a.effective_grade AS g, a.epistemic_status AS s "
        "FROM claim_assessment_heads h JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.config_snapshot_id = :c AND h.claim_id = :k",
        {"c": str(shadow_id), "k": str(claim_id)},
    )
    assert len(rows) == 1, rows
    shadow = rows[0]

    live_ids = [seeded["hr_ids"][0]]
    live_rows = await _rows(
        scratch_url,
        "SELECT evidence_kind AS k, relation AS r, identity_hash AS i FROM evidence WHERE id = ANY(:ids)",
        {"ids": [str(i) for i in live_ids]},
    )
    expected = canonical_sha256(
        sorted(f"{r['k']}:{r['r']}:{r['i']}" for r in live_rows)
    )
    all_rows = await _rows(
        scratch_url,
        "SELECT evidence_kind AS k, relation AS r, identity_hash AS i FROM evidence "
        "WHERE claim_id = :k ORDER BY id",
        {"k": str(claim_id)},
    )
    assert len(all_rows) == 3
    with_revoked = canonical_sha256(
        sorted(f"{r['k']}:{r['r']}:{r['i']}" for r in all_rows)
    )
    assert shadow["h"] == expected, (shadow["h"], expected, with_revoked)
    assert shadow["h"] != with_revoked
    assert (shadow["g"], shadow["s"]) == E1_HYPOTHESIS


# ─── т6: read-only план отзыва не подставляет отозванные связи в пересчёт «стало» ─


async def test_plan_recount_does_not_put_revoked_links_back_into_the_assessment(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """План `link-audit` называет отозванные связи отдельной строкой (они в плане и должны
    оставаться), но его пересчёт «стало» не возвращает их во вход оценки и не называет их
    источники «уходящими из групп»: они уже вне групп с момента отзыва."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed(scratch_url, store)
    await _revoke_and_reassess(scratch_url, store, seeded)

    report = await _with_session(
        scratch_url, lambda db: audit_recorded_links(db, store, claim_ids=[seeded["claim_id"]])
    )
    row = next(r for r in report.rows if r.statement == STATEMENT_83)
    assert {link.evidence_id for link in row.revoked_links} == set(seeded["sbercib_ids"])
    # уже снятое записью не пересчитывается повторно: «стало» = записанное
    assert row.groups_changed is False, (row.old_groups, row.new_groups)
    assert (row.persisted_grade, row.persisted_status) == E1_HYPOTHESIS
    assert (row.new_grade, row.new_status) == E1_HYPOTHESIS
    assert row.new_confidence == pytest.approx(CONF_ONE_GROUP)
    assert row.assessment_changed is False
    # «ушли бы из групп» — только то, что ещё в оценке; отозванный источник уже вне групп
    assert row.excluded_source_uris == (), row.excluded_source_uris
