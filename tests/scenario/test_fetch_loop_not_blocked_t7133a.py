"""T7.133a (часть 4а): медленная нормализация НЕ блокирует событийный цикл.

Настоящий `ResearchProxyService` с подставной сетью и подставным медленным
нормализатором: пока тело нормализуется, параллельная корутина-тикальщик обязана
продолжать тикать с паузами много меньше секунды. На `ae930dd` нормализация
стояла в цикле синхронно, и тикание замирало на всю длительность разбора.
Стендовые сессия, API и веб живут в одном процессе и одном цикле (замечание
приёмки T7.133), поэтому тест — про цикл сервиса, а не только про нормализатор.

Тест меряет wall-clock и помечен `timing` (AGENTS.md §7, T7.56: новые wall-clock
тесты в параллельный пул не добавлять).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from tests.scenario.test_fetch_refusal_messages_t7133 import _StubbornTransport
from tests.scenario.test_research_evidence import _section, _set_section

pytestmark = [pytest.mark.scenario, pytest.mark.timing]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7133"
URL = "https://rosstat.example/employment_0926.pdf"

#: длительность подставной медленной нормализации и допустимая задержка тика
SLOW_SECONDS = 2.0
MAX_TICK_GAP = 0.5


async def test_slow_normalization_does_not_block_event_loop(
    migrated_db: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Пока медленный нормализатор работает, цикл тикает; конверт fetch — прежний."""
    import apps.research_proxy.service as svc

    pdf_bytes = (FIXTURES / "employment-0926.pdf").read_bytes()
    golden_text_sha = "d2aa364cebec3bb96ec050d9c5c4c3a36731ed6de4f525bd78d73d5bbdba9109"
    real_normalize = svc.normalize_content
    golden_text_sha = "d2aa364cebec3bb96ec050d9c5c4c3a36731ed6de4f525bd78d73d5bbdba9109"

    def slow_normalize(content_type: str | None, data: bytes) -> Any:
        time.sleep(SLOW_SECONDS)  # имитация 290-страничного разбора (15,4 с на стенде)
        return real_normalize(content_type, data)

    class _Transport(_StubbornTransport):
        BODIES: ClassVar[dict[str, tuple[str | None, bytes]]] = {URL: ("application/pdf", pdf_bytes)}

    monkeypatch.setattr(svc, "normalize_content", slow_normalize)
    monkeypatch.setattr(svc, "FetchClient", _Transport)

    scratch_url, engine = migrated_db
    await _set_section(scratch_url, _section(mode="curated"))
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    service = ResearchProxyService(async_sessionmaker(engine, expire_on_commit=False), store)

    loop = asyncio.get_running_loop()
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = loop.time()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = loop.time()
            gaps.append(now - last)
            last = now

    tick_task = asyncio.create_task(ticker())
    try:
        envelope = await service.fetch(URL)
    finally:
        stop.set()
        await tick_task
        await engine.dispose()

    # цикл не стоял: ни одна пауза тиканья не превысила допустимую задержку
    assert gaps, "тикальщик не успел сделать ни одного тика"
    assert max(gaps) < MAX_TICK_GAP, f"цикл блокировался на {max(gaps):.2f} с"
    assert len(gaps) >= int(SLOW_SECONDS / 0.1)  # тиканье шло всё время разбора

    # результат не изменился: конверт и хэши прежние
    assert envelope["normalized_sha256"] == golden_text_sha
    assert envelope["normalized_text_sha256"] == golden_text_sha
    assert envelope["original_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert envelope["transform_chain"] == ["fetch", "normalize:pdf-text", "pdf-pages:4"]
    assert store.get(golden_text_sha).decode("utf-8").startswith("Срочная информация")
