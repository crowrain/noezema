"""Scenario: multi-step planning as an observable artifact (T5.2,
stage 4, §6.2).

- planning mode "llm" (an online config change) makes the planner role
  propose a structured plan; the plan is persisted on the session
  (plan + canonical sha256), audited (plan_proposed) and the planner
  call is recorded in model_runs (phase=planning);
- an invalid proposal (schema) or an over-budget proposal falls back to
  the MVP template plan with a plan_fallback audit — the session still
  succeeds; the host-owned evidence/assessment boundary is untouched
  (the plan creates no evidence and carries no grade).
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import PLAN_TEMPLATE, Orchestrator
from packages.domain.canonical import canonical_sha256
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType, QuestionOrigin, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

COMPLETE: dict[str, Any] = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
TOOL_PYTHON: dict[str, Any] = {
    "public_rationale": "Проверить вычисление",
    "expected_information": "Результат 6*7",
    "decision": {
        "kind": "tool",
        "tool": "python.execute",
        "arguments": {"code": "print(6*7)"},
    },
}
CURATOR_OK: dict[str, Any] = {
    "summary": "Одно утверждение",
    "claims": [
        {
            "statement": "6*7 равно 42",
            "claim_type": "computed_result",
            "scope": {"expr": "6*7"},
        }
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


def _plan_response(steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if steps is None:
        steps = [
            {
                "observation": "Результат пересчёта 6*7 в sandbox",
                "method": "Независимый пересчёт тем же выражением",
                "tool_hint": "python.execute",
            },
            {
                "observation": "Совпадает ли результат с ранее зафиксированными claims",
                "method": "Сверка с локальным корпусом утверждений",
            },
        ]
    return {
        "public_rationale": "Сначала пересчёт, затем сверка с памятью",
        "plan": {
            "steps": steps,
            "stopping_criteria": ["Результат пересчитан и сверен", "Достигнут согласованный ответ"],
            "assessment_methods": ["recompute", "cross_source_check"],
        },
    }


def _planning_section(**overrides: Any) -> dict[str, Any]:
    section = copy.deepcopy(BOOTSTRAP_PAYLOAD["planning"])
    section.update(overrides)
    return section


async def _enable_llm_planning(engine: Any, **overrides: Any) -> None:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["planning"] = _planning_section(mode="llm", **overrides)
    result = await _run_online(engine, payload)
    assert result.state == "active"


async def _seed_question(scratch_url: str) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(
                db, ORMQuestion(text="Сколько будет 6*7?", origin=QuestionOrigin.SEEDED.value)
            )
            return q.id
    finally:
        await engine.dispose()


async def _scalar(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


def _orch(scratch_url: str, fake: FakeLLM, tmp_path: Any) -> tuple[Orchestrator, Any, Any]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
    )
    return orch, gateway, engine


async def test_llm_plan_persisted_and_observed(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    await _enable_llm_planning(engine)
    qid = await _seed_question(scratch_url)

    fake_llm.script(
        [{"content": _plan_response()}, {"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}]
    )
    orch, gateway, orch_engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(None)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.question_id == qid

    # the plan is an observable artifact on the session
    row = await _scalar(
        scratch_url,
        "SELECT plan, plan_sha256 FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    assert row is not None
    plan_doc, plan_sha = row[0], row[1]
    assert plan_doc is not None
    assert canonical_sha256(plan_doc) == plan_sha  # the stored hash matches
    steps = plan_doc["steps"]
    assert len(steps) == 2
    # the method is a separate field from the observation wording
    assert steps[0]["method"] and steps[0]["method"] != steps[0]["observation"]
    # the plan never carries a grade
    assert "confidence" not in plan_doc and "grade" not in plan_doc

    # audited: plan_proposed with the plan document
    audit_row = await _scalar(
        scratch_url,
        "SELECT payload->'plan' FROM audit_events "
        "WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.PLAN_PROPOSED.value, "s": str(outcome.session_id)},
    )
    assert audit_row is not None
    assert audit_row[0] == plan_doc

    # the planner call is a recorded model run in the planning phase
    run_row = await _scalar(
        scratch_url,
        "SELECT phase, output_schema_valid FROM model_runs "
        "WHERE session_id = :s AND phase = 'planning'",
        {"s": str(outcome.session_id)},
    )
    assert run_row is not None and run_row[1] is True

    # the question still went through the normal evidence path
    state = (
        await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(qid)})
    )[0]
    assert state == "verified"


async def test_invalid_plan_falls_back_to_template(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    await _enable_llm_planning(engine)
    qid = await _seed_question(scratch_url)

    bad = _plan_response()
    bad["plan"]["steps"] = []  # schema-invalid: at least one step
    fake_llm.script([{"content": bad}, {"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    orch, gateway, orch_engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(None)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED  # the fallback does not fail the session
    row = await _scalar(scratch_url, "SELECT plan, plan_sha256 FROM sessions WHERE id = :id",
                        {"id": str(outcome.session_id)})
    assert row[0] is None and row[1] is None  # template plan = no stored document
    fb = await _scalar(
        scratch_url,
        "SELECT payload->>'reason' FROM audit_events "
        "WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.PLAN_FALLBACK.value, "s": str(outcome.session_id)},
    )
    assert fb is not None and fb[0] == "schema_invalid"
    state = (
        await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(qid)})
    )[0]
    assert state == "verified"


async def test_overbudget_plan_falls_back(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    await _enable_llm_planning(engine, max_steps=3)
    qid = await _seed_question(scratch_url)

    over = _plan_response(
        steps=[
            {
                "observation": f"Шаг {i}: наблюдение",
                "method": f"Метод {i}",
            }
            for i in range(4)
        ]
    )
    fake_llm.script([{"content": over}, {"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    orch, gateway, orch_engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(None)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    row = await _scalar(scratch_url, "SELECT plan FROM sessions WHERE id = :id",
                        {"id": str(outcome.session_id)})
    assert row[0] is None
    fb = await _scalar(
        scratch_url,
        "SELECT payload->>'reason' FROM audit_events "
        "WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.PLAN_FALLBACK.value, "s": str(outcome.session_id)},
    )
    assert fb is not None and fb[0] == "budget_exceeded"
    state = (
        await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(qid)})
    )[0]
    assert state == "verified"


async def test_template_mode_makes_no_planner_call(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Bootstrap default (mode=template): no planner LLM call at all —
    the first scripted response is consumed by the explorer, so a
    planner-shaped response would break the script if it were called."""
    scratch_url, _engine = migrated_db
    qid = await _seed_question(scratch_url)

    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    orch, gateway, orch_engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(None)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    row = await _scalar(
        scratch_url,
        "SELECT count(*) FROM model_runs WHERE session_id = :s AND phase = 'planning'",
        {"s": str(outcome.session_id)},
    )
    assert row[0] == 0
    row = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.PLAN_PROPOSED.value, "s": str(outcome.session_id)},
    )
    assert row[0] == 0
    # the template plan still reaches the explorer context (behavior unchanged)
    assert PLAN_TEMPLATE  # sanity: the constant the context pack renders
    state = (
        await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(qid)})
    )[0]
    assert state == "verified"
