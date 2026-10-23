"""Content normalization for fetched pages (T6.1, §5.12).

The proxy stores the ORIGINAL bytes, the NORMALIZED text and both
hashes (the full transform chain + provenance journal is T6.3). v1
normalization is deliberately conservative and host-side:

- text/html → visible text (tags, script/style, comments stripped);
- text/plain, text/markdown, application/json → utf-8 decode
  (latin-1 fallback);
- anything else → no normalized text (the original is still stored).

The result is ALWAYS untrusted external content: normalization removes
markup, not meaning, and the text keeps the untrusted-content marking
in every place it is later placed into a model context (§11.2 data
boundaries, T6.3).

T7.112 (а1): declared character encoding for text/html and text/plain.
The closed order (no statistical guessing, no heuristics over text):
Content-Type `charset` parameter → byte-order mark → HTML `<meta>`
charset declaration in the first bytes of the body → UTF-8 with
errors="replace". The encoding actually used is recorded in the
transform chain (`decode:<name>`); an unknown declared name decodes as
UTF-8 and is marked (`decode:unknown:<name>`). Pages that end up plain
UTF-8 keep their exact v1 chains, text and hashes.

T7.133: PDF becomes text through pypdf (BSD-3-Clause, pure Python — the
runtime dependency added by user decision 2026-10-23). The closed PDF
recognition: the body signature `%PDF-` at the first byte (wins over the
declared type — sites serve PDF under non-.pdf addresses and HTML under
.pdf ones) or the declared `application/pdf`. Page texts come out in
order behind a page separator, under closed limits (see PDF_MAX_*);
every pypdf failure is an honest normalization refusal (text None with a
reason) and never a fetch crash. Whenever the normalized text is missing
or empty the reason rides in ``NormalizedContent.refusal`` (closed set,
module constants) so the research.fetch observation says the truth
(unsupported format / PDF without a text layer / bot-challenge stub /
empty page) instead of the false "normalized artifact unreadable".

T7.133a (замечание приёмки T7.133): разбор PDF идёт постранично и проверяет бюджет
времени PDF_MAX_SECONDS между страницами; при исчерпании текст усекается с меткой
`pdf-truncated:time` в цепочке (та же форма, что pages/chars); исключения — прежний
честный отказ. Вызов нормализации (и хэширование/запись тела в хранилище) уведён из
событийного цикла в поток (`asyncio.to_thread` в `service.py`) — результат, цепочки
и хэши байт в байт прежние.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final

from pypdf import PdfReader

#: transform chain recorded on the artifact chunks (T6.3)
PARSER_FINGERPRINT = "noezema-normalize-v1"

# ─── T7.112 (а1): закрытый порядок объявления кодировки ────────────────────────────────
#
# Стендовый дефект (сессия 14095b8d, страница finmarket.ru/news/6727171): тело в windows-1251,
# в HTTP-заголовке charset нет, объявление только в `<meta http-equiv="Content-Type"
# content="text/html; charset=windows-1251">`. Прежняя нормализация декодировала всё как UTF-8
# с errors="replace": кириллица превращалась в U+FFFD, и детекторы атрибуции не видели ни
# алиасов, ни шаблонов на живой странице пересказа.
#
# Порядок ЗАКРЫТЫЙ и декларативный: (1) параметр charset из Content-Type; (2) BOM;
# (3) для HTML — `<meta charset=…>` или `<meta http-equiv="Content-Type" content="…charset=…">`
# в первых META_CHARSET_WINDOW_BYTES байтов тела; (4) иначе UTF-8 errors="replace".
# Статистического угадывания нет: кодировка не выбирается по содержимому текста.
# Неизвестное объявленное имя → UTF-8 + пометка в цепочке (ложная подмена кодировки дороже
# честной отметки: ADR-0029 «Риски»).

#: сколько начальных байтов HTML-тела просматривается ради объявления charset
META_CHARSET_WINDOW_BYTES: Final = 4096

#: параметр charset из самого Content-Type (например `text/html; charset=windows-1251`)
_CHARSET_PARAM_RE: Final = re.compile(r"charset\s*=\s*\"?([A-Za-z0-9_.:-]+)", re.IGNORECASE)
#: объявление в теле: `charset=` после `<meta …` (оба наблюдаемых синтаксиса — и
#: `<meta charset="…">`, и http-equiv-вариант); поиск по БАЙТАМ, имя ограничено безопасным
#: алфавитом имён кодировок
_META_CHARSET_RE: Final = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE
)
#: BOM проверяются после параметра заголовка (тот же закрытый порядок); UTF-16 LE/BE —
#: одна кодировка `utf-16` (codec сам разбирает порядок байт по BOM)
_BOM_ENCODINGS: Final[tuple[tuple[bytes, str], ...]] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _is_html(ctype: str) -> bool:
    return ctype == "text/html" or ctype == "application/xhtml+xml"


def _is_plain(ctype: str) -> bool:
    return ctype in ("text/plain", "text/markdown", "text/x-markdown")


def detect_declared_encoding(content_type: str | None, data: bytes) -> str | None:
    """T7.112 (а1): ОБЪЯВЛЕННАЯ кодировка тела по закрытому порядку, либо None (голый UTF-8).

    Имя возвращается ровно таким, каким объявлено (без канонизации aliases — так оно и
    записывается в цепочку преобразований). Функция ничего не угадывает: имя есть в
    заголовке, в BOM или в meta-объявлении — иначе None. Нужна обоим местам: нормализации
    при чтении и переатрибуции (T7.112 а2), которая перечитывает испорченный текст из
    оригинала только когда оригинал объявлен не-UTF-8."""
    header = _CHARSET_PARAM_RE.search(content_type or "")
    if header:
        return header.group(1).lower()
    for bom, encoding in _BOM_ENCODINGS:
        if data.startswith(bom):
            return encoding
    if _is_html((content_type or "").split(";")[0].strip().lower()):
        meta = _META_CHARSET_RE.search(data[:META_CHARSET_WINDOW_BYTES])
        if meta:
            return meta.group(1).decode("ascii", errors="replace").lower()
    return None


def _decode_body(content_type: str | None, data: bytes) -> tuple[str, str]:
    """Decode the body per the closed order and return (text, transform-chain label).

    Пустая метка — декодирование обычным UTF-8 replace: цепочка остаётся ровно v1 (байт в
    байт прежние текст и хеш UTF-8 страниц). Не-UTF-8 или неизвестное имя получают метку
    `decode:<имя>` / `decode:unknown:<имя>` — фактически применённая кодировка записана."""
    declared = detect_declared_encoding(content_type, data)
    if declared is None or declared == "utf-8":
        return data.decode("utf-8", errors="replace"), ""
    try:
        codecs.lookup(declared)
    except LookupError:
        return data.decode("utf-8", errors="replace"), f"decode:unknown:{declared}"
    return data.decode(declared, errors="replace"), f"decode:{declared}"


# ─── T7.133: PDF-ветка (pypdf) и закрытые причины отказа нормализации ────────────────
#
# Стендовый разбор 16–22.10 (фикстуры задачи): 36 из 86 неудачных research.fetch —
# PDF (Росстат, cbr.ru, stats.hh.ru, superjob): нормализатор PDF не поддерживал,
# `normalized_sha256` = null, а оркестратор делал `str(None)` = "None", лез в
# хранилище по ключу "None" и врал про «normalized artifact unreadable». 17 неудач —
# заглушки защиты от ботов с пустым текстом (frankmedia, banki.ru, rusbonds,
# bcs-express): оркестратор не различал их с настоящей пустой страницей.

#: подпись PDF: pypdf ждёт её ровно с нулевого байта (`_basic_validation`) — тот же
#: закрытый признак здесь; объявленный тип заголовки сайтов врут в обе стороны
_PDF_MAGIC: Final = b"%PDF-"

#: предельное число страниц, из которых читается текст: максимальный реальный
#: документ стенда — 290 строк (rosstat osn-06-2026, замер 14,7 с извлечения)
PDF_MAX_PAGES: Final = 400
#: предельный объём извлечённого текста: максимальный реальный документ —
#: 628 206 знаков; в контекст модели текст режется и так бюджетом RESEARCH_CONTEXT_BUDGET
PDF_MAX_TEXT_CHARS: Final = 1_000_000
#: T7.133a: бюджет времени разбора PDF (секунды). Замер osn-06-2026 (290 строк,
#: 3,4 МиБ — крупнейший реальный документ стенда): 15,4 с извлечения (замер
#: менеджера) / 15,0 с повторный замер, т.е. ~53 мс/страницу, 400 страниц
#: (PDF_MAX_PAGES) — ~21 с. 20 с покрывают максимальный реальный документ
#: с запасом ~30 % и держат один разбор внутри аренды сессии
#: (DEFAULT_LEASE_TTL = 30 с: во время исполнения инструмента аренда не
#: продлевается, продление — только вокруг вызовов модели и на шаге цикла).
#: С config-v30 (16 МиБ) PDF могут быть в 4-5 раз больше — без бюджета разбор
#: ушёл бы на минуту и дольше; такие документы теперь усекаются по сроку.
PDF_MAX_SECONDS: Final = 20
#: разделитель страниц в тексте (стоит между страницами, начиная со второй)
PDF_PAGE_SEPARATOR: Final = "\n\n<<<PAGE {page}>>>\n\n"
#: пометка усечения в конце текста (дублируется меткой в цепочке преобразований)
PDF_TRUNCATION_MARKER: Final = "\n\n[... нормализация PDF остановлена: достигнут лимит ...]"
#
# Распаковочные бомбы и раздутые структуры держат СОБСТВЕННЫЕ лимиты pypdf 6.x
# (`pypdf._configuration.Configuration`): декларируемая длина потока и декомпрессия
# ≤ 75 МиБ на поток, накопленная работа декодирования ≤ 300 МиБ на поток, элементы
# массивов/словарей ≤ 100 000, дерево страниц ≤ 100 000 записей на глубину ≤ 100,
# бюджет разбора потока содержимого 2 000 000 токенов, формов /XObject ≤ 5 000 на
# страницу. Размер тела сверху ограничен fetch-политикой (max_response_bytes).

#: отказ: типа контента нормализатор не касается (прежнее поведение, текст не было)
REFUSAL_UNSUPPORTED_FORMAT: Final = "unsupported_format"
#: отказ: pypdf не смог разобрать тело (битый, защищённый паролем, не PDF)
REFUSAL_PDF_UNREADABLE: Final = "pdf_unreadable"
#: отказ: PDF разобран, но извлекаемого текста нет ни на одной странице (скан)
REFUSAL_PDF_NO_TEXT_LAYER: Final = "pdf_no_text_layer"
#: пустой текст: страница — заглушка защиты от ботов (закрытые признаки ниже)
REFUSAL_BOT_CHALLENGE_STUB: Final = "bot_challenge_stub"
#: пустой текст: закрытых признаков заглушки нет — настоящая пустая страница
REFUSAL_EMPTY_PAGE: Final = "empty_page"

#: ЗАКРЫТЫЕ признаки заглушки защиты от ботов (стендовые фикстуры T7.133): проверяются
#: ТОЛЬКО когда видимого текста нет вовсе; эвристик «по тексту» нет. servicepipe отдаёт
#: одинаковую страницу frankmedia/bcs-express/rusbonds, banki.ru — свой JS-загрузчик.
_BOT_CHALLENGE_MARKERS: Final = (
    # редирект посетителя без JavaScript: `<noscript><meta http-equiv="refresh" …>`
    re.compile(r"<noscript[^>]*>\s*<meta[^>]+http-equiv\s*=\s*[\"']?refresh", re.IGNORECASE),
    # известный загрузчик проверки servicepipe
    re.compile(r"servicepipe\.tech/loaders/", re.IGNORECASE),
    # banki.ru: JS-вызов с cookie `__jhash_=`
    re.compile(r"__jhash_="),
)


@dataclass(frozen=True)
class NormalizedContent:
    text: str | None
    transform_chain: tuple[str, ...]
    text_sha256: str | None
    #: T7.133: честная причина пустого/отсутствующего текста (закрытый набор
    #: REFUSAL_* выше); None — текст есть и он непустой
    refusal: str | None = None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "template", "noscript"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "template", "noscript") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._pieces.append(data)

    def text(self) -> str:
        return "\n".join(self._pieces)


def _pdf_refusal(reason: str) -> NormalizedContent:
    """Честный отказ PDF-ветви: текста нет, цепочка прежняя («fetch»), fetch не падает."""
    return NormalizedContent(text=None, transform_chain=("fetch",), text_sha256=None, refusal=reason)


def _normalize_pdf(
    data: bytes,
    *,
    budget_seconds: float = PDF_MAX_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> NormalizedContent:
    """Извлекает текст из PDF через pypdf (non-strict) с закрытыми пределами.

    Страницы идут по порядку за разделителем `PDF_PAGE_SEPARATOR`; число страниц и
    усечение (если оно случилось) метятся в цепочке преобразований. Любое исключение
    pypdf — честный отказ (`REFUSAL_PDF_UNREADABLE`), а не падение fetch; PDF без
    единого знака текста (скан) — `REFUSAL_PDF_NO_TEXT_LAYER`.

    T7.133a: срок разбора считается с `clock` (по умолчанию monotonic) и проверяется
    МЕЖДУ страницами — после извлечения каждой страницы, до перехода к следующей.
    При исчерпании текста accumulated до этого момента остаётся, текст помечается
    усечением, в цепочке — `pdf-truncated:time:<бюджет>` (та же форма, что
    `pdf-truncated:pages` / `pdf-truncated:chars`; приоритет меток — pages, chars,
    time: первая случившаяся остановка и есть причина остановки). Бюджет и часы —
    именованные аргументы только для тестов; `normalize_content` вызывает разбор
    с закрытой константой и обычными часами, результат для реальных документов не
    меняется, пока разбор укладывается в срок.
    """
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        # многие документы «зашифрованы» с пустым пользовательским паролем —
        # это не защита, а дефолт упаковщиков; отказ только если не открылся
        if reader.is_encrypted and not reader.decrypt(""):
            return _pdf_refusal(REFUSAL_PDF_UNREADABLE)
        page_count = len(reader.pages)
        start = clock()
        pieces: list[str] = []
        used = 0
        truncated_pages = False
        truncated_chars = False
        truncated_time = False
        for index in range(page_count):
            if index >= PDF_MAX_PAGES:
                truncated_pages = True
                break
            page_text = (reader.pages[index].extract_text() or "").strip()
            # срок проверяется между страницами: текущая страница уже разобрана
            # (её текст честнее выбросить нельзя), к следующей — только в срок
            expired = clock() - start >= budget_seconds
            if not page_text:
                if expired:
                    truncated_time = True
                    break
                continue
            separator = "" if not pieces else PDF_PAGE_SEPARATOR.format(page=index + 1)
            cost = len(separator) + len(page_text)
            if used + cost > PDF_MAX_TEXT_CHARS:
                room = PDF_MAX_TEXT_CHARS - used - len(separator)
                if room > 0:
                    pieces.append(separator + page_text[:room])
                truncated_chars = True
                break
            pieces.append(separator + page_text)
            used += cost
            if expired:
                truncated_time = True
                break
    except Exception:
        # стабильный контракт pypdf по ошибкам шире списка PdfError (ошибки
        # фильтров, декодеров, рекурсии); любая из них — честный отказ
        return _pdf_refusal(REFUSAL_PDF_UNREADABLE)
    if not pieces:
        return _pdf_refusal(REFUSAL_PDF_NO_TEXT_LAYER)
    text = "".join(pieces)
    chain: list[str] = ["fetch", "normalize:pdf-text", f"pdf-pages:{page_count}"]
    if truncated_pages:
        chain.append(f"pdf-truncated:pages:{PDF_MAX_PAGES}")
        text += PDF_TRUNCATION_MARKER
    if truncated_chars:
        chain.append(f"pdf-truncated:chars:{PDF_MAX_TEXT_CHARS}")
        text += PDF_TRUNCATION_MARKER
    if truncated_time:
        chain.append(f"pdf-truncated:time:{budget_seconds:g}")
        text += PDF_TRUNCATION_MARKER
    text = text.strip()
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return NormalizedContent(text=text, transform_chain=tuple(chain), text_sha256=sha)


def _no_text_reason(decoded: str, text: str) -> str | None:
    """Причина ПУСТОГО текста успешной ветви (html/plain): закрытые признаки
    заглушки защиты от ботов — иначе настоящая пустая страница. Непустому
    тексту причины нет."""
    if text:
        return None
    if any(marker.search(decoded) for marker in _BOT_CHALLENGE_MARKERS):
        return REFUSAL_BOT_CHALLENGE_STUB
    return REFUSAL_EMPTY_PAGE


def refusal_observation_message(refusal: str | None, content_type: str | None) -> str:
    """Понятное исследователю сообщение о причине пустого/отсутствующего текста
    (T7.133; читает оркестратор в `research.fetch`). Отсутствующая или неизвестная
    причина (конверты до T7.133, подставные сервисы тестов) — прежняя формулировка."""
    if refusal == REFUSAL_UNSUPPORTED_FORMAT:
        declared = (content_type or "").split(";")[0].strip()
        return (
            f"формат «{declared or 'не объявлен'}» не поддерживается нормализатором: "
            "нормализованного текста нет"
        )
    if refusal == REFUSAL_PDF_UNREADABLE:
        return (
            "не удалось разобрать PDF: документ повреждён, защищён паролем или это "
            "не PDF; нормализованного текста нет"
        )
    if refusal == REFUSAL_PDF_NO_TEXT_LAYER:
        return (
            "PDF без текстового слоя: в документе нет извлекаемого текста "
            "(вероятно, скан); текста для анализа нет"
        )
    if refusal == REFUSAL_BOT_CHALLENGE_STUB:
        return (
            "страница — заглушка защиты от ботов (сайт требует JavaScript); "
            "текста нет — не возвращайся к этому сайту в этой сессии"
        )
    if refusal == REFUSAL_EMPTY_PAGE:
        return "пустая страница: видимого текста на странице нет"
    return "no normalized text available for this content"


def normalize_content(content_type: str | None, data: bytes) -> NormalizedContent:
    """Produce the normalized text (or None) for a fetched body."""
    ctype = (content_type or "").split(";")[0].strip().lower()

    # T7.133: PDF-ветка — ЗАКРЫТЫЙ признак: подпись тела `%PDF-` с нулевого байта
    # (важнее объявленного типа: stats.hh.ru отдаёт PDF по API-адресу без расширения;
    # rusbonds отдаёт HTML-заглушку по адресу .pdf — подпись тут честно скажет, что
    # это не PDF) или объявленный `application/pdf` без подписи (пусть pypdf решит;
    # не разберёт — будет честный отказ с причиной, а не падение fetch).
    if data.startswith(_PDF_MAGIC) or ctype == "application/pdf":
        return _normalize_pdf(data)

    if _is_html(ctype):
        decoded, label = _decode_body(content_type, data)
        parser = _TextExtractor()
        try:
            parser.feed(decoded)
            parser.close()
        except Exception:
            pass
        text = parser.text().strip()
        chain: tuple[str, ...] = ("fetch", "normalize:html-text")
        if label:
            chain = (*chain, label)
        refusal = _no_text_reason(decoded, text)
    elif _is_plain(ctype):
        decoded, label = _decode_body(content_type, data)
        text = decoded.strip()
        chain = ("fetch", "normalize:decode-utf8")
        if label:
            chain = (*chain, label)
        refusal = REFUSAL_EMPTY_PAGE if not text else None
    elif ctype == "application/json":
        try:
            decoded = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            text = data.decode("utf-8", errors="replace").strip()
        else:
            text = json.dumps(decoded, ensure_ascii=False, indent=1)
        chain = ("fetch", "normalize:json-canonical")
        refusal = _no_text_reason(data.decode("utf-8", errors="replace"), text)
    else:
        # no v1 normalizer for this type: original only (T6.3 may add
        # more types; the trust marking applies either way)
        return NormalizedContent(
            text=None,
            transform_chain=("fetch",),
            text_sha256=None,
            refusal=REFUSAL_UNSUPPORTED_FORMAT,
        )

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return NormalizedContent(text=text, transform_chain=chain, text_sha256=sha, refusal=refusal)
