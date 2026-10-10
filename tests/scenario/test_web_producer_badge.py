"""Scenario (DB): подпись «Опубликовано производителем» на витринах (T7.87, ADR-0035 вариант A).

Репродюсеры стенда .92 (замеры — STATUS.md T7.86 и `~/fixtures-t786`):

* `9266248e…` «Инфляция в России по итогам 2025 года» — восемь прочитанных источников, ОДНА
  группа независимости: пять пересказов склеены с якорем Росстата по основанию `parent:`,
  остальные попали в ту же группу по домену и одиночеству; оценка E1/0,15 hypothesis записана
  рабочим переоценки с причиной `insufficient_independence`; на части улик есть запись об
  атрибутции значения на Росстат. Пользователь видел «Подтверждено слабо» и совет «нужны два
  независимых источника», хотя независимость здесь не «мало источников», а «все чтения — один
  производитель».
* `391c4388…` «Инфляционные ожидания …» — страница ЦБ и два пересказа (forbes, kommersant),
  склеенные с ней по якорю; одна группа независимости; атрибутции значения ведут на Банк России.

Что проверяется: витрина называет уже посчитанное правилами (ADR-0026). Уровень, порог и группы
здесь никто не пересчитывает; подпись появляется только там, где для неё есть записанные
основания, и не появляется там, где их нет. Отдельных запросов ради подписи нет — это тоже
тесты (бюджет карточки и списка).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.web.labels import MAX_HINT_CHARS
from apps.web.producer_view import INDEPENDENCE_SHORTFALL_REASONS
from apps.web.reliability import LEVEL_WEAK, PRODUCER_LABEL
from tests.scenario.test_web_answer_api import _client, _make, _seed_claim, _seed_question, _seed_session

pytestmark = [pytest.mark.scenario]

EFF = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"

# домашний адрес из словаря первоисточников и адрес конкретной страницы — разные вещи:
# указатель ведёт по хосту (`is_home_host`), имя наружу берётся из словаря.
ROSSTAT_URI = "https://rosstat.gov.ru/"
CBR_URI = "https://cbr.ru/"
ROSSTAT_HOME = "https://www.rosstat.gov.ru/press/cpi-2025.html"
CPI_STATEMENT = "Годовая инфляция в России по итогам 2025 года составила 5,59%"
SHORTFALL = sorted(INDEPENDENCE_SHORTFALL_REASONS)


# ─── посев: ровно те таблицы, которые пишет прод-контур ──────────────────────


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def _execute(engine: AsyncEngine, sql: str, **params: Any) -> None:
    async with engine.connect() as conn:
        await conn.execute(text(sql), params)
        await conn.commit()


async def _source(
    engine: AsyncEngine, *, uri: str, tag: str, parent: uuid.UUID | None = None
) -> uuid.UUID:
    source_id = uuid.uuid4()
    await _execute(
        engine,
        "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata, parent_source_id) "
        "VALUES (:id, 'external_url', :uri, :ch, '{}', :p)",
        id=source_id,
        uri=uri,
        ch=f"hash-{tag}",
        p=parent,
    )
    return source_id


def _attribution_scope(
    primary_key: str, primary_name: str, primary_uri: str, parent_source_id: uuid.UUID
) -> str:
    """Scope улики с записью об атрибутции значения — ровно в той форме, в какой её пишет
    прод-контур (`packages/memory/scope.py`: запись живёт внутри ключа `value_attribution`,
    схема `host-value-attribution-v1`, адрес — домашний адрес первоисточника)."""
    return _json(
        {
            "scope_schema": "host-scope-v1",
            "source_domain": "interfax.example",
            "value_attribution": {
                "schema": "host-value-attribution-v1",
                "primary_key": primary_key,
                "primary_name": primary_name,
                "primary_uri": primary_uri,
                "parent_source_id": str(parent_source_id),
                "method": "host-value-attribution-v5",
                "basis_fragment": "По данным Росстата",
                "pairing": "value_and_primary_in_fragment",
            },
        }
    )


async def _evidence(
    engine: AsyncEngine, *, claim_id: uuid.UUID, source_id: uuid.UUID, tag: str, scope: str
) -> uuid.UUID:
    evidence_id = uuid.uuid4()
    await _execute(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, scope, source_id, chunk_id) "
        "VALUES (:id, :c, 'supports', 'source_assertion', :h, CAST(:sc AS JSONB), :s, :k)",
        id=evidence_id,
        c=claim_id,
        h=f"identity-{tag}",
        sc=scope,
        s=source_id,
        k=f"chunk-{tag}",
    )
    return evidence_id


async def _current_assessment(engine: AsyncEngine, claim_id: uuid.UUID) -> uuid.UUID:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT current_assessment_id FROM claim_assessment_heads "
                    f"WHERE claim_id = :c AND config_snapshot_id = {EFF} AND assessment_state = 'current'"
                ),
                {"c": claim_id},
            )
        ).scalar_one()


async def _worker_assessment(
    engine: AsyncEngine,
    *,
    claim_id: uuid.UUID,
    grade: str,
    epistemic: str,
    confidence: float,
    reasons: list[str],
) -> uuid.UUID:
    """Оценка рабочего переоценки (`packages/memory/reassessment.py`): голова от
    `reassessment_worker`, оценка без строки сессии, причины — в событии
    `reassessment_job_completed` (sessii у рабочего нет)."""
    assessment_id = uuid.uuid4()
    await _execute(
        engine,
        "INSERT INTO claim_assessments (id, claim_id, effective_grade, epistemic_status, rules_version, "
        "rules_hash, evidence_set_hash, assessed_scope, confidence, valid, created_in_session) "
        "VALUES (:a, :c, :g, :e, 'rules-v2', 'h', 'ev', '{}', :f, true, NULL)",
        a=assessment_id,
        c=claim_id,
        g=grade,
        e=epistemic,
        f=confidence,
    )
    await _execute(
        engine,
        "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
        "current_assessment_id, epistemic_status, prepared_by) VALUES "
        f"(:c, {EFF}, 'current', :a, :e, 'reassessment_worker')",
        c=claim_id,
        a=assessment_id,
        e=epistemic,
    )
    await _execute(
        engine,
        "INSERT INTO audit_events (id, session_id, sequence, type, actor, public_summary, payload) "
        "VALUES (:id, NULL, :seq, 'reassessment_job_completed', 'memory.reassessment', :summary, "
        "CAST(:payload AS JSONB))",
        id=uuid.uuid4(),
        seq=120,
        summary=f"reassessment {claim_id}: {grade}/{epistemic}",
        payload=_json(
            {
                "job_id": str(uuid.uuid4()),
                "claim_id": str(claim_id),
                "assessment_id": str(assessment_id),
                "attempt": 1,
                "grade": grade,
                "epistemic_status": epistemic,
                "confidence": confidence,
                "reasons": reasons,
            }
        ),
    )
    return assessment_id


async def _fix_groups(
    engine: AsyncEngine,
    *,
    assessment_id: uuid.UUID,
    members: list[tuple[uuid.UUID, str, str]],
    roles: dict[uuid.UUID, str],
) -> None:
    """Запечатанные группы независимости этой оценки и роли улик — те же строки, которые
    страница происхождения уже показывает. Пересчёта групп тестом нет."""
    snapshot_id = uuid.uuid4()
    await _execute(
        engine,
        "INSERT INTO source_independence_snapshots "
        "(id, algorithm_version, thresholds, psl_fingerprint, uri_normalizer_version) "
        "VALUES (:id, 'noezema-source-v1', '{}', 'fingerprint', 'v1')",
        id=snapshot_id,
    )
    await _execute(
        engine,
        "INSERT INTO source_independence_members (snapshot_id, source_id, group_id, basis) VALUES "
        + ", ".join(
            f"('{snapshot_id}', '{source_id}', '{group}', '{basis}')"
            for source_id, group, basis in members
        ),
    )
    await _execute(
        engine,
        "INSERT INTO assessment_evidence (assessment_id, evidence_id, role) VALUES "
        + ", ".join(f"('{assessment_id}', '{eid}', '{role}')" for eid, role in roles.items()),
    )
    await _execute(
        engine,
        "UPDATE claim_assessments SET source_independence_snapshot_id = :s WHERE id = :a",
        s=snapshot_id,
        a=assessment_id,
    )


async def _question_with_worker_assessment(
    engine: AsyncEngine,
    *,
    statement: str,
    grade: str,
    epistemic: str,
    confidence: float,
    reasons: list[str],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Вопрос + завершившаяся сессия + утверждение этого вопроса; оценку пересчитал рабочий."""
    question_id, session_id, claim_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_question(engine, question_id=question_id, state="verified")
    await _seed_session(
        engine,
        session_id=session_id,
        question_id=question_id,
        state="succeeded",
        termination_reason="goal_reached",
    )
    await _seed_claim(
        engine, claim_id=claim_id, session_id=session_id, statement=statement, head_state="none"
    )
    # стендовые репродюсеры — утверждения про показатель с датой: порог типа E3 (config-v20)
    await _execute(
        engine, "UPDATE claims SET claim_type = 'temporal_fact', as_of = DATE '2025-12-31' WHERE id = :c", c=claim_id
    )
    await _worker_assessment(
        engine,
        claim_id=claim_id,
        grade=grade,
        epistemic=epistemic,
        confidence=confidence,
        reasons=reasons,
    )
    return question_id, claim_id


