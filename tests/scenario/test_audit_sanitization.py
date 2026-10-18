"""Scenario (DB): T7.46a — the JSONB write boundary masks NUL bytes.

SMOKE-V12-K2 §3: a NUL byte inside a report payload made asyncpg encode
the JSONB parameter with a ``\\u0000`` escape, the server parser rejected
it (UntranslatableCharacterError) and the WHOLE phase-1 transaction
rolled back. The defensive layer at the audit boundary must guarantee:
a NUL in ANY string field of a payload (including nested dict/list) is
replaced by the visible marker, never dropped silently, and the write
succeeds.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType
from packages.domain.sanitization import NUL_MARKER
from packages.domain.services.audit import AuditService

pytestmark = [pytest.mark.scenario]


async def _row(engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        return res.first()


@pytest.mark.asyncio
async def test_audit_payload_nul_in_nested_fields_is_masked_not_dropped(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """Red before the fix: the INSERT raised
    UntranslatableCharacterError and rolled back the caller transaction."""
    _scratch_url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db, transaction(db):
        await AuditService(db).record(
            AuditEventType.WAKE_SKIPPED,
            payload={
                # a NUL in a top-level string, in a NESTED dict, in a
                # list element — every shape that reaches the JSONB
                "top": "a\x00b",
                # two real NULs + the 4-char literal itself → three
                # markers after masking (the ambiguity noted in
                # packages/domain/sanitization.py is acceptable)
                "nested": {"deeper": {"s": f"x\x00{NUL_MARKER}\x00y"}},
                "list": ["p\x00q", 1, None, "clean"],
                "no_nul": "plain",
            },
            public_summary="sum\x00mary",
            actor="operator",
        )

    audit = await _row(
        engine,
        "SELECT payload, public_summary FROM audit_events "
        "WHERE type = 'wake_skipped' ORDER BY id LIMIT 1",
    )
    assert audit is not None
    payload = audit[0]
    assert payload["top"] == f"a{NUL_MARKER}b"
    assert payload["nested"]["deeper"]["s"] == f"x{NUL_MARKER}{NUL_MARKER}{NUL_MARKER}y"
    assert payload["list"][0] == f"p{NUL_MARKER}q"
    assert payload["list"][1:] == [1, None, "clean"]
    assert payload["no_nul"] == "plain"
    assert "\x00" not in str(payload)
    assert audit[1] == f"sum{NUL_MARKER}mary"

    # the outbox twin carries the same sanitized payload
    outbox = await _row(
        engine,
        "SELECT payload FROM outbox_events WHERE audit_event_id = "
        "(SELECT id FROM audit_events WHERE type = 'wake_skipped' ORDER BY id LIMIT 1)",
    )
    assert outbox is not None
    assert outbox[0]["payload"]["top"] == f"a{NUL_MARKER}b"
    assert outbox[0]["payload"]["list"][0] == f"p{NUL_MARKER}q"
    assert outbox[0]["public_summary"] == f"sum{NUL_MARKER}mary"
