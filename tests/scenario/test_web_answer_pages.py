"""Scenario: простые страницы этапа 2 (T7.65) — что на них есть и чего на них нет.

Проверяются страницы, которые видит человек без инженерного режима: новая главная
`/` («Вопросы и ответы») и карточка ответа `/answer/<id>`. Инженерная страница
`/engineer` — прежнее содержимое главной, поэтому она проверяется отдельно и на
чистоту текстов не проверяется: ей коды показывать можно.

Главное требование этапа: простая страница не знает ни одного кода состояния. Все
подписи приходят из JSON-API (`apps/web/labels.py`), поэтому словарь не может быть
задублирован в JS — тест это и ловит: любой код или ссылка на спецификацию в тексте
страницы делает тест красным.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.unit_state import publish_unit_state

pytestmark = [pytest.mark.scenario]

ADMIN_TOKEN = "pages-token"

#: Единственный код из закрытых перечислений, который простой странице разрешено
#: произносить: значение команды `wake_now` в теле запроса (не текст на экране).
_ALLOWED_CODES = frozenset({"wake_now"})

_FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("ссылка на раздел спецификации", r"§"),
    ("номер задачи плана", r"\bT\d+\.\d+"),
    ("uuid со дефисами", r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    ("hex-хеш или длинное hex-подобное слово", r"\b[0-9a-fA-F]{8,}\b"),
    ("слово о проверке вывода", r"[Пп]роверен"),
)

_SCRIPT_RE = re.compile(r"<script>(?P<body>.*?)</script>", re.S)
_QUOTED_RE = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


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


def _simple_page_text(html: str) -> str:
    """Всё, что страница может показать человеку: разметка и строковые литералы JS.

    Имена свойств JSON (например `state_label`) в этот текст не входят: это ключи
    ответа API, а не надпись на экране.
    """
    static = _SCRIPT_RE.sub("", html)
    literals = "".join(match.group("body") for match in _SCRIPT_RE.finditer(html))
    quoted = " ".join(_QUOTED_RE.findall(literals))
    return f"{static}\n{quoted}"


def _assert_pure(page_name: str, html: str) -> None:
    text = _simple_page_text(html)
    for title, pattern in _FORBIDDEN:
        found = re.findall(pattern, text)
        assert not found, f"{page_name}: {title} в тексте страницы: {found[:5]}"
    codes = set(re.findall(r"[a-z]+_[a-z]+", text)) - _ALLOWED_CODES
    assert not codes, f"{page_name}: код перечисления в простой странице: {sorted(codes)}"
    # простой режим не тянет сторонний код и не прячет второй <script>
    assert "<script src=" not in html and html.count("<script>") == 1
    assert "confirm(" not in html and "prompt(" not in html


# ─── главная страница простого режима ─────────────────────────────────────


@pytest.mark.asyncio
async def test_home_page_is_the_question_and_answer_view(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        r = await client.get("/")
        assert r.status_code == 200
        html = r.text
    await engine.dispose()

    for marker in (
        "<title>NOEZEMA — вопросы и ответы</title>",
        "Задать вопрос",
        "Пароль оператора",
        "Насколько срочно",
        "Обычный",
        "Срочно (вперёд очереди)",
        "Потом",
        "Отправить",
        "дополнительно",
        "Запустить обработку",
        "Возобновить",
        "Пауза",
        "Один запуск обрабатывает один вопрос из очереди",
        "Мои вопросы",
        "Открыть ответ",
        "href=\"/knowledge\"",
        "href=\"/diagnostics\"",
        "href=\"/engineer\"",
    ):
        assert marker in html, marker
    _assert_pure("/", html)


@pytest.mark.asyncio
async def test_engineer_page_keeps_everything_the_old_main_page_had(migrated_db, tmp_path: Path) -> None:
    """Прежняя главная страница переехала на `/engineer` ничего не потеряв."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        r = await client.get("/engineer")
        assert r.status_code == 200
        html = r.text
    await engine.dispose()

    for marker in (
        "<title>NOEZEMA — узел</title>",
        "Очередь вопросов",
        'id="ask-form"',
        'id="ask-text"',
        'id="ask-priority"',
        'id="ask-token"',
        'id="queue"',
        'id="wake-now"',
        "wake now",
        "/api/v1/questions",
        "/api/v1/commands",
        "X-Admin-Token",
        "sessionStorage",
        # инженерные ссылки, которых в простом режиме нет
        'href="/metrics"',
        'href="/evaluation"',
        "Метрики (§16)",
        "Evaluation (§22.2)",
        # выход обратно в простой режим
        "← простой режим",
    ):
        assert marker in html, marker
    # инженерной странице коды показывать разрешено: проверка на чистоте сюда не входит
    assert "§" in html


