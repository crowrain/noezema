"""Scenario (DB): таблица меток витрины «было» → «стало» на стендовых 20 утверждениях (T7.101).

Посев — побайтовые строки базы .92 из фикстуры `~/fixtures-t7101`
(`tests/fixtures/t7101/`): 20 утверждений, 33 улики, 22 прочитанных источника, два якоря
(`33912509…` — Росстат, `88fb9e2f…` — PDF-прогноз ЦБ), операторская коррекция merge
nbj → sbercib (ADR-0031, стенда), запечатанные группы независимости и роли улик — ровно те,
которые показывает страница происхождения, оценки рабочего с причинами из ленты.

Что закреплено:

* «было» (состояние `817f2cc`): четыре подписи «Опубликовано производителем»
  (`9266248e` Росстат, `391c4388`/`136a0ca0` Банк России, `7cc2b578` Росстат —
  указатель по прежней записи v1 и по родителю, T7.100) и шестнадцать «Подтверждено слабо».
  Таблица пересчитана T7.116 (часть б): `6d9a12ff` («…по данным опроса „инФОМ“.») раньше
  подписывался пятым — по одному лишь указателю на документ ЦБ; теперь его утверждение
  производителя не называет, указатель имени не даёт: метка честная «слабо», уровень тот же.
* прогон `--evidence-level` по журналу окна: две записи о собственной публикации `c6729d4c`
  (обе страницы ЦБ) плюс третья — `cc75e97f` на странице события ЦБ: T7.120a (ADR-0029 §24)
  видит в ней собственную публикацию семьи `cbr` (инФОМ и Банк России — один производитель);
  строгим пересказом остаётся только `07126d06` (прогноз Альфа-Банка на internationalinvestment.biz —
  законное расширение словаря T7.120, к инФОМ отношения не имеет). Вето третьего актора для
  `2d010d53`/`58e2d5d4` (ADR-0035 §15), пять улик под коррекцией оператора не переписываются
  (ADR-0031), восемь прежних записей v6 — «уже записано», прежние v1 под коррекцией остаются v1;
* «стало»: изменившиеся метки — три: `c6729d4c`, `07126d06` (Альфа-Банк) и `cc75e97f`
  (Банк России, не «инФОМ» — заблуждение T7.120 исправлено); остальные 17 меток побайтово равны;
* оценки и причины НЕ изменились вовсе (снимки равны); группировка изменилась ровно для одного
  утверждения (`07126d06` — одиночная группа свернута на новый якорь alfabank.ru; `cc75e97f`
  и `c6729d4c` — прежняя доменная группировка: собственные публикации родителя не создают,
  якорь infom.org.ru больше не заводится), создана одна строка источника-якоря; каскад: один
  снятый head, одна задача переоценки;
* второй прогон идемпотентен (как в T7.101; заблуждение T7.120 требовало третьего прогона:
  без якоря инФОМ дописывать на Infl_exp_26-09 нечего);
* свёртка дублей значения (T7.99): пара `c6729d4c`/`136a0ca0` после прогона согласована
  (обе подписи «Опубликовано производителем»), пара `9266248e`/`e189c80d` остаётся
  несогласованной честно: у `e189c80d` (garant, пересказ без записанного указателя)
  записывать нечего — fail-closed, а не подгонка метки.

Сеть не используется: страницы поднимаются из хранилища артефактов по журналу fetch'ей —
тот же путь, что у `hostctl research-reattribute --evidence-level` на стенде.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.orchestrator.value_duplicate import value_duplicate_pair
from apps.research_proxy.reattribution import NO_DECISION_YET, reattribute_evidence_window
from apps.web.reliability import LEVEL_WEAK, PRODUCER_LABEL
from packages.artifacts.store import FilesystemArtifactStore
from tests.scenario.test_web_answer_api import (
    _client,
    _seed_claim,
    _seed_question,
    _seed_session,
)
from tests.scenario.test_web_knowledge import _make_app
from tests.scenario.test_web_producer_badge import _execute, _fix_groups, _worker_assessment

pytestmark = [pytest.mark.scenario]

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "t7101"

SINCE = datetime(2000, 1, 1, tzinfo=UTC)
#: стенд T7.101 запечатан методом значений v6; с T7.107 текущий метод — v7, поэтому тест явно
#: моделирует эпоху v6 подменой констант переатрибуции (тот же приём эпохи, что в
#: `test_reattribute_stale_records`): таблица подписей закрепляет состояние стенда ЭПОХИ v6,
#: а не случайный срез нового метода
CURRENT_VALUE_METHOD = "host-value-attribution-v6"

WEAK_LABEL = "Подтверждено слабо"
SELF_ACTION = "будет записано: собственная публикация производителя Банк России"
SELF_DONE_ACTION = "изменений нет: собственная публикация уже записана"
VETO_ACTION = (
    "изменений нет: собственная публикация не записана — "
    "значение названо оценкой третьего актора (ADR-0035 §15)"
)
NOT_COLLECTED_ACTION = "изменений нет: атрибуция значения не собрана"
DECIDED_ACTION = "изменений нет: решение уже записано ранее"
CORRECTED_ACTION = "под коррекцией оператора (ADR-0031) — улики не переписываем"

ANCHOR_ROSSTAT = uuid.UUID("33912509-6fa7-44a4-ba30-c7224cc8c36e")
ANCHOR_ROSSTAT_URI = "https://rosstat.gov.ru/"
ANCHOR_CBR_PDF = uuid.UUID("88fb9e2f-dca3-4e99-8cfb-eb502ce5d033")
ANCHOR_CBR_PDF_URI = "https://www.cbr.ru/Content/Document/File/194258/forecast_260724.pdf"

SOURCE_SBERCIB_2025 = uuid.UUID("92c2671e-f589-4fcb-a5f5-03ee39d62ebc")
SOURCE_NBJ = uuid.UUID("b29f0226-f816-4922-b15b-6f7e3e0c942e")

EVIDENCE_SELF = ("6a042e53", "23055278")  # улики c6729d4c на страницах самого ЦБ
EVIDENCE_VETO = ("4d8c62e7", "da3d51e6")  # 2d010d53 / 58e2d5d4 на Infl_exp_25_12
EVIDENCE_CORRECTED = ("7331bebc", "9031241d", "bb927587", "f5df832d", "6ed603f4")

URL_INFL_26_09 = "https://www.cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_26-09/"
URL_CPD_2026_7 = "https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/"
URL_INFL_25_12 = "https://cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_25-12/"
URL_SBERCIB_2025 = "https://sbercib.ru/publication/inflyatsiya-v-2025-godu"
URL_NBJ = "https://nbj.ru/publs/obshchaya_inflyatsiya_po_itogam_2025_goda_/71790/"
URL_ALFA_INTL = "https://internationalinvestment.biz/analytics/8583-alfa-bank-uhudshil-prognoz-po-jekonomike-rossii.html"
URL_EVENT_32879 = "https://cbr.ru/press/event/?id=32879"
URL_FORBES_552221 = (
    "https://www.forbes.ru/finansy/552221-cb-soobsil-o-roste-inflacionnyh-ozidanij-rossian-v-dekabre-2025-goda"
)

#: T7.120a (ADR-0029 §24): строгим пересказом остаётся только законное расширение T7.120 —
#: прогноз Альфа-Банка на ретрансляционной странице. Улика cc75e97f на странице события ЦБ
#: (5422d75c) — собственная публикация семьи `cbr` (входит в EVIDENCE_SELF ниже); якорь
#: infom.org.ru больше не создаётся
WRITE_ACTION = "будет записано происхождение значения улики"
# T7.129a (ADR-0029 §26.3): к законному строгому пересказу Альфа-Банка добавилась улица
# 6d9a12ff на forbes.ru — утверждение само называет производителя («по данным опроса „инФОМ“»,
# корень семьи — Банк России), а страница называет 13,1 и 15,6 ровно в том же показателе
# («оценка годовой наблюдаемой инфляции»). Было (до T7.129a): одна запись. Стало: две;
# метка «Подтверждено слабо» у 6d9a12ff остаётся (подпись «Опубликовано производителем» —
# за собственными публикациями), меняется только происхождение значения и группировка.
EVIDENCE_NEW_WRITES = ("5fab715e", "e62b7834")  # 07126d06 (Альфа-Банк) + 6d9a12ff (семья cbr)
EVIDENCE_SELF_CBR_EVENT = ("5422d75c",)  # cc75e97f на странице события ЦБ — публикация семьи cbr
#: T7.129a: вторым якорем дело не кончилось бы, но для forbes-записи пятого сопряжения
#: `resolve_primary_source` переиспользует существующий указатель Банка России — якорей ровно
#: столько же, сколько до T7.129a
NEW_ANCHOR_URIS = {"https://www.alfabank.ru/"}
#: строка источника forbes-улики (родитель после apply проверяется отдельно)
FORBES_SOURCE_ID = "d4a936ff-b4bb-446c-9cb0-430639e6287a"

#: метки «было» на состоянии 817f2cc: утверждение → имя производителя из словаря
#: T7.116 (часть б): `6d9a12ff` убран из таблицы — при T7.101–T7.115 он подписывался
#: «Опубликовано производителем» по ОДНОМУ указателю на документ ЦБ, хотя его утверждение
#: («…по данным опроса „инФОМ“.») никакого производителя из словаря не называет; теперь
#: указатель имени не даёт (флип проверяет блок «было» этого теста).
WAS_PRODUCER: dict[str, str] = {
    "9266248e": "Росстат",
    "391c4388": "Банк России",
    "136a0ca0": "Банк России",
    "7cc2b578": "Росстат",
}
#: «стало» после переатрибуции: T7.101 — добавлялась ровно одна подпись (c6729d4c);
#: с T7.120/T7.120a — три: прогноз Альфа-Банка на реле-странице (строгий пересказ) и
#: cc75e97f на странице события ЦБ (собственная публикация семьи `cbr`, подпись «Банк
#: России» — не «инФОМ»: заблуждение T7.120 исправлено склейкой семьи);
#: с T7.129a (ADR-0029 §26.3) — четыре: 6d9a12ff на forbes подписывается «Банк России»,
#: потому что пятое сопряжение записало происхождение значения опроса «инФОМ» (корень семьи
#: — cbr), а витрина называет строку записью о происхождении значения, а не страничным
#: якорем (T7.109: запись сильнее указателя; правило T7.116 про указатель здесь ни при чём)
BECAME_PRODUCER: dict[str, str] = {
    **WAS_PRODUCER,
    "c6729d4c": "Банк России",
    "07126d06": "Альфа-Банк",
    "cc75e97f": "Банк России",
    "6d9a12ff": "Банк России",
}


def _load_fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    claims = json.loads((FIXTURE_DIR / "claims_detail_provenance.json").read_text(encoding="utf-8"))
    evidence = json.loads((FIXTURE_DIR / "evidence_sources_all20.json").read_text(encoding="utf-8"))
    assert len(claims) == 20 and len(evidence) == 33
    return claims, evidence


# ─── посев стендового состояния (те же таблицы, что пишет прод-контур) ──────────


async def _seed_stand_state(engine: AsyncEngine, evidence: list[dict[str, Any]]) -> None:
    """Якоря, прочитанные источники, операторская коррекция, улики со скоупами стенда."""

    for anchor_id, uri in ((ANCHOR_ROSSTAT, ANCHOR_ROSSTAT_URI), (ANCHOR_CBR_PDF, ANCHOR_CBR_PDF_URI)):
        await _execute(
            engine,
            "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata) "
            "VALUES (:id, 'external_url', :uri, :ch, '{}')",
            id=anchor_id,
            uri=uri,
            ch=f"anchor-{anchor_id.hex[:8]}",
        )
    for row in evidence:
        await _execute(
            engine,
            "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata, parent_source_id) "
            "VALUES (:id, 'external_url', :uri, :ch, '{}', :p) ON CONFLICT (id) DO NOTHING",
            id=uuid.UUID(row["source_id"]),
            uri=row["source_uri"],
            ch=str((row.get("source_metadata") or {}).get("normalized_sha256") or "x"),
            p=uuid.UUID(row["source_parent"]) if row.get("source_parent") else None,
        )
    # стендовая коррекция оператора (ADR-0031): nbj → sbercib, «одна официальная цифра»
    await _execute(
        engine,
        "INSERT INTO source_graph_corrections (id, actor, kind, from_source_id, to_source_id, "
        "rules_version, valid) VALUES (:id, 'operator:hostctl', 'merge', :f, :t, 'rules-v2', true)",
        id=uuid.uuid4(),
        f=SOURCE_NBJ,
        t=SOURCE_SBERCIB_2025,
    )


async def _seed_evidence(engine: AsyncEngine, evidence: list[dict[str, Any]]) -> None:
    for row in evidence:
        await _execute(
            engine,
            "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, scope, source_id, chunk_id) "
            "VALUES (:id, :c, :rel, :kind, :h, CAST(:sc AS JSONB), :s, :k)",
            id=uuid.UUID(row["evidence_id"]),
            c=uuid.UUID(row["claim_id"]),
            rel=row["relation"],
            kind=row["kind"],
            h=f"identity-{row['evidence_id'][:8]}",
            sc=json.dumps(row["scope"], ensure_ascii=False),
            s=uuid.UUID(row["source_id"]),
            k="chunk-0",
        )


async def _seed_claims_and_assessments(
    engine: AsyncEngine, claims: list[dict[str, Any]]
) -> dict[str, uuid.UUID]:
    """Утверждения и оценки рабочего с причинами из ленты; возвращает id оценок по утверждениям."""

    assessment_ids: dict[str, uuid.UUID] = {}
    for row in claims:
        detail = row["detail"]
        head = detail["heads"][0]
        claim_id = uuid.UUID(row["claim"])
        question_id, session_id = uuid.uuid4(), uuid.uuid4()
        await _seed_question(engine, question_id=question_id, state="verified")
        await _seed_session(
            engine,
            session_id=session_id,
            question_id=question_id,
            state="succeeded",
            termination_reason="goal_reached",
        )
        await _seed_claim(
            engine, claim_id=claim_id, session_id=session_id, statement=detail["statement"], head_state="none"
        )
        await _execute(
            engine, "UPDATE claims SET claim_type = :t WHERE id = :c", t=detail["claim_type"], c=claim_id
        )
        reasons = [g["code"] if isinstance(g, dict) else str(g) for g in (detail.get("grade_reasons") or [])]
        assessment_ids[row["claim"]] = await _worker_assessment(
            engine,
            claim_id=claim_id,
            grade=str(head["effective_grade"]),
            epistemic=str(head["epistemic_status"]),
            confidence=float(head["confidence"]),
            reasons=reasons,
        )
    return assessment_ids


async def _seed_groups(
    engine: AsyncEngine, claims: list[dict[str, Any]], assessment_ids: dict[str, uuid.UUID]
) -> None:
    """Запечатанные группы независимости и роли улик — ровно те строки, что показывает стенд."""

    for row in claims:
        prov = row["provenance"]
        members = [
            (uuid.UUID(g["source_id"]), str(g["group_id"]), str(g["basis"]))
            for g in prov["source_groups"]
        ]
        roles = {uuid.UUID(r["evidence_id"]): str(r["role"]) for r in prov["assessment_evidence_roles"]}
        await _fix_groups(
            engine, assessment_id=assessment_ids[row["claim"]], members=members, roles=roles
        )


def _page_bytes(sha: str) -> bytes:
    """Побайтовый нормализованный текст страницы стенда; CJK-страница сохранена
    JSON-экранированной (diff только ASCII), декод — ровно исходные байты."""

    plain = FIXTURE_DIR / f"{sha}.txt"
    if plain.exists():
        data = plain.read_bytes()
    else:
        data = json.loads((FIXTURE_DIR / f"{sha}.txt.json").read_text(encoding="ascii")).encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == sha, sha
    return data


async def _seed_journal_and_artifacts(
    engine: AsyncEngine, store: FilesystemArtifactStore, evidence: list[dict[str, Any]]
) -> None:
    """Журнал fetch'ей окна + нормализованные тексты страниц в хранилище артефактов."""

    seen: dict[str, str] = {}
    for row in evidence:
        meta = row.get("source_metadata") or {}
        sha = str(meta.get("normalized_sha256") or "")
        if sha and sha not in seen:
            text_bytes = _page_bytes(sha)
            assert sha == store.put(text_bytes, origin="research.fetch", trust_class="public", mime="text/plain")
            seen[sha] = str(text_bytes, encoding="utf-8")
    for index, row in enumerate(evidence):
        meta = row.get("source_metadata") or {}
        sha = str(meta.get("normalized_sha256") or "")
        if not sha:
            continue
        await _execute(
            engine,
            "INSERT INTO audit_events (id, session_id, sequence, type, actor, public_summary, payload) "
            "VALUES (:id, NULL, :seq, 'research_fetch_completed', 'research_proxy', :summary, CAST(:payload AS JSONB))",
            id=uuid.uuid4(),
            seq=1000 + index,
            summary=f"fetch {row['source_uri'][:60]}",
            payload=json.dumps(
                {
                    "source_id": row["source_id"],
                    "canonical_uri": row["source_uri"],
                    "normalized_sha256": sha,
                    "attribution_status": str(meta.get("attribution_status") or "not_attributed"),
                    "attribution_method": "host-source-attribution-v1",
                },
                ensure_ascii=False,
            ),
        )


