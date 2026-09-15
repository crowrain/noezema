"""Unit: content normalization of the research proxy (T6.1, §5.12).

The normalizer is host-side and conservative: it turns a fetched body
into plain text (or None) for the artifact journal. It removes markup,
not meaning — the text stays untrusted external content (the trust
marking lives on the artifacts and the context fences, T6.3).
"""

from __future__ import annotations

import hashlib
import json

from apps.research_proxy.normalization import (
    PARSER_FINGERPRINT,
    normalize_content,
)


def test_html_becomes_visible_text() -> None:
    page = b"""<html><head><title>t</title>
    <script>var x = 'secret-script';</script>
    <style>.a{color:red}</style></head>
    <body><h1>Title</h1><p>First <b>bold</b> word.</p>
    <!-- comment: hidden -->
    <noscript>nojs</noscript></body></html>"""
    result = normalize_content("text/html; charset=utf-8", page)
    assert result.text is not None
    text = result.text
    assert "Title" in text
    assert "First" in text and "bold" in text
    # script/style/comment/noscript content is not visible text
    assert "secret-script" not in text
    assert "color:red" not in text
    assert "hidden" not in text
    assert "nojs" not in text
    assert result.text_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert "normalize:html-text" in result.transform_chain


def test_html_broken_page_is_still_text() -> None:
    result = normalize_content("text/html", b"<div>ok")
    assert result.text is not None and "ok" in result.text
    assert result.transform_chain[0] == "fetch"


def test_plain_text_decoded_utf8() -> None:
    result = normalize_content("text/plain", "привет мир".encode())
    assert result.text == "привет мир"
    assert "normalize:decode-utf8" in result.transform_chain


def test_json_canonicalized() -> None:
    raw = b'{"b": 1, "a": "x"}'
    result = normalize_content("application/json", raw)
    assert result.text is not None
    assert json.loads(result.text) == {"b": 1, "a": "x"}
    assert "normalize:json-canonical" in result.transform_chain


def test_binary_type_has_no_normalization() -> None:
    result = normalize_content("application/pdf", b"%PDF-1.4 whatever")
    assert result.text is None
    assert result.text_sha256 is None
    assert result.transform_chain == ("fetch",)


def test_unknown_type_has_no_normalization() -> None:
    result = normalize_content(None, b"???")
    assert result.text is None
    assert PARSER_FINGERPRINT == "noezema-normalize-v1"
