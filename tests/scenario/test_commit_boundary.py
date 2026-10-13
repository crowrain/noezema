"""T7.24 (EVAL-4 abort 2026-09-21, §6.5, §6.7): a disallowed evidence kind
never kills the series.

EVAL-4 died on session 16/69 with the combination "a computed_result claim
supported by a local_observation evidence": a RuleValidationError escaped
the commit path AFTER the durable prepared row and the session was left in
``committing`` — fail-closed admission then blocked the whole series. Two
properties must hold:

1. the combination is rejected at the CURATOR PROPOSAL stage
   (consolidating, T7.9 pre-commit check) — before any staging op is
   recorded and long before the prepared row: the claim is not committed,
   but the session ends in a normal terminal state (§6.7), no attempt is
   left unresolved, and the next session is admitted;
2. even if a RuleValidationError is raised INSIDE the fenced final
   transaction (the body fails before COMMIT — a KNOWN rollback, not a
   lost answer), it must not escape to the driver and leave the session
   nonterminal: the session ends failed, the attempt aborted, and the
   next session is admitted. (UNKNOWN outcomes — a lost COMMIT answer —
   still go to the reconciler: hostctl reconcile-tick,
   tests/scenario/test_reconciler.py.)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.db.uow import transaction
from packages.domain.models.enums import SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.memory.rules_engine import RuleValidationError
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

READING_MD = "The Little Prince\nDune\nThe Master and Margarita\n"


def _tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": "r",
            "expected_information": "e",
            "decision": {"kind": "tool", "tool": tool, "arguments": arguments},
        }
    }


def _complete() -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": "r",
            "expected_information": "e",
            "decision": {"kind": "complete", "reason": "goal_reached"},
        }
    }


def _curator(claims: list[dict[str, Any]], links: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "content": {"summary": "s", "claims": claims, "evidence_links": links, "new_questions": []}
    }


def _make_orchestrator(
    scratch_url: str, fake: FakeLLM, workspace: Path
) -> tuple[Orchestrator, LLMMiddleware, Any]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(workspace),
    )
    return orch, gateway, engine


async def _seed_question(scratch_url: str, text_: str) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = ORMQuestion(text=text_, origin="seeded")
            await QuestionRepository.create(db, q)
            return q.id
    finally:
        await engine.dispose()


async def _unresolved_attempts(scratch_url: str) -> int:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM commit_attempts "
                        "WHERE status IN ('prepared','reconciling')"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()


async def _session_state(scratch_url: str, session_id: uuid.UUID) -> tuple[str, str | None]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text("SELECT state, termination_reason FROM sessions WHERE id=:id"),
                        {"id": str(session_id)},
                    )
                )
                .mappings()
                .first()
            )
            return (row["state"], row["termination_reason"])
    finally:
        await engine.dispose()


async def _run_next_session(scratch_url: str, fake: FakeLLM, workspace: Path) -> SessionState:
    """The next question: proves admission is granted (a nonterminal
    session would make run_session refuse to start)."""
    qid = await _seed_question(scratch_url, "Сколько будет 6*7?")
    fake.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "6*7 равно 42",
                        "claim_type": "computed_result",
                        "scope": {"expr": "6*7"},
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake, workspace)
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await engine.dispose()
    return outcome.final_state


@pytest.mark.asyncio
async def test_disallowed_evidence_kind_rejected_at_consolidating(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """The exact EVAL-4 combination (computed_result claim + a
    local_observation support evidence) is bounced at the curator
    proposal stage: the claim is never committed, the session ends in a
    normal terminal state, no attempt is left unresolved, and the next
    session is admitted."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(
        scratch_url, "Сколько непустых строк в файле notes/reading.md?"
    )

    # explorer: write the file, read it back (the read is the
    # local_observation evidence, index 0)
    fake_llm.script(
        [
            _tool("workspace.write", {"path": "notes/reading.md", "content": READING_MD}),
            _tool("workspace.read", {"path": "notes/reading.md"}),
            _complete(),
            # the disallowed combination, verbatim: computed_result
            # supported by the local_observation evidence
            _curator(
                [
                    {
                        "statement": "Количество непустых строк в файле notes/reading.md равно 3.",
                        "claim_type": "computed_result",
                        "scope": {"path": "notes/reading.md", "metric": "nonempty_lines", "value": 3},
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
            # T7.106 (ADR-0034 вариант C): хост возвращает отклонённое целиком предложение
            # на ОДНО исправление — скриптован идентичный ответ: он отклоняется вторично,
            # финальный отказ ровно один (пустая очередь больше не «случайный» путь)
            _curator(
                [
                    {
                        "statement": "Количество непустых строк в файле notes/reading.md равно 3.",
                        "claim_type": "computed_result",
                        "scope": {"path": "notes/reading.md", "metric": "nonempty_lines", "value": 3},
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    orch, gateway, orch_engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    # fail-closed on the claim, terminal on the session (§6.7)
    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.claims_proposed == 0

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            # no staging op was recorded: the proposal was bounced BEFORE
            # any staging write (T7.9 pre-commit check)
            staging = (
                await db.execute(
                    text("SELECT count(*) FROM session_staging WHERE session_id = :s"),
                    {"s": str(outcome.session_id)},
                )
            ).scalar_one()
            assert staging == 0
            # the claim was NOT committed
            claims = (
                await db.execute(text("SELECT count(*) FROM claims"))
            ).scalar_one()
            assert claims == 0
            # no unresolved attempt: the single attempt is committed
            # (a 0-claim commit), never left prepared/reconciling
            attempts = (
                (
                    await db.execute(text("SELECT status FROM commit_attempts"))
                )
                .scalars()
                .all()
            )
            assert attempts == ["committed"]
            # the rejection is explainable from the audit
            rejected = (
                (
                    await db.execute(
                        text(
                            "SELECT payload::text FROM audit_events "
                            "WHERE session_id = :s AND payload ? 'curator_rejected_by_rules'"
                        ),
                        {"s": str(outcome.session_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert len(rejected) == 1
            assert "support evidence kind 'local_observation' not allowed for computed_result" in (
                rejected[0]
            )
            # T7.106: финальный отказ несёт аддитивный ключ раунда (дайджест ПЕРВОГО ответа)
            assert '"correction_round"' in rejected[0]
            # раунд ровно один: событие возврата на исправление — единственное
            fixations = (
                (
                    await db.execute(
                        text(
                            "SELECT public_summary FROM audit_events WHERE session_id = :s "
                            "AND payload ? 'curator_correction_requested'"
                        ),
                        {"s": str(outcome.session_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert fixations == ["curator proposal returned for one correction round"]
        state, _reason = await _session_state(scratch_url, outcome.session_id)
    finally:
        await engine.dispose()
    assert state == "succeeded"

    # ровно одно исправление запрашивалось у модели, и оба ответа съедены скриптом
    assert sum(1 for r in fake_llm.requests() if "# Исправление предложения" in str(r.get("last_user", ""))) == 1
    assert fake_llm.state()["queue_left"] == 0

    assert await _unresolved_attempts(scratch_url) == 0

    # the next session is admitted and runs to a terminal state
    assert await _run_next_session(scratch_url, fake_llm, tmp_path / "ws2") is SessionState.SUCCEEDED


@pytest.mark.asyncio
async def test_commit_boundary_error_ends_session_terminal(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path, monkeypatch: Any
) -> None:
    """A RuleValidationError raised INSIDE the fenced final transaction
    (the body fails before COMMIT — a known rollback) must not escape to
    the driver and leave the session in ``committing``: the session ends
    failed, the attempt aborted (fenced writes, audit carries the
    diagnostics), and the next session is admitted."""
    from packages.memory import service as memory_service

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuleValidationError(
            "support evidence kind 'local_observation' not allowed for computed_result"
        )

    original_apply = memory_service.MemoryService.apply_claim_staging
    monkeypatch.setattr(memory_service.MemoryService, "apply_claim_staging", _boom)

    scratch_url, _engine = migrated_db
    question_id = await _seed_question(
        scratch_url, "Сколько будет 6*7? (проверь вычислением)"
    )
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "6*7 равно 42",
                        "claim_type": "computed_result",
                        "scope": {"expr": "6*7"},
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    orch, gateway, orch_engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        # must NOT raise: the exception is resolved, not propagated
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.FAILED
    assert outcome.termination_reason == "commit_boundary_error"
    assert outcome.claims_proposed == 0

    # the failure was specific to this session's final transaction — the
    # boundary is healthy again for the next one
    monkeypatch.setattr(memory_service.MemoryService, "apply_claim_staging", original_apply)

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            state_row = (
                (
                    await db.execute(
                        text(
                            "SELECT state, termination_reason FROM sessions WHERE id = :s"
                        ),
                        {"s": str(outcome.session_id)},
                    )
                )
                .mappings()
                .first()
            )
            assert state_row["state"] == "failed"
            assert str(state_row["termination_reason"]).startswith(
                "commit_boundary_error: RuleValidationError"
            )
            attempt = (
                (
                    await db.execute(
                        text("SELECT status FROM commit_attempts WHERE session_id = :s"),
                        {"s": str(outcome.session_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert attempt == ["aborted"]
            # nothing was applied (the fenced transaction rolled back)
            claims = (await db.execute(text("SELECT count(*) FROM claims"))).scalar_one()
            assert claims == 0
            # the host failure report (§6.5) carries the diagnostics
            audit_rows = (
                (
                    await db.execute(
                        text(
                            "SELECT type FROM audit_events WHERE session_id = :s "
                            "ORDER BY sequence"
                        ),
                        {"s": str(outcome.session_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert "session_failed" in audit_rows
            assert "commit_attempt_aborted" in audit_rows
    finally:
        await engine.dispose()

    assert await _unresolved_attempts(scratch_url) == 0

    # the next session is admitted and runs to a terminal state
    assert await _run_next_session(scratch_url, fake_llm, tmp_path / "ws2") is SessionState.SUCCEEDED


@pytest.mark.asyncio
async def test_disallowed_evidence_kind_bounces_only_that_operation(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """T7.105 (ADR-0034 вариант B): предпроверка правил называет ОДНУ операцию —
    только она и снимается. Запрещённая комбинация (computed_result + local_observation)
    снята с прежней именованной причиной движка правил; законная computed_result
    того же предложения применяется; отказа предложения целиком нет, связь снятой
    операции названа в `rejected_operations`, а не перенесена на выживший claim."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url, "Сколько строк в notes/reading.md и сколько будет 3+3?")
    fake_llm.script(
        [
            _tool("workspace.write", {"path": "notes/reading.md", "content": READING_MD}),
            # прочитанный файл — local_observation evidence (индекс 0), как в прежнем тесте;
            # python.execute — computation (индекс 1)
            _tool("workspace.read", {"path": "notes/reading.md"}),
            _tool("python.execute", {"code": "print(3+3)"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "Количество непустых строк в файле notes/reading.md равно 3.",
                        "claim_type": "computed_result",
                        "scope": {"path": "notes/reading.md", "metric": "nonempty_lines", "value": 3},
                    },
                    {
                        "statement": "3+3 равно 6",
                        "claim_type": "computed_result",
                        "scope": {"expr": "3+3"},
                    },
                ],
                [
                    {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                    {"evidence_index": 1, "claim_index": 1, "relation": "supports"},
                ],
            ),
        ]
    )
    orch, gateway, orch_engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.claims_proposed == 2

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            # прежнего отказного события нет: снята одна операция, не предложение целиком
            rejected = (
                await db.execute(
                    text(
                        "SELECT payload FROM audit_events WHERE session_id = :s "
                        "AND payload ? 'curator_rejected_by_rules'"
                    ),
                    {"s": str(outcome.session_id)},
                )
            ).scalars().all()
            assert rejected == []

            # T7.106 (ADR-0034 вариант C): частичный отказ варианта B раунд исправления
            # НЕ запускает — события раунда нет
            fixations = (
                await db.execute(
                    text(
                        "SELECT payload FROM audit_events WHERE session_id = :s "
                        "AND payload ? 'curator_correction_requested'"
                    ),
                    {"s": str(outcome.session_id)},
                )
            ).scalars().all()
            assert fixations == []

            rows = (
                await db.execute(
                    text(
                        "SELECT payload FROM audit_events WHERE session_id = :s "
                        "AND type = 'claim_created' AND jsonb_typeof(payload -> 'claims') = 'array'"
                    ),
                    {"s": str(outcome.session_id)},
                )
            ).scalars().all()
            assert len(rows) == 1
            payload = dict(rows[0])
            assert payload["claims_proposed"] == 2
            assert payload["claims_applied"] == 1
            assert payload.get("correction_round") is None  # T7.106: варианта B раунд не касается
            entries = payload["rejected_operations"]
            assert len(entries) == 1
            assert entries[0]["claim_index"] == 0
            # та же строка причины движка правил, что раньше роняла всё предложение
            assert "claim 0 (computed_result):" in entries[0]["reason"]
            assert "support evidence kind 'local_observation' not allowed for computed_result" in (
                entries[0]["reason"]
            )
            assert entries[0]["evidence_links_rejected"] == [
                {"claim_index": 0, "evidence_index": 0, "relation": "supports"}
            ]

            statements = (await db.execute(text("SELECT statement FROM claims"))).scalars().all()
            assert sorted(statements) == sorted(["3+3 равно 6"])
            staging = (
                await db.execute(
                    text("SELECT op FROM session_staging WHERE session_id = :s"),
                    {"s": str(outcome.session_id)},
                )
            ).scalars().all()
            assert sorted(staging) == ["claim", "evidence"]
            attempts = (await db.execute(text("SELECT status FROM commit_attempts"))).scalars().all()
            assert attempts == ["committed"]
    finally:
        await engine.dispose()

    assert await _unresolved_attempts(scratch_url) == 0

    # раздел исправления модели не предлагался вовсе, единственный ответ съеден скриптом:
    # частичный отказ варианта B раунда не имеет (T7.106)
    assert sum(1 for r in fake_llm.requests() if "# Исправление предложения" in str(r.get("last_user", ""))) == 0
    assert fake_llm.state()["queue_left"] == 0


@pytest.mark.asyncio
async def test_all_claims_rejected_by_rules_still_rejects_whole_proposal(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """T7.105 (ADR-0034 §7): когда сняты ВСЕ операции, отказ предложения целиком сохраняется —
    прежние ключи события (`curator_rejected_by_rules` + `rejected_proposal`), нулевой staging.
    T7.106 (ADR-0034 вариант C): поверх этого — ровно ОДИН раунд исправления: второй ответ
    скриптован идентичным, отклоняется вторично, финальный отказ один и несёт дайджест ПЕРВОГО
    ответа в аддитивном ключе `correction_round`."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url, "Сколько строк в двух заметках?")
    fake_llm.script(
        [
            _tool("workspace.write", {"path": "notes/a.md", "content": READING_MD}),
            # каждое чтение — local_observation evidence: обе связи запрещены для computed_result
            _tool("workspace.read", {"path": "notes/a.md"}),
            _tool("workspace.write", {"path": "notes/b.md", "content": "One\nTwo\n"}),
            _tool("workspace.read", {"path": "notes/b.md"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "Количество непустых строк в notes/a.md равно 3.",
                        "claim_type": "computed_result",
                        "scope": {"path": "notes/a.md", "metric": "nonempty_lines", "value": 3},
                    },
                    {
                        "statement": "Количество непустых строк в notes/b.md равно 2.",
                        "claim_type": "computed_result",
                        "scope": {"path": "notes/b.md", "metric": "nonempty_lines", "value": 2},
                    },
                ],
                [
                    {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                    {"evidence_index": 1, "claim_index": 1, "relation": "supports"},
                ],
            ),
            # T7.106: ОДИН раунд исправления — идентичный ответ отклоняется вторично
            _curator(
                [
                    {
                        "statement": "Количество непустых строк в notes/a.md равно 3.",
                        "claim_type": "computed_result",
                        "scope": {"path": "notes/a.md", "metric": "nonempty_lines", "value": 3},
                    },
                    {
                        "statement": "Количество непустых строк в notes/b.md равно 2.",
                        "claim_type": "computed_result",
                        "scope": {"path": "notes/b.md", "metric": "nonempty_lines", "value": 2},
                    },
                ],
                [
                    {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                    {"evidence_index": 1, "claim_index": 1, "relation": "supports"},
                ],
            ),
        ]
    )
    orch, gateway, orch_engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.claims_proposed == 0

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            rejected = (
                await db.execute(
                    text(
                        "SELECT payload::text FROM audit_events WHERE session_id = :s "
                        "AND payload ? 'curator_rejected_by_rules'"
                    ),
                    {"s": str(outcome.session_id)},
                )
            ).scalars().all()
            assert len(rejected) == 1
            assert "claim 0 (computed_result):" in rejected[0]
            assert "claim 1 (computed_result):" in rejected[0]
            # T7.106: аддитивный ключ раунда — дайджест ПЕРВОГО ответа внутри финального отказа
            assert '"correction_round"' in rejected[0]
            fixations = (
                await db.execute(
                    text(
                        "SELECT public_summary FROM audit_events WHERE session_id = :s "
                        "AND payload ? 'curator_correction_requested'"
                    ),
                    {"s": str(outcome.session_id)},
                )
            ).scalars().all()
            assert fixations == ["curator proposal returned for one correction round"]
            digest = (
                await db.execute(
                    text(
                        "SELECT payload->'rejected_proposal'->'claims' FROM audit_events "
                        "WHERE session_id = :s AND payload ? 'curator_rejected_by_rules'"
                    ),
                    {"s": str(outcome.session_id)},
                )
            ).scalar_one()
            assert len(digest) == 2
            staging = (
                await db.execute(
                    text("SELECT count(*) FROM session_staging WHERE session_id = :s"),
                    {"s": str(outcome.session_id)},
                )
            ).scalar_one()
            assert staging == 0
            claims = (await db.execute(text("SELECT count(*) FROM claims"))).scalar_one()
            assert claims == 0
    finally:
        await engine.dispose()

    assert await _unresolved_attempts(scratch_url) == 0

    # один раунд = ровно одно обращение с разделом исправления; скрипт съеден
    assert sum(1 for r in fake_llm.requests() if "# Исправление предложения" in str(r.get("last_user", ""))) == 1
    assert fake_llm.state()["queue_left"] == 0