# ─── снимки «до/после»: метки, группы, оценки, роли, скоупы, источники ─────────


async def _labels(scratch_url: str, claim_ids: dict[str, uuid.UUID]) -> dict[str, dict[str, Any]]:
    app, engine = await _make_app(scratch_url)
    try:
        out: dict[str, dict[str, Any]] = {}
        async with _client(app) as client:
            for prefix, claim_id in claim_ids.items():
                r = await client.get(f"/api/v1/knowledge/claims/{claim_id}")
                assert r.status_code == 200, (prefix, r.status_code)
                out[prefix] = dict(r.json()["reliability"])
        return out
    finally:
        await engine.dispose()


async def _snapshot_groups(engine: AsyncEngine) -> list[tuple[str, str, str]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.id::text AS claim, m.basis AS basis, m.group_id AS grp, s.canonical_uri AS uri "
                    "FROM source_independence_members m "
                    "JOIN source_independence_snapshots sn ON sn.id = m.snapshot_id "
                    "JOIN claim_assessments a ON a.source_independence_snapshot_id = sn.id "
                    "JOIN claims c ON c.id = a.claim_id JOIN sources s ON s.id = m.source_id "
                    "ORDER BY c.id, m.source_id"
                )
            )
        ).mappings().all()
    return [(str(r["claim"]), str(r["basis"]), str(r["grp"]), str(r["uri"])) for r in rows]