# ─── репродюсер 9266248e: восемь прочитанных источников, одна группа ──────────


async def _seed_rosstat_cluster(
    engine: AsyncEngine, *, claim_id: uuid.UUID, assessment_id: uuid.UUID
) -> list[uuid.UUID]:
    anchor = await _source(engine, uri=ROSSTAT_HOME, tag="rosstat-anchor")
    readings = [
        ("https://www.interfax.ru/news/1091562", "parent", "attributed"),
        ("https://www.interfax.ru/news/1091570", "parent", "attributed"),
        ("https://www.interfax.ru/news/1091588", "parent", "attributed"),
        ("https://expert.ru/publications/inflyaciya-2025", "parent", "plain"),
        ("https://www.cbr.ru/press/reginfl/?id=64837", "domain", "plain"),
        ("https://sbercib.ru/idea/prognosis-inflyacii-2025", "single", "attributed"),
    ]
    evidence_ids: list[uuid.UUID] = []
    members: list[tuple[uuid.UUID, str, str]] = []
    roles: dict[uuid.UUID, str] = {}
    for index, (uri, kind, attribution) in enumerate(readings):
        parent = anchor if kind == "parent" else None
        source_id = await _source(engine, uri=uri, tag=f"ros-{index}", parent=parent)
        scope = (
            _attribution_scope("rosstat", "Росстат", ROSSTAT_URI, anchor)
            if attribution == "attributed"
            else '{"x": 1}'
        )
        evidence_id = await _evidence(
            engine, claim_id=claim_id, source_id=source_id, tag=f"ros-e{index}", scope=scope
        )
        evidence_ids.append(evidence_id)
        roles[evidence_id] = "support"
        basis = {
            "parent": f"parent:{anchor}",
            "domain": "domain:cbr.ru",
            "single": "single",
        }[kind]
        members.append((source_id, "g0", basis))
    await _fix_groups(engine, assessment_id=assessment_id, members=members, roles=roles)
    return evidence_ids


