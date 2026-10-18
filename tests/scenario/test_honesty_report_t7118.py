"""T7.118: отчёт честности — сценарные тесты на синтетических записях (т1–т8).

Тестовая БД (`migrated_db`), без LLM и сети. Все флаги и счётчики проверяются на данных,
которые сидируются как их пишет продукт: карточки знания (claims/assessments/heads), улики с
записями атрибуции, снимки групп независимости, лента `audit_events` с явными `sequence` и
реальными типами событий (`action_started`/`action_completed`/`action_failed` cPayload ворот
`denied:true` — те же формы, что пишет `apps/orchestrator/orchestrator.py`).

Ловушка CliRunner (AGENTS §7, T7.59): CLI-команда поднимает свой event loop, поэтому тесты
команды не держат общий engine — сидинг своим engine'ом с `dispose()`, вызов команды своим.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from hostctl.cli import main
from packages.memory.honesty_report import (
    SCHEMA_VERSION,
    HonestyReport,
    HonestyReportError,
    collect_honesty_report,
)

pytestmark = pytest.mark.scenario

# В отчёте id усекаются до первых 8 hex-знаков — у констант различается первая группа.
Q_SALARY = uuid.UUID("01000000-0000-4000-8000-000000000001")
Q_INFLATION = uuid.UUID("02000000-0000-4000-8000-000000000002")
Q_CONTROL = uuid.UUID("03000000-0000-4000-8000-000000000003")
S1 = uuid.UUID("11000000-0000-4000-8000-000000000010")
S2 = uuid.UUID("12000000-0000-4000-8000-000000000011")
S3 = uuid.UUID("13000000-0000-4000-8000-000000000012")
SRC_ROSSTAT = uuid.UUID("a1000000-0000-4000-8000-000000000101")
SRC_RETOLD = uuid.UUID("a2000000-0000-4000-8000-000000000102")
SRC_KONSTRUKTOR = uuid.UUID("a3000000-0000-4000-8000-000000000103")
SRC_FRANK = uuid.UUID("a4000000-0000-4000-8000-000000000104")
SRC_SBERCIB = uuid.UUID("a5000000-0000-4000-8000-000000000105")
C1 = uuid.UUID("c1000000-0000-4000-8000-000000000201")  # безработица под вопросом о зарплатах
C2 = uuid.UUID("c2000000-0000-4000-8000-000000000202")  # честное E3 по теме вопроса
C3 = uuid.UUID("c3000000-0000-4000-8000-000000000203")  # собственная публикация SberCIB
C4 = uuid.UUID("c4000000-0000-4000-8000-000000000204")  # опертое, не причастное
C5 = uuid.UUID("c5000000-0000-4000-8000-000000000205")  # переиспользованное оценкой сессии
C6 = uuid.UUID("c6000000-0000-4000-8000-000000000206")  # перепроверенное сессией
SALARY_TEXT = "Средние зарплаты в России в 2026 году"
INFLATION_TEXT = "Какой прогноз инфляции в России на 2026 год?"
CONTROL_TEXT = "Что будет с ценами на продукты? (контроль v118a)"

STEPS_EVENTS_S1: list[tuple[int, str, Any]] = [
    (10, "action_started", {"action_id": "aa1", "tool": "web.search",
                            "arguments": json.dumps({"query": "средние зарплаты в России rosstat 2026"})}),
    (11, "action_completed", {"action_id": "aa1", "ok": True}),
    (12, "action_started", {"action_id": "aa2", "tool": "research.fetch",
                            "arguments": json.dumps({"url": "https://rosstat.gov.ru/salaries-2026"})}),
    (13, "action_completed", {"action_id": "aa2", "ok": True}),
    (14, "research_content_read", {"source_id": str(SRC_ROSSTAT),
                                   "url": "https://rosstat.gov.ru/salaries-2026",
                                   "normalized_sha256": "sha-pay-1", "bytes": 4200,
                                   "truncated": False, "mode": "page_text"}),
    # отказ ворот почти-дубликатов (форма payload — как у orchestrator, до строки действия)
    (15, "action_failed", {"denied": True, "reason": "search_query_near_duplicate",
                           "tool": "web.search", "arguments_hash": "h1",
                           "similar_query": "зарплаты России rosstat 2026", "similar_step": 1,
                           "similarity": 0.83}),
    (16, "action_started", {"action_id": "aa3", "tool": "web.search",
                            "arguments": json.dumps({"query": "оценки зарплат hh ru SuperJob банки"})}),
    (17, "action_started", {"action_id": "aa4", "tool": "web.search",
                            "arguments": json.dumps({"query": "банки оценки прогноз 2026"})}),
    (18, "action_started", {"action_id": "aa5", "tool": "research.fetch",
                            "arguments": json.dumps({"url": "https://frankmedia.example/article"})}),
    (19, "action_failed", {"action_id": "aa5", "ok": False, "error": "non-200 status: 401", "data": None}),
    (20, "action_failed", {"denied": True, "reason": "host_unavailable_in_session",
                           "tool": "research.fetch", "arguments_hash": "h2",
                           "host": "frankmedia.example", "error_kind": "401", "failed_step": 4}),
    (21, "action_started", {"action_id": "aa6", "tool": "research.fetch",
                            "arguments": json.dumps({"url": "https://frankmedia.example/article-2"})}),
    (22, "action_failed", {"action_id": "aa6", "ok": False, "error": "timeout: connect", "data": None}),
    (23, "action_started", {"action_id": "aa7", "tool": "research.fetch",
                            "arguments": json.dumps({"url": "https://rosstat.gov.ru/salaries-2026"})}),
    (24, "action_completed", {"action_id": "aa7", "ok": True}),
    (25, "research_content_read", {"source_id": str(SRC_ROSSTAT),
                                   "url": "https://rosstat.gov.ru/salaries-2026",
                                   "normalized_sha256": "sha-pay-1", "bytes": 4200,
                                   "truncated": False, "mode": "page_text"}),
    (26, "action_started", {"action_id": "aa8", "tool": "memory.search",
                            "arguments": json.dumps({"query": "зарплата 2026"})}),
    (27, "action_completed", {"action_id": "aa8", "ok": True}),
]

STEPS_EVENTS_S2: list[tuple[int, str, Any]] = [
    (10, "action_started", {"action_id": "bb1", "tool": "research.fetch",
                            "arguments": json.dumps({"url": "https://frankmedia.example/other"})}),
    (11, "action_failed", {"action_id": "bb1", "ok": False, "error": "non-200 status: 404", "data": None}),
]


async def _execute(engine: AsyncEngine, sql: str, **params: Any) -> None:
    async with engine.connect() as conn:
        await conn.execute(text(sql), params)
        await conn.commit()


async def _count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


async def _snapshot_id(engine: AsyncEngine) -> uuid.UUID:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global'")
            )
        ).scalar_one()


async def _seed_world(engine: AsyncEngine) -> None:
    """Синтетический мир отчёта: 3 вопроса, 3 сессии, 5 источников, 6 утверждений, лента шагов."""
    snapshot = await _snapshot_id(engine)
    for qid, text_value in ((Q_SALARY, SALARY_TEXT), (Q_INFLATION, INFLATION_TEXT), (Q_CONTROL, CONTROL_TEXT)):
        await _execute(
            engine,
            "INSERT INTO questions (id, text, origin, state, priority) "
            "VALUES (:id, :t, 'seeded', 'verified', 0)",
            id=qid, t=text_value,
        )
    await _execute(
        engine,
        "INSERT INTO sessions (id, state, question_id, config_snapshot_id, termination_reason, "
        "started_at, finished_at) VALUES "
        "(:s1, 'succeeded', :q1, :c, 'goal_reached', now() - interval '35 minutes', now() - interval '20 minutes'),"
        "(:s2, 'failed', :q1, :c, 'budget_exhausted', now() - interval '15 minutes', now() - interval '12 minutes'),"
        "(:s3, 'succeeded', :q2, :c, 'goal_reached', now() - interval '40 minutes', now() - interval '30 minutes')",
        s1=S1, s2=S2, s3=S3, q1=Q_SALARY, q2=Q_INFLATION, c=snapshot,
    )
    await _execute(
        engine,
        "INSERT INTO model_runs (id, session_id, turn_id, phase, model_fingerprint, input_tokens, "
        "output_tokens) VALUES "
        "(:t1, :s1, :u1, 'explorer', '{}', 1000, 300), (:t2, :s1, :u2, 'curator', '{}', 900, 200), "
        "(:t3, :s2, :u3, 'explorer', '{}', 400, 100)",
        t1=uuid.uuid4(), t2=uuid.uuid4(), t3=uuid.uuid4(), s1=S1, s2=S2,
        u1=uuid.uuid4(), u2=uuid.uuid4(), u3=uuid.uuid4(),
    )
    await _execute(
        engine,
        "INSERT INTO sources (id, source_type, canonical_uri, retrieved_at, content_hash) VALUES "
        "(:a, 'external_url', 'https://rosstat.gov.ru/salaries-2026', now(), :ha),"
        "(:b, 'external_url', 'https://example-news.example/payments-2026', now(), :hb),"
        "(:c, 'external_url', 'https://konstruktor.example/rating-salaries', now(), :hc),"
        "(:d, 'external_url', 'https://frankmedia.example/article', now(), :hd),"
        "(:e, 'external_url', 'https://sbercib.example/inflation-forecast', now(), :he)",
        a=SRC_ROSSTAT, b=SRC_RETOLD, c=SRC_KONSTRUKTOR, d=SRC_FRANK, e=SRC_SBERCIB,
        ha="a" * 64, hb="b" * 64, hc="c" * 64, hd="d" * 64, he="e" * 64,
    )

    claims = {
        C1: ("Уровень безработицы в России в 2026 году составил около трёх процентов", S1, S1),
        C2: ("Средняя начисленная зарплата в России превысила 70 тысяч рублей в 2026 году", S1, S1),
        C3: ("Инфляция в России по оценке аналитиков составит около пяти процентов", S3, S3),
        C4: ("Денежные доходы населения выросли на 4 процента", S3, S3),
        C5: ("Рост зарплат в России замедлился к 2026 году", S3, S1),
        C6: ("Инфляция в России по прогнозам равна четырём процентам", S3, S1),
    }
    grades = {C1: "E3", C2: "E3", C3: "E3", C4: "E2", C5: "E2", C6: "E2"}
    for cid, (statement, created_session, assessed_session) in claims.items():
        await _execute(
            engine,
            "INSERT INTO claims (id, statement, claim_type, freshness_status, created_in_session) "
            "VALUES (:id, :s, 'external_fact', 'fresh', :c)",
            id=cid, s=statement, c=created_session,
        )
        await _execute(
            engine,
            "INSERT INTO claim_assessments (id, claim_id, effective_grade, epistemic_status, "
            "rules_version, rules_hash, evidence_set_hash, assessed_scope, confidence, valid, created_in_session) "
            "VALUES (:a, :c, :g, 'supported', 'rules-v2', 'h', 'ev', '{\"x\": 1}', 0.9, true, :s)",
            a=uuid.UUID(f"00000000-0000-4000-8000-0000000003{cid.hex[-2:]}"), c=cid, g=grades[cid],
            s=assessed_session,
        )
        await _execute(
            engine,
            "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
            "current_assessment_id, epistemic_status, prepared_by) VALUES "
            "(:c, (SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global'), "
            "'current', :a, 'supported', 'rules_activation')",
            c=cid, a=uuid.UUID(f"00000000-0000-4000-8000-0000000003{cid.hex[-2:]}"),
        )

    # улики + записи атрибуции (формы scope — из packages/memory/scope.py)
    value_record = {
        "schema": "host-value-attribution-v1",
        "primary_key": "rosstat",
        "primary_name": "Федеральная служба государственной статистики",
        "primary_uri": "https://rosstat.gov.ru/salaries-2026",
        "parent_source_id": str(SRC_ROSSTAT),
        "method": "host-value-numeric-v2",
        "basis_fragment": "около трёх процентов",
    }
    self_publication = {
        "schema": "host-value-self-publication-v1",
        "primary_key": "sbercib",
        "primary_name": "SberCIB Investment Research",
        "primary_uri": "https://sbercib.example/inflation-forecast",
        "method": "host-value-self-publication-v2",
        "basis_fragment": "по оценке аналитиков",
    }
    evidence_rows = [
        (C1, SRC_ROSSTAT, {"x": 1}),
        (C1, SRC_RETOLD, {"x": 1, "value_attribution": value_record}),
        (C2, SRC_ROSSTAT, {"x": 1}),
        (C2, SRC_KONSTRUKTOR, {"x": 1}),
        (C3, SRC_SBERCIB, {"x": 1, "value_self_publication": self_publication}),
        (C4, SRC_ROSSTAT, {"x": 1}),
        (C5, SRC_KONSTRUKTOR, {"x": 1}),
        (C6, SRC_ROSSTAT, {"x": 1}),
    ]
    for index, (cid, source_id, scope) in enumerate(evidence_rows):
        await _execute(
            engine,
            "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, scope, "
            "source_id, chunk_id) VALUES (:id, :c, 'supports', 'source_assertion', :h, "
            "CAST(:scope AS JSONB), :s, :ch)",
            id=uuid.UUID(f"00000000-0000-4000-8000-0000000004{index:02d}"), c=cid, s=source_id,
            h=f"t7118-{index}", scope=json.dumps(scope), ch=f"chunk-{index}",
        )

    # снимки групп независимости (synthetic: две группы — первоисточник и его перепечатка)
    snaps = [
        ("00000000-0000-4000-8000-000000000501", C1, [(SRC_ROSSTAT, "source:A"), (SRC_RETOLD, "source:B")]),
        ("00000000-0000-4000-8000-000000000502", C2, [(SRC_ROSSTAT, "source:A"), (SRC_KONSTRUKTOR, "source:B")]),
        ("00000000-0000-4000-8000-000000000503", C3, [(SRC_SBERCIB, "source:A")]),
    ]
    for snap_id, claim_id, members in snaps:
        await _execute(
            engine,
            "INSERT INTO source_independence_snapshots "
            "(id, algorithm_version, thresholds, psl_fingerprint, uri_normalizer_version) "
            "VALUES (:id, 'noezema-source-v1', '{}', 'f', 'v1')",
            id=uuid.UUID(snap_id),
        )
        for source_id, group_id in members:
            await _execute(
                engine,
                "INSERT INTO source_independence_members (snapshot_id, source_id, group_id, basis) "
                "VALUES (:s, :u, :g, 'canonical_uri')",
                s=uuid.UUID(snap_id), u=source_id, g=group_id,
            )
        await _execute(
            engine,
            "UPDATE claim_assessments SET source_independence_snapshot_id = :s WHERE claim_id = :c",
            s=uuid.UUID(snap_id), c=claim_id,
        )

    # роли улик действующей оценки
    role_rows = [
        ("00000000-0000-4000-8000-000000000301", "00000000-0000-4000-8000-000000000400"),
        ("00000000-0000-4000-8000-000000000301", "00000000-0000-4000-8000-000000000401"),
        ("00000000-0000-4000-8000-000000000302", "00000000-0000-4000-8000-000000000402"),
        ("00000000-0000-4000-8000-000000000302", "00000000-0000-4000-8000-000000000403"),
        ("00000000-0000-4000-8000-000000000303", "00000000-0000-4000-8000-000000000404"),
    ]
    for assessment_id, evidence_id in role_rows:
        await _execute(
            engine,
            "INSERT INTO assessment_evidence (assessment_id, evidence_id, role) VALUES (:a, :e, 'support')",
            a=uuid.UUID(assessment_id), e=uuid.UUID(evidence_id),
        )

    # лента: шаги сессий + события оценки + опора на записанное утверждение
    events_by_session = {
        S1: STEPS_EVENTS_S1,
        S2: STEPS_EVENTS_S2,
        S3: [
            (5, "action_started", {"action_id": "cc1", "tool": "web.search",
                                   "arguments": json.dumps({"query": "инфляция прогноз аналитики cbr 2026"})}),
            (6, "action_completed", {"action_id": "cc1", "ok": True}),
        ],
    }
    for session_id, events in events_by_session.items():
        async with engine.connect() as conn:
            for sequence, event_type, payload in events:
                await conn.execute(
                    text(
                        "INSERT INTO audit_events (id, session_id, sequence, type, payload) "
                        "VALUES (:id, :s, :seq, :type, CAST(:payload AS JSONB))"
                    ),
                    {"id": uuid.uuid4(), "s": session_id, "seq": sequence, "type": event_type,
                     "payload": json.dumps(payload)},
                )
            await conn.commit()

    assessment_events = [
        (S1, 40, {"assessment_id": "00000000-0000-4000-8000-000000000301",
                  "claim_id": str(C1), "grade": "E3", "reasons": ["insufficient_independence"]}),
        (S1, 41, {"assessment_id": "00000000-0000-4000-8000-000000000302",
                  "claim_id": str(C2), "grade": "E3", "reasons": ["two_independent_groups"]}),
        (S3, 42, {"assessment_id": "00000000-0000-4000-8000-000000000303",
                  "claim_id": str(C3), "grade": "E3", "reasons": ["insufficient_independence"]}),
        (S1, 43, {"claim_id": str(C6), "reverified": True}),  # событие перепроверки (relation reverified)
        (S1, 44, {"claim_id": str(C5), "statement": "Рост зарплат в России замедлился",
                  "relied_claim_ids": [str(C4)]}),  # опора на уже записанное утверждение
    ]
    async with engine.connect() as conn:
        for session_id, sequence, payload in assessment_events:
            event_type = "claim_reverified" if payload.get("reverified") else (
                "claim_created" if "relied_claim_ids" in payload else "claim_assessed"
            )
            await conn.execute(
                text(
                    "INSERT INTO audit_events (id, session_id, sequence, type, payload) "
                    "VALUES (:id, :s, :seq, :type, CAST(:payload AS JSONB))"
                ),
                {"id": uuid.uuid4(), "s": session_id, "seq": sequence, "type": event_type,
                 "payload": json.dumps(payload)},
            )
        await conn.commit()


async def _collect(engine: AsyncEngine, **selection: Any) -> HonestyReport:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        return await collect_honesty_report(db, **selection)


def _find(report: HonestyReport, question_id: uuid.UUID) -> Any:
    for question in report.questions:
        if question.question_id == str(question_id)[:8]:
            return question
    raise AssertionError(f"вопрос {question_id} не найден в отчёте")


def _claim(question: Any, bucket: str, claim_id: uuid.UUID) -> Any:
    for claim in question.claims[bucket]:
        if claim.claim_id == str(claim_id)[:8]:
            return claim
    raise AssertionError(f"утверждение {claim_id} не в корзине {bucket}")


def _flag_codes(claim: Any) -> set[str]:
    return {flag.code for flag in claim.flags}


# ─── т1: пересказ одного первоисточника в двух группах E3 → флаг Ф1 ─────────────────────


async def test_f1_fires_for_retelling_of_one_primary_source_in_two_groups(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        report = await _collect(engine, question_ids=[Q_SALARY])
        claim = _claim(_find(report, Q_SALARY), "created", C1)
        assert claim.grade == "E3"
        codes = _flag_codes(claim)
        assert "f1" in codes, claim.flags
        assert "E2+ при общем производителе" in {flag.name for flag in claim.flags}
        assert len(claim.groups) == 2
        by_host = {source.host: source for group in claim.groups for source in group.sources}
        retold = by_host["example-news.example"]
        assert retold.parent_via == "value_record"
        assert retold.parent_source_id == str(SRC_ROSSTAT)[:8]
        assert retold.parent_host == "rosstat.gov.ru"
        assert report.totals["flags_f1"] >= 1
    finally:
        await engine.dispose()


# ─── т2: честный E3 с разными производителями → флага нет ──────────────────────────────


async def test_f1_absent_for_honest_e3_with_different_producers(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        report = await _collect(engine, question_ids=[Q_SALARY])
        claim = _claim(_find(report, Q_SALARY), "created", C2)
        assert claim.grade == "E3"
        assert len(claim.groups) == 2
        assert "f1" not in _flag_codes(claim), claim.flags
        hosts = {source.host for group in claim.groups for source in group.sources}
        assert "konstruktor.example" in hosts
    finally:
        await engine.dispose()


# ─── т3: утверждение о безработице на вопрос о зарплатах → Ф2 (и не на «по теме») ──────


async def test_f2_fires_for_off_topic_claim_and_not_for_on_topic_one(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        report = await _collect(engine, question_ids=[Q_SALARY])
        question = _find(report, Q_SALARY)
        off_topic = _claim(question, "created", C1)
        on_topic = _claim(question, "created", C2)
        assert "f2" in _flag_codes(off_topic), off_topic.flags
        assert "f2" not in _flag_codes(on_topic), on_topic.flags
        # перепроверенное чужое утверждение тоже честно помечается под этим вопросом
        reverified = _claim(question, "reverified", C6)
        assert "f2" in _flag_codes(reverified), reverified.flags
    finally:
        await engine.dispose()


# ─── т4: подпись производителя, не названного в формулировке → Ф3 ──────────────────────


async def test_f3_fires_when_producer_signature_names_unstated_author(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        report = await _collect(engine, question_ids=[Q_INFLATION])
        claim = _claim(_find(report, Q_INFLATION), "created", C3)
        assert claim.producer_signature is not None
        assert "SberCIB Investment Research" in claim.producer_signature
        assert "f3" in _flag_codes(claim), claim.flags
        assert "f2" not in _flag_codes(claim), claim.flags  # по теме — не флагать
    finally:
        await engine.dispose()


# ─── т5: шаги исследователя посчитаны по синтетической ленте ───────────────────────────


async def test_step_counters_counted_like_the_manager_gates_count(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        report = await _collect(engine, question_ids=[Q_SALARY])
        sessions = {session.session_id: session for session in _find(report, Q_SALARY).sessions}
        first = sessions[str(S1)[:8]].steps
        assert first.searches_total == 3
        assert first.searches_official == 1
        assert first.searches_other == 2
        assert first.wasted_searches == 1  # aa3: после поиска — другой поиск
        assert first.fetch_ok_total == 2 and first.fetch_ok_official == 2
        assert first.fetch_fail_total == 2
        by_host = {(item["host"], item["kind"]): item["count"] for item in first.fetch_failures_by_host}
        assert by_host[("frankmedia.example", "401")] == 1
        assert by_host[("frankmedia.example", "timeout")] == 1
        assert first.repeated_failed_host_fetches == 1  # aa6 по уже отказавшему хосту
        assert first.repeated_reads == 1  # aa7: тот же URL и тот же хэш нормализованного текста
        assert first.gate_near_duplicate == 1
        assert first.gate_host_unavailable == 1
        assert first.other_tools == 1
        assert first.args_unparsed == 0
        second = sessions[str(S2)[:8]].steps
        assert second.fetch_known_bad_host == 1  # frankmedia.example уже отказывал в S1
        assert second.fetch_fail_total == 1
        session_row = sessions[str(S1)[:8]]
        assert session_row.duration_seconds == 900.0
        assert session_row.model_calls == 2
        assert (session_row.input_tokens, session_row.output_tokens) == (1900, 500)
    finally:
        await engine.dispose()


# ─── т6: отчёт только читает — счётчики строк ключевых таблиц не меняются ──────────────


async def test_report_is_read_only_row_counts_do_not_change(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    tables = (
        "questions", "sessions", "claims", "claim_assessments", "claim_assessment_heads",
        "evidence", "sources", "assessment_evidence", "source_independence_snapshots",
        "source_independence_members", "model_runs", "audit_events",
    )
    try:
        await _seed_world(engine)
        before = {table: await _count(engine, table) for table in tables}
        await _collect(engine, question_ids=[Q_SALARY, Q_INFLATION])
        await _collect(engine, control_tag="v118a")
        after = {table: await _count(engine, table) for table in tables}
        assert before == after
    finally:
        await engine.dispose()


# ─── т7: --control-tag берёт ровно вопросы с суффиксом метки ───────────────────────────


async def test_control_tag_selects_only_suffixed_questions(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        report = await _collect(engine, control_tag="v118a")
        assert [question.question_id for question in report.questions] == [str(Q_CONTROL)[:8]]
        question = report.questions[0]
        assert question.text == CONTROL_TEXT
        assert question.missing_sessions is True
        assert question.answered is False
        no_match = await _collect(engine, control_tag="v118")
        assert no_match.questions == []
        with pytest.raises(HonestyReportError):
            await _collect(engine, control_tag="плохая метка!")
    finally:
        await engine.dispose()


# ─── т8: CLI honesty-report — JSON-схема стабильна, baseline даёт «было → стало» ────────


def test_cli_writes_stable_json_and_baseline_diff(migrated_db, tmp_path: Path) -> None:
    # Синхронный тест (ловушка T7.59): CLI-команда поднимает свой event loop, поэтому
    # сидинг — отдельный asyncio.run со своим engine и dispose, а CliRunner — вне цикла.
    scratch_url, _ = migrated_db

    async def _seed() -> None:
        engine = create_async_engine(scratch_url)
        try:
            await _seed_world(engine)
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    json_file = tmp_path / "report.json"
    runner = CliRunner()

    def _invoke(args: list[str]):
        return runner.invoke(main, ["honesty-report", *args], env={"NOEZEMA_DATABASE_URL": scratch_url})

    result = _invoke(["--questions", str(Q_SALARY), "--json", str(json_file)])
    assert result.exit_code == 0, result.output
    payload = json.loads(json_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["selection"]["question_ids"] == [str(Q_SALARY)]
    assert set(payload) == {
        "schema_version", "generated_at", "selection", "questions", "totals",
    }
    assert "## Итог прогона" in result.output
    assert "флаг Ф1" in result.output
    assert "Ответ есть" in result.output

    diff = _invoke(["--questions", str(Q_SALARY), "--baseline", str(json_file)])
    assert diff.exit_code == 0, diff.output
    assert "Было → стало" in diff.output
    assert "| метрика | было | стало | изменение |" in diff.output
    assert "| questions_answered | 1 | 1 | +0 |" in diff.output

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"schema_version": "иная-схема", "totals": {}}), encoding="utf-8")
    bad = _invoke(["--questions", str(Q_SALARY), "--baseline", str(broken)])
    assert bad.exit_code == 1
    assert "схема baseline не совпадает" in bad.output

    empty = _invoke([])
    assert empty.exit_code == 2
    assert "критерий отбора" in empty.output
