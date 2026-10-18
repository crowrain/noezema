"""Scenario (T7.119 части 2–4): потолок 20 и подсказки независимых источников в реальной сессии.

Активируется payload из файлов `docs/eval/config-v26-payload.json` / `config-v27-payload.json`
(без подмен), вопрос из контрольного набора (c1 — «официальные + независимые», c4 — только
официальная цифра). Проверяется связка целиком: классификация факта в payload события
QUESTION_SELECTED (нового AuditEventType нет), итоговый потолок шагов, секция контекста
«Независимые источники по теме» и её отсутствие для неквалифицированных вопросов и для
config-v26 (отсутствие ключей = прежнее поведение).

`research.fetch` обслуживает подставной канал (уникальное содержимое на каждый URL), LLM —
FakeLLM: ни одного внешнего запроса, ни одного реального движка. Хосты вымышленные
(hint.example), каталог подсказки называет только закрепленные в payload домены.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

REPO_ROOT = Path(__file__).resolve().parents[2]
V26_PAYLOAD_FILE = REPO_ROOT / "docs" / "eval" / "config-v26-payload.json"
V27_PAYLOAD_FILE = REPO_ROOT / "docs" / "eval" / "config-v27-payload.json"

C1 = (
    "Как изменились реальные заработные платы в России в 2026 году по данным Росстата и что "
    "показывают независимые источники (hh.ru, SuperJob, аналитики банков)? Приведи официальные "
    "цифры и независимые оценки раздельно, с источниками и периодами."
)
C4 = (
    "Какова годовая инфляция в России в сентябре 2026 года по данным Росстата? Приведи "
    "официальную цифру с источником."
)

HINTS_HEADER = "# Независимые источники по теме"
STEP_LINE = "Предложи ровно одно следующее действие (JSON по схеме)."


class _DictStore:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages

    def get(self, sha: str) -> bytes:
        if sha in self.pages:
            return self.pages[sha]
        raise KeyError(sha)


class FakeFetchService:
    """Подставной research-канал: каждый URL получает СВОЁ нормализованное содержимое —
    сессия делает шаг к новой улике, а не топчется на одной странице."""

    def __init__(self) -> None:
        self.store = _DictStore({})

    async def fetch(self, url: str) -> dict[str, Any]:
        body = (
            f"Вымышленная страница {url}: независимая оценка актора «Гелиос», индекс 7,8 %.".encode()
        )
        sha = hashlib.sha256(url.encode("utf-8")).hexdigest()
        self.store.pages[sha] = body
        return {
            "source_id": "fake-src",
            "final_url": url,
            "mode": "curated",
            "original_sha256": hashlib.sha256(("o:" + url).encode("utf-8")).hexdigest(),
            "normalized_sha256": sha,
            "transform_chain": ["fetch"],
        }


async def _rows(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())
    finally:
        await engine.dispose()


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


def _fetch_script(count: int, *, complete: bool) -> list[dict[str, Any]]:
    script = [_fetch_call(f"https://hint.example/p{i:02d}") for i in range(1, count + 1)]
    if complete:
        script.append(copy.deepcopy(_COMPLETE))
    script.append(copy.deepcopy(_CURATOR))
    return script


async def _activate_and_seed(scratch_url: str, engine: Any, payload_file: Path, question_text: str) -> Any:
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    result = await _run_online(engine, payload)
    assert result.state == "active"
    return await _seed_question(scratch_url, question_text)


async def _seed_question(scratch_url: str, text_: str) -> Any:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            question = await QuestionRepository.create(
                db, ORMQuestion(text=text_, origin="seeded", priority=1)
            )
            return question.id
    finally:
        await engine.dispose()


def _explorer_contexts(fake_llm: FakeLLM) -> list[str]:
    return [r["last_user"] for r in fake_llm.requests() if STEP_LINE in r["last_user"]]


async def _question_selected_budget(scratch_url: str) -> dict[str, Any]:
    rows = await _rows(
        scratch_url,
        "SELECT payload->'explorer_budget' FROM audit_events WHERE type = :t",
        {"t": AuditEventType.QUESTION_SELECTED.value},
    )
    assert len(rows) == 1
    value = rows[0][0]
    # asyncpg декодирует JSONB в dict напрямую; строка встречается при другом драйвере
    return value if isinstance(value, dict) else json.loads(value)


# ─── config-v27: вопрос c1 получает потолок 20 и секцию подсказки ─────────────


async def test_v27_official_independent_question_runs_past_16_steps_with_hint_section(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    question_id = await _activate_and_seed(scratch_url, engine, V27_PAYLOAD_FILE, C1)

    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=FakeFetchService(),
    )
    # 17 выполненных fetch + complete: на config-v26 (16 шагов) этот скрипт не умещается —
    # шаг 17 был бы отменён бюджетом, а куратор читал бы complete
    fake_llm.script(_fetch_script(17, complete=True))
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()

    assert outcome.final_state is SessionState.SUCCEEDED
    # шаги 1..17 — чтения, шаг 18 — complete: на потолке 16 (config-v26) до complete не хватило бы
    assert outcome.steps == 18

    executed = await _rows(scratch_url, "SELECT count(*) FROM actions WHERE tool = 'research.fetch'")
    assert executed[0][0] == 17  # шаг 17 состоялся: поднятый потолок 20, а не 16

    budget = await _question_selected_budget(scratch_url)
    assert budget["classified_official_independent"] is True
    assert budget["max_explorer_steps"] == 20
    assert budget["independent_hint_topics"] == ["labor_wages"]
    assert "hint_catalog_problems" not in budget

    contexts = _explorer_contexts(fake_llm)
    assert len(contexts) == 18
    # секция подсказки стоит в контексте КАЖДОГО шага (фиксированная позиция, как у бюджета шага)
    assert all(HINTS_HEADER in ctx for ctx in contexts)
    assert all("подсказка, не ограничение" in ctx for ctx in contexts)
    assert all("- hh.ru — индекс hh.ru и аналитика зарплат" in ctx for ctx in contexts)
    # учёт шагов показывает новый потолок: шаг 17 состоялся бы только с ним
    assert "Шаг 17 из 20" in contexts[16]


# ─── config-v26 (без новых ключей): прежнее поведение на том же вопросе ────────


async def test_v26_same_question_keeps_cap_16_and_no_hint_section(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    question_id = await _activate_and_seed(scratch_url, engine, V26_PAYLOAD_FILE, C1)

    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=FakeFetchService(),
    )
    # ровно 16 fetch + куратор: бюджет исчерпан, complete не запрошен
    fake_llm.script(_fetch_script(16, complete=False))
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()

    executed = await _rows(scratch_url, "SELECT count(*) FROM actions WHERE tool = 'research.fetch'")
    assert executed[0][0] == 16
    assert outcome.termination_reason == "budget_exhausted"
    terminations = await _rows(
        scratch_url, "SELECT termination_reason FROM sessions WHERE termination_reason IS NOT NULL"
    )
    assert [row[0] for row in terminations] == ["budget_exhausted"]

    budget = await _question_selected_budget(scratch_url)
    # классификация считается и на v26 (она не зависит от наличия ключей), но потолка 20 нет:
    # отсутствие ключа max_explorer_steps_official_independent = прежнее поведение
    assert budget["classified_official_independent"] is True
    assert budget["max_explorer_steps"] == 16
    # каталога research_hints в v26 нет -> пустая подборка и пустая секция
    assert budget["independent_hint_topics"] == []

    contexts = _explorer_contexts(fake_llm)
    assert len(contexts) == 16
    assert all(HINTS_HEADER not in ctx for ctx in contexts)


# ─── config-v27, вопрос c4 (только официальная цифра): без повышения и подсказки ─


async def test_v27_official_only_question_keeps_cap_16_and_no_hint_section(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    question_id = await _activate_and_seed(scratch_url, engine, V27_PAYLOAD_FILE, C4)

    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=FakeFetchService(),
    )
    fake_llm.script(_fetch_script(16, complete=False))
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()

    executed = await _rows(scratch_url, "SELECT count(*) FROM actions WHERE tool = 'research.fetch'")
    assert executed[0][0] == 16  # потолок не поднят: вопрос не «официальные + независимые»
    assert outcome.termination_reason == "budget_exhausted"

    budget = await _question_selected_budget(scratch_url)
    assert budget["classified_official_independent"] is False
    assert budget["max_explorer_steps"] == 16
    assert budget["independent_hint_topics"] == []

    contexts = _explorer_contexts(fake_llm)
    assert all(HINTS_HEADER not in ctx for ctx in contexts)