# ─── репродюсер 391c4388: страница ЦБ и два пересказа с атрибуцией ────────────


async def _seed_cbr_cluster(
    engine: AsyncEngine, *, claim_id: uuid.UUID, assessment_id: uuid.UUID
) -> None:
    primary = await _source(engine, uri="https://www.cbr.ru/inflation_expectations/?id=67325", tag="cbr")
    retellings = ["https://www.forbes.ru/ekonomika/inflyaciya-2025", "https://www.kommersant.ru/doc/6712004"]
    members: list[tuple[uuid.UUID, str, str]] = []
    roles: dict[uuid.UUID, str] = {}
    primary_source_id = await _source(
        engine, uri="https://www.cbr.ru/press/reginfl/?id=64837", tag="cbr-press"
    )
    evidence_ids = [
        await _evidence(
            engine, claim_id=claim_id, source_id=primary_source_id, tag="cbr-press", scope='{"x": 1}'
        )
    ]
    roles[evidence_ids[0]] = "support"
    members.append((primary_source_id, "g0", f"parent:{primary}"))
    for index, uri in enumerate(retellings):
        source_id = await _source(engine, uri=uri, tag=f"cbr-{index}", parent=primary)
        evidence_id = await _evidence(
            engine,
            claim_id=claim_id,
            source_id=source_id,
            tag=f"cbr-e{index}",
            scope=_attribution_scope("cbr", "Банк России", CBR_URI, primary),
        )
        evidence_ids.append(evidence_id)
        roles[evidence_id] = "support"
        members.append((source_id, "g0", f"parent:{primary}"))
    await _fix_groups(engine, assessment_id=assessment_id, members=members, roles=roles)


