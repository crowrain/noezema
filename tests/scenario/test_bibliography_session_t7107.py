"""Scenario (fake LLM + fake search + fake pages): новая сессия записывает pairing
`bibliography_known_producer` без переатрибуции (T7.107, ADR-0029 дополнение).

Тот же стендовый случай, что в `test_reattribute_bibliography_t7107.py`, но порядок обратной:
деловое издание со строгой парой («… 2,2%, следует из данных Росстата») читается ПЕРВЫМ и его
запись строгого режима попадает в накопитель цикла формулировки; блог с плашкой площадки и
строкой «Источники: Росстат и данные портала HeadHunter.» читается вторым и получает запись
нового pairing в той же финальной транзакции — без ожидания переатрибуции окна.

Где живёт знание (закреплено тестом): накопитель `known_primaries_by_claim` — локальный для
одного предложения; записью нового pairing он НЕ пополняется (гвард «без цепочек»); утверждения,
для которых производитель не известен, остаются без записей.

После сессии `research-reattribute --evidence-level --dry-run` (тот же каталог артефактов) не
предлагает никаких записей: путь сессии и путь переатрибуции идемпотентны друг к другу.

Сети нет: `apps.research_proxy.service.FetchClient` подменён целиком (страница и upstream поиска);
страницы отдаются абзацами `<p>` — нормализованный текст артефакта равен фикстуре построчно.
Окно улики пересобирается T7.82 по дословным цитатам финальной rationale исследователя.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
from packages.artifacts.store import FilesystemArtifactStore
from tests.conftest import FakeLLM
from tests.scenario import test_two_sided_search_session as two
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_scope_coverage import _scalar, _seed_question

pytestmark = [pytest.mark.scenario]

CURRENT_VALUE_METHOD = "host-value-attribution-v7"
SINCE_ISO = "2000-01-01T00:00:00+00:00"

PLAQUE = (
    "Данная публикация является личным мнением автора. "
    "Мнение владельца сайта может не совпадать с мнением автора."
)

URL_INTERFAX = "https://interfax.example/business/1119461"
URL_SMARTLAB = "https://smart-lab.example/blog/1349924"

PAGES: dict[str, list[str]] = {
    URL_INTERFAX: [
        "Уровень безработицы в России опустился до 2,2%, следует из данных Росстата.",
        "Это минимальное значение за последние годы.",
    ],
    URL_SMARTLAB: [
        "Блог инвестора. О безработице.",
        "Раньше писал про уровень выше, сейчас он опустился до 2,2%.",
        "Настроения на рынке труда медленно улучшаются третий квартал подряд.",
        "Зарплаты растут быстрее цен, но не везде одинаково.",
        "Данная публикация является личным мнением автора. Мнение владельца сайта может "
        "не совпадать с мнением автора.",
        "Источники: Росстат и данные портала HeadHunter.",
    ],
}

QUERY = "уровень безработицы в России сейчас"
QUESTION = "Какой сейчас уровень безработицы в России?"
STATEMENT = "Уровень безработицы в России опустился до 2,2%."

#: дословные цитаты — якоря T7.82-переселекции окон обеих улик (подстроки фикстурных страниц)
RATIONALE = (
    'Прочитал два текста: "Уровень безработицы в России опустился до 2,2%, следует из данных '
    'Росстата." и "Раньше писал про уровень выше, сейчас он опустился до 2,2%." '
    "Числа совпадают, готово."
)


class FakeEngineClient:
    """Подмена HTTP-слоя research proxy целиком: и страница, и upstream-поиск SearXNG."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.path.endswith("/search"):
            hits = [
                {"url": URL_INTERFAX, "title": "Уровень безработицы", "content": "данные Росстата"},
                {"url": URL_SMARTLAB, "title": "Блог инвестора: безработица", "content": "2,2%"},
            ]
            data = json.dumps({"results": hits}, ensure_ascii=False).encode()
            content_type = "application/json"
        else:
            paragraphs = "".join(f"<p>{line}</p>" for line in PAGES[url] if line.strip())
            data = f"<html><body>{paragraphs}</body></html>".encode()
            content_type = "text/html"
        return FetchResult(
            url=url,
            final_url=url,
            content_type=content_type,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)


def _complete() -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": RATIONALE,
            "decision": {"kind": "complete", "reason": "goal_reached"},
        }
    }


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _record_of(scratch_url: str, canonical_uri: str) -> dict[str, Any] | None:
    row = await _scalar(
        scratch_url,
        "SELECT e.scope -> 'value_attribution' AS origin FROM evidence e "
        "JOIN claims c ON c.id = e.claim_id JOIN sources s ON s.id = e.source_id "
        "WHERE c.statement = " + _quoted(STATEMENT) + " AND s.canonical_uri = " + _quoted(canonical_uri),
    )
    assert row is not None, f"улики {canonical_uri} нет у утверждения"
    return None if row[0] is None else dict(row[0])


@pytest.mark.asyncio
async def test_new_session_writes_bibliography_pairing_from_the_first_pass(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_llm: FakeLLM, fake_engine: None
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, two._payload())).state == "active"
    question_id = await _seed_question(scratch_url, QUESTION)

    fake_llm.script(
        [
            two._search(QUERY),
            two._fetch(URL_INTERFAX),
            two._fetch(URL_SMARTLAB),
            _complete(),
            two._curator(STATEMENT, 2),
        ]
    )
    session = await two._run_session(
        scratch_url, fake_llm, tmp_path / "ws", tmp_path / "data" / "artifacts", question_id
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    # первая улика (деловое издание): обычный строгий режим — он и принёс знание
    strict = await _record_of(scratch_url, URL_INTERFAX)
    assert strict is not None and strict["primary_key"] == "rosstat", strict
    assert strict["pairing"] == "value_and_primary_in_fragment", strict
    assert strict["method"] == CURRENT_VALUE_METHOD, strict

    # вторая улика (блог с плашкой): третий режим, тот же родитель-якорь, без переатрибуции
    blog = await _record_of(scratch_url, URL_SMARTLAB)
    assert blog is not None, "запись нового pairing не появилась в сессии"
    assert blog["schema"] == "host-value-attribution-v1", blog
    assert blog["pairing"] == "bibliography_known_producer", blog
    assert blog["method"] == CURRENT_VALUE_METHOD, blog
    assert blog["primary_key"] == "rosstat", blog
    assert str(blog["parent_source_id"]) == str(strict["parent_source_id"]), blog
    assert "Источники: Росстат" in blog["basis_fragment"], blog

    # группировка свернулась сразу: обе улики — дети одного якоря
    groups = await _scalar(
        scratch_url,
        "SELECT count(DISTINCT m.group_id) FROM source_independence_members m "
        "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id AND h.assessment_state = 'current' "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = " + _quoted(STATEMENT),
    )
    assert groups is not None and int(groups[0]) == 1, groups

    # идемпотентность между путями: переатрибуция того же окна не предлагает ничего нового
    from apps.research_proxy.reattribution import reattribute_evidence_window

    engine2 = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine2, expire_on_commit=False)
    try:
        report = await reattribute_evidence_window(
            factory,
            FilesystemArtifactStore(tmp_path / "data" / "artifacts"),
            since=datetime.fromisoformat(SINCE_ISO).replace(tzinfo=UTC),
            dry_run=True,
        )
    finally:
        await engine2.dispose()
    assert report.changed_evidence == 0, report.rows
    assert all(row.action == "изменений нет: решение уже записано ранее" for row in report.rows), report.rows
