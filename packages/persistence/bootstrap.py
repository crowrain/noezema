"""Application-side copy of the immutable operational bootstrap constants."""

from __future__ import annotations

import hashlib
import json
from typing import Final
from uuid import UUID

BOOTSTRAP_CONFIG_SNAPSHOT_ID: Final = UUID("9c791cea-c197-564d-9a86-9a0e0e648cd6")
INVALID_QUESTION_NAMESPACE: Final = UUID("adda0065-869e-58bb-8bc6-f95e6603bc24")

BOOTSTRAP_PAYLOAD_JSON: Final = (
    '{"activation_limits":{},"claim_type_rules":{},"curiosity":{"selector":"fifo"},'
    '"embeddings":null,"model":null,"policy":{"version":"bootstrap/deny-all"},'
    '"prompts":{},"schema_version":"runtime-config/v1","session_limits":{},'
    '"token_budgets":{}}'
)
BOOTSTRAP_PAYLOAD_SHA256: Final = "137d82fbff349a45354e14525b4dcbbf8bf90d2bd9f0e75d773e0b9746b8dc7e"
BOOTSTRAP_REVISION_SHA256: Final = (
    "c6db1c1af4a656477402514f67f32e0882e3aa577e427b4ec81749a9ef5c41c6"
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bootstrap_payload() -> dict[str, object]:
    """Return a fresh bootstrap payload so callers cannot mutate shared state."""

    return json.loads(BOOTSTRAP_PAYLOAD_JSON)


def validate_bootstrap_constants() -> None:
    """Fail early if a literal and its canonical representation ever diverge."""

    payload_sha = _sha256(BOOTSTRAP_PAYLOAD_JSON)
    if payload_sha != BOOTSTRAP_PAYLOAD_SHA256:
        raise RuntimeError("bootstrap payload SHA-256 does not match its pinned literal")

    revision_json = json.dumps(
        {"base_snapshot_id": None, "payload_sha256": payload_sha},
        separators=(",", ":"),
        sort_keys=True,
    )
    if _sha256(revision_json) != BOOTSTRAP_REVISION_SHA256:
        raise RuntimeError("bootstrap revision SHA-256 does not match its pinned literal")


validate_bootstrap_constants()
