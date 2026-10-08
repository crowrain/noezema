"""Scenario: T7.82 (A) — реселекция окон улики по финальной rationale, сквозная сессия.

Репродюсер дефекта стенда .92 (сессия f0d7e844) на синтетической странице той же формы:
заголовок, повторённый в шапке и навигации; терминологически плотный блок в середине;
ключевая строка со значением «14,5%» — в конце страницы. В момент чтения окна слепы
(сигналов исследователя ещё нет), после конца exploration финальная rationale цитирует
эту строку дословно, и хост пересобирает фрагмент улики (apps/orchestrator/evidence.py::
reselect_assertion_fragment) — куратор получает окно с «14,5%».

Инварианты, проверяемые здесь: идентичность улики (§14.3) не меняется (фрагмент в неё не
входит), число строк evidence и аудит-событий от реселекции не растёт, поведение без
точного якоря не затронуто. На прежнем коде assert про «14,5%» в curator prompt краснеет —
это и есть редкость дефекта (проверено временным откатом модуля окон, STATUS.md T7.82).
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.models.enums import AuditEventType, SessionState
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.memory.evidence import source_assertion_identity
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_provenance import _seed_question

pytestmark = [pytest.mark.scenario]

_Q = "Какое значение наблюдаемой годовой инфляции приводит регулятор?"

_PARA_A = (
    "Плотный регион: инфляция населения и ожидания оцениваются Росстатом; расхождение оценок "
    "и первоисточник каждой цифры сравниваются в отчёте. "
) * 14
_FILLER = (
    "Приложения к отчёту содержат хронологию публикаций, перечень использованных материалов, "
    "адреса территориальных отделений и порядок доступа к архиву пресс-службы за период. "
) * 18

PAGE = (
    "<html><head><title>Инфляционные ожидания</title></head><body>"
    "<h1>Инфляционные ожидания и потребительские настроения</h1>"
    "<nav>Инфляционные ожидания и потребительские настроения | Денежно-кредитная политика "
    "| Аналитика 8 800 300-30-00 7 499 300-30-00 Карта сайта О сайте </nav>"
    "<main><p>Отчёт регулятора о настроениях населения за отчётный период.</p>"
    f"<section><h2>Обзор</h2><p>{_PARA_A}</p>"
    "<p>Дополнительные пояснения: прогноз аналитиков составил 6,3%, прежний консенсус 6,6%.</p></section>"
    f"<section><h2>Приложения</h2><p>{_FILLER}</p></section>"
    "<p>Наблюдаемая населением годовая инфляция составила 14,5% в декабре отчётного года "
    "и остаётся выше цели по данным наблюдений.</p>"
    "<footer>Контактная информация и адреса отделений.</footer></main></body></html>"
)

_FINAL_RATIONALE = (
    "Прочитано дословно: «Наблюдаемая населением годовая инфляция составила 14,5% в декабре "
    "отчётного года и остаётся выше цели по данным наблюдений». Это значение отличается от "
    "прогноза аналитиков 6,3%."
)


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/page":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


class FakeOrigin:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
        self.port = self.server.server_address[1]
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
def origin() -> Iterator[FakeOrigin]:
    o = FakeOrigin()
    yield o
    o.stop()


async def _row(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    from sqlalchemy import text

    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def test_final_rationale_reselects_the_evidence_window(
    migrated_db: tuple[str, Any],
    origin: FakeOrigin,
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = origin.base
    rp["private_allowlist"] = origin.allowlist
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], "research.fetch"]
    result = await _run_online(engine, payload)
    assert result.state == "active"

    question_id = await _seed_question(scratch_url, _Q)

    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=service,
    )
    fake_llm.script(
        [
            {
                "content": {
                    "public_rationale": "Ищу первоисточник с фактическим значением показателя.",
                    "decision": {
                        "kind": "tool",
                        "tool": "research.fetch",
                        "arguments": {"url": f"{origin.base}/page"},
                    },
                }
            },
            {
                "content": {
                    "public_rationale": _FINAL_RATIONALE,
                    "decision": {"kind": "complete", "reason": "goal_reached"},
                }
            },
            {
                "content": {
                    "summary": "Регулятор приводит наблюдаемую годовую инфляцию.",
                    "claims": [
                        {
                            "statement": "По данным отчёта регулятора наблюдаемая населением "
                            "годовая инфляция в декабре составила 14,5%.",
                            "claim_type": "external_fact",
                        }
                    ],
                    "evidence_links": [{"claim_index": 0, "evidence_index": 0, "relation": "supports"}],
                    "new_questions": [],
                }
            },
        ]
    )

    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()

    assert outcome.final_state is SessionState.SUCCEEDED

    # 1) промпт куратора: evidence-строка source_assertion несёт фрагмент с «14,5%»
    curator_reqs = [r["last_user"] for r in fake_llm.requests() if "Предложи изменения памяти" in r["last_user"]]
    assert curator_reqs, "no curator request"
    curator_ctx = max(curator_reqs, key=len)
    assert "source_assertion" in curator_ctx
    assert "14,5%" in curator_ctx, (
        "финальная rationale исследователя должна вернуть ключевое значение в фрагмент улики"
    )

    # 2) идентичность улики не тронута реселекцией: §14.3 — ORIGINAL-хеш + chunk + kind
    ev = await _row(
        scratch_url,
        "SELECT identity_hash, source_id FROM evidence ORDER BY created_at DESC LIMIT 1",
    )
    assert ev is not None and ev[1] is not None
    src = await _row(
        scratch_url,
        "SELECT content_hash FROM sources WHERE id = :id",
        {"id": str(ev[1])},
    )
    assert src is not None
    expected_identity = source_assertion_identity(str(src[0]), "chunk-0", "source_assertion")
    assert ev[0] == expected_identity

    # 3) реселекция не заводит ни новых строк, ни новых аудит-событий
    counts = await _row(
        scratch_url,
        "SELECT (SELECT count(*) FROM evidence), "
        "(SELECT count(*) FROM audit_events WHERE type = :t AND session_id = "
        "(SELECT id FROM sessions ORDER BY created_at DESC LIMIT 1))",
        {"t": AuditEventType.RESEARCH_CONTENT_READ.value},
    )
    assert counts is not None
    assert counts[0] == 1  # ровно одна улика, как при чтении
    assert counts[1] == 1  # одно прочтение страницы — реселекция заново не читает
