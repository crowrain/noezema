"""Scenario (fake LLM + локальный origin): значение вопроса и цитата исследователя доводят
фрагмент доказательства до куратора (T7.79, дополнение к ADR-0011).

Стендовый отказ (сессия 96c710ff, STATUS T7.79): первоисточник прочитан полностью, фраза
«Годовая инфляция … составила 5,59%.» на странице есть, куратор про неё рассуждает — но в
доказательство этой странице ушли два окна по 2000 знаков, где этого числа нет. Куратор
увидел «видны отдельные показатели (9,3%, 5,2%, 2,6%, диапазон 4–6%)», утверждений ноль,
вопрос закрылся как «ответ получен и проверен».

Здесь проверяется путь целиком, через настоящий конвейер хоста (research proxy → артефакт →
assertion-окно → строка evidence в промпте куратора), на живом длинном тексте:

1. **значение из доверенного вопроса** («5,59») занимает второе окно: ключевая фраза с этого
   числа попадает и в payload улики (журнал `report`), и в промпт куратора (проверяется по
   реальному запросу к FakeLLM); утверждение предлагается и применяется;
2. **дословная цитата исследователя** занимает второе окно на второй, ещё более длинной
   странице — когда в вопросе вообще нет чисел: сигнал берётся из публичного rationale шага,
   а вырез остаётся текстом источника (текста модели в доказательстве нет).

Ни бюджет окна, ни число окон не изменили: меняется только то, что попадает в тот же слот.
Сети нет: origin — локальный HTTP-сервер, как в `tests/scenario/test_research_provenance.py`.
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
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_scope_coverage import _scalar, _seed_question

pytestmark = [pytest.mark.scenario]

KEY_SENTENCE = "Годовая инфляция в декабре 2025 года составила"
VALUE = "5,59%"

QUESTION_WITH_VALUE = (
    "Банк России публикует ли официально точное значение 5,59% (например, в пресс-релизе о решении "
    "21.01.2026), или во всех своих материалах указывает только округлённое 5,6%?"
)
# ни одного числа в вопросе: общий value-якорь не знает, что искать; термины вопроса
# плотнее всего в хроме навигации (пресс-релизы, точные величины, даты изменения), так что
# на старом коде обе окна уходили в начало страницы и в средний числовой блок
QUESTION_WITHOUT_VALUES = (
    "Какие точные величины публикует регулятор в пресс-релизах и на своей аналитической странице?"
)

NAV = "\n".join(
    f"Материалы раздела {i}: регулятор официально публикует пресс-релиз и точные величины; переместить в "
    f"боковую панель, Отобразить/Скрыть подраздел, Дата изменения: {10 + i % 20}.01.2026"
    for i in range(45)
)
COMMENTARY = "\n".join(
    f"Абзац {i}: мониторинг ценовой динамики продолжается, показатели устойчивости оцениваются по широкому "
    "набору данных за отчётный период; отдельный блок описывает структуру потребительских цен и их вклад в "
    "общий индекс."
    for i in range(45)
)


def _long_page(body_lead: str) -> str:
    """Длинная страница аналитики: хром навигации, таблица «ключевые показатели» с насыщением числами,
    длинный комментарий и ключевая фраза ближе к концу текста."""
    return (
        f"<html><body><h1>{body_lead}</h1><p>{NAV}</p>"
        "<p>Ключевые показатели: годовая инфляция, инфляционные ожидания, динамика цен.</p>"
        "<p>9,3% 5,2% 2,6% 4–6% 7,1% 8,4% 3,9% 6,0% 11,2% 2,1%</p>"
        f"<p>{COMMENTARY}</p>"
        f"<p>{KEY_SENTENCE} {VALUE}</p>"
        "<p>Оценка устойчивости будет опубликована в следующем отчёте.</p></body></html>"
    )


PAGE_LONG_ANALYTICS = _long_page("Мониторинг ключевых показателей")
# короткая страница-релиз: та же формулировка стоит в первых строках
PAGE_RELEASE = (
    "<html><body><h1>Пресс-релиз о потребительских ценах</h1>"
    f"<p>{KEY_SENTENCE} {VALUE}. Регулятор подтверждает это значение опубликованным рядом.</p>"
    "</body></html>"
)


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = {"/": PAGE_RELEASE, "/long": PAGE_LONG_ANALYTICS}.get(self.path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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

    def url(self, path: str = "") -> str:
        return f"{self.base}{path}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def origin() -> Iterator[FakeOrigin]:
    served = FakeOrigin()
    yield served
    served.stop()


async def _activate(engine: Any, origin: FakeOrigin) -> None:
    """Curated-профиль с research.fetch и локальным origin (тот же путь, что в test_research_provenance)."""
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = origin.base
    rp["private_allowlist"] = origin.allowlist
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], "research.fetch", "web.search"]
    assert (await _run_online(engine, payload)).state == "active"


def _fetch(url: str, rationale: str = "Читаю страницу источника") -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": rationale,
            "expected_information": "текст источника",
            "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": url}},
        }
    }


_COMPLETE = {
    "content": {
        "public_rationale": "Значение первоисточника прочитано, прекращаю исследование",
        "decision": {"kind": "complete", "reason": "goal_reached"},
    }
}


def _curator(statement: str) -> dict[str, Any]:
    return {
        "content": {
            "summary": "Значение подтверждено первоисточником",
            "claims": [
                {"statement": statement, "claim_type": "external_fact", "scope": {"объект": "годовая инфляция"}}
            ],
            "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            "new_questions": [],
        }
    }


async def _run(
    scratch_url: str, fake_llm: FakeLLM, origin: FakeOrigin, tmp_path: Path, question_id: Any
) -> Any:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=ResearchProxyService(factory, FilesystemArtifactStore(tmp_path / "artifacts")),
    )
    try:
        return await orchestrator.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()


async def _report_evidence(scratch_url: str) -> list[dict[str, Any]]:
    """Улики, записанные хостом в отчёт сессии (тот же объект, что уходил в промпт куратора)."""
    row = await _scalar(
        scratch_url,
        "SELECT payload->'evidence' FROM audit_events WHERE type = 'session_state_changed' "
        "AND payload ? 'evidence' ORDER BY sequence DESC LIMIT 1",
    )
    payload = row[0] if row is not None else None
    assert isinstance(payload, list), row
    return payload


def _curator_prompts(fake_llm: FakeLLM) -> list[str]:
    """Промпты роли «куратор»: их хост собирает с разделом `# Evidence` (role_prompt_curator-v8)."""
    marker = "# Evidence"
    return [
        str(request["last_user"])
        for request in fake_llm.requests()
        if isinstance(request.get("last_user"), str) and marker in request["last_user"]
    ]


@pytest.mark.asyncio
async def test_question_value_pulls_the_deep_sentence_into_the_curator_prompt(
    migrated_db: tuple[str, Any],
    origin: FakeOrigin,
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    await _activate(engine, origin)
    question_id = await _seed_question(scratch_url, QUESTION_WITH_VALUE)

    long_url = origin.url("/long")
    fake_llm.script(
        [
            _fetch(long_url),
            _COMPLETE,
            _curator(f"Годовая инфляция в России за декабрь 2025 года составила {VALUE}"),
        ]
    )
    outcome = await _run(scratch_url, fake_llm, origin, tmp_path, question_id)
    assert outcome.final_state.value == "succeeded", outcome

    # (1) payload улики: второе окно заняло точное значение вопроса, а не ранний блок с числами
    evidence = await _report_evidence(scratch_url)
    fragments = [str(item["payload"].get("assertion_text") or "") for item in evidence]
    # фрагмент одной улики = окна по 2000 знаков, склеенные сепаратором (их не больше двух)
    windows = [window for fragment in fragments for window in fragment.split("\n[…]\n") if window]
    assert len(windows) <= 2 * len(fragments), fragments
    deep = [window for window in windows if KEY_SENTENCE in window and VALUE in window]
    assert deep, fragments
    # якорное окно — вырез вокруг самого значения: навигационный хром в него не попал
    assert all("Отобразить/Скрыть подраздел" not in window for window in deep), deep

    # (2) промпт куратора: ключевая фраза реально дошла до модели (на старом коде этого не было)
    prompts = _curator_prompts(fake_llm)
    assert prompts, "кураторский запрос к модели не найден"
    assert any(KEY_SENTENCE in prompt and VALUE in prompt for prompt in prompts), prompts

    # (3) куратор смог предложить утверждение, и оно применено: ответ записан
    assert outcome.claims_proposed == 1, outcome
    row = await _scalar(
        scratch_url, "SELECT statement FROM claims WHERE created_in_session = :s", {"s": str(outcome.session_id)}
    )
    assert row is not None and VALUE in str(row[0]), row


@pytest.mark.asyncio
async def test_researcher_quote_pulls_the_deep_sentence_of_a_second_page(
    migrated_db: tuple[str, Any],
    origin: FakeOrigin,
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """В вопросе нет ни одного числа — общий value-якорь не знает, что искать. Сигнал даёт
    дословная цитата исследователя: он прочитал формулировку в релизе и называет её своими
    словами, затем читает длинную аналитическую страницу с той же фразой в глубине текста."""
    scratch_url, engine = migrated_db
    await _activate(engine, origin)
    question_id = await _seed_question(scratch_url, QUESTION_WITHOUT_VALUES)

    release_url = origin.url()
    long_url = origin.url("/long")
    fake_llm.script(
        [
            _fetch(release_url, "Читаю пресс-релиз регулятора"),
            {
                "content": {
                    "public_rationale": (
                        f"В релизе прочитано дословно: «{KEY_SENTENCE} {VALUE}». Сверяю эту формулировку "
                        "с аналитической страницей того же регулятора."
                    ),
                    "expected_information": "та же формулировка на аналитической странице",
                    "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": long_url}},
                }
            },
            _COMPLETE,
            _curator(f"Годовая инфляция в России за декабрь 2025 года составила {VALUE}"),
        ]
    )
    outcome = await _run(scratch_url, fake_llm, origin, tmp_path, question_id)
    assert outcome.final_state.value == "succeeded", outcome

    fragments = [str(item["payload"].get("assertion_text") or "") for item in await _report_evidence(scratch_url)]
    assert len(fragments) == 2, fragments
    # второе окно (длинная страница) заняла цитата исследователя: фраза с концом страницы попала в фрагмент
    deep = fragments[1]
    assert KEY_SENTENCE in deep and VALUE in deep, fragments
    # окна не превращаются в пересказ: текст модели («Сверяю эту формулировку») в доказательство не попал
    assert "Сверяю эту формулировку" not in deep, deep[:200]

    prompts = _curator_prompts(fake_llm)
    assert any(KEY_SENTENCE in prompt for prompt in prompts), prompts
    assert outcome.claims_proposed == 1, outcome
