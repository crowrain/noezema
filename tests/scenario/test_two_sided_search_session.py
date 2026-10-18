"""Scenario (fake LLM + fake search + fake pages): двусторонний поиск спорного числа (T7.76).

Что воспроизводится по форме стенда .92 (STATUS T7.76 §1): вопрос про годовую инфляцию, официальный
первоисточник прочитан, и на этом поиск заканчивался — вторая сторона (независимая оценка) не
выполнялась вовсе. После T7.75 (ADR-0029 B) хост склеивает пересказы одного первоисточника в одну
группу независимости, поэтому такая работа больше не может выглядеть как «Проверено»: она честно падает
до `insufficient_independence`. Тесты ниже проверяют обе половины правки:

1. **двусторонний поиск доводит до двух групп**: официальный первоисточник + отдельный запрос про
   независимую оценку → две группы независимости, оценка `requirements_met`, карточка показывает оба
   прочитанных источника и обе величины;
2. **односторонний поиск остаётся честным E1**: первоисточник и его пересказ (разные сайты, один
   первоисточник) — одна группа, причина названа прямо, «Проверено» не выставлено;
3. **бюджет шагов и схемы аргументов доходят до модели**: в первом же запросе к модели контекст содержит
   раздел «Схемы аргументов выданных инструментов» с контрактом `question.create` (ровно `text`/`origin`)
   и раздел «Бюджет шага» с числами снапшота (`Шаг 1 из 16` — `max_explorer_steps` config-v16);
4. **повторное чтение того же адреса видно модели**: второй заход на тот же URL исполняется (он legitimately
   проверяет свежесть), но наблюдение следующего шага говорит, что первой попыткой был шаг N и второй
   группы независимости от этого не появится — повтор не добавляет ни знания, ни группы независимости.

Сети нет: `apps.research_proxy.service.FetchClient` подменён полностью (и для страницы, и для upstream
поиска). Домены страниц — заведомо разные registrable domains (`rosstat.example`, `interfax.example`,
`research.example.org`): `.example` PSL не знает, и хосты вида `x.example.ru` слились бы в `example.ru`
(AGENTS §7, T7.75).
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question

pytestmark = [pytest.mark.scenario]

REPO_ROOT = Path(__file__).resolve().parents[2]

STATEMENT_TWO_SIDED = (
    "Годовая инфляция в России по итогам 2025 года: официальные данные 5,59%, независимая оценка 6,4%"
)
STATEMENT_OFFICIAL_ONLY = "Годовая инфляция в России по итогам 2025 года составила 5,59%"

QUESTION_TEXT = (
    "Какая годовая инфляция в России по итогам 2025 года и совпадают ли с ней независимые оценки?"
)

URL_OFFICIAL = "https://rosstat.gov.ru/storage/mediabank/cpi-2025.html"
URL_RETAIL = "https://interfax.example/news/inflyaciya-2025"
URL_RETAIL_OTHER = "https://expert.example/economics/inflyaciya"
URL_INDEPENDENT = "https://research.example.org/inflation-estimate-2025"

PAGES: dict[str, str] = {
    # первоисточник: собственные данные ведомства, заявленные от первого лица
    URL_OFFICIAL: (
        "Служба государственной статистики сообщает по итогам собственного расчёта: потребительские цены "
        "в 2025 году выросли на 5,59%."
    ),
    # пересказ релиза ведомства на другом сайте (тот же первоисточник)
    URL_RETAIL: (
        "По данным Росстата, потребительские цены в 2025 году выросли на 5,59%. Об этом говорится "
        "в опубликованных материалах ведомства."
    ),
    # второй пересказ того же релиза: другой сайт, то же сообщение первоисточника
    URL_RETAIL_OTHER: (
        "Как сообщает Федеральная служба государственной статистики, годовая инфляция достигла 5,59% "
        "по итогам декабря."
    ),
    # независимая оценка: своя методика, другая величина, явный маркер независимого расчёта
    URL_INDEPENDENT: (
        "Независимая оценка инфляции 2025 года: по нашей методике показатель составил 6,4% — в отличие "
        "от официальных данных, которые мы пересчитали по своей корзине."
    ),
}

QUERY_OFFICIAL = "Росстат годовая инфляция 2025 официальный релиз"
QUERY_INDEPENDENT = "независимая оценка инфляции 2025 альтернативные оценки"


class FakeEngineClient:
    """Подмена HTTP-слоя research proxy целиком: и страницы, и upstream поиск SearXNG.

    Ответ поиска зависит от формулировки запроса — именно так тест видит, искала ли модель вторую
    сторону или перечитывала официальный релиз.
    """

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.path.endswith("/search"):
            query = (parse_qs(parsed.query).get("q") or [""])[0]
            data = json.dumps({"results": _hits_for(query)}, ensure_ascii=False).encode()
        else:
            body = PAGES[url]
            data = f"<html><body><p>{body}</p></body></html>".encode()
        return FetchResult(
            url=url,
            final_url=url,
            content_type="application/json" if parsed.path.endswith("/search") else "text/html",
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


def _hits_for(query: str) -> list[dict[str, str]]:
    lowered = query.lower()
    if "независ" in lowered or "альтернативн" in lowered:
        return [
            {
                "url": URL_INDEPENDENT,
                "title": "Независимая оценка инфляции 2025 года",
                "content": "по нашей методике показатель составил 6,4%",
            },
            {
                "url": URL_RETAIL,
                "title": "Инфляция 2025: цифры ведомства",
                "content": "по данным Росстата цены выросли на 5,59%",
            },
        ]
    return [
        {
            "url": URL_OFFICIAL,
            "title": "Официальный релиз о потребительских ценах за 2025 год",
            "content": "потребительские цены в 2025 году выросли на 5,59%",
        }
    ]


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)


def _payload() -> dict[str, Any]:
    """Настоящий payload config-v16 (тот же файл, что активирует менеджер): промпт explorer-v8 и
    `max_explorer_steps` 16 приходят в сессию как есть."""
    payload = json.loads((REPO_ROOT / "docs" / "eval" / "config-v16-payload.json").read_text(encoding="utf-8"))
    payload = copy.deepcopy(payload)
    # адрес поиска остаётся локальным заглушённым origin: FetchClient подменён, сети в тесте нет
    payload["research_proxy"]["searxng_url"] = "http://127.0.0.1:8888"
    payload["research_proxy"]["rate_limit_max"] = 20
    return payload


def _search(query: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": "Ищу источник",
            "expected_information": "адреса подходящих страниц",
            "decision": {"kind": "tool", "tool": "web.search", "arguments": {"query": query}},
        }
    }


def _fetch(url: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": "Читаю страницу источника",
            "expected_information": "текст источника",
            "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": url}},
        }
    }


_COMPLETE = {
    "content": {
        "public_rationale": "Официальная и независимая стороны собраны, прекращаю исследование",
        "decision": {"kind": "complete", "reason": "goal_reached"},
    }
}


def _curator(statement: str, evidence_count: int) -> dict[str, Any]:
    return {
        "content": {
            "summary": "Годовая инфляция 2025 года",
            "claims": [
                {
                    "statement": statement,
                    "claim_type": "external_fact",
                    "scope": {"объект": "годовая инфляция"},
                }
            ],
            "evidence_links": [
                {"evidence_index": i, "claim_index": 0, "relation": "supports"} for i in range(evidence_count)
            ],
            "new_questions": [],
        }
    }


async def _run_session(
    scratch_url: str,
    fake_llm: FakeLLM,
    workspace: Path,
    artifacts: Path,
    question_id: uuid.UUID,
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
        executor=StubToolExecutor(workspace),
        research_service=ResearchProxyService(factory, FilesystemArtifactStore(artifacts)),
    )
    try:
        return await orchestrator.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()


async def _groups(scratch_url: str, statement: str) -> list[Any]:
    """Группы независимости текущей оценки утверждения (source → group → основание)."""
    return await _all(
        scratch_url,
        "SELECT s.canonical_uri, m.group_id, m.basis FROM source_independence_members m "
        "JOIN sources s ON s.id = m.source_id "
        "JOIN source_independence_snapshots sn ON sn.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = sn.id "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement = '" + statement.replace("'", "''") + "' ORDER BY s.canonical_uri",
    )


async def _grade(scratch_url: str, statement: str) -> Any:
    return await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status FROM claims c "
        "JOIN claim_assessment_heads h ON h.claim_id = c.id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id WHERE c.statement = :s",
        {"s": statement},
    )


async def _reasons(scratch_url: str) -> Any:
    return await _scalar(
        scratch_url,
        "SELECT payload->'reasons' FROM audit_events WHERE type = 'claim_assessed' ORDER BY sequence DESC LIMIT 1",
    )


async def _supports(scratch_url: str, statement: str) -> list[Any]:
    """Источники, которые реально стоят подтверждениями утверждения (relation supports)."""
    return await _all(
        scratch_url,
        "SELECT s.canonical_uri FROM evidence e JOIN sources s ON s.id = e.source_id "
        "JOIN claims c ON c.id = e.claim_id WHERE e.relation = 'supports' AND c.statement = '"
        + statement.replace("'", "''") + "' ORDER BY s.canonical_uri",
    )


# ─── 1. двусторонний поиск: две группы независимости и обе величины ──────────


@pytest.mark.asyncio
async def test_two_sided_search_reaches_two_independence_groups(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    question_id = await _seed_question(scratch_url, QUESTION_TEXT)
    fake_llm.script(
        [
            _search(QUERY_OFFICIAL),
            _fetch(URL_OFFICIAL),
            _search(QUERY_INDEPENDENT),
            _fetch(URL_INDEPENDENT),
            _COMPLETE,
            _curator(STATEMENT_TWO_SIDED, 2),
        ]
    )
    session = await _run_session(scratch_url, fake_llm, tmp_path / "ws", tmp_path / "artifacts", question_id)
    assert session.final_state.value == "succeeded"

    # (а) вторая сторона поиска действительно была: два разных upstream-запроса, второй — про независимую оценку
    queries = await _all(
        scratch_url,
        "SELECT payload->>'query' AS q FROM audit_events WHERE type = 'research_upstream_request' ORDER BY sequence",
    )
    texts = [row["q"] for row in queries]
    assert QUERY_OFFICIAL in texts and QUERY_INDEPENDENT in texts, texts

    # (б) хост разнёс эти два источника по разным группам независимости
    members = await _groups(scratch_url, STATEMENT_TWO_SIDED)
    assert {URL_OFFICIAL, URL_INDEPENDENT} <= {row["canonical_uri"] for row in members}, members
    groups = {row["group_id"] for row in members}
    assert len(groups) == 2, f"ожидались две группы независимости: {members}"

    # (в) оценка произведена правилами: требования выполнены, а не «недостаточно независимости»
    grade = await _grade(scratch_url, STATEMENT_TWO_SIDED)
    assert grade is not None and grade[0] == "E3", grade
    reasons = await _reasons(scratch_url)
    assert reasons is not None and "insufficient_independence" not in reasons[0], reasons

    # (г) карточка показывает оба прочитанных источника и обе величины — расхождение не спрятано
    from tests.scenario.test_web_answer_api import _client, _make

    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
    await app_engine.dispose()

    verification = card["claims"][0]["verification"]
    assert any("прочитано: 2" in phrase for phrase in verification), verification
    statement = card["claims"][0]["statement"]
    assert "5,59" in statement and "6,4" in statement, statement
    assert "использован один источник" not in verification, verification
    # бейдж надёжности — у утверждения: он перевод уже вычисленной оценки rules engine
    assert card["claims"][0]["reliability"]["level"] == "verified", card["claims"][0]

    # обе стороны стоят именно подтверждениями утверждения (а не «прочитали и забыли»)
    supports = await _supports(scratch_url, STATEMENT_TWO_SIDED)
    assert {row["canonical_uri"] for row in supports} == {URL_OFFICIAL, URL_INDEPENDENT}, supports


# ─── 2. односторонний поиск: честный E1 с названной причиной ─────────────────


@pytest.mark.asyncio
async def test_official_only_search_stays_an_honest_e1(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """Тот же вопрос, но вторая половина поиска не сделана: первоисточник и пересказ его релиза на
    другом сайте. После ADR-0029 B это одна группа независимости, и «Проверено» выставлено быть не может."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    question_id = await _seed_question(scratch_url, QUESTION_TEXT)
    fake_llm.script(
        [
            _search(QUERY_OFFICIAL),
            _fetch(URL_OFFICIAL),
            _search(QUERY_OFFICIAL),
            _fetch(URL_RETAIL),
            _COMPLETE,
            _curator(STATEMENT_OFFICIAL_ONLY, 2),
        ]
    )
    session = await _run_session(scratch_url, fake_llm, tmp_path / "ws", tmp_path / "artifacts", question_id)
    assert session.final_state.value == "succeeded"

    members = await _groups(scratch_url, STATEMENT_OFFICIAL_ONLY)
    assert {URL_OFFICIAL, URL_RETAIL} <= {row["canonical_uri"] for row in members}, members
    groups = {row["group_id"] for row in members}
    assert len(groups) == 1, f"пересказ релиза не имеет права давать вторую группу: {members}"

    grade = await _grade(scratch_url, STATEMENT_OFFICIAL_ONLY)
    assert grade is not None and grade[0] == "E1", grade
    reasons = await _reasons(scratch_url)
    assert reasons is not None and "insufficient_independence" in reasons[0], reasons

    from tests.scenario.test_web_answer_api import _client, _make

    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
    await app_engine.dispose()

    # «Проверено» при одной группе — нет; причина оценки названа прямо на карточке
    claim = card["claims"][0]
    assert claim["reliability"]["level"] != "verified", claim
    reasons = [row["code"] for row in claim["grade_reasons"]]
    assert "insufficient_independence" in reasons, claim["grade_reasons"]


