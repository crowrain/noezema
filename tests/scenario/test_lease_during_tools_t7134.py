"""T7.134: долгий инструмент не должен стоить сессии аренды; чтение — под общим сроком.

Случай стенда (прогон `c91ac6b-v30`, 23.10 2026, 22:00–23:19): вопрос `c2` трижды терял сессию
с `LeaseLost: heartbeat refused for session …`, каждый раз — сразу после долгого `research.fetch`
PDF Росстата (`Rab_sila_2026.pdf` — 42 396 мс; `osn-08-2026.pdf` — 13 425 мс + разбор;
`osn-08-2026.pdf` — 79 960 мс). TTL аренды 30 с во время исполнения инструмента НЕ продлевался:
guard был только вокруг вызовов модели, поэтому первый heartbeat после шага отказывал и сессия
умирала, не дожив до фиксации.

(а) `research.fetch` дольше TTL — сессия доживает до фиксации, отказа аренды нет.
(б) то же для долгого исполнения кода (`python.execute`: sandbox-профиль считает 60 с команды
    плюс 15 с хозяйского запаса против 30 с TTL).
(в) ОБЩИЙ срок одного чтения (загрузка + сохранение + нормализация): медленное чтение
    заканчивается честным отказом инструмента с закрытой формулировкой; сессия продолжается,
    хост НЕ помечается недоступным, текст доходит до модели.
(г) срок ФАЗЫ по-прежнему ограничивает: если продление отказано после `phase_deadline`,
    LeaseLost остаётся LeaseLost (регрессия).

Внешних запросов нет: сеть — подставной транспорт/подставной канал, модель — `fake_llm`.
Тесты измеряют wall-clock и помечены `timing` (AGENTS.md §7, T7.56: новые wall-clock тесты
в параллельный пул не добавлять).
"""

from __future__ import annotations

import asyncio
import copy
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.service import ResearchProxyError, ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.models.enums import AuditEventType, SessionState
from packages.domain.services.lease import LeaseLost
from packages.llm_gateway.config import ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_explorer_host_availability_t7111 import (
    _COMPLETE,
    _CURATOR,
    LIVE_SHA,
    LIVE_TEXT,
    _curated_payload,
    _fetch_call,
    _gateway,
    _rows,
    _seed_question,
)
from tests.scenario.test_fetch_refusal_messages_t7133 import _StubbornTransport
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import _section, _set_section

pytestmark = [pytest.mark.scenario, pytest.mark.timing]

# Масштаб — тот же, что у T7.47b/T7.51/T7.56: запас держится в несколько интервалов
# продления (guard продлевает каждые ttl/3), относительные доли TTL не используются.
LEASE_TTL_SECONDS = 3.0
SLOW_TOOL_SECONDS = 2.5 * LEASE_TTL_SECONDS  # 7,5 с: дольше TTL, много короче срока фазы (600 с)

SLOW_URL = "https://rosstat.example/slow_big.pdf"
OTHER_URL = "https://rosstat.example/other.pdf"
QUESTION_TEXT = "Какой индекс рынка труда у сервиса Гелиос?"


