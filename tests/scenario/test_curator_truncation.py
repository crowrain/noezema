"""Scenario (T7.80, ADR-0030): an answer cut by the output limit, through the orchestrator.

Reproduces what session 1d0886fa hit on halogen-flash-next: the curator answered three
times, each time with exactly `max_output_tokens` completion tokens, its JSON was cut
mid-string ("Unterminated string starting at: char 3350"), and the journal said only
«curator unavailable; host failure report» — while the cut attempts existed NOWHERE in
our journal (a failed curator call wrote no model_runs row at all).

The same shape is produced here offline by the fake OpenAI server (`finish_reason=length`
plus a verbatim cut-off document), so every assertion is about what the HOST records:

  * a truncated curator proposal is its own outcome, not a schema hiccup;
  * each cut attempt becomes a model_runs row (finish_reason / output_tokens /
    output_schema_valid=false) and the audit payload names truncation honestly — no new
    AuditEventType: SESSION_STATE_CHANGED already carries curator outcomes;
  * an engine that CAN disable reasoning gets exactly ONE such follow-up, and the claim
    is recorded when that follow-up answers within the limit;
  * with profile "none" (the default) no reasoning parameter is sent at all;
  * activation refuses a payload whose phase policy is malformed.

Session plumbing is reused from test_orchestrator.py / test_online_activation.py rather
than duplicated. No real LLM, no network.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import AuditEventType, SessionState
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_orchestrator import COMPLETE, TOOL_PYTHON, _seed_question

pytestmark = [pytest.mark.scenario]

# the measured signature: a document cut inside a value, no closing brace
CUT_CURATOR_ANSWER = '{"summary": "Итог по ставке: ключевая ставка ЦБ РФ остаётся 14,00'

CURATOR_OK_MINIMAL: JsonDict = {
    "summary": "Одно утверждение",
    "claims": [
        {"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


# ── plumbing ────────────────────────────────────────────────────────────


def _curator_script(*, cut_attempts: int, recovered: bool) -> list[dict[str, Any]]:
    """explorer step → complete → curator answers (cut `cut_attempts` times, then valid)."""
    script: list[dict[str, Any]] = [{"content": TOOL_PYTHON}, {"content": COMPLETE}]
    script.extend(
        {"raw_message": CUT_CURATOR_ANSWER, "finish_reason": "length"} for _ in range(cut_attempts)
    )
    if recovered:
        script.append({"content": CURATOR_OK_MINIMAL})
    return script


async def _run(
    scratch_url: str,
    engine: AsyncEngine,
    fake_llm: FakeLLM,
    tmp_path: Path,
    question_id: uuid.UUID,
    script: list[dict[str, Any]],
    *,
    reasoning_profile: str = "none",
    payload_model: dict[str, Any] | None = None,
) -> Any:
    """One full session through the real orchestrator, with a chosen engine profile and
    (optionally) an effective snapshot whose model section carries a phase policy."""
    if payload_model is not None:
        payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
        payload["model"] = {**payload["model"], **payload_model}
        result = await _run_online(engine, payload)
        assert result.state == "active", result

    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url,
            model="fake-thinker",
            max_retries=2,
            retry_base_delay=0.01,
            reasoning_profile=reasoning_profile,
        )
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / f"ws-{reasoning_profile}-n{len(script)}"),
    )
    fake_llm.script(list(script))
    try:
        return await orch.run_session(question_id)
    finally:
        await gateway.close()


def _cut_tag(script: list[dict[str, Any]]) -> str:
    """Distinct workspace per scenario (tmp_path is shared by tests of one module)."""
    return f"n{len(script)}"


async def _rows(scratch_url: str, sql: str, params: dict[str, Any]) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params)).all())
    finally:
        await engine.dispose()


async def _model_runs_rows(scratch_url: str, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await _rows(
        scratch_url,
        "SELECT finish_reason, output_schema_valid, output_tokens, phase FROM model_runs "
        "WHERE session_id = :s ORDER BY created_at",
        {"s": str(session_id)},
    )
    return [
        {
            "finish_reason": r[0],
            "output_schema_valid": r[1],
            "output_tokens": r[2],
            "phase": r[3],
        }
        for r in rows
    ]


async def _curator_marker(scratch_url: str, session_id: uuid.UUID) -> JsonDict | None:
    """The audit payload of the curator outcome (SESSION_STATE_CHANGED carries it)."""
    rows = await _rows(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = :t ORDER BY sequence",
        {"s": str(session_id), "t": AuditEventType.SESSION_STATE_CHANGED.value},
    )
    for (payload,) in rows:
        if isinstance(payload, dict) and "curator_error_kind" in payload:
            return payload
    return None


def _curator_requests(fake: FakeLLM) -> list[JsonDict]:
    """Only the requests the fake server saw for the CURATOR call (the gateway names
    the response schema in `response_format.json_schema.name`)."""
    return [
        r
        for r in fake.requests()
        if r["response_format"]["json_schema"]["name"] == "CuratorProposal"
    ]


# ── 1. profile "none": honest truncation, attempts in model_runs ───────


@pytest.mark.asyncio
async def test_truncated_curator_answer_is_recorded_as_truncation(
    migrated_db: tuple[str, AsyncEngine], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """The 1d0886fa shape with no engine switch available: zero claims and a committed
    session (§6.5), but the journal names TRUNCATION and the cut attempt is in model_runs."""
    scratch_url, engine = migrated_db
    qid = await _seed_question(scratch_url)

    outcome = await _run(
        scratch_url, engine, fake_llm, tmp_path, qid, _curator_script(cut_attempts=1, recovered=False)
    )

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.claims_proposed == 0

    marker = await _curator_marker(scratch_url, outcome.session_id)
    assert marker is not None, "the curator failure left no audit marker at all"
    assert marker["curator_error_kind"] == "truncated_output", marker

    rows = await _model_runs_rows(scratch_url, outcome.session_id)
    cut = [r for r in rows if r["finish_reason"] == "length"]
    assert cut, f"the cut attempt never reached model_runs: {[r['finish_reason'] for r in rows]}"
    assert cut[0]["output_schema_valid"] is False
    # the fake reports completion tokens at the requested cap — the measured halogen signature
    assert cut[0]["output_tokens"] == 4096, cut[0]
    # model_runs.phase stays a SessionState: the call phase is not a state column
    assert cut[0]["phase"] == "consolidating", cut[0]

    requests = _curator_requests(fake_llm)
    assert len(requests) == 1, [r["params"] for r in requests]
    assert "reasoning_effort" not in requests[0]["params"]
    assert "chat_template_kwargs" not in requests[0]["params"]


# ── 2. profile "halogen": ONE reasoning-off retry recovers the answer ──


@pytest.mark.asyncio
async def test_reasoning_off_retry_recovers_the_claim_and_keeps_the_cut_visible(
    migrated_db: tuple[str, AsyncEngine], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """The fix itself on an engine that can disable reasoning: the follow-up is a
    DIFFERENT request (reasoning off), the claim is recorded, and the cut attempt stays
    in model_runs — recovery does not erase the evidence."""
    scratch_url, engine = migrated_db
    qid = await _seed_question(scratch_url)

    outcome = await _run(
        scratch_url,
        engine,
        fake_llm,
        tmp_path,
        qid,
        _curator_script(cut_attempts=1, recovered=True),
        reasoning_profile="halogen",
    )

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.claims_proposed == 1, "the recovered proposal was not applied"

    requests = _curator_requests(fake_llm)
    assert len(requests) == 2, [r["params"] for r in requests]
    assert "reasoning_effort" not in requests[0]["params"], requests[0]["params"]
    assert requests[1]["params"].get("reasoning_effort") == "none", requests[1]["params"]

    rows = await _model_runs_rows(scratch_url, outcome.session_id)
    cut = [r for r in rows if r["finish_reason"] == "length"]
    valid_curator = [r for r in rows if r["output_schema_valid"] and r["phase"] == "consolidating"]
    assert cut, f"the truncated attempt vanished from model_runs: {[r['finish_reason'] for r in rows]}"
    assert cut[0]["output_schema_valid"] is False
    assert len(valid_curator) == 1, valid_curator


# ── 3. the phase policy lives in the snapshot, not in code (§3) ────────


@pytest.mark.asyncio
async def test_snapshot_phase_policy_reaches_the_wire_without_waiting_for_truncation(
    migrated_db: tuple[str, AsyncEngine], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """`model.reasoning_by_phase.consolidation = "off"` in the EFFECTIVE snapshot asks
    the engine to stop thinking on the FIRST curator call — no truncation is needed for
    the policy to apply, and a phase without a policy keeps its untouched request."""
    scratch_url, engine = migrated_db
    qid = await _seed_question(scratch_url)

    outcome = await _run(
        scratch_url,
        engine,
        fake_llm,
        tmp_path,
        qid,
        _curator_script(cut_attempts=0, recovered=True),
        reasoning_profile="halogen",
        payload_model={"reasoning_by_phase": {"consolidation": "off"}},
    )

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.claims_proposed == 1

    curator = _curator_requests(fake_llm)
    assert len(curator) == 1, [r["params"] for r in curator]
    assert curator[0]["params"].get("reasoning_effort") == "none", curator[0]["params"]

    explorer = [
        r for r in fake_llm.requests() if r["response_format"]["json_schema"]["name"] == "ModelResponse"
    ]
    assert explorer, "the exploration call is missing from the journal"
    assert all("reasoning_effort" not in r["params"] for r in explorer), [r["params"] for r in explorer]


@pytest.mark.asyncio
async def test_profile_none_ignores_the_policy_rather_than_guess_a_switch(
    migrated_db: tuple[str, AsyncEngine], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """A deployment that does not know its engine sends NO reasoning parameter even when
    the snapshot asks for "off": we never guess how an unknown engine turns reasoning off."""
    scratch_url, engine = migrated_db
    qid = await _seed_question(scratch_url)

    outcome = await _run(
        scratch_url,
        engine,
        fake_llm,
        tmp_path,
        qid,
        _curator_script(cut_attempts=0, recovered=True),
        reasoning_profile="none",
        payload_model={"reasoning_by_phase": {"consolidation": "off"}},
    )

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.claims_proposed == 1
    for request in fake_llm.requests():
        assert "reasoning_effort" not in request["params"], request["params"]
        assert "chat_template_kwargs" not in request["params"], request["params"]


# ── 4. activation refuses a payload whose policy is malformed ───────────


@pytest.mark.asyncio
async def test_activation_refuses_a_malformed_reasoning_policy(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """Fail-closed before publishing (§5.4.1): a snapshot naming an unknown phase or a
    nonsense mode must not become effective — the sessions would only fail later."""
    from packages.memory.activation import ActivationError

    _scratch_url, engine = migrated_db
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["model"] = {**payload["model"], "reasoning_by_phase": {"curating": "sometimes"}}

    with pytest.raises(ActivationError):
        await _run_online(engine, payload)
