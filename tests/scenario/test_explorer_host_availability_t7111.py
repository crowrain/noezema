"""Scenario (T7.111 часть 2): недоступный хост — отказ в сессии и секция «Недавно недоступны».

(а) После отказа `research.fetch` с кодом 401/403/429 или таймаута следующая попытка чтения того
же хоста в этой же сессии отклоняется (`host_unavailable_in_session`, стоит шаг, называется вид
отказа и шаг первой неудачи). 404, нечитаемый артефакт и сетевой сбой хост не метят.

(б) На старте новой сессии по существующему журналу (один read-only SELECT, новой таблицы нет)
в контекст каждого шага попадает короткая секция «Недавно недоступны» — она только называет хосты,
запрета не вводит.

Все хосты вымышленные; `research.fetch` обслуживает подставной канал — реальных внешних запросов
тест не делает.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator import orchestrator as orchestrator_module
from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.host_availability import HOST_AVAILABILITY_SECTION_CHAR_LIMIT
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.service import ResearchProxyError
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType, SessionState
from packages.domain.models.events import ORMAuditEvent
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

LIVE_TEXT = "Гелиос (Тармения): индекс рынка труда 7,8 % к июню 2029 года."
LIVE_SHA = "b" * 64


class _DictStore:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages

    def get(self, sha: str) -> bytes:
        if sha in self.pages:
            return self.pages[sha]
        raise KeyError(sha)


class FakeFetchService:
    """Подставной research-канал: у каждого вымышленного хоста свой scripted-исход."""

    def __init__(self) -> None:
        self.store = _DictStore({LIVE_SHA: LIVE_TEXT.encode("utf-8")})

    async def fetch(self, url: str) -> dict[str, Any]:
        if url.startswith("https://live.example"):
            return {
                "source_id": "fake-src",
                "final_url": url,
                "mode": "curated",
                "original_sha256": "a" * 64,
                "normalized_sha256": LIVE_SHA,
                "transform_chain": ["fetch"],
            }
        if "dead.example" in url:
            raise ResearchProxyError("non-200 status: 403", "rejected")
        if "slow.example" in url:
            raise ResearchProxyError("timeout: upstream read timed out", "failed")
        if "missing.example" in url:
            raise ResearchProxyError("non-200 status: 404", "rejected")
        if "artifact.example" in url:
            # envelope есть, нормализованный текст в хранилище отсутствует
            return {
                "source_id": "fake-src",
                "final_url": url,
                "mode": "curated",
                "original_sha256": "a" * 64,
                "normalized_sha256": "0" * 64,
                "transform_chain": ["fetch"],
            }
        raise ResearchProxyError("transport error: unexpected url", "failed")


async def _rows(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())
    finally:
        await engine.dispose()


def _curated_payload(tools: list[str]) -> dict[str, Any]:
    # curated-режим нужен только ради выдачи инструментов; сеть этому тесту не нужна:
    # все fetch'и обслуживает FakeFetchService, реальных upstream-запросов нет
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["private_allowlist"] = ["127.0.0.1:1"]
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], *tools]
    return payload


def _fetch_call(url: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": f"чтение: {url}",
            "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": url}},
        }
    }


_COMPLETE = {"content": {"public_rationale": "готово", "decision": {"kind": "complete", "reason": "goal_reached"}}}
_CURATOR = {"content": {"summary": "Данные собраны", "claims": [], "evidence_links": [], "new_questions": []}}


def _gateway(fake_llm: FakeLLM) -> LLMMiddleware:
    return LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )


async def _seed_question(scratch_url: str, text_: str) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            question = await QuestionRepository.create(db, ORMQuestion(text=text_, origin="seeded", priority=1))
            return question.id
    finally:
        await engine.dispose()


# ─── (а) отказ хоста в этой сессии ─────────────────────────────────────────────


async def test_host_refusal_denies_next_fetch_to_same_host_in_session(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """403 и таймаут метят хост до конца сессии; 404 и нечитаемый артефакт — нет.
    Отклонённый повтор стоит шаг и называется с видом отказа и шагом первой неудачи."""
    scratch_url, engine = migrated_db
    result = await _run_online(engine, _curated_payload(["research.fetch"]))
    assert result.state == "active"
    question_id = await _seed_question(scratch_url, "Какой индекс рынка труда у сервиса Гелиос?")

    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=FakeFetchService(),
    )
    fake_llm.script(
        [
            _fetch_call("https://dead.example/a"),  # шаг 1: выполняется, отказ 403 -> метит хост
            _fetch_call("https://dead.example/b"),  # шаг 2: другой URL того же хоста -> отказ guard'а
            _fetch_call("https://missing.example/x"),  # шаг 3: 404 -> хост не помечен
            _fetch_call("https://missing.example/y"),  # шаг 4: выполняется (это не отказ guard'а)
            _fetch_call("https://artifact.example/z"),  # шаг 5: нечитаемый артефакт -> не метит
            _fetch_call("https://slow.example/p"),  # шаг 6: таймаут -> метит хост
            _fetch_call("https://slow.example/q"),  # шаг 7: отказ (таймаут, первая неудача — шаг 6)
            _fetch_call("https://live.example/page"),  # шаг 8: выполняется
            copy.deepcopy(_COMPLETE),  # шаг 9
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
    assert outcome.final_state is SessionState.SUCCEEDED

    denials = await _rows(
        scratch_url,
        "SELECT payload->>'host', payload->>'error_kind', payload->>'failed_step' FROM audit_events "
        "WHERE type = :t AND payload->>'reason' = 'host_unavailable_in_session' ORDER BY sequence",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert denials == [("dead.example", "403", "1"), ("slow.example", "timeout", "6")]

    executed = await _rows(scratch_url, "SELECT count(*) FROM actions WHERE tool = 'research.fetch'")
    assert executed[0][0] == 6  # 8 попыток минус 2 отказа guard'а
    other = await _rows(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t AND payload->>'reason' = 'tool_call_repeated'",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert other[0][0] == 0  # другой URL того же хоста — не T7.12-повтор: guard отдельный

    # текст отказа доходит до модели во всех последующих запросах (накопленные наблюдения, шаги 3..9)
    denied_ctx = [r["last_user"] for r in fake_llm.requests() if "research.fetch ОТКЛОНЕНО" in r["last_user"]]
    assert len(denied_ctx) == 7
    assert any("dead.example уже отказывал в этой сессии на шаге 1 (403)" in ctx for ctx in denied_ctx)
    assert any("slow.example уже отказывал в этой сессии на шаге 6 (таймаут)" in ctx for ctx in denied_ctx)
    assert all("ищи те же данные на другом сайте" in ctx for ctx in denied_ctx)

    # живые отказы (403/404/артефакт/таймаут) остаются обычными неудачами: без denied
    plain_failures = await _rows(
        scratch_url,
        "SELECT payload->>'error' FROM audit_events WHERE type = :t AND payload->>'denied' IS NULL "
        "ORDER BY sequence",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert [row[0] for row in plain_failures] == [
        "non-200 status: 403",
        "non-200 status: 404",
        "non-200 status: 404",
        "normalized artifact unreadable",
        "timeout: upstream read timed out",
    ]

    # прочитанная страница журназируется как обычно — именно этот журнал кормит часть (б)
    read_ok = await _rows(
        scratch_url,
        "SELECT payload->>'url' FROM audit_events WHERE type = :t",
        {"t": AuditEventType.RESEARCH_CONTENT_READ.value},
    )
    assert [row[0] for row in read_ok] == ["https://live.example/page"]

    assert outcome.steps == 9
    assert fake_llm.state()["queue_left"] == 0


# ─── (б) секция «Недавно недоступны» из журнала прошлых сессий ─────────────────


async def _seed_journal(scratch_url: str, spec: list[tuple[str, datetime, str]]) -> None:
    """Прямые строки журнала (те же типы событий, что пишет оркестратор): пары action_started +
    action_failed research.fetch и research_content_read. Прошлые сессии — вымышленные uuid,
    своей сессии среди них нет. Ничего, кроме audit_events, не появляется."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seq = 0
    try:
        async with factory() as db, transaction(db):
            for host, at, kind in spec:
                if kind == "read":
                    db.add(
                        ORMAuditEvent(
                            session_id=uuid.uuid4(),
                            sequence=(seq := seq + 1),
                            type=AuditEventType.RESEARCH_CONTENT_READ.value,
                            occurred_at=at,
                            payload={"url": f"https://{host}/page", "normalized_sha256": "0" * 64},
                        )
                    )
                    continue
                action_id = uuid.uuid4()
                session_id = uuid.uuid4()
                error = (
                    "timeout: upstream read timed out" if kind == "timeout" else f"non-200 status: {kind}"
                )
                db.add(
                    ORMAuditEvent(
                        session_id=session_id,
                        sequence=(seq := seq + 1),
                        type=AuditEventType.ACTION_STARTED.value,
                        occurred_at=at,
                        payload={
                            "action_id": str(action_id),
                            "tool": "research.fetch",
                            "arguments": json.dumps({"url": f"https://{host}/doc"}),
                        },
                    )
                )
                db.add(
                    ORMAuditEvent(
                        session_id=session_id,
                        sequence=(seq := seq + 1),
                        type=AuditEventType.ACTION_FAILED.value,
                        occurred_at=at,
                        payload={"action_id": str(action_id), "ok": False, "error": error, "data": None},
                    )
                )
    finally:
        await engine.dispose()


