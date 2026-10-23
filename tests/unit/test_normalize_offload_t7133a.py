"""T7.133a (часть 4в): увод работы из цикла НЕ меняет результат — регрессия хэшей.

Первый тест фиксирует золотые значения нормализации реальных PDF-фикстур,
HTML/plain/JSON-тел (сняты с кода `ae930dd`): нормализация обязана остаться
байт в байт прежней. Второй прогоняет те же тела через новый увод в поток
(`ResearchProxyService._persist_and_normalize`) и требует ровно тех же хэшей,
цепочек, отказов и содержимого хранилища. Красный на `ae930dd`: метода увода там нет.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final, cast

import pytest

from apps.research_proxy.normalization import normalize_content
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7133"

#: (content_type, text_sha256, цепочка, refusal) — золотые значения кода ae930dd
GOLDEN: Final[dict[str, tuple[str | None, str | None, tuple[str, ...], str | None]]] = {
    "CPD-2026-9.pdf": (
        "application/pdf",
        "2465ad06383def03c2b1f4d3536a2c7a8f6244e3757c6990275c00bf1029b3a9",
        ("fetch", "normalize:pdf-text", "pdf-pages:37"),
        None,
    ),
    "employment-0926.pdf": (
        "application/pdf",
        "d2aa364cebec3bb96ec050d9c5c4c3a36731ed6de4f525bd78d73d5bbdba9109",
        ("fetch", "normalize:pdf-text", "pdf-pages:4"),
        None,
    ),
    "hh-monthly-report.pdf": (
        "application/pdf",
        "674167efbbd26e005c0f419cb4ba5608f93226259c83dcab51f9ba25275358b3",
        ("fetch", "normalize:pdf-text", "pdf-pages:24"),
        None,
    ),
    "frankmedia-stub.html": (
        "text/html; charset=UTF-8",
        hashlib.sha256(b"").hexdigest(),
        ("fetch", "normalize:html-text"),
        "bot_challenge_stub",
    ),
}

#: inline-тела: прежние ветки html/plain/json обязаны пережить увод побайтово
INLINE: Final[dict[str, tuple[str | None, bytes]]] = {
    "html-utf8": (
        "text/html; charset=utf-8",
        "<html><script>x=1</script><body><p>Привет мир</p></body></html>".encode(),
    ),
    "html-cp1251": (
        "text/html",
        '<html><head><meta charset="windows-1251"></head><body><p>Тест</p></body></html>'.encode("windows-1251"),
    ),
    "plain": ("text/plain", "  обычный текст  ".encode()),
    "plain-bom": ("text/plain", "текст с bom".encode("utf-8-sig")),
    "json": ("application/json", json.dumps({"ключ": "значение", "n": [1, 2]}, ensure_ascii=False).encode()),
    "json-broken": ("application/json", b"{not json"),
    "unknown": ("application/vnd.ms-excel", b"binary"),
}

INLINE_SHA_GOLDEN: Final[dict[str, str | None]] = {
    "html-utf8": "830d1964dc8673182a40f9adebf598960d37fbe200405b249774ecfa5b465748",
    "html-cp1251": "eae4c06a5b3632e8a99f9cad1639f022b61cf6ca438da963bba898799d27a398",
    "plain": "28c4f3255d973215f52b1eb5530514e9ac93a087dcb3b0507bec36445d7abb97",
    "plain-bom": "b688a69106a83fca197d738dc3dcb269653ff8c668947f426b3e9a464b3e1f56",
    "json": "ad8747004d8e387754fc643494bd4ae9d89e8c24481cf3f264c7814752870ce3",
    "json-broken": "92072df399cb74703f8e86f450d552bc0bb01eeeb98a90985a1b7772c8fd0016",
    "unknown": None,
}


def _bodies() -> dict[str, tuple[str | None, bytes]]:
    bodies = {
        name: (ctype, (FIXTURES / name).read_bytes())
        for name, (ctype, _, _, _) in GOLDEN.items()
    }
    bodies.update(INLINE)
    return bodies


def test_normalize_golden_hashes_unchanged() -> None:
    """Золотые хэши и цепочки нормализации (сняты с ae930dd) не сдвинулись."""
    for name, body in _bodies().items():
        ctype, data = body
        result = normalize_content(ctype, data)
        expected_sha = GOLDEN[name][1] if name in GOLDEN else INLINE_SHA_GOLDEN[name]
        assert result.text_sha256 == expected_sha, name
        if name in GOLDEN:
            _, _, chain, refusal = GOLDEN[name]
            assert result.transform_chain == chain, name
            assert result.refusal == refusal, name


async def test_thread_offload_returns_byte_identical_results(tmp_path: Path) -> None:
    """Поточный путь даёт ровно тот же NormalizedContent, те же хэши артефактов и
    то же содержимое хранилища, что прежняя последовательность синхронных вызовов."""
    service = ResearchProxyService(cast(Any, None), FilesystemArtifactStore(tmp_path / "art"))
    for name, (ctype, data) in _bodies().items():
        original_sha, normalized, normalized_sha = await service._persist_and_normalize(ctype, data)
        direct = normalize_content(ctype, data)
        assert normalized == direct, name  # текст, цепочка, хеш текста, refusal
        assert original_sha == hashlib.sha256(data).hexdigest(), name
        assert service.store.get(original_sha) == data, name
        assert normalized_sha == direct.text_sha256, name
        if normalized.text is not None:
            assert normalized_sha is not None
            assert service.store.get(normalized_sha) == normalized.text.encode("utf-8"), name
        else:
            assert normalized_sha is None, name
