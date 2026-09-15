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
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from html.parser import HTMLParser

#: transform chain recorded on the artifact chunks (T6.3)
PARSER_FINGERPRINT = "noezema-normalize-v1"


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

    if ctype == "text/html" or ctype == "application/xhtml+xml":
        parser = _TextExtractor()
        try:
            parser.feed(data.decode("utf-8", errors="replace"))
            parser.close()
        except Exception:
            pass
        text = parser.text().strip()
        chain = ("fetch", "normalize:html-text")
    elif ctype in ("text/plain", "text/markdown", "text/x-markdown"):
        text = data.decode("utf-8", errors="replace").strip()
        chain = ("fetch", "normalize:decode-utf8")
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
