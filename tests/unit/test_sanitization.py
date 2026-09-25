"""Unit: T7.46a — the NUL-byte sanitizer (packages.domain.sanitization).

Policy under test: a NUL never disappears silently — it is replaced by
the visible ASCII marker ``\\x00`` (distinguishable from U+FFFD, which
``decode("utf-8", "replace")`` already produces for INVALID byte
sequences, so the audit can tell «there was a NUL» from «there was
invalid UTF-8»); the marker is NUL-free, so masking is idempotent.
"""

from __future__ import annotations

import pytest

from packages.domain.sanitization import NUL_MARKER, mask_nul, mask_nul_deep


@pytest.mark.unit
def test_marker_is_visible_ascii_and_nul_free() -> None:
    assert NUL_MARKER == "\\x00"  # the 4-char literal, not a NUL
    assert "\x00" not in NUL_MARKER
    assert all(ord(c) < 128 for c in NUL_MARKER)
    # U+FFFD is deliberately NOT the marker: it is already the
    # decode("utf-8", "replace") product of invalid byte sequences
    assert NUL_MARKER != "\ufffd"


@pytest.mark.unit
def test_mask_nul_replaces_every_nul() -> None:
    assert mask_nul("a\x00b\x00\x00c") == f"a{NUL_MARKER}b{NUL_MARKER}{NUL_MARKER}c"


@pytest.mark.unit
def test_mask_nul_idempotent_and_noop_without_nul() -> None:
    once = mask_nul("a\x00b")
    assert mask_nul(once) == once
    clean = "hello\nworld\twith\\x00-literal-text"
    assert mask_nul(clean) == clean  # the literal 4 chars pass through


@pytest.mark.unit
def test_mask_nul_deep_covers_nested_structures() -> None:
    value = {
        "key\x00": "top\x00",
        "list": ["a\x00b", 1, None, True, {"n": "c\x00d"}],
        "clean": 42,
    }
    out = mask_nul_deep(value)
    assert out == {
        f"key{NUL_MARKER}": f"top{NUL_MARKER}",
        "list": [f"a{NUL_MARKER}b", 1, None, True, {"n": f"c{NUL_MARKER}d"}],
        "clean": 42,
    }
    # idempotent on the result
    assert mask_nul_deep(out) == out
    # scalars pass through untouched
    assert mask_nul_deep("s\x00") == f"s{NUL_MARKER}"
    assert mask_nul_deep(7) == 7
    assert mask_nul_deep(None) is None
