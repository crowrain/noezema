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
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final

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


@dataclass(frozen=True)
class NormalizedContent:
    text: str | None
    transform_chain: tuple[str, ...]
    text_sha256: str | None


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


def normalize_content(content_type: str | None, data: bytes) -> NormalizedContent:
    """Produce the normalized text (or None) for a fetched body."""
    ctype = (content_type or "").split(";")[0].strip().lower()

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
    elif _is_plain(ctype):
        decoded, label = _decode_body(content_type, data)
        text = decoded.strip()
        chain = ("fetch", "normalize:decode-utf8")
        if label:
            chain = (*chain, label)
    elif ctype == "application/json":
        try:
            decoded = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            text = data.decode("utf-8", errors="replace").strip()
        else:
            text = json.dumps(decoded, ensure_ascii=False, indent=1)
        chain = ("fetch", "normalize:json-canonical")
    else:
        # no v1 normalizer for this type: original only (T6.3 may add
        # more types; the trust marking applies either way)
        return NormalizedContent(text=None, transform_chain=("fetch",), text_sha256=None)

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return NormalizedContent(text=text, transform_chain=chain, text_sha256=sha)