async def _snapshot_assessments(engine: AsyncEngine) -> list[tuple[str, str, str, str, tuple[str, ...]]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.id::text AS claim, a.effective_grade AS g, a.epistemic_status AS e, "
                    "a.confidence::text AS f, "
                    "(SELECT array_agg(r.reason ORDER BY r.n) FROM (SELECT payload FROM audit_events "
                    "  WHERE type IN ('claim_assessed', 'reassessment_job_completed') "
                    "  AND payload->>'assessment_id' = a.id::text "
                    "  ORDER BY occurred_at DESC, sequence DESC LIMIT 1) l "
                    "LEFT JOIN LATERAL jsonb_array_elements_text("
                    "  COALESCE(l.payload->'reasons', '[]'::jsonb)) "
                    "WITH ORDINALITY AS r(reason, n) ON TRUE) AS reasons "
                    "FROM claim_assessments a JOIN claim_assessment_heads h ON h.current_assessment_id = a.id "
                    "JOIN claims c ON c.id = a.claim_id ORDER BY c.id"
                )
            )
        ).mappings().all()
    return [
        (str(r["claim"]), str(r["g"]), str(r["e"]), str(r["f"]), tuple(str(x) for x in (r["reasons"] or ())))
        for r in rows
    ]


async def _snapshot_roles(engine: AsyncEngine) -> list[tuple[str, str, str]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT a.claim_id::text AS claim, e.id::text AS ev, ae.role AS role "
                    "FROM assessment_evidence ae "
                    "JOIN evidence e ON e.id = ae.evidence_id "
                    "JOIN claim_assessments a ON a.id = ae.assessment_id "
                    "ORDER BY a.claim_id, e.id"
                )
            )
        ).mappings().all()
    return [(str(r["claim"]), str(r["ev"]), str(r["role"])) for r in rows]