# ─── карточка ответа ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_card_names_the_producer_behind_all_readings(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Одна группа независимости и все чтения ведут к Росстату → «Опубликовано производителем»."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id = await _question_with_worker_assessment(
            engine,
            statement=CPI_STATEMENT,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=SHORTFALL,
        )
        assessment_id = await _current_assessment(engine, claim_id)
        await _seed_rosstat_cluster(engine, claim_id=claim_id, assessment_id=assessment_id)

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        item = card["claims"][0]
        badge = item["reliability"]
        assert badge["label"] == PRODUCER_LABEL, badge
        # производитель назван по словарю; уровень и цвет остаются прежними (слабый)
        assert "Росстат" in badge["hint"], badge
        assert "истинность не проверена" in badge["hint"], badge
        assert badge["level"] == LEVEL_WEAK, badge
        assert len(badge["hint"]) <= MAX_HINT_CHARS, badge["hint"]
        # причина оценки видима по-человечески: не код, а подпись словаря
        assert item["grade_reason_lines"] == ["причина: независимых групп меньше нужного"], item
        assert [r["code"] for r in item["grade_reasons"]] == SHORTFALL, item
        # заголовок строки подтверждения не меняется: «проверено» звучит только при verified
        assert item["verification_lead"] == "чем подтверждено", item
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_card_names_the_cbr_producer_for_retellings_of_one_page(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Страница ЦБ и её пересказы (атрибутции ведут на Банк России) → тот же бейдж, другой имя."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id = await _question_with_worker_assessment(
            engine,
            statement="Инфляционные ожидания россиян на 2025 год выросли",
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=SHORTFALL,
        )
        assessment_id = await _current_assessment(engine, claim_id)
        await _seed_cbr_cluster(engine, claim_id=claim_id, assessment_id=assessment_id)

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        badge = card["claims"][0]["reliability"]
        assert badge["label"] == PRODUCER_LABEL, badge
        assert "Банк России" in badge["hint"], badge
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_two_independence_groups_keep_the_verified_badge(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Условие «ровно одна группа» не выполнено — прежняя подпись остаётся, слова о производителе не появляются."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id = await _question_with_worker_assessment(
            engine,
            statement=CPI_STATEMENT,
            grade="E3",
            epistemic="supported",
            confidence=0.75,
            reasons=["requirements_met"],
        )
        assessment_id = await _current_assessment(engine, claim_id)
        anchor = await _source(engine, uri=ROSSTAT_HOME, tag="anchor-two")
        independent = await _source(engine, uri="https://ria.ru/20251230/inflyaciya-1", tag="independent")
        derivative = await _source(
            engine, uri="https://www.interfax.ru/news/1091562", tag="derivative", parent=anchor
        )
        ev_anchor = await _evidence(
            engine,
            claim_id=claim_id,
            source_id=derivative,
            tag="ev-derivative",
            scope=_attribution_scope("rosstat", "Росстат", ROSSTAT_URI, anchor),
        )
        ev_independent = await _evidence(
            engine, claim_id=claim_id, source_id=independent, tag="ev-independent", scope='{"x": 1}'
        )
        await _fix_groups(
            engine,
            assessment_id=assessment_id,
            members=[(derivative, "g0", f"parent:{anchor}"), (independent, "g1", "single")],
            roles={ev_anchor: "support", ev_independent: "support"},
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        item = card["claims"][0]
        # две независимые группы: прежняя честная подпись «Проверено», без слов о производителе
        assert item["reliability"]["label"] == "Проверено", item["reliability"]
        assert item["verification_lead"] == "как проверено", item
        assert item["grade_reason_lines"] == ["причина: требования правил выполнены"], item
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_a_counter_reading_cancels_the_producer_signature(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Условие «нет контр-свидетельства» (условие г): одна из улик действующей оценки — counter."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id = await _question_with_worker_assessment(
            engine,
            statement=CPI_STATEMENT,
            grade="E1",
            epistemic="disputed",
            confidence=0.15,
            reasons=SHORTFALL,
        )
        assessment_id = await _current_assessment(engine, claim_id)
        anchor = await _source(engine, uri=ROSSTAT_HOME, tag="anchor-counter")
        derivative = await _source(
            engine, uri="https://www.interfax.ru/news/1091562", tag="counter-derivative", parent=anchor
        )
        counter = await _source(engine, uri="https://reformer.ru/analysis/inflyaciya-spora", tag="counter")
        ev_support = await _evidence(
            engine,
            claim_id=claim_id,
            source_id=derivative,
            tag="ev-support",
            scope=_attribution_scope("rosstat", "Росстат", ROSSTAT_URI, anchor),
        )
        ev_counter = await _evidence(
            engine, claim_id=claim_id, source_id=counter, tag="ev-counter", scope='{"x": 1}'
        )
        await _fix_groups(
            engine,
            assessment_id=assessment_id,
            members=[(derivative, "g0", f"parent:{anchor}"), (counter, "g0", "single")],
            roles={ev_support: "support", ev_counter: "counter"},
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        badge = card["claims"][0]["reliability"]
        assert badge["label"] != PRODUCER_LABEL, badge
        assert "производител" not in badge["hint"], badge
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_no_attribution_keeps_the_previous_wording(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Одна группа, но указателя на производителя нет (чтения с разных хостов, атрибуций нет):
    прежняя подсказка rules engine остаётся — витрина не придумывает производителя."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id = await _question_with_worker_assessment(
            engine,
            statement=CPI_STATEMENT,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=SHORTFALL,
        )
        assessment_id = await _current_assessment(engine, claim_id)
        members: list[tuple[uuid.UUID, str, str]] = []
        roles: dict[uuid.UUID, str] = {}
        for index, uri in enumerate(
            ["https://forum.example.org/thread-1", "https://blog.example.net/post-2"]
        ):
            source_id = await _source(engine, uri=uri, tag=f"anon-{index}")
            evidence_id = await _evidence(
                engine, claim_id=claim_id, source_id=source_id, tag=f"anon-e{index}", scope='{"x": 1}'
            )
            members.append((source_id, "g0", "single"))
            roles[evidence_id] = "support"
        await _fix_groups(engine, assessment_id=assessment_id, members=members, roles=roles)

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        badge = card["claims"][0]["reliability"]
        assert badge["label"] == "Подтверждено слабо", badge
        assert "нужны два независимых источника" in badge["hint"], badge
    finally:
        await app_engine.dispose()


# ─── одна подпись на всех витринах + стоимость витрин ────────────────────────


@pytest.mark.asyncio
async def test_list_and_claim_detail_show_the_same_signature_as_the_card(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Список знаний и карточка утверждения не имеют права расходиться с карточкой ответа."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id = await _question_with_worker_assessment(
            engine,
            statement=CPI_STATEMENT,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=SHORTFALL,
        )
        assessment_id = await _current_assessment(engine, claim_id)
        await _seed_rosstat_cluster(engine, claim_id=claim_id, assessment_id=assessment_id)

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
            listing = (await client.get("/api/v1/knowledge/claims?limit=50")).json()
            detail = (await client.get(f"/api/v1/knowledge/claims/{claim_id}")).json()
            queue = (await client.get("/api/v1/questions?limit=50")).json()

        card_badge = card["claims"][0]["reliability"]
        list_item = next(item for item in listing["claims"] if item["id"] == str(claim_id))
        assert list_item["reliability"] == card_badge, (list_item["reliability"], card_badge)
        assert detail["reliability"] == card_badge, detail["reliability"]
        assert detail["grade_reason_lines"] == ["причина: независимых групп меньше нужного"], detail
        assert list_item["grade_reasons"] == card["claims"][0]["grade_reasons"], list_item
        summary = next(row["answer"] for row in queue["questions"] if row["id"] == str(question_id))
        assert summary["reliability"]["label"] == PRODUCER_LABEL, summary

        # ни одна витрина не утверждает проверку там, где её нет
        for text_value in (card_badge["label"], card_badge["hint"]):
            assert "производител" in text_value.lower() or "не проверена" in text_value.lower(), text_value
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_the_card_does_not_add_queries_per_claim(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Факты производителя и причины оценки приходят теми же запросами карточки: N+1 запрещён."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")

    statements: list[str] = []

    @event.listens_for(app_engine.sync_engine, "before_cursor_execute")
    def _capture(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, many: Any) -> None:
        statements.append(stmt.strip())

    def _select_count() -> int:
        return sum(1 for stmt in statements if stmt.upper().lstrip().startswith(("SELECT", "WITH")))

    try:
        question_id, claim_id = await _question_with_worker_assessment(
            engine,
            statement=CPI_STATEMENT,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=SHORTFALL,
        )
        assessment_id = await _current_assessment(engine, claim_id)
        await _seed_rosstat_cluster(engine, claim_id=claim_id, assessment_id=assessment_id)

        async with _client(app) as client:
            statements.clear()
            await client.get(f"/api/v1/questions/{question_id}/answer")
            one_claim = _select_count()

            # второе утверждение того же вопроса: та же стоимость карточки
            second_claim = uuid.uuid4()
            async with engine.connect() as conn:
                session_row = (
                    await conn.execute(
                        text("SELECT id FROM sessions WHERE question_id = :q LIMIT 1"), {"q": question_id}
                    )
                ).scalar_one()
            await _seed_claim(
                engine,
                claim_id=second_claim,
                session_id=session_row,
                statement="Второе утверждение этого же вопроса",
                head_state="none",
            )
            second_assessment = await _worker_assessment(
                engine,
                claim_id=second_claim,
                grade="E1",
                epistemic="hypothesis",
                confidence=0.15,
                reasons=SHORTFALL,
            )
            await _seed_rosstat_cluster(
                engine, claim_id=second_claim, assessment_id=second_assessment
            )

            statements.clear()
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
            two_claims = _select_count()

        assert len(card["claims"]) == 2, card["claims"]
        assert two_claims == one_claim, f"N+1: {one_claim} → {two_claims}"
        # Потолок закреплён по замеру прежнего кода (19c06ca, тот же посев: 10 SELECT'ов на
        # карточку). Факты производителя и причин добавлены JOIN'ами в уже существующие
        # запросы, а не отдельными обращениями: тест краснеет, если бюджет вырастет.
        assert one_claim <= 10, statements
    finally:
        event.remove(app_engine.sync_engine, "before_cursor_execute", _capture)
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_the_knowledge_list_does_not_query_per_claim(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Список знаний платит за подпись фиксированно: один запрос фактов и один запрос причин."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")

    statements: list[str] = []

    @event.listens_for(app_engine.sync_engine, "before_cursor_execute")
    def _capture(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, many: Any) -> None:
        statements.append(stmt.strip())

    def _select_count() -> int:
        return sum(1 for stmt in statements if stmt.upper().lstrip().startswith(("SELECT", "WITH")))

    try:
        _first_question, first_claim = await _question_with_worker_assessment(
            engine,
            statement=CPI_STATEMENT,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=SHORTFALL,
        )
        assessment_id = await _current_assessment(engine, first_claim)
        await _seed_rosstat_cluster(engine, claim_id=first_claim, assessment_id=assessment_id)

        async with _client(app) as client:
            statements.clear()
            one = (await client.get("/api/v1/knowledge/claims?limit=50")).json()
            one_count = _select_count()

            for index in range(3):
                _, claim_id = await _question_with_worker_assessment(
                    engine,
                    statement=f"Утверждение {index} того же узла",
                    grade="E1",
                    epistemic="hypothesis",
                    confidence=0.15,
                    reasons=SHORTFALL,
                )
                assessment = await _current_assessment(engine, claim_id)
                await _seed_rosstat_cluster(engine, claim_id=claim_id, assessment_id=assessment)

            statements.clear()
            many = (await client.get("/api/v1/knowledge/claims?limit=50")).json()
            many_count = _select_count()

        assert len(one["claims"]) == 1 and len(many["claims"]) == 4, (one["total"], many["total"])
        assert many_count == one_count, f"N+1 в списке знаний: {one_count} → {many_count}"
        # Потолок закреплён по замеру прежнего кода (19c06ca: 3 SELECT'а). Правка добавляет
        # ровно два фиксированных запроса — факты улик и причины оценки — и они не растут
        # вместе с числом утверждений страницы.
        assert many_count <= 5, statements
        signed = [item for item in many["claims"] if item["reliability"]["label"] == PRODUCER_LABEL]
        assert len(signed) == 4, many["claims"]
    finally:
        event.remove(app_engine.sync_engine, "before_cursor_execute", _capture)
        await app_engine.dispose()


