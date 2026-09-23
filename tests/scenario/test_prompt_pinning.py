"""Scenario (T7.35, ADR-0019): prompt content pinning through the
orchestrator on PostgreSQL + the deterministic fake LLM.

1. every model_runs row records prompt_version AND prompt_sha256, and
   the sha equals the effective snapshot's payload pin for the role;
2. a corrupted pin in the effective snapshot is fail-closed at
   admission: the session does not start, no session row exists, and
   the reason is in the audit (prompt_pin_mismatch).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.models.enums import SessionState
from packages.llm_gateway.roles import PromptPinError
from tests.conftest import FakeLLM
from tests.scenario.test_orchestrator import (
    COMPLETE,
    CURATOR_OK,
    TOOL_PYTHON,
    TOOL_WRITE,
    _make_orchestrator,
    _seed_question,
)

pytestmark = [pytest.mark.scenario]


async def _rows(scratch_url: str, sql: str, params: dict | None = None) -> list[tuple]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).all()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_model_runs_record_prompt_version_and_content_hash(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": TOOL_WRITE},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()
    assert outcome.final_state is SessionState.SUCCEEDED

    runs = await _rows(
        scratch_url,
        "SELECT phase, prompt_version, prompt_sha256, model_fingerprint "
        "FROM model_runs WHERE session_id = :s",
        {"s": str(outcome.session_id)},
    )
    assert len(runs) == 4, f"expected 4 model runs (3 explorer steps + 1 curator), got {len(runs)}"
    # every row records BOTH the label and the content hash
    for phase, version, sha, fingerprint in runs:
        assert version is not None and version, f"{phase}: prompt_version empty"
        assert sha is not None and len(sha) == 64, f"{phase}: prompt_sha256 empty"
        assert fingerprint["prompt_version"] == version
        assert fingerprint["prompt_sha256"] == sha

    explorer_rows = [r for r in runs if r[0] == "exploring"]
    curator_rows = [r for r in runs if r[0] == "consolidating"]
    assert len(explorer_rows) == 3 and len(curator_rows) == 1
    # the recorded sha equals the payload pin of the role (the content
    # the model actually received)
    pins = {role: lp.sha256 for role, lp in orch.prompts.items()}
    from packages.llm_gateway.roles import Role

    for _, version, sha, _ in explorer_rows:
        assert version == "explorer-v4"
        assert sha == pins[Role.EXPLORER]
    for _, version, sha, _ in curator_rows:
        assert version == "curator-v4"
        assert sha == pins[Role.CURATOR]


@pytest.mark.asyncio
async def test_corrupted_pin_fails_closed_at_admission(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """The effective snapshot's prompt pin no longer matches the file —
    the session must NOT start, and the reason must be in the audit."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    # corrupt the bootstrap snapshot's explorer pin (flip one hex digit)
    rows = await _rows(
        scratch_url,
        "SELECT prompts FROM config_snapshots WHERE activation_mode = 'bootstrap'",
    )
    assert len(rows) == 1
    prompts = json.loads(rows[0][0]) if isinstance(rows[0][0], str) else dict(rows[0][0])
    explorer_pin = prompts["explorer"]
    flipped = "0" if explorer_pin["sha256"][0] != "0" else "1"
    explorer_pin["sha256"] = flipped + explorer_pin["sha256"][1:]
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE config_snapshots SET prompts = CAST(:p AS jsonb) "
                    "WHERE activation_mode = 'bootstrap'"
                ),
                {"p": json.dumps(prompts)},
            )
    finally:
        await engine.dispose()

    fake_llm.script([{"content": COMPLETE}])
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        with pytest.raises(PromptPinError, match="sha256 mismatch"):
            await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    # the session never started
    sessions = await _rows(scratch_url, "SELECT count(*) FROM sessions")
    assert sessions[0][0] == 0
    # the reason is in the audit (out-of-session event)
    audit = await _rows(
        scratch_url,
        "SELECT type, payload, public_summary FROM audit_events WHERE type = 'prompt_pin_mismatch'",
    )
    assert len(audit) == 1
    payload: dict[str, Any] = audit[0][1] if isinstance(audit[0][1], dict) else json.loads(audit[0][1])
    assert "sha256 mismatch" in str(payload.get("error"))
    assert "not started" in str(audit[0][2])