async def _snapshot_scopes(engine: AsyncEngine) -> list[tuple[str, str]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(text("SELECT id::text AS id, scope::text AS sc FROM evidence ORDER BY id"))
        ).mappings().all()
    return [(str(r["id"]), str(r["sc"])) for r in rows]


async def _snapshot_sources(engine: AsyncEngine) -> list[tuple[str, str | None, bool]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id::text AS id, parent_source_id::text AS parent, metadata ? 'derivative_of' AS marked "
                    "FROM sources ORDER BY id"
                )
            )
        ).mappings().all()
    return [(str(r["id"]), r["parent"], bool(r["marked"])) for r in rows]


async def _basis_kinds(engine: AsyncEngine, claim_id: uuid.UUID) -> set[str]:
    """Виды оснований группировки под ТЕКУЩЕЙ оценкой утверждения (single/domain/parent).

    T7.120 (часть 2): родительские uuid новых якорей различаются между прогонами,
    поэтому группировку сравнивают по виду основания, а не по литералу."""

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT DISTINCT split_part(m.basis, ':', 1) AS kind FROM source_independence_members m "
                    "JOIN source_independence_snapshots sn ON sn.id = m.snapshot_id "
                    "JOIN claim_assessments a ON a.source_independence_snapshot_id = sn.id "
                    "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id "
                    "AND h.assessment_state = 'current' WHERE a.claim_id = :c"
                ),
                {"c": claim_id},
            )
        ).all()
    return {str(r[0]) for r in rows}