async def test_journal_populates_recently_unavailable_section(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_url, engine = migrated_db

    now = datetime.now(UTC)
    day = timedelta(days=1)
    await _seed_journal(
        scratch_url,
        [
            # два qualifying-отказа подряд -> в списке, вид = последний отказ
            ("dead.example", now - 2 * day, "403"),
            ("dead.example", now - day, "429"),
            # два отказа, потом успешное чтение -> вне списка (хост починился)
            ("healed.example", now - 3 * day, "403"),
            ("healed.example", now - 2 * day, "403"),
            ("healed.example", now - day, "read"),
            # единственный отказ -> вне списка
            ("once.example", now - day, "403"),
            # два qualifying-отказа, успех был ДО них -> в списке
            ("back.example", now - 4 * day, "403"),
            ("back.example", now - 3 * day, "read"),
            ("back.example", now - 2 * day, "timeout"),
            # два отказа старше окна (7 суток) -> вне списка
            ("old.example", now - 9 * day, "403"),
            ("old.example", now - 8 * day, "403"),
            # www-вариант и без www — один хост: две неудачи -> в списке
            ("www.stats.hh.ru", now - 2 * day, "timeout"),
            ("stats.hh.ru", now - day, "401"),
        ],
    )

    calls: list[Any] = []
    real_query = orchestrator_module.recently_unavailable_hosts

    async def counting_query(db: Any, current_session_id: Any) -> Any:
        calls.append(current_session_id)
        return await real_query(db, current_session_id)

    monkeypatch.setattr(orchestrator_module, "recently_unavailable_hosts", counting_query)

    result = await _run_online(engine, _curated_payload(["research.fetch"]))
    assert result.state == "active"
    question_id = await _seed_question(scratch_url, "Какой индекс рынка труда у сервиса Гелиос?")

    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=FakeFetchService(),
    )
    fake_llm.script([_fetch_call("https://live.example/page"), copy.deepcopy(_COMPLETE), copy.deepcopy(_CURATOR)])
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
    assert outcome.final_state is SessionState.SUCCEEDED

    # ровно один read-only запрос к журналу на сессию
    assert len(calls) == 1 and calls[0] is not None

    decision_requests = [
        r["last_user"] for r in fake_llm.requests() if "Предложи ровно одно следующее действие" in r["last_user"]
    ]
    assert decision_requests
    section_requests = [ctx for ctx in decision_requests if "# Недавно недоступны" in ctx]
    assert section_requests == decision_requests, "секция обязана доходить до модели на каждом шаге"

    ctx = decision_requests[0]
    head = ctx.index("# Недавно недоступны")
    tail_end = ctx.index("\n\n", ctx.index("Не стройте поиск вокруг них"))
    section = ctx[head:tail_end]
    assert "dead.example — 429" in section and "back.example — таймаут" in section
    assert "stats.hh.ru — 401" in section and "www.stats.hh.ru" not in section
    assert "healed.example" not in section  # после последнего отказа было успешное чтение
    assert "once.example" not in section  # единственный отказ — не повод
    assert "old.example" not in section  # за пределами семисуточного окна
    assert section.count("\n- ") == 3
    assert len(section.encode("utf-8")) <= HOST_AVAILABILITY_SECTION_CHAR_LIMIT

    # секция стоит до наблюдений: вырезание старых наблюдений не может её спрятать
    if "# Наблюдения" in ctx:
        assert head < ctx.index("# Наблюдения")

    # секция ничего не запрещает: живое чтение live-хоста выполнено, отказов нет
    executed = await _rows(scratch_url, "SELECT count(*) FROM actions WHERE tool = 'research.fetch'")
    assert executed[0][0] == 1
    denials = await _rows(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t AND payload->>'reason' IN "
        "('host_unavailable_in_session', 'search_query_near_duplicate')",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert denials[0][0] == 0


async def test_empty_journal_has_no_section(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """Пустой журнал — секции нет вовсе (ни заголовка, ни пустого списка)."""
    scratch_url, engine = migrated_db
    result = await _run_online(engine, _curated_payload(["research.fetch"]))
    assert result.state == "active"
    question_id = await _seed_question(scratch_url, "Какой индекс рынка труда у сервиса Гелиос?")

    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=FakeFetchService(),
    )
    fake_llm.script([_fetch_call("https://live.example/page"), copy.deepcopy(_COMPLETE), copy.deepcopy(_CURATOR)])
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
    assert outcome.final_state is SessionState.SUCCEEDED
    assert not any("# Недавно недоступны" in r["last_user"] for r in fake_llm.requests())
