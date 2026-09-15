"""Scenario: the verifier role (T5.3, stage 4, §3.7, §5.5, §6.4).

- verification mode "llm" (an online config change) makes the verifier
  role organize deterministic checks over the session's typed
  evidence; the report is persisted (sessions.verification +
  canonical sha256), audited (verification_completed) and the call is
  recorded in model_runs (phase=verifying);
- the report is a PROPOSAL: it carries no grade/confidence (the schema
  cannot express them) and it never changes a claim's assessment —
  the rules engine computes the same grade with and without the
  verifier (gate M5);
- an invalid / over-budget / dangling-reference report falls back to
  the MVP no-op verifying phase (audit, never a session failure).
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
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


def _verifier_report(checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if checks is None:
        checks = [
            {
                "description": "Пересчёт 6*7 воспроизводится",
                "method": "Независимый пересчёт того же выражения",
                "evidence_indexes": [0],
                "result": "pass",
            },
            {
                "description": "Совпадает ли с ранее зафиксированными claims",
                "method": "Сверка с локальным корпусом утверждений",
                "evidence_indexes": [],
                "result": "not_applicable",
                "note": "Совпадения в памяти нет",
            },
        ]
    return {
        "public_rationale": "Пересчёт воспроизведён; независимой сверки нет",
        "checks": checks,
        "gaps": ["Независимая репликация пересчёта"],
    }


def _verifier_section(**overrides: Any) -> dict[str, Any]:
    section = copy.deepcopy(BOOTSTRAP_PAYLOAD["verification"])
    section.update(overrides)
    return section


async def _enable_verifier(engine: Any, **overrides: Any) -> None:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["verification"] = _verifier_section(mode="llm", **overrides)
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


async def _run(fake: FakeLLM, script: list[dict[str, Any]], tmp_path: Any, scratch_url: str) -> Any:
    orch, gateway, orch_engine = _orch(scratch_url, fake, tmp_path)
    fake_llm_script = script
    try:
        fake.script(fake_llm_script)
        return await orch.run_session(None)
    finally:
        await gateway.close()
        await orch_engine.dispose()


async def _claim_assessment(scratch_url: str, claim_statement: str) -> tuple[str, float, str]:
    row = await _scalar(
        scratch_url,
        """
        SELECT a.effective_grade, a.confidence, h.epistemic_status
        FROM claims c
        JOIN claim_assessment_heads h ON h.claim_id = c.id
        JOIN claim_assessments a ON a.id = h.current_assessment_id
        WHERE c.statement = :st AND h.assessment_state = 'current'
        """,
        {"st": claim_statement},
    )
    assert row is not None, "no current assessment for the claim"
    return row[0], float(row[1]), row[2]


async def test_verifier_report_persisted_and_observed(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    await _enable_verifier(engine)
    qid = await _seed_question(scratch_url)

    outcome = await _run(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": _verifier_report()}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.question_id == qid

    # the report is an observable artifact on the session
    row = await _scalar(
        scratch_url,
        "SELECT verification, verification_sha256 FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    report_doc, report_sha = row[0], row[1]
    assert report_doc is not None
    assert canonical_sha256(report_doc) == report_sha
    assert len(report_doc["checks"]) == 2
    # the report structurally cannot carry a grade/confidence
    assert "grade" not in report_doc and "confidence" not in report_doc
    for check in report_doc["checks"]:
        assert "grade" not in check and "confidence" not in check

    # audited with the document
    audit_row = await _scalar(
        scratch_url,
        "SELECT payload->'verification' FROM audit_events "
        "WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.VERIFICATION_COMPLETED.value, "s": str(outcome.session_id)},
    )
    assert audit_row is not None
    assert audit_row[0] == report_doc

    # the verifier call is a recorded model run in the verifying phase
    run_row = await _scalar(
        scratch_url,
        "SELECT phase, output_schema_valid FROM model_runs "
        "WHERE session_id = :s AND phase = 'verifying'",
        {"s": str(outcome.session_id)},
    )
    assert run_row is not None and run_row[1] is True

    # the claim still got its assessment from the rules engine
    state = (await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(qid)}))[0]
    assert state == "verified"
    grade, confidence, epistemic = await _claim_assessment(scratch_url, "6*7 равно 42")
    assert grade in ("E0", "E1", "E2", "E3", "E4")
    assert 0.0 <= confidence <= 1.0
    assert epistemic in ("hypothesis", "supported", "disputed", "refuted", "deferred")


async def test_verifier_judgment_never_changes_grade(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Gate M5: the verifier does not assign grade/confidence. The
    claim's assessment is identical whether the verifying phase is the
    MVP no-op or a verifier report (with an enthusiastic
    interpretation)."""
    scratch_url, engine = migrated_db
    await _seed_question(scratch_url)
    # a second question for the with-verifier run (the first one is
    # consumed — answered — by the baseline run)
    await _seed_question(scratch_url)

    # baseline: verification off (bootstrap default)
    baseline = await _run(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert baseline.final_state is SessionState.SUCCEEDED
    base_grade, base_conf, base_ep = await _claim_assessment(scratch_url, "6*7 равно 42")

    # with the verifier enabled
    await _enable_verifier(engine)
    with_verifier = await _run(
        fake_llm,
        [
            {"content": TOOL_PYTHON},
            {"content": COMPLETE},
            {
                "content": _verifier_report(
                    [
                        {
                            "description": "Пересчёт воспроизведён (уверен полностью)",
                            "method": "Пересчёт",
                            "evidence_indexes": [0],
                            "result": "pass",
                            "note": "Всё идеально",
                        }
                    ]
                )
            },
            {"content": CURATOR_OK},
        ],
        tmp_path,
        scratch_url,
    )
    assert with_verifier.final_state is SessionState.SUCCEEDED

    v_grade, v_conf, v_ep = await _claim_assessment(scratch_url, "6*7 равно 42")
    assert (v_grade, v_conf, v_ep) == (base_grade, base_conf, base_ep)


async def test_dangling_evidence_reference_falls_back(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    await _enable_verifier(engine)
    qid = await _seed_question(scratch_url)

    bad = _verifier_report(
        [
            {
                "description": "Проверка несуществующего evidence",
                "method": "Пересчёт",
                "evidence_indexes": [9],
                "result": "pass",
            }
        ]
    )
    outcome = await _run(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": bad}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED  # fallback, not failure
    row = await _scalar(
        scratch_url,
        "SELECT verification, verification_sha256 FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    assert row[0] is None and row[1] is None  # MVP no-op phase
    fb = await _scalar(
        scratch_url,
        "SELECT payload->>'reason' FROM audit_events "
        "WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.VERIFICATION_FALLBACK.value, "s": str(outcome.session_id)},
    )
    assert fb is not None and "evidence" in fb[0]
    state = (await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(qid)}))[0]
    assert state == "verified"


async def test_overbudget_report_falls_back(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    await _enable_verifier(engine, max_checks=1)
    qid = await _seed_question(scratch_url)

    over = _verifier_report(
        [
            {
                "description": f"Проверка {i}",
                "method": "Пересчёт",
                "evidence_indexes": [0],
                "result": "pass",
            }
            for i in range(2)
        ]
    )
    outcome = await _run(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": over}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    row = await _scalar(
        scratch_url,
        "SELECT verification FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    assert row[0] is None
    fb = await _scalar(
        scratch_url,
        "SELECT payload->>'reason' FROM audit_events "
        "WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.VERIFICATION_FALLBACK.value, "s": str(outcome.session_id)},
    )
    assert fb is not None and "max_checks" in fb[0]
    state = (await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(qid)}))[0]
    assert state == "verified"


async def test_off_mode_makes_no_verifier_call(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Bootstrap default (mode=off): no verifier LLM call — the first
    three scripted responses are explorer/tool/complete/curator."""
    scratch_url, _engine = migrated_db
    qid = await _seed_question(scratch_url)

    outcome = await _run(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    row = await _scalar(
        scratch_url,
        "SELECT count(*) FROM model_runs WHERE session_id = :s AND phase = 'verifying'",
        {"s": str(outcome.session_id)},
    )
    assert row[0] == 0
    row = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.VERIFICATION_COMPLETED.value, "s": str(outcome.session_id)},
    )
    assert row[0] == 0
    state = (await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(qid)}))[0]
    assert state == "verified"