# ─── 3. контекст шага: бюджет и схемы аргументов доходят до модели ──────────


@pytest.mark.asyncio
async def test_step_context_reaches_the_model_with_budget_and_argument_schemas(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    question_id = await _seed_question(scratch_url, QUESTION_TEXT)
    fake_llm.script(
        [
            _search(QUERY_OFFICIAL),
            _fetch(URL_OFFICIAL),
            _COMPLETE,
            _curator(STATEMENT_OFFICIAL_ONLY, 1),
        ]
    )
    session = await _run_session(scratch_url, fake_llm, tmp_path / "ws", tmp_path / "artifacts", question_id)
    assert session.final_state.value == "succeeded"

    prompts = [request["last_user"] for request in fake_llm.requests() if request.get("last_user")]
    explorer_prompts = [p for p in prompts if "# Доступные инструменты" in p]
    assert explorer_prompts, "ни один запрос к модели не был шагом исследователя"

    first = explorer_prompts[0]
    # точный контракт question.create: ровно text/origin; кураторские поля названы НЕ аргументами
    assert "# Схемы аргументов выданных инструментов" in first
    assert "- question.create(text:" in first and "origin:" in first, first[:600]
    assert "search_statements" not in _line_of(first, "question.create"), "схема не должна обещать чужие поля"
    assert "аргументом инструмента не являются" in first
    # числа снапшота config-v16 (max_explorer_steps = 16), а не догадка модели
    assert "# Бюджет шага" in first and "Шаг 1 из 16" in first, first[:600]

    # бюджет виден и на следующем шаге, уже с учётом потраченного действия
    later = explorer_prompts[1]
    assert "Шаг 2 из 16" in later and "осталось действий: 14" in later, later[:600]


def _line_of(prompt: str, tool: str) -> str:
    for line in prompt.splitlines():
        if line.startswith(f"- {tool}("):
            return line
    raise AssertionError(f"нет строки контракта {tool}: {prompt[:400]}")


# ─── 4. повторное чтение адреса видно модели (и не создаёт независимости) ───


@pytest.mark.asyncio
async def test_repeated_fetch_of_the_same_address_is_visible_to_the_model(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """Стенд потратил шаг на повторное чтение уже прочитанного `cbr.ru`. Такое действие и раньше
    стоило шаг; правка T7.76 делает его цену видимой: наблюдение следующего шага называет первую
    попытку и говорит, что второй группы независимости от повтора не будет."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    question_id = await _seed_question(scratch_url, QUESTION_TEXT)
    fake_llm.script(
        [
            _search(QUERY_OFFICIAL),
            _fetch(URL_OFFICIAL),
            _fetch(URL_OFFICIAL),  # повтор того же адреса
            _COMPLETE,
            _curator(STATEMENT_OFFICIAL_ONLY, 1),
        ]
    )
    session = await _run_session(scratch_url, fake_llm, tmp_path / "ws", tmp_path / "artifacts", question_id)
    assert session.final_state.value == "succeeded"

    prompts = [r["last_user"] for r in fake_llm.requests() if "# Доступные инструменты" in (r.get("last_user") or "")]
    assert len(prompts) >= 4, prompts
    assert all("повтор того же адреса" not in p for p in prompts[:3]), prompts[2][:600]
    after = prompts[3]
    assert "повтор того же адреса" in after, after[:800]
    assert "первая попытка была на шаге 2" in after, after[:800]

    # повтор исполнен как обычное действие (шаг списан), а не скрыт: два action_completed research.fetch
    started = await _all(
        scratch_url,
        "SELECT s.payload->>'tool' AS tool FROM audit_events s JOIN audit_events c "
        "ON c.type = 'action_completed' AND c.payload->>'action_id' = s.payload->>'action_id' "
        "AND c.payload->>'ok' = 'true' WHERE s.type = 'action_started' "
        "AND s.payload->>'tool' = 'research.fetch' ORDER BY s.sequence",
    )
    assert len(started) == 2, started

    # и он не принес второй группы независимости: тот же источник остаётся одной группой
    members = await _groups(scratch_url, STATEMENT_OFFICIAL_ONLY)
    assert {row["canonical_uri"] for row in members} == {URL_OFFICIAL}, members
    assert len({row["group_id"] for row in members}) == 1, members