# ─── карточка ответа ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_answer_page_is_a_viewer_of_the_answer_card(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid = uuid.uuid4()
    async with _client(app) as client:
        r = await client.get(f"/answer/{qid}")
        assert r.status_code == 200
        html = r.text
    await engine.dispose()

    for marker in (
        "<title>NOEZEMA — ответ на вопрос</title>",
        "Ответ",
        "Как это получено",
        "Честно об этом ответе",
        "Что можно сделать дальше",
        "подробно (для инженера)",
        "Задать уточняющий вопрос",
        "Показать все знания",
        'href="/knowledge"',
        'href="/engineer"',
        "/api/v1/questions/",  # страница смотрит тот же JSON-API
        "setTimeout(load, 2500)",  # автообновление, пока работа идёт
    ):
        assert marker in html, marker

    # id вопроса — единственное, что страница подставляет из адресной строки
    assert f"const QUESTION_ID='{qid}';" in html
    assert html.count(str(qid)) == 1


@pytest.mark.asyncio
async def test_answer_page_of_an_unknown_question_says_so_honestly(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        r = await client.get(f"/answer/{uuid.uuid4()}")
        assert r.status_code == 200
        html = r.text
    await engine.dispose()

    assert "такого вопроса нет" in html
    assert "не удалось прочитать ответ" in html


# ─── чистота текстов простого режима ──────────────────────────────────────


@pytest.mark.asyncio
async def test_simple_pages_do_not_repeat_the_dictionary_or_show_codes(migrated_db, tmp_path: Path) -> None:
    """Ловушка этапа: простая страница не имеет права знать коды состояний."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    qid = uuid.uuid4()
    async with _client(app) as client:
        home = (await client.get("/")).text
        answer = (await client.get(f"/answer/{qid}")).text
    await engine.dispose()

    # id вопроса — единственная подстановка сервера; сверяем текст без него
    assert answer.count(str(qid)) == 1
    _assert_pure("/", home)
    _assert_pure("/answer/<id>", answer.replace(str(qid), "id"))


def test_purity_check_is_red_when_a_code_or_a_spec_mark_leaks() -> None:
    """Краснота предыдущей проверки: синтетическая страница с кодом обязана быть отклонена."""
    leaks = (
        "document.getElementById('x').textContent=s.node_state;",
        "<p>см. §5.2.1</p>",
        "<p>задача T7.65</p>",
        "<p>3f2a9c4b-1d5e-4a77-8b0c-9922ee10ab34</p>",
        "<p>session_running</p>",
        "<p>Проверено: вывод подтверждён</p>",
    )
    for leak in leaks:
        with pytest.raises(AssertionError):
            _assert_pure("синтетическая страница", f"<html><body>{leak}</body></html>")


@pytest.mark.asyncio
async def test_answer_page_never_invents_a_verification_word_of_its_own(migrated_db, tmp_path: Path) -> None:
    """Слово о проверке приходит только из API: на странице его нет даже в JS."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        html = (await client.get(f"/answer/{uuid.uuid4()}")).text
        home = (await client.get("/")).text
    await engine.dispose()

    lowered = html.lower() + home.lower()
    assert "проверен" not in lowered
    assert "надежно" not in lowered and "надёжен" not in lowered


# ─── T7.67a: колонка №, порядок «последние сверху» и номер в заголовке ─────


@pytest.mark.asyncio
async def test_home_page_numbers_questions_and_asks_server_for_recent_order(migrated_db, tmp_path: Path) -> None:
    """Колонки таблицы: № · Вопрос · Ответ · Когда; последние вопросы — сверху."""
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        home = (await client.get("/")).text
    await engine.dispose()

    assert "<th>№</th>" in home
    for marker in ("<th>Вопрос</th>", "<th>Ответ</th>", "<th>Когда</th>", "Мои вопросы", "Открыть ответ"):
        assert marker in home
    # порядок просит у сервера и не переиначивает его: сортировки в JS главной нет
    assert "order=recent" in home
    script = "".join(match.group("body") for match in _SCRIPT_RE.finditer(home))
    assert ".sort(" not in script
    # колонка «Ответ» собираются из данных API: номер, итог и бейдж надёжности
    assert "q.number" in script and "q.answer" in script and "reliability" in script
    assert "'в очереди: '" in script  # человеческая фраза позиции очереди — не код состояния
    _assert_pure("/", home)


@pytest.mark.asyncio
async def test_answer_page_header_shows_question_number(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    qid = uuid.uuid4()
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        answer = (await client.get(f"/answer/{qid}")).text
    await engine.dispose()

    assert 'id="q-number"' in answer
    script = "".join(match.group("body") for match in _SCRIPT_RE.finditer(answer))
    assert "q.number" in script  # «Вопрос №N» собирается из данных API, N не выдумывается страницей
    assert "'Вопрос №'" in script
    _assert_pure("/answer/<id>", answer.replace(str(qid), "id"))
