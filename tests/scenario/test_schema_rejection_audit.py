"""Scenario: the engine REFUSES the curator request (T7.23, ADR-0012).

The SMOKE-HALOGEN finding: an engine that does not support part of the
JSON Schema keywords (halogen: `format`) refuses the curator call with
HTTP 400, and the session silently ended without knowledge — in the
audit it looked exactly like "model unavailable". After T7.23:

- an HTTP 4xx is a REQUEST REFUSAL (the engine is up): the gateway
  raises LLMRequestRejectedError (distinct from LLMTransientError),
  the orchestrator records ``curator_error_kind = "request_rejected"``
  with an explicit public summary pointing at schema_profile;
- the refusal is SOFT (the research work is kept, the session commits
  with zero claims — same as "curator unavailable"), but it is
  EXPLICIT in the journal: ``curator_error_kind`` distinguishes
  request_rejected from unavailable by a simple SELECT;
- a truly down engine (5xx after retries) keeps the
  ``unavailable`` marker.

The only faked part is the LLM (the fake OpenAI server scripts the
HTTP 400 / 503); the orchestrator, audit and DB are the real code.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from tests.conftest import FakeLLM
from tests.scenario.test_source_coverage import (
    QUESTION_NO_URL,
    _all,
    _complete_response,
    _run_session,
)

pytestmark = [pytest.mark.scenario]


async def _curator_error_rows(scratch_url: str) -> list[Any]:
    return await _all(
        scratch_url,
        "SELECT payload, public_summary FROM audit_events "
        "WHERE type = 'session_state_changed' AND payload->>'curator_error' IS NOT NULL "
        "ORDER BY sequence",
    )


async def _claims_count(scratch_url: str) -> int:
    engine: Any = None
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT count(*) FROM claims"))).first()
    finally:
        await engine.dispose()
    return int(row[0])


@pytest.mark.asyncio
async def test_engine_refusal_is_explicit_request_rejected(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Any,
) -> None:
    """Curator call → HTTP 400 (engine refused the request): the session
    stays soft (succeeded, zero claims — the research is not wasted),
    but the audit carries the DISTINCT request_rejected marker, not
    'unavailable'."""
    scratch_url, engine = migrated_db
    script = [
        _complete_response(),  # explorer: done on the first step
        {"error": 400},  # curator: the engine refuses the request
    ]
    outcome = await _run_session(
        scratch_url, engine, fake_llm, tmp_path, script, question_text=QUESTION_NO_URL
    )

    # soft refusal: the session completes, nothing is fabricated
    assert outcome.final_state.value == "succeeded"
    assert outcome.evidence_count == 0
    assert await _claims_count(scratch_url) == 0

    rows = await _curator_error_rows(scratch_url)
    assert len(rows) == 1, f"expected exactly one curator_error audit, got {rows}"
    payload, summary = rows[0]["payload"], rows[0]["public_summary"]
    assert payload["curator_error_kind"] == "request_rejected"
    assert "400" in payload["curator_error"]
    assert "refused by engine" in summary
    assert "unavailable" not in summary


@pytest.mark.asyncio
async def test_down_engine_keeps_unavailable_marker(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Any,
) -> None:
    """Curator call → 503 twice (retries exhausted, LLMTransientError):
    the audit keeps the 'unavailable' marker — the two kinds must stay
    distinguishable by the journal."""
    scratch_url, engine = migrated_db
    script = [
        _complete_response(),  # explorer: done on the first step
        {"error": 503},  # curator: down, retry 1
        {"error": 503},  # curator: down, retry 2 → LLMTransientError
    ]
    outcome = await _run_session(
        scratch_url, engine, fake_llm, tmp_path, script, question_text=QUESTION_NO_URL
    )

    assert outcome.final_state.value == "succeeded"
    assert await _claims_count(scratch_url) == 0

    rows = await _curator_error_rows(scratch_url)
    assert len(rows) == 1
    payload, summary = rows[0]["payload"], rows[0]["public_summary"]
    assert payload["curator_error_kind"] == "unavailable"
    assert "unavailable" in summary
