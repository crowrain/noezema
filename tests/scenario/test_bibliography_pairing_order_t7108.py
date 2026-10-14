"""T7.107 gap (T7.108 часть б, ADR-0029 дополнение): порядок связей не теряет связку.

Дополнение T7.107 закрыло пробел: pairing `bibliography_known_producer` писался только
когда строгая связь той же фразы шла РАНЬШЕ в `evidence_links` (накопитель цикла пополнялся
по ходу). Если блог со списком источников стоит ПЕРВЫМ, а деловое издание со строгой парой —
вторым, блог оставался без записи до переатрибуции окна. Теперь цикл формулировки идёт в два
прохода: первый собирает честные решения строгого режима и режима публикации для ВСЕХ связей
без ключей (из них — ключи «известной производности» утверждения), второй досчитывает третий
режим неизовязанных связей этими ключами плюс якорь перепроверки. Строгий режим и режим
публикации решают первыми и не ослаблены; порядок записей в staging прежний.

Гвард «без цепочек» закреплён отдельно: ключи даёт только pairing строгого режима или режима
публикации (чистый сборщик `apps/orchestrator/attribution_keys.py`, юнит-тест
`test_attribution_key_collector_t7108.py`); решение третьего режима ключей не добавляет. В
двапроходной схеме это наблюдаемо: два блога, оба ссылающиеся «Источники: Росстат», БЕЗ
строгой связи той же фразы остаются без записей (sentinel-тест ниже).

Тесты используют страницы и фиксуру T7.107 (`test_bibliography_session_t7107.py`), но с
обратным порядком чтения/связей. Идемпотентность пути переатрибуции к этим записям уже
закреплена там же (`research-reattribute --evidence-level --dry-run` → «изменений нет»).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from apps.research_proxy.fetch import FetchResult
from tests.conftest import FakeLLM
from tests.scenario import test_two_sided_search_session as two
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question

pytestmark = pytest.mark.scenario

CURRENT_VALUE_METHOD = "host-value-attribution-v12"

PLAQUE = (
    "Данная публикация является личным мнением автора. "
    "Мнение владельца сайта может не совпадать с мнением автора."
)

URL_INTERFAX = "https://interfax.example/business/1119461"
URL_SMARTLAB = "https://smart-lab.example/blog/1349924"
URL_BLOG2 = "https://invest-blog.example/post/77"

PAGES: dict[str, list[str]] = {
    URL_INTERFAX: [
        "Уровень безработицы в России опустился до 2,2%, следует из данных Росстата.",
        "Это минимальное значение за последние годы.",
    ],
    URL_SMARTLAB: [
        "Блог инвестора. О безработице.",
        "Раньше писал про уровень выше, сейчас он опустился до 2,2%.",
        "Настроения на рынке труда медленно улучшаются третий квартал подряд.",
        PLAQUE,
        "Источники: Росстат и данные портала HeadHunter.",
    ],
    URL_BLOG2: [
        "Зарплаты и цены. Квартальные заметки.",
        "По рынку труда: безработица опустилась до 2,2%.",
        "Реальные доходы восстанавливаются неровно.",
        "Источники: Росстат и данные портала HeadHunter.",
    ],
}

QUERY = "уровень безработицы в России сейчас"
QUESTION = "Какой сейчас уровень безработицы в России?"
STATEMENT = "Уровень безработицы в России опустился до 2,2%."

#: дословные цитаты — якоря T7.82-переселекции окон (подстроки фикстурных страниц)
RATIONALE = (
    'Прочитал три текста: "Уровень безработицы в России опустился до 2,2%, следует из данных '
    'Росстата." и "Раньше писал про уровень выше, сейчас он опустился до 2,2%." и '
    '"По рынку труда: безработица опустилась до 2,2%." Числа совпадают, готово.'
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
                {"url": URL_BLOG2, "title": "Заметки: безработица", "content": "2,2%"},
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


async def _groups(scratch_url: str) -> int:
    row = await _scalar(
        scratch_url,
        "SELECT count(DISTINCT m.group_id) FROM source_independence_members m "
        "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id AND h.assessment_state = 'current' "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = " + _quoted(STATEMENT),
    )
    return int(row[0])


@pytest.mark.asyncio
async def test_bibliography_pairing_survives_when_strict_link_comes_last(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_llm: FakeLLM, fake_engine: None
) -> None:
    """Блог со списком источников прочитан ПЕРВЫМ, строгая связь — последней связкой в
    `evidence_links`: прежний накопитель цикла терял pairing; два прохода сохраняют его."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, two._payload())).state == "active"
    question_id = await _seed_question(scratch_url, QUESTION)

    fake_llm.script(
        [
            two._search(QUERY),
            two._fetch(URL_SMARTLAB),
            two._fetch(URL_INTERFAX),
            _complete(),
            two._curator(STATEMENT, 2),
        ]
    )
    session = await two._run_session(
        scratch_url, fake_llm, tmp_path / "ws", tmp_path / "data" / "artifacts", question_id
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    # связь блока — первая в списке связей, но её третий режим досчитан ключом
    # строгой связи, идущей позже (gap T7.107 закрыт)
    blog = await _record_of(scratch_url, URL_SMARTLAB)
    assert blog is not None, "запись третьего режима не появилась: pairing потерян из-за порядка связей"
    assert blog["pairing"] == "bibliography_known_producer", blog
    assert blog["method"] == CURRENT_VALUE_METHOD, blog
    assert blog["primary_key"] == "rosstat", blog
    assert "Источники: Росстат" in blog["basis_fragment"], blog

    strict = await _record_of(scratch_url, URL_INTERFAX)
    assert strict is not None and strict["pairing"] == "value_and_primary_in_fragment", strict

    assert str(blog["parent_source_id"]) == str(strict["parent_source_id"]), "якорь должен быть общий"
    assert await _groups(scratch_url) == 1
    assert fake_llm.state()["queue_left"] == 0


@pytest.mark.asyncio
async def test_two_restatements_pair_from_a_later_strict_link(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_llm: FakeLLM, fake_engine: None
) -> None:
    """Два пересказа с одинаковым списком источников и строгая связь — ТРЕТЬЕЙ по порядку:
    оба пересказа получают третий режим от ключей первого прохода (иначе — молчание до
    переатрибуции окна)."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, two._payload())).state == "active"
    question_id = await _seed_question(scratch_url, QUESTION)

    fake_llm.script(
        [
            two._search(QUERY),
            two._fetch(URL_SMARTLAB),
            two._fetch(URL_BLOG2),
            two._fetch(URL_INTERFAX),
            _complete(),
            two._curator(STATEMENT, 3),
        ]
    )
    session = await two._run_session(
        scratch_url, fake_llm, tmp_path / "ws", tmp_path / "data" / "artifacts", question_id
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    for url in (URL_SMARTLAB, URL_BLOG2):
        record = await _record_of(scratch_url, url)
        assert record is not None, f"пересказ {url} остался без связки"
        assert record["pairing"] == "bibliography_known_producer", record
    strict = await _record_of(scratch_url, URL_INTERFAX)
    assert strict is not None and strict["pairing"] == "value_and_primary_in_fragment", strict
    assert await _groups(scratch_url) == 1
    assert fake_llm.state()["queue_left"] == 0


@pytest.mark.asyncio
async def test_bibliography_pairing_never_chains_from_bibliography(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_llm: FakeLLM, fake_engine: None
) -> None:
    """Sentinel без цепочек: два блога с одинаковым списком источников, строгой связи той же
    фразы НЕТ — ни один блог не получает запись (ни в первом проходе, ни вторым: ключей нет).
    Утверждение при этом живёт с двумя уликами без атрибуции."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, two._payload())).state == "active"
    question_id = await _seed_question(scratch_url, QUESTION)

    fake_llm.script(
        [
            two._search(QUERY),
            two._fetch(URL_SMARTLAB),
            two._fetch(URL_BLOG2),
            {
                "content": {
                    "public_rationale": (
                        'Прочитал два блока: "Раньше писал про уровень выше, сейчас он '
                        'опустился до 2,2%." и "По рынку труда: безработица опустилась до 2,2%." '
                        "Первоисточника среди прочитанного нет."
                    ),
                    "decision": {"kind": "complete", "reason": "goal_reached"},
                }
            },
            two._curator(STATEMENT, 2),
        ]
    )
    session = await two._run_session(
        scratch_url, fake_llm, tmp_path / "ws", tmp_path / "data" / "artifacts", question_id
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    assert await _record_of(scratch_url, URL_SMARTLAB) is None
    assert await _record_of(scratch_url, URL_BLOG2) is None
    rows = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM evidence e JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = " + _quoted(STATEMENT),
    )
    assert int(dict(rows[0])["n"]) == 2
    # без общей производности группы независимости не сворачиваются: два разных домена
    assert await _groups(scratch_url) == 2
    assert fake_llm.state()["queue_left"] == 0