class _DictStore:
    """Тот же интерфейс, что у FilesystemArtifactStore: чтение нормализованного текста."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages

    def get(self, sha: str) -> bytes:
        if sha in self.pages:
            return self.pages[sha]
        raise KeyError(sha)


class _SlowFetchService:
    """Подставной research-канал, чьё чтение длится дольше аренды.

    Замер стенда: 42,4 с и 79,96 с против TTL 30 с — те же отношения в масштабе теста.
    Сессию вызывающего канал НЕ трогает (у настоящего прокси свои сессии), поэтому
    продление из guard'а законно — это и есть проверяемый инвариант.
    """

    def __init__(self) -> None:
        self.store = _DictStore({LIVE_SHA: LIVE_TEXT.encode("utf-8")})
        self.calls = 0

    async def fetch(self, url: str) -> dict[str, Any]:
        self.calls += 1
        await asyncio.sleep(SLOW_TOOL_SECONDS)
        return {
            "source_id": "fake-src",
            "final_url": url,
            "mode": "curated",
            "original_sha256": "a" * 64,
            "normalized_sha256": LIVE_SHA,
            "transform_chain": ["fetch"],
        }


class _BudgetRefusalService(_SlowFetchService):
    """Канал, который отвечает ровно тем отказом, что рождает общий срок чтения.

    Текст отказа передаётся из теста (имя новой функции импортируется внутри теста):
    на `c91ac6b` модуль обязан собраться, чтобы красность была по поведению, а не
    по ошибке импорта.
    """

    def __init__(self, notice: str) -> None:
        super().__init__()
        self.notice = notice

    async def fetch(self, url: str) -> dict[str, Any]:
        self.calls += 1
        raise ResearchProxyError(self.notice, "failed")


def _tool_call(tool: str, arguments: dict[str, Any], rationale: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": rationale,
            "decision": {"kind": "tool", "tool": tool, "arguments": arguments},
        }
    }


def _orchestrator(
    engine: Any, fake_llm: FakeLLM, workspace: Path, *, research_service: Any = None
) -> tuple[Orchestrator, Any]:
    gateway = _gateway(fake_llm)
    orchestrator = Orchestrator(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(workspace),
        research_service=research_service,
        lease_ttl=timedelta(seconds=LEASE_TTL_SECONDS),
    )
    return orchestrator, gateway


async def _lease_failures(scratch_url: str) -> list[Any]:
    """Любой след сорванной попытки или честного `session_failed` (отказ аренды)."""
    return await _rows(
        scratch_url,
        "SELECT type, payload->>'error_class' FROM audit_events "
        "WHERE type = :failed OR payload->>'unrecorded_attempt' = 'true' ORDER BY sequence",
        {"failed": AuditEventType.SESSION_FAILED.value},
    )


# ─── (а) долгое чтение под защитой аренды ──────────────────────────────────────


async def test_research_fetch_longer_than_lease_ttl_keeps_the_session(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """Чтение в 2,5 раза дольше TTL: аренда продлевается все 7,5 с, сессия фиксируется.

    На `c91ac6b` тест красный: после шага heartbeat отказывает (`LeaseLost`), фаза 1
    откатывается вместе со строкой сессии, `run_session` бросает исключение.
    """
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _curated_payload(["research.fetch"]))).state == "active"
    question_id = await _seed_question(scratch_url, QUESTION_TEXT)

    service = _SlowFetchService()
    orch, gateway = _orchestrator(engine, fake_llm, tmp_path / "ws", research_service=service)
    fake_llm.script(
        [
            _fetch_call(SLOW_URL),  # шаг 1: чтение дольше TTL
            copy.deepcopy(_COMPLETE),  # шаг 2: здесь и был первый отказ аренды
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert service.calls == 1
    assert outcome.final_state is SessionState.SUCCEEDED
    assert await _rows(
        scratch_url, "SELECT state FROM sessions WHERE id = :s", {"s": str(outcome.session_id)}
    ) == [("succeeded",)]
    assert await _rows(
        scratch_url, "SELECT count(*) FROM actions WHERE tool = 'research.fetch'"
    ) == [(1,)]
    assert await _lease_failures(scratch_url) == []


# ─── (б) долгое исполнение кода под защитой аренды ─────────────────────────────


async def test_long_code_execution_keeps_the_session(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """`python.execute` дольше TTL — то же самое: сессия доживает до фиксации.

    Этот инструмент назвал анализ: потолок исполнения (sandbox-профиль: 60 с команды + 15 с
    хозяйского запаса; dev-подставка: 15 с) заведомо больше TTL 30 с. На `c91ac6b` — красный
    по той же причине, что и (а).
    """
    scratch_url, engine = migrated_db
    question_id = await _seed_question(scratch_url, "Сколько будет 6*7, если считать медленно?")

    orch, gateway = _orchestrator(engine, fake_llm, tmp_path / "ws")
    fake_llm.script(
        [
            _tool_call(
                "python.execute",
                {"code": f"import time\ntime.sleep({SLOW_TOOL_SECONDS:g})\nprint(6*7)"},
                "медленное вычисление",
            ),
            copy.deepcopy(_COMPLETE),
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    actions = await _rows(
        scratch_url,
        "SELECT tool, state FROM actions WHERE session_id = :s",
        {"s": str(outcome.session_id)},
    )
    assert actions == [("python.execute", "completed")]
    assert await _lease_failures(scratch_url) == []


class _SlowSearchService:
    """Подставной канал, чей поиск (медленный upstream) длится дольше аренды."""

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str) -> dict[str, Any]:
        self.calls += 1
        await asyncio.sleep(SLOW_TOOL_SECONDS)
        return {
            "mode": "curated",
            "profile": "curated",
            "local": [],
            "upstream": [{"url": SLOW_URL, "title": "Выпуск", "content": "индекс рынка труда"}],
            "note": "недоверенный внешний контент",
        }


async def test_long_web_search_keeps_the_session(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """`web.search` дольше TTL — тот же путь: поиск идёт под тем же guard'ом.

    У поиска тот же неограниченный канал, что у чтения (поэстайм-ауты вместо общего
    срока), и analysis назвал его третьим. На `c91ac6b` — красный по той же причине.
    """
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _curated_payload(["web.search"]))).state == "active"
    question_id = await _seed_question(scratch_url, QUESTION_TEXT)

    service = _SlowSearchService()
    orch, gateway = _orchestrator(engine, fake_llm, tmp_path / "ws", research_service=service)
    fake_llm.script(
        [
            _tool_call("web.search", {"query": "независимая оценка индекса рынка труда"}, "поиск оценок"),
            copy.deepcopy(_COMPLETE),
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert service.calls == 1
    assert outcome.final_state is SessionState.SUCCEEDED
    assert await _rows(scratch_url, "SELECT count(*) FROM actions WHERE tool = 'web.search'") == [(1,)]
    assert await _lease_failures(scratch_url) == []


# ─── (в) общий срок одного чтения ──────────────────────────────────────────────


class _SlowTransport(_StubbornTransport):
    """Подставная сеть, которая не укладывается в общий срок чтения."""

    BODIES: ClassVar[dict[str, tuple[str | None, bytes]]] = {
        SLOW_URL: ("text/html; charset=UTF-8", b"<html><body>42</body></html>")
    }

    async def fetch(self, url: str) -> FetchResult:
        await asyncio.sleep(5.0)  # заведомо дольше и общего срока, и срока транспорта
        return await super().fetch(url)


async def test_slow_read_hits_total_budget_and_refuses_honestly(
    migrated_db: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Чтение целиком (загрузка + сохранение + нормализация) ограниченно ОДНИМ сроком.

    Отказ — тот же класс `ResearchProxyError` и тот же журнал `research_fetch_rejected`,
    но текстом, который исследователь понимает («…слишком долго (N с); не возвращайся к
    этому адресу в этой сессии»). Строка `sources` не появляется.
    """
    import apps.research_proxy.service as svc

    assert svc.FETCH_TOTAL_BUDGET_SECONDS == 90.0  # закрытая константа (обоснование — в service.py)
    monkeypatch.setattr(svc, "FETCH_TOTAL_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(svc, "FetchClient", _SlowTransport)

    scratch_url, engine = migrated_db
    await _set_section(scratch_url, _section(mode="curated"))
    service = ResearchProxyService(
        async_sessionmaker(engine, expire_on_commit=False), FilesystemArtifactStore(tmp_path)
    )

    started = time.monotonic()
    with pytest.raises(ResearchProxyError) as raised:
        await service.fetch(SLOW_URL)
    elapsed = time.monotonic() - started
    await engine.dispose()

    assert elapsed < 2.0  # отказ по общему сроку, а не по ожиданию сети
    assert str(raised.value) == (
        "страница читается слишком долго (0.2 с); не возвращайся к этому адресу в этой сессии"
    )
    assert raised.value.status == "failed"

    rejected = await _rows(
        scratch_url,
        "SELECT payload->>'reason' FROM audit_events WHERE type = :t ORDER BY sequence",
        {"t": AuditEventType.RESEARCH_FETCH_REJECTED.value},
    )
    assert [row[0] for row in rejected] == ["read budget exceeded: 0.2 s"]
    assert await _rows(scratch_url, "SELECT count(*) FROM sources") == [(0,)]


def test_read_budget_notice_is_the_closed_phrase() -> None:
    """Формулировка отказа — закрытая (число подставляет сам срок); величина — константа."""
    from apps.research_proxy.service import FETCH_TOTAL_BUDGET_SECONDS, read_budget_notice

    assert FETCH_TOTAL_BUDGET_SECONDS == 90.0
    assert read_budget_notice() == (
        "страница читается слишком долго (90 с); не возвращайся к этому адресу в этой сессии"
    )


async def test_read_budget_refusal_continues_session_and_does_not_mark_host(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """Отказ по общему сроку — неудача шага, а не смерть сессии; хост не помечается.

    Прежнее поведение для ТРАНСПОРТНОГО таймаута (`timeout: …`) — метить хост (T7.111);
    текст общего срока начинается иначе, поэтому хост остаётся доступным и повторное
    чтение того же хоста НЕ отклоняется guard'ом недоступности.
    """
    from apps.research_proxy.service import read_budget_notice

    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _curated_payload(["research.fetch"]))).state == "active"
    question_id = await _seed_question(scratch_url, QUESTION_TEXT)

    service = _BudgetRefusalService(read_budget_notice())
    orch, gateway = _orchestrator(engine, fake_llm, tmp_path / "ws", research_service=service)
    fake_llm.script(
        [
            _fetch_call(SLOW_URL),
            _fetch_call(OTHER_URL),  # тот же хост: guard недоступности не мешает
            copy.deepcopy(_COMPLETE),
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    assert service.calls == 2
    assert await _rows(
        scratch_url, "SELECT count(*) FROM actions WHERE tool = 'research.fetch'"
    ) == [(2,)]
    failures = await _rows(
        scratch_url,
        "SELECT payload->>'error', payload->>'reason' FROM audit_events WHERE type = :t "
        "ORDER BY sequence",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert len(failures) == 2
    assert all("страница читается слишком долго (90 с)" in str(row[0]) for row in failures)
    assert all(row[1] is None for row in failures)  # отказа guard'ом недоступности нет
    contexts = [r["last_user"] for r in fake_llm.requests()]
    assert "не возвращайся к этому адресу в этой сессии" in contexts[1]


# ─── (г) срок фазы по-прежнему ограничивает ────────────────────────────────────


async def _set_phase_deadline(scratch_url: str, seconds: int) -> None:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE config_snapshots SET session_limits = "
                    "jsonb_set(session_limits, '{phase_deadline_seconds}', to_jsonb(CAST(:v AS int))) "
                    "WHERE activation_state = 'active'"
                ),
                {"v": seconds},
            )
    finally:
        await engine.dispose()


async def test_renewal_refused_after_phase_deadline_still_raises_lease_lost(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """Guard не отменяет срок фазы: продление отказано — LeaseLost остаётся LeaseLost.

    Регрессия: после `phase_deadline` heartbeat обязан отказать, а попытка — оставить
    долговечный след (T7.132), потому что транзакция фазы 1 откатывается целиком.
    Отличие от прогона `c91ac6b-v30` — ОТКУДА именно отказ: там аренда кончалась сама
    и говорил heartbeat на границе шага («heartbeat refused for session …»); здесь
    продление внутри guard'а отказывает по сроку фазы («lease lost during long
    operation for session …») — на `c91ac6b` такой текст получить нечем, тест красный.
    """
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _curated_payload(["research.fetch"]))).state == "active"
    await _set_phase_deadline(scratch_url, 2)  # срок фазы короче, чем долгое чтение
    question_id = await _seed_question(scratch_url, QUESTION_TEXT)

    orch, gateway = _orchestrator(
        engine, fake_llm, tmp_path / "ws", research_service=_SlowFetchService()
    )
    fake_llm.script(
        [
            _fetch_call(SLOW_URL),
            copy.deepcopy(_COMPLETE),
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        with pytest.raises(LeaseLost) as lost:
            await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert "lease lost during long operation" in str(lost.value)
    trace = await _rows(
        scratch_url,
        "SELECT payload->>'error_class', payload->>'tool_calls' FROM audit_events "
        "WHERE type = :t AND payload->>'unrecorded_attempt' = 'true' ORDER BY sequence",
        {"t": AuditEventType.SESSION_FAILED.value},
    )
    assert trace == [("LeaseLost", "1")]
