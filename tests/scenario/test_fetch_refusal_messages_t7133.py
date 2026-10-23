"""T7.133 (части 3 и 5в/5г): честные сообщения research.fetch о пустом тексте.

Первый тест — живой цикл оркестратора с подставным research-каналом: модель
получает РАЗНЫЕ понятные причины вместо прежнего «normalized artifact unreadable»
на всём, что не читалось как HTML. Второй — настоящий `ResearchProxyService` с
подставной сетью: причина отказа живёт в конверте fetch и аддитивным ключом в
существующем журнальном событии `research_fetch_completed` (новых AuditEventType нет).
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.normalization import (
    REFUSAL_BOT_CHALLENGE_STUB,
    REFUSAL_EMPTY_PAGE,
    REFUSAL_PDF_NO_TEXT_LAYER,
    REFUSAL_UNSUPPORTED_FORMAT,
)
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.models.enums import AuditEventType, SessionState
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_explorer_host_availability_t7111 import (
    _COMPLETE,
    _CURATOR,
    _curated_payload,
    _DictStore,
    _fetch_call,
    _gateway,
    _rows,
    _seed_question,
)
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import _section, _set_section

pytestmark = [pytest.mark.scenario]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7133"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

MSG_UNSUPPORTED = (
    "формат «application/vnd.ms-excel» не поддерживается нормализатором: "
    "нормализованного текста нет"
)
MSG_NO_TEXT_LAYER = (
    "PDF без текстового слоя: в документе нет извлекаемого текста (вероятно, скан); "
    "текста для анализа нет"
)
MSG_BOT_STUB = (
    "страница — заглушка защиты от ботов (сайт требует JavaScript); "
    "текста нет — не возвращайся к этому сайту в этой сессии"
)
MSG_EMPTY_PAGE = "пустая страница: видимого текста на странице нет"
MSG_ARTIFACT_UNREADABLE = "normalized artifact unreadable"
MSG_LEGACY = "no normalized text available for this content"


class FakeRefusalService:
    """Подставной research-канал: конверты ровно формы T7.133 (причина отказа —
    закрытый набор `normalization_refusal` прокси)."""

    def __init__(self) -> None:
        self.store = _DictStore({EMPTY_SHA256: b""})

    async def fetch(self, url: str) -> dict[str, Any]:
        base: dict[str, Any] = {
            "source_id": "fake-src",
            "final_url": url,
            "mode": "curated",
            "original_sha256": "a" * 64,
            "transform_chain": ["fetch"],
        }
        if "excel.example" in url:
            return {
                **base,
                "normalized_sha256": None,
                "content_type": "application/vnd.ms-excel",
                "normalization_refusal": REFUSAL_UNSUPPORTED_FORMAT,
            }
        if "scan.example" in url:
            return {
                **base,
                "normalized_sha256": None,
                "content_type": "application/pdf",
                "normalization_refusal": REFUSAL_PDF_NO_TEXT_LAYER,
            }
        if "stub.example" in url:
            # заглушка: артефакт ЕСТЬ (sha пустой строки), он читается, он пуст
            return {
                **base,
                "normalized_sha256": EMPTY_SHA256,
                "content_type": "text/html",
                "normalization_refusal": REFUSAL_BOT_CHALLENGE_STUB,
            }
        if "blank.example" in url:
            return {
                **base,
                "normalized_sha256": EMPTY_SHA256,
                "content_type": "text/html",
                "normalization_refusal": REFUSAL_EMPTY_PAGE,
            }
        if "artifact.example" in url:
            # sha есть, артефакта по этому адресу в хранилище нет — единственный
            # случай, где «normalized artifact unreadable» остаётся правдой
            return {**base, "normalized_sha256": "0" * 64, "content_type": "application/pdf"}
        if "legacy.example" in url:
            # конверт до T7.133: ключа причины нет — прежняя формулировка сохранена
            return {**base, "normalized_sha256": None}
        raise AssertionError(f"unexpected url: {url}")


async def test_research_fetch_messages_carry_honest_refusal_reasons(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """Шесть форм отказа → шесть разных сообщений модели; ни одно не метит хост."""
    scratch_url, engine = migrated_db
    result = await _run_online(engine, _curated_payload(["research.fetch"]))
    assert result.state == "active"
    question_id = await _seed_question(scratch_url, "Где Росстат публикует численность рабочей силы?")

    gateway: LLMMiddleware = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=FakeRefusalService(),
    )
    fake_llm.script(
        [
            _fetch_call("https://excel.example/report.xlsx"),  # шаг 1: не форматируемый тип
            _fetch_call("https://scan.example/skan.pdf"),  # шаг 2: PDF без текстового слоя
            _fetch_call("https://stub.example/news"),  # шаг 3: заглушка защиты от ботов
            _fetch_call("https://blank.example/empty"),  # шаг 4: настоящая пустая страница
            _fetch_call("https://artifact.example/z"),  # шаг 5: артефакт правда не читается
            _fetch_call("https://legacy.example/z"),  # шаг 6: конверт до T7.133
            copy.deepcopy(_COMPLETE),  # шаг 7
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
    assert outcome.final_state is SessionState.SUCCEEDED

    failures = await _rows(
        scratch_url,
        "SELECT payload->>'error' FROM audit_events WHERE type = :t AND payload->>'denied' IS NULL "
        "ORDER BY sequence",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert [row[0] for row in failures] == [
        MSG_UNSUPPORTED,
        MSG_NO_TEXT_LAYER,
        MSG_BOT_STUB,
        MSG_EMPTY_PAGE,
        MSG_ARTIFACT_UNREADABLE,
        MSG_LEGACY,
    ]
    assert len({row[0] for row in failures}) == 6

    # пустой текст — свойство страницы: хосты не закрываются (T7.111 не тронут)
    marked = await _rows(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t AND payload->>'reason' = "
        "'host_unavailable_in_session'",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert marked[0][0] == 0
    # ни одного прочитанного наблюдения (все ответы — отказы)
    read = await _rows(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t",
        {"t": AuditEventType.RESEARCH_CONTENT_READ.value},
    )
    assert read[0][0] == 0


# ─── настоящий прокси: причина в конверте и аддитивный ключ журнала ──────────────


class _StubbornTransport:
    """Подставная сеть вместо FetchClient: у каждого адреса своё тело и Content-Type."""

    BODIES: ClassVar[dict[str, tuple[str | None, bytes]]] = {}

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def fetch(self, url: str) -> FetchResult:
        content_type, data = self.BODIES[url]
        return FetchResult(
            url=url,
            final_url=url,
            content_type=content_type,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


async def test_service_envelope_and_journal_carry_refusal_reason(
    migrated_db: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Заглушка/скан/неизвестный тип → ключ `normalization_refusal` в конверте и в
    payload существующего события; у PDF-успеха ключа нет и текст нормализован."""
    import apps.research_proxy.service as svc

    stub_html = (FIXTURES / "frankmedia-stub.html").read_bytes()
    pdf_bytes = (FIXTURES / "employment-0926.pdf").read_bytes()
    _StubbornTransport.BODIES = {
        "https://frankmedia.example/260326": ("text/html; charset=UTF-8", stub_html),
        "https://rosstat.example/employment_0926.pdf": ("application/pdf", pdf_bytes),
        "https://docs.example/report.docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK\x03\x04fake",
        ),
    }
    monkeypatch.setattr(svc, "FetchClient", _StubbornTransport)

    scratch_url, engine = migrated_db
    await _set_section(scratch_url, _section(mode="curated"))
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    service = ResearchProxyService(async_sessionmaker(engine, expire_on_commit=False), store)
    envelopes: dict[str, dict[str, Any]] = {}
    try:
        for url in _StubbornTransport.BODIES:
            envelopes[url] = await service.fetch(url)
    finally:
        await engine.dispose()

    stub_env = envelopes["https://frankmedia.example/260326"]
    assert stub_env["normalized_sha256"] == EMPTY_SHA256  # поведение по хешам не сдвинулось
    assert stub_env["normalization_refusal"] == REFUSAL_BOT_CHALLENGE_STUB

    pdf_env = envelopes["https://rosstat.example/employment_0926.pdf"]
    assert pdf_env.get("normalization_refusal") is None  # ключа отказа у успеха нет
    assert pdf_env["normalized_sha256"]  # PDF теперь НОРМАЛИЗУЕТСЯ
    assert "normalize:pdf-text" in pdf_env["transform_chain"]
    assert "pdf-pages:4" in pdf_env["transform_chain"]
    assert store.get(str(pdf_env["normalized_sha256"])).decode("utf-8").startswith("Срочная информация")

    docx_env = envelopes["https://docs.example/report.docx"]
    assert docx_env["normalized_sha256"] is None
    assert docx_env["normalization_refusal"] == REFUSAL_UNSUPPORTED_FORMAT

    journal = await _rows(
        scratch_url,
        "SELECT payload->>'url', coalesce(payload->>'normalization_refusal', '-') "
        "FROM audit_events WHERE type = :t ORDER BY sequence",
        {"t": AuditEventType.RESEARCH_FETCH_COMPLETED.value},
    )
    assert journal == [
        ("https://frankmedia.example/260326", REFUSAL_BOT_CHALLENGE_STUB),
        ("https://rosstat.example/employment_0926.pdf", "-"),
        ("https://docs.example/report.docx", REFUSAL_UNSUPPORTED_FORMAT),
    ]
