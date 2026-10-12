"""Scenario: защитный тест T7.103 — гомоглифы в видимых строках веба.

Решение пользователя 2026-10-12 («с п.1»): дефект — латинская буква a внутри
русского слова «Фаза» в двух местах apps/web/api.py — исправлен заменой буквы, а
повторение ловит этот тест. Проверяет тем же шаблоном, что и скан AGENTS §7
(запись T7.102):

(а) все строковые подписи словаря apps/web/labels.py (label/hint/action каждой
    записи, включая таблицу человеческих этапов сессии);
(б) видимый текст HTML всех страниц веба: текстовые узлы разметки и строковые
    литералы и шаблоны встроенных JS-скриптов. CSS-классы и стили, адреса,
    идентификаторы и регулярные выражения в проверку не входят: теги и блоки
    стилей вырезаны, из скриптов берутся только quoted-литералы, а литералы с
    обратными слэшами (регулярные выражения и escape-последовательности)
    отфильтрованы — как и в скане AGENTS §7.

Критерий: латинская буква вплотную к кириллической внутри одного слова (или
наоборот). Законные гибриды — явный список-исключение с причиной на каждое
имя; сейчас он пуст: ни словарь подписей, ни видимый текст страниц смешанных
слов не содержат. Молча ослаблять проверку нельзя: любое новое попадание либо
исправляется заменой буквы, либо заносится в список с причиной.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.web import labels
from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.unit_state import publish_unit_state

pytestmark = [pytest.mark.scenario]

ADMIN_TOKEN = "homoglyphs-token"

# Кириллица: основная кириллическая строка и её дополняющий блок — то же
# покрытие букв, что и \p{Cyrillic} в скане AGENTS §7 (для текста репо).
_CYRILLIC = "\u0400-\u04FF\u0500-\u052F"
_HOMOGLYPH_RE = re.compile(rf"\S*(?:[{_CYRILLIC}][A-Za-z]|[A-Za-z][{_CYRILLIC}])\S*")
_CYRILLIC_RE = re.compile(f"[{_CYRILLIC}]")

#: Законные гибриды (смешанное слово обязано остаться) → причина на каждое имя.
#: Пуст: ни словарь подписей, ни страницы смешанных слов не содержат. Каждое
#: новое попадание обязано попасть сюда с явной причиной или быть исправлено.
_ALLOWED_HYBRIDS: dict[str, str] = {}

_SCRIPT_RE = re.compile(r"<script>(?P<body>.*?)</script>", re.S)
_STYLE_RE = re.compile(r"<style>.*?</style>", re.S)
_TAG_RE = re.compile(r"<[^>]*>")
_JS_STRING_RE = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`]*`")

#: Страницы, которые видит оператор (список задачи T7.103).
_PLAIN_PAGES = ("/", "/engineer", "/knowledge", "/diagnostics", "/metrics", "/evaluation")
_ID_PAGE_TEMPLATES = ("/answer/{ident}", "/claim/{ident}", "/session/{ident}")


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make(scratch_url: str, host_lib: Path, unit_state: Path):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})
    app = create_app(
        engine=engine,
        factory=factory,
        host_adapter=HostStatusAdapter(host_lib_base=host_lib, unit_state_path=unit_state),
        admin_token=ADMIN_TOKEN,
    )
    return app, engine


def _homoglyph_hits(text: str) -> list[str]:
    """Смешанные слова в тексте тем же шаблоном, что и скан AGENTS §7.

    Попадания с обратным слэшем — регулярные выражения и escape-последовательности —
    отфильтрованы (тот же фильтр `grep -v '\\'`, что и в скане); законные гибриды
    из `_ALLOWED_HYBRIDS` пропущены.
    """
    hits: list[str] = []
    for hit in _HOMOGLYPH_RE.findall(text):
        if "\\" in hit:
            continue
        if hit in _ALLOWED_HYBRIDS:
            continue
        hits.append(hit)
    return hits


def _visible_page_text(html: str) -> str:
    """Всё, что страница может показать человеку: текстовые узлы разметки
    (теги и блоки стилей вырезаны) плюс строковые литералы и шаблоны JS."""
    static = _TAG_RE.sub(" ", _SCRIPT_RE.sub("", _STYLE_RE.sub("", html)))
    scripts = "".join(match.group("body") for match in _SCRIPT_RE.finditer(html))
    literals = " ".join(_JS_STRING_RE.findall(scripts))
    return f"{static}\n{literals}"


def _label_texts() -> list[tuple[str, str]]:
    """Каждая строковая подпись словаря подписей: (где стоит, сам текст)."""
    out: list[tuple[str, str]] = []
    for category, table in labels.LABELS.items():
        for value, entry in table.items():
            for field in ("label", "hint", "action"):
                out.append((f"labels[{category}][{value}].{field}", entry[field]))
    for state, (_index, name, hint) in labels._STAGE_TABLE.items():
        out.append((f"session_stage[{state}].name", name))
        out.append((f"session_stage[{state}].hint", hint))
    return out


def test_labels_dictionary_has_no_homoglyphs() -> None:
    texts = _label_texts()
    assert texts, "словарь подписей пуст — проверка не может быть осмысленной"
    offenders = [
        f"{where}: {hit!r}"
        for where, text in texts
        for hit in _homoglyph_hits(text)
    ]
    assert not offenders, "гомоглифы в подписях apps/web/labels.py: " + "; ".join(offenders)


def test_labels_dictionary_carries_cyrillic_text() -> None:
    """Непустая проверка: словарь действительно содержит русские подписи."""
    assert any(_CYRILLIC_RE.search(text) for _where, text in _label_texts())


async def test_visible_page_text_has_no_homoglyphs(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit-state.json")
    offenders: list[str] = []
    try:
        paths = list(_PLAIN_PAGES) + [
            template.format(ident=uuid.uuid4()) for template in _ID_PAGE_TEMPLATES
        ]
        async with _client(app) as client:
            for path in paths:
                r = await client.get(path)
                assert r.status_code == 200, f"{path}: {r.status_code}"
                visible = _visible_page_text(r.text)
                if not _CYRILLIC_RE.search(visible):
                    offenders.append(f"{path}: страница не несёт русского текста — проверка пуста")
                offenders.extend(f"{path}: {hit!r}" for hit in _homoglyph_hits(visible))
    finally:
        await engine.dispose()
    assert not offenders, "гомоглифы в видимом тексте страниц веба: " + "; ".join(offenders)
