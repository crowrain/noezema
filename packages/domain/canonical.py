"""Canonical JSON serialization and hashing (T1.3).

One canonicalization for the whole system: config payloads, activation
seals (RFC 8785-style JCS arrays, §8.7.1), evidence identity hashes.
Rules: keys sorted, compact separators, UTF-8, non-ASCII preserved.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic JSON bytes for a JSON-serializable value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_hex(canonical_json_bytes(value))