async def _source_parent_uri(engine: AsyncEngine, source_id: str) -> str:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT p.canonical_uri FROM sources s "
                    "LEFT JOIN sources p ON p.id = s.parent_source_id "
                    "WHERE s.id = CAST(:id AS uuid)"
                ),
                {"id": source_id},
            )
        ).first()
    assert row is not None
    return str(row[0])


async def _source_uri(engine: AsyncEngine, source_id: str) -> str:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT canonical_uri FROM sources WHERE id = CAST(:id AS uuid)"), {"id": source_id}
            )
        ).first()
    assert row is not None
    return str(row[0])


async def _run_window(
    scratch_url: str, store: FilesystemArtifactStore, *, dry_run: bool
) -> Any:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await reattribute_evidence_window(factory, store, since=SINCE, dry_run=dry_run)
    finally:
        await engine.dispose()


# ─── сам тест ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_twenty_stand_claims_label_table_was_becomes(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # эпоха v6 (комментарий у CURRENT_VALUE_METHOD): переатрибуция считает ТЕКУЩИМ методом v6,
    # иначе стенд T7.101 пересматривался бы свежим v7 (T7.107) и таблица была бы о другом стенде
    import apps.research_proxy.reattribution as reattribution

    monkeypatch.setattr(reattribution, "CURRENT_VALUE_METHOD_VERSION", CURRENT_VALUE_METHOD)
    monkeypatch.setattr(reattribution, "_SELF_PUBLICATION_METHOD", CURRENT_VALUE_METHOD)
    monkeypatch.setattr(reattribution, "VALUE_ATTRIBUTION_METHOD_VERSION", CURRENT_VALUE_METHOD)
    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    claims, evidence = _load_fixture()
    claim_ids = {row["claim"][:8]: uuid.UUID(row["claim"]) for row in claims}

    await _seed_stand_state(engine, evidence)
    assessment_ids = await _seed_claims_and_assessments(engine, claims)
    await _seed_evidence(engine, evidence)
    await _seed_groups(engine, claims, assessment_ids)
    await _seed_journal_and_artifacts(engine, store, evidence)

    # ── «было»: ровно пять подписей «Опубликовано производителем», остальные — слабо ──
    was_labels = await _labels(scratch_url, claim_ids)
    was_groups = await _snapshot_groups(engine)
    was_assessments = await _snapshot_assessments(engine)
    was_roles = await _snapshot_roles(engine)
    was_scopes = await _snapshot_scopes(engine)
    was_sources = await _snapshot_sources(engine)
    # T7.120 (часть 2): «было» группировки трёх утверждений, чья группировка изменится;
    # T7.129a: четвёртое — 6d9a12ff (страница forbes сворачивается на якорь Банка России)
    was_kinds = {
        prefix: await _basis_kinds(engine, claim_ids[prefix])
        for prefix in ("07126d06", "cc75e97f", "c6729d4c", "6d9a12ff")
    }

    producer_was = {p: lab for p, lab in was_labels.items() if lab["label"] == PRODUCER_LABEL}
    assert set(producer_was) == set(WAS_PRODUCER), producer_was
    assert len(producer_was) == 4
    # T7.116 (часть б): доказательство флипа ровно одной строки — `6d9a12ff` был пятой
    # подписью T7.101 (указатель на PDF ЦБ при утверждениях без имени производителя), теперь
    # он честное «Подтверждено слабо»; уровень и причины оценки той же строки не изменились.
    assert was_labels["6d9a12ff"]["label"] == WEAK_LABEL, was_labels["6d9a12ff"]
    assert was_labels["6d9a12ff"]["level"] == LEVEL_WEAK, was_labels["6d9a12ff"]
    for prefix, label in producer_was.items():
        assert WAS_PRODUCER[prefix] in str(label["hint"]), (prefix, label)
    for prefix, label in was_labels.items():
        if prefix in WAS_PRODUCER:
            assert label["label"] == PRODUCER_LABEL, prefix
        else:
            assert label["label"] == WEAK_LABEL, (prefix, label)
        assert label["level"] == LEVEL_WEAK, prefix

    # ── dry-run: план на 33 улики стенда ──
    plan = await _run_window(scratch_url, store, dry_run=True)
    assert plan.dry_run and plan.changed_evidence == 0 and not plan.changed_source_ids, plan.rows
    assert len(plan.rows) == 33, len(plan.rows)

    def _rows_with(action: str) -> list[Any]:
        return [row for row in plan.rows if row.action == action]

    # T7.120a (ADR-0029 §24): третья собственная публикация — cc75e97f на странице события ЦБ:
    # страница стоит на home-хосте семьи cbr, называет Банк России и не может быть пересказом
    # самой себя; отдельного якоря инФОМ больше нет (было T7.120: derivative→инФОМ)
    self_rows = _rows_with(SELF_ACTION)
    assert len(self_rows) == 3, self_rows
    assert {(str(r.claim_id)[:8], r.canonical_uri) for r in self_rows} == {
        ("c6729d4c", URL_INFL_26_09),
        ("c6729d4c", URL_CPD_2026_7),
        ("cc75e97f", URL_EVENT_32879),
    }, self_rows
    assert all(r.was_status == NO_DECISION_YET and r.now_status == "self_primary" for r in self_rows), self_rows
    assert all(r.primary_name == "Банк России" for r in self_rows), self_rows

    veto_rows = _rows_with(VETO_ACTION)
    assert len(veto_rows) == 2, veto_rows
    assert {str(r.claim_id)[:8] for r in veto_rows} == {"2d010d53", "58e2d5d4"}, veto_rows
    assert all(r.now_status == "self_primary" and r.canonical_uri == URL_INFL_25_12 for r in veto_rows), veto_rows

    corrected_rows = _rows_with(CORRECTED_ACTION)
    assert len(corrected_rows) == 5, corrected_rows
    assert {(str(r.claim_id)[:8], r.canonical_uri) for r in corrected_rows} == {
        ("9266248e", URL_SBERCIB_2025),
        ("9266248e", URL_NBJ),
        ("c970bc08", URL_SBERCIB_2025),
        ("c970bc08", URL_NBJ),
        ("7cc2b578", URL_SBERCIB_2025),
    }, corrected_rows

    decided = _rows_with(DECIDED_ACTION)
    assert len(decided) == 8, decided
    assert all(r.was_method == CURRENT_VALUE_METHOD for r in decided), decided
    assert {str(r.claim_id)[:8] for r in decided} == {"9266248e", "391c4388", "136a0ca0"}, decided

    # строгое происхождение (T7.120, законно): прогноз Альфа-Банка на реле;
    # улица cc75e97f перешла из write_rows в self_rows (семья cbr, T7.120a);
    # T7.129a: второй строкой — forbes-перепечатка опроса «инФОМ» для 6d9a12ff
    # (пятое сопряжение `statement_names_producer`, ADR-0029 §26.3)
    write_rows = _rows_with(WRITE_ACTION)
    assert len(write_rows) == 2, write_rows
    assert {(str(r.claim_id)[:8], r.canonical_uri) for r in write_rows} == {
        ("07126d06", URL_ALFA_INTL),
        ("6d9a12ff", URL_FORBES_552221),
    }, write_rows
    assert all(r.now_status == "derivative" for r in write_rows), write_rows
    assert {r.primary_name for r in write_rows} == {"Альфа-Банк", "Банк России"}, write_rows

    not_collected = _rows_with(NOT_COLLECTED_ACTION)
    assert len(not_collected) == 33 - 3 - 2 - 2 - 5 - 8, [row.action for row in plan.rows]

    # ── apply: пять записей (три собственные публикации семьи cbr + строгий пересказ
    # Альфа-Банка + перепечатка опроса «инФОМ» на forbes, T7.129a) и каскад переоценки
    # для группы Альфа-Банка и затронутой страницы forbes ──
    report = await _run_window(scratch_url, store, dry_run=False)
    assert report.changed_evidence == 5, report.rows
    # изменённые источники — строки источников двух затронутых пересказов: реле-страница
    # Альфа-Банка и страница forbes; новый якорь alfabank.ru создаётся отдельно и в
    # changed_source_ids не входит
    assert len(report.changed_source_ids or ()) == 2, report.rows
    # каскад задел и 136a0ca0: его улики опираются на ту же реле-страницу;
    # T7.129a: плюс 6d9a12ff — его улица получила запись происхождения значения
    assert report.invalidated_heads == 3 and report.jobs_created == 3, report.rows
    assert report.worker is not None and report.worker.completed == 3, report.worker

    # записи появились на трёх собственных публикациях cbr-семьи и на улике Альфа-Банка;
    # скоупы остальных 29 улики не изменились
    after_scopes = await _snapshot_scopes(engine)
    changed_scopes = [
        (a[0], b[0]) for a, b in zip(was_scopes, after_scopes, strict=True) if a[1] != b[1]
    ]
    assert sorted(x[0] for x in changed_scopes) == sorted(
        str(uuid.UUID(evidence[i]["evidence_id"]))
        for i in range(len(evidence))
        if evidence[i]["evidence_id"][:8] in EVIDENCE_SELF + EVIDENCE_SELF_CBR_EVENT + EVIDENCE_NEW_WRITES
    ), changed_scopes
    for row in after_scopes:
        if row[0][:8] in EVIDENCE_SELF + EVIDENCE_SELF_CBR_EVENT:
            scope = json.loads(row[1])
            rec = scope["value_self_publication"]
            assert rec["schema"] == "host-value-self-publication-v1", rec
            assert rec["primary_key"] == "cbr" and rec["primary_name"] == "Банк России", rec
            assert rec["method"] == CURRENT_VALUE_METHOD, rec
            assert "parent_source_id" not in rec, "первоисточник не может быть родителем самого себя"
            assert scope.get("value_attribution") is None, rec

    # строгое сопряжение во фрагменте (value_and_primary_in_fragment) — у пересказа Альфа-Банка;
    # T7.120a: записи «infom» больше нет: улица cc75e97f получила собственную публикацию семьи
    new_records = {
        row[0][:8]: json.loads(row[1])["value_attribution"]
        for row in after_scopes
        if row[0][:8] in EVIDENCE_NEW_WRITES
    }
    assert set(new_records) == set(EVIDENCE_NEW_WRITES), new_records
    # строгое сопряжение (T7.120) осталось строгим; новая запись — пятое сопряжение v17,
    # записанное под именем КОРНЯ семьи (инФОМ → Банк России, ADR-0029 §24)
    assert new_records["5fab715e"]["primary_key"] == "alfa", new_records
    assert new_records["5fab715e"]["pairing"] == "value_and_primary_in_fragment", new_records
    assert new_records["e62b7834"]["primary_key"] == "cbr", new_records
    assert new_records["e62b7834"]["primary_name"] == "Банк России", new_records
    assert new_records["e62b7834"]["pairing"] == "statement_names_producer", new_records
    for rec in new_records.values():
        assert rec["method"] == CURRENT_VALUE_METHOD, rec
        assert rec.get("parent_source_id"), rec

    # ── «стало»: единственная изменившаяся метка — c6729d4c ──
    now_labels = await _labels(scratch_url, claim_ids)
    producer_now = {p for p, lab in now_labels.items() if lab["label"] == PRODUCER_LABEL}
    assert producer_now == set(BECAME_PRODUCER), producer_now
    assert now_labels["c6729d4c"]["label"] == PRODUCER_LABEL
    assert "Банк России" in now_labels["c6729d4c"]["hint"], now_labels["c6729d4c"]
    assert now_labels["c6729d4c"]["level"] == LEVEL_WEAK, "витрина не назначает уровень"
    # две дополнительные подписи названы производителем из записи; cc75e97f — «Банк России»
    # (собственная публикация семьи cbr), не «инФОМ»: имя опроса осталось в basis, производитель один
    assert "Альфа-Банк" in now_labels["07126d06"]["hint"], now_labels["07126d06"]
    assert "Банк России" in now_labels["cc75e97f"]["hint"], now_labels["cc75e97f"]
    for prefix in WAS_PRODUCER:
        assert now_labels[prefix] == was_labels[prefix], prefix
    for prefix, label in now_labels.items():
        if prefix not in BECAME_PRODUCER:
            assert label["label"] == WEAK_LABEL, (prefix, label)

    # ── оценки не изменились вовсе; группировка изменилась ровно для одного утверждения
    # (Альфа-Банк); в источниках создан один якорь производителя; роли — тот же набор троек
    # плюс строки нового снимка переоценки ──
    assert await _snapshot_assessments(engine) == was_assessments
    now_groups = await _snapshot_groups(engine)
    # новых снимков переоценки: 1 (07126d06) + 2 (136a0ca0 — его улики держатся за ту же
    # реле-страницу) + 1 (6d9a12ff — страница forbes свернута на якорь Банка России, T7.129a)
    # строк участников (было T7.120: пять — каскад задел c6729d4c и cc75e97f
    # из-за якоря инФОМ в группе ЦБ)
    assert len(now_groups) == len(was_groups) + 4, (len(was_groups), len(now_groups))
    now_kinds = {
        prefix: await _basis_kinds(engine, claim_ids[prefix])
        for prefix in ("07126d06", "cc75e97f", "c6729d4c", "6d9a12ff")
    }
    # страница реле с прогнозом Альфа-Банка: была одиночной группой — свернута указателем
    # на новый якорь alfabank.ru; группировки cc75e97f и c6729d4c не изменились: собственные
    # публикации семьи cbr родителя не создают (было T7.120: к cc75e97f добавлялся родитель
    # infom.org.ru)
    assert was_kinds["07126d06"] == {"single"} and now_kinds["07126d06"] == {"parent"}, was_kinds
    assert was_kinds["cc75e97f"] == now_kinds["cc75e97f"] == {"domain"}, was_kinds
    assert was_kinds["c6729d4c"] == now_kinds["c6729d4c"] == {"domain"}, was_kinds
    # T7.129a: улица 6d9a12ff вела на документ ЦБ (страничный указатель) и ведёт на него же —
    # вид группировки не изменился; изменилось то, что строку называет запись происхождения
    # значения (витрина подписывает её по записи, T7.109)
    assert was_kinds["6d9a12ff"] == now_kinds["6d9a12ff"] == {"parent"}, was_kinds
    now_roles = await _snapshot_roles(engine)
    assert set(now_roles) == set(was_roles) and len(now_roles) == len(was_roles) + 4, (
        len(was_roles),
        len(now_roles),
    )
    now_sources = await _snapshot_sources(engine)
    assert all(row in now_sources for row in was_sources), now_sources
    added_sources = [row for row in now_sources if row not in was_sources]
    # новый якорь ровно один — alfabank.ru (было T7.120: два, включая infom.org.ru);
    # T7.129a: для записи пятого сопряжения на forbes нового якоря не потребовалось —
    # `resolve_primary_source` переиспользует уже существующий указатель Банка России
    assert len(added_sources) == 1 and len(now_sources) == len(was_sources) + 1, added_sources
    assert {await _source_uri(engine, row[0]) for row in added_sources} == NEW_ANCHOR_URIS, added_sources
    # родитель строки forbes остался прежним (документ прогноза ЦБ): изменилось только то, что
    # строку теперь называет запись происхождения значения, а не страничный указатель (T7.109)
    assert await _source_parent_uri(engine, FORBES_SOURCE_ID) == ANCHOR_CBR_PDF_URI

    # ── второй прогон идемпотентен (T7.101): дописывать на Infl_exp_26-09 нечего — заблуждение
    # T7.120 дописывало одну запись только потому, что первый прогон заводил якорь инФОМ, а
    # семейная склейка T7.120a его не создаёт (собственная публикация родителя не имеет) ──
    again = await _run_window(scratch_url, store, dry_run=False)
    assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
    again_self = [
        row
        for row in again.rows
        if row.action == SELF_DONE_ACTION
        and str(row.claim_id)[:8] == "c6729d4c"
        and row.canonical_uri in (URL_INFL_26_09, URL_CPD_2026_7)
    ]
    assert len(again_self) == 2, again.rows
    assert all(
        row.was_status == "собственная публикация → Банк России (host-value-attribution-v6)"
        for row in again_self
    ), again_self

    # ── свёртка дублей значения (T7.99): согласованность пары после прогона ──
    statements = {row["claim"][:8]: (row["detail"]["claim_type"], row["detail"]["statement"]) for row in claims}
    # якорь свёртки — более ранняя запись по created_at (136a0ca0 сворачивается под c6729d4c,
    # e189c80d — под 9266248e); предикат направлен: сворачиваемая запись не шире якоря по периоду
    pair_folded = value_duplicate_pair(
        op_claim_type=statements["136a0ca0"][0],
        op_statement=statements["136a0ca0"][1],
        op_as_of_year=None,
        candidate_claim_type=statements["c6729d4c"][0],
        candidate_statement=statements["c6729d4c"][1],
        candidate_as_of_year=None,
    )
    assert pair_folded, "136a0ca0 под якорем c6729d4c — дубль по значению (T7.99)"
    assert now_labels["c6729d4c"]["label"] == now_labels["136a0ca0"]["label"] == PRODUCER_LABEL, (
        now_labels["c6729d4c"],
        now_labels["136a0ca0"],
    )
    pair_559 = value_duplicate_pair(
        op_claim_type=statements["e189c80d"][0],
        op_statement=statements["e189c80d"][1],
        op_as_of_year=None,
        candidate_claim_type=statements["9266248e"][0],
        candidate_statement=statements["9266248e"][1],
        candidate_as_of_year=None,
    )
    assert pair_559, "e189c80d под якорем 9266248e — дубль по значению (T7.99)"
    # честный расход: у e189c80d (garant, пересказ без записанного указателя) записывать нечего
    assert now_labels["9266248e"]["label"] == PRODUCER_LABEL
    assert now_labels["e189c80d"]["label"] == WEAK_LABEL, now_labels["e189c80d"]
