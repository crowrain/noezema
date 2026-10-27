"""Scenario (T7.137 части 3, 5): повторный сводный web.search отклоняется в живом цикле сессии
до исполнения (ACTION_FAILED с `denied: True`, без строки действия — способ T7.12/T7.111), отказ
стоит шаг, а одиночный поиск по названному актору после отказа выполняется.

Вопрос — реальный c3 стенда (называет пять акторов закрытого каталога). Скрипт модели: сводный
поиск (выполняется, вооружает гейт), второй сводный (ОТКЛОНЁН с прежним запросом и шагом), затем
«Банк России прогноз инфляции 2026» — одиночный, выполняется. Модель видит русскую заметку с
акторами без отдельного поиска; контекст каждого шага несёт секцию «Дата и названные акторы».

Отдельным тестом — отчёт честности (часть 5): новый счётчик `gate_multi_actor_repeat` считает
этот отказ, прежние ключи не тронуты; на СТАРЫХ данных (лента сессии 066e1e5f из фикстуры, где
новых отказов нет) прежние значения прежние.

Поисковый движок — локальный фейк SearXNG; внешних запросов тест не делает.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.memory.honesty_report import collect_honesty_report, render_markdown
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "t7137"

C3_QUESTION = (
    "Какие прогнозы инфляции в России на 2026 год дают аналитики крупных банков "
    "(Сбербанк, ВТБ, Альфа-Банк, Газпромбанк)? Сравните с прогнозом Банка России."
)
AGGREGATE_FIRST = "Сбербанк ВТБ Альфа-Банк Газпромбанк прогноз инфляции 2026"
AGGREGATE_SECOND = "Сбербанк ВТБ Альфа-Банк Газпромбанк прогноз инфляции 2026 Банк России"
SINGLE_CBR = "Банк России прогноз инфляции 2026"


class _SearchHandler(BaseHTTPRequestHandler):
    """Fake SearXNG: `/search?format=json&q=…` отвечает один и тот же набор хитов."""

    base: str = ""

    def do_GET(self) -> None:
        parsed_url = self.path
        if parsed_url.startswith("/search"):
            body = json.dumps(
                {
                    "results": [
                        {
                            "url": f"{self.base}/page",
                            "title": "Обзор прогнозов",
                            "content": "навигационный фрагмент из поисковой выдачи",
                        }
                    ]
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


class FakeSearchEngine:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _SearchHandler)
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = type(
            "BoundSearchHandler", (_SearchHandler,), {"base": f"http://127.0.0.1:{self.port}"}
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def allowlist(self) -> list[str]:
        return [f"127.0.0.1:{self.port}"]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def search_engine() -> Iterator[FakeSearchEngine]:
    engine = FakeSearchEngine()
    yield engine
    engine.stop()


async def _rows(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())
    finally:
        await engine.dispose()


def _curated_payload(tools: list[str], origin_base: str, allowlist: list[str]) -> dict[str, Any]:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = origin_base
    rp["private_allowlist"] = allowlist
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], *tools]
    return payload


def _search_call(query: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": f"поиск: {query[:40]}",
            "decision": {"kind": "tool", "tool": "web.search", "arguments": {"query": query}},
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


async def test_second_aggregate_search_denied_single_actor_runs(
    migrated_db: tuple[str, Any],
    search_engine: FakeSearchEngine,
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """Вопрос называет пять акторов каталога: первый сводный выполняется, второй сводный — отказ
    до исполнения (шаг 2), одиночный «Банк России …» выполняется (шаг 3). Отказ стоит шаг:
    всего 4 шага = 3 попытки поиска + complete."""
    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    result = await _run_online(
        engine,
        _curated_payload(["research.fetch", "web.search"], search_engine.base, search_engine.allowlist),
    )
    assert result.state == "active"
    question_id = await _seed_question(scratch_url, C3_QUESTION)

    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=ResearchProxyService(factory, store),
    )
    fake_llm.script(
        [
            _search_call(AGGREGATE_FIRST),  # шаг 1: первый сводный — выполняется
            _search_call(AGGREGATE_SECOND),  # шаг 2: второй сводный — ОТКАЗ до исполнения
            _search_call(SINGLE_CBR),  # шаг 3: один актор — выполняется
            copy.deepcopy(_COMPLETE),  # шаг 4
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
        "SELECT payload->>'prior_step', payload->>'prior_query', payload->>'uncovered_actors' "
        "FROM audit_events WHERE type = :t AND payload->>'reason' = 'search_query_multi_actor_repeat' "
        "ORDER BY sequence",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert len(denials) == 1, "второй сводный обязан быть отклонён ровно один раз"
    assert denials[0][0] == "1"
    assert denials[0][1] == AGGREGATE_FIRST
    assert json.loads(denials[0][2]) == [
        "Банк России", "Сбербанк", "ВТБ", "Альфа-Банк", "Газпромбанк",
    ]

    # отказ записан как T7.12: denied-аудит без строки действия — выполненных поисков ровно 2
    executed = await _rows(scratch_url, "SELECT count(*) FROM actions WHERE tool = 'web.search'")
    assert executed[0][0] == 2

    # модель видит русскую заметку: прежний сводный запрос и шаг, замена «по одному актору»,
    # список непокрытых акторов и запрет вставлять известные числа
    denied_ctx = [r["last_user"] for r in fake_llm.requests() if "web.search ОТКЛОНЕНО" in r["last_user"]]
    assert len(denied_ctx) == 2  # заметка живёт в наблюдениях: шаги 3 и 4
    assert any(f"уже выполнялся на шаге 1: «{AGGREGATE_FIRST}»" in ctx for ctx in denied_ctx)
    assert any(
        "Без отдельного поиска пока: Банк России, Сбербанк, ВТБ, Альфа-Банк, Газпромбанк." in ctx
        for ctx in denied_ctx
    )
    assert all("Не вставляй в запрос уже известные числа" in ctx for ctx in denied_ctx)
    assert all("Отклонённое действие тоже стоит один шаг" in ctx for ctx in denied_ctx)

    # секция контекста шага (часть 4): дата узла в каждом шаге; до отказа — все пять акторов
    # без отдельного поиска, после одиночного «Банк России» — четверо
    requests = [r["last_user"] for r in fake_llm.requests()]
    explorer_requests = [ctx for ctx in requests if "Дата узла:" in ctx]
    assert len(explorer_requests) == 4
    assert all(re.search(r"Дата узла: \d{4}-\d{2}-\d{2}", ctx) for ctx in explorer_requests)
    assert (
        explorer_requests[0]
        .count("Названные в вопросе акторы без отдельного поиска: Банк России, Сбербанк, ВТБ, Альфа-Банк, Газпромбанк.")
        == 1
    )
    assert (
        explorer_requests[3]
        .count("Названные в вопросе акторы без отдельного поиска: Сбербанк, ВТБ, Альфа-Банк, Газпромбанк.")
        == 1
    )

    # шаг за отклонённое действие платится по-прежнему (T7.76): 4 = 3 попытки поиска + complete
    assert outcome.steps == 4
    assert fake_llm.state()["queue_left"] == 0


async def test_honesty_report_counts_new_gate_with_old_keys_intact(
    migrated_db: tuple[str, Any],
    search_engine: FakeSearchEngine,
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """Часть 5: аддитивный счётчик `gate_multi_actor_repeat` посчитан в JSON и тексте отчёта;
    прежние счётчики ворот на этом же прогоне на месте (почти-дубликатов не было — 0)."""
    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _run_online(
        engine,
        _curated_payload(["research.fetch", "web.search"], search_engine.base, search_engine.allowlist),
    )
    question_id = await _seed_question(scratch_url, C3_QUESTION)
    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=ResearchProxyService(factory, store),
    )
    fake_llm.script(
        [
            _search_call(AGGREGATE_FIRST),
            _search_call(AGGREGATE_SECOND),
            _search_call(SINGLE_CBR),
            copy.deepcopy(_COMPLETE),
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        await orch.run_session(question_id)
    finally:
        await gateway.close()

    async with factory() as db:
        report = await collect_honesty_report(db, question_ids=[question_id])
    session = report.questions[0].sessions[0]
    steps = session.steps
    assert steps.gate_multi_actor_repeat == 1
    # прежние ключи на месте и не изменились по смыслу: почти-дубликатов в прогоне не было
    assert steps.gate_near_duplicate == 0
    assert steps.gate_host_unavailable == 0
    assert steps.gate_tool_repeated == 0
    assert steps.searches_total == 2  # отказаный сводный — не выполненный поиск

    as_json = report.to_dict()
    totals = as_json["totals"]
    assert totals["gate_multi_actor_repeat"] == 1
    for old_key in ("gate_near_duplicate", "gate_host_unavailable", "gate_tool_repeated"):
        assert old_key in totals

    text_report = render_markdown(report)
    assert "повтор сводного поиска 1" in text_report


async def test_honesty_report_on_old_fixture_data_old_counters_unchanged(
    migrated_db: tuple[str, Any],
) -> None:
    """Старые данные не меняются: лента реальной сессии 066e1e5f из фикстуры (два отказа
    почти-дубликата, новых отказов в ней нет) — прежние значения прежние, новый счётчик — 0."""
    scratch_url, _engine = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        meta = json.loads((FIXTURES / "session_066e1e5f_meta.json").read_text(encoding="utf-8"))
        async with engine.connect() as conn:
            snapshot = (
                await conn.execute(
                    text("SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global'")
                )
            ).scalar_one()
            question_id = uuid.uuid4()
            session_id = uuid.UUID(meta["session_id"])
            await conn.execute(
                text("INSERT INTO questions (id, text, origin, state, priority) "
                     "VALUES (:id, :t, 'seeded', 'verified', 0)"),
                {"id": question_id, "t": meta["question_text"]},
            )
            await conn.execute(
                text("INSERT INTO sessions (id, state, question_id, config_snapshot_id, "
                     "termination_reason, started_at, finished_at) "
                     "VALUES (:s, :state, :q, :c, :term, CAST(:from_ts AS timestamptz), "
                     "CAST(:to_ts AS timestamptz))"),
                {"s": session_id, "state": meta["state"], "q": question_id, "c": snapshot,
                 "term": meta["termination_reason"],
                 "from_ts": datetime.fromisoformat(str(meta["started_at"])),
                 "to_ts": datetime.fromisoformat(str(meta["finished_at"]))},
            )
            for line in (FIXTURES / "session_066e1e5f_audit.jsonl").read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                row = json.loads(line)
                await conn.execute(
                    text("INSERT INTO audit_events (id, session_id, sequence, type, payload, occurred_at) "
                         "VALUES (:id, :s, :seq, :type, CAST(:payload AS JSONB), CAST(:occ AS timestamptz))"),
                    {"id": uuid.uuid4(), "s": session_id, "seq": int(row["seq"]),
                     "type": str(row["type"]), "payload": json.dumps(row["payload"]),
                     "occ": datetime.fromisoformat(str(row["occurred_at"]))},
                )
            await conn.commit()

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            report = await collect_honesty_report(db, question_ids=[question_id])
        steps = report.questions[0].sessions[0].steps
        # замеры прежних метрик по этой сессии (до задачи): 7 поисков, 2 отказа почти-дубликата,
        # 2 поиска без последующего чтения — значения не изменились
        assert steps.searches_total == 7
        assert steps.gate_near_duplicate == 2
        assert steps.wasted_searches == 2
        # нового гейта в старых данных нет
        assert steps.gate_multi_actor_repeat == 0
    finally:
        await engine.dispose()
