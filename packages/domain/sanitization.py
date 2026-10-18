"""NUL-byte sanitization for host text that reaches JSONB (T7.46a).

Postgres cannot store U+0000 in JSONB: asyncpg encodes a NUL inside a
text parameter as the JSON escape ``\\u0000``, and the server-side JSONB
parser rejects it (``UntranslatableCharacterError: unsupported Unicode
escape sequence``). One NUL byte in a session's report payload was
enough to roll back the ENTIRE phase-1 transaction — every step of the
session lost (SMOKE-V12-K2 §3: 8 steps of work).

Policy (T7.46a): a NUL never disappears silently. It is replaced by the
visible 4-character ASCII marker ``\\x00`` (backslash, "x", "0", "0"),
so the model and the audit trail both see that a NUL byte was there.

U+FFFD is deliberately NOT the marker: ``decode("utf-8", "replace")``
already produces U+FFFD for INVALID byte sequences, and the audit must
be able to tell «a NUL byte was present» from «invalid UTF-8 was
present». The marker is pure ASCII (safe in any serializer) and is
NUL-free, so masking is idempotent: applying it to an already-masked
value changes nothing.

Layers (SMOKE-V12-K2 §3.4): the SOURCE is the output capture (executor /
sandbox runtime) — one masking point per capture guarantees the
invariant «host observations carry no NUL»; the EVIDENCE boundary
(``apps.orchestrator.evidence``) and the AUDIT boundary
(``AuditService.record``) are defensive lines for any future NUL
source, and only the audit line can exclude AMPLIFICATION (one NUL
anywhere in the report → death of the whole session transaction).

Known limitation (accepted): a tool that literally prints the 4 ASCII
characters ``\\x00`` produces text indistinguishable from a masked NUL.
That is inherent to any visible marker (U+FFFD included) — the goal is
visibility, not reversibility.
"""

from __future__ import annotations

from typing import Any

#: The visible marker that replaces a NUL byte in host text (4 ASCII chars).
NUL_MARKER = "\\x00"


def mask_nul(text: str) -> str:
    """Replace every NUL byte in ``text`` with :data:`NUL_MARKER`.

    Idempotent; returns ``text`` unchanged when it carries no NUL.
    """
    if "\x00" not in text:
        return text
    return text.replace("\x00", NUL_MARKER)


def mask_nul_deep(value: Any) -> Any:
    """Recursively mask NUL bytes in every string of a JSON-like value.

    dict keys and values, list items and bare strings are masked;
    numbers, None and booleans pass through unchanged. Idempotent.
    """
    if isinstance(value, str):
        return mask_nul(value)
    if isinstance(value, dict):
        return {
            mask_nul(k) if isinstance(k, str) else k: mask_nul_deep(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_nul_deep(v) for v in value]
    return value
