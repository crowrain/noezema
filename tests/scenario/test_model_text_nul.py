"""T7.47a (ADR-0020, open question of T7.46a): NUL in MODEL-GENERATED
text, i.e. outside the three T7.46a boundaries (tool observations,
evidence, audit write).

The LLM response is the ONLY model-text source that reaches the host;
it enters at a single point — ``LLMMiddleware.chat`` — and fans out to
JSONB/TEXT columns that T7.46a never touched:

- ``session_staging.payload`` (claim statement/scope/search_statements,
  question text/rationale, evidence note) — fatal: a NUL in the JSONB
  rolls back the whole phase-1 transaction (the SMOKE-V12-K2 failure
  mode, now reachable from model text);
- ``sessions.plan`` / ``sessions.verification`` / ``sessions.extraction``
  (JSONB) — fatal the same way;
- ``sessions.termination_reason`` (TEXT) — Postgres accepts a raw NUL
  in a binary TEXT parameter, so the session survives, but the marker
  must be stored for observability (NUL never vanishes silently).

Before the fix these tests are RED: the staging/plan/verification/
extraction ones crash ``run_session`` with an UntranslatableCharacter-
class DB error (phase-1 rollback), the termination_reason one stores a
raw NUL. After the fix (mask at the gateway entry + defensive mask in
``StagingService.record``) the session commits and the visible marker
``\\x00`` is stored instead of the NUL byte.
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
from packages.domain.models.enums import QuestionOrigin, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.domain.sanitization import NUL_MARKER
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

TOOL_PYTHON: dict[str, Any] = {
    "public_rationale": "Проверить вычисление",
    "expected_information": "Результат 6*7",
    "decision": {
        "kind": "tool",
        "tool": "python.execute",
        "arguments": {"code": "print(6*7)"},
    },
}
COMPLETE: dict[str, Any] = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
# the NUL is in the MODEL's text (the curator's own wording), not in any
# tool observation: the exact channel the T7.46a boundaries do not cover
NUL_STATEMENT = "6*7 рав\x00но 42"
MASKED_STATEMENT = f"6*7 рав{NUL_MARKER}но 42"

CURATOR_NUL_STATEMENT: dict[str, Any] = {
    "summary": "Одно утверждение",
    "claims": [
        {
            "statement": NUL_STATEMENT,
            "claim_type": "computed_result",
            "scope": {"expr": "6*7"},
        }
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}
CURATOR_NUL_QUESTION: dict[str, Any] = {
    "summary": "Одно утверждение и вопрос",
    "claims": [
        {
            "statement": "6*7 равно 42",
            "claim_type": "computed_result",
            "scope": {"expr": "6*7"},
        }
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [
        {"text": "Почему произведение рав\x00но 42?", "origin": "previous_result"}
    ],
}
COMPLETE_NUL_REASON: dict[str, Any] = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached\x00"},
}


async def _seed_question(scratch_url: str, text_: str = "Сколько будет 6*7?") -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(db, ORMQuestion(text=text_, origin=QuestionOrigin.SEEDED.value))
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


def _orch(scratch_url: str, fake: FakeLLM, tmp_path: Any) -> tuple[Orchestrator, LLMMiddleware, Any]:
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


async def test_nul_in_claim_statement_commits_and_dedupes(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """The ADR-0020 open question: NUL in the curator's claim statement
    reaches session_staging.payload (JSONB) and, pre-fix, rolls back the
    whole phase-1 transaction. Post-fix the masked statement commits,
    the staging payload_hash is computed over the SAME masked value, and
    a second session with the same NUL-bearing statement dedupes against
    the stored (masked) claim — T7.9 dedup is not broken by masking."""
    scratch_url, _engine = migrated_db
    qid = await _seed_question(scratch_url)

    # ── session 1 ──
    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": COMPLETE},
            {"content": CURATOR_NUL_STATEMENT},
        ]
    )
    orch, gateway, engine = _orch(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await engine.dispose()

    # pre-fix this raised (phase-1 rolled back): UntranslatableCharacter
    # on the session_staging INSERT
    assert outcome.final_state is SessionState.SUCCEEDED

    row = await _scalar(
        scratch_url,
        "SELECT statement FROM claims WHERE created_in_session = :id",
        {"id": str(outcome.session_id)},
    )
    assert row is not None, "the claim did not commit"
    assert row[0] == MASKED_STATEMENT  # the marker, not the NUL byte

    # staging: the stored payload and its hash agree — both computed over
    # the masked value (mask-then-hash, ADR-0020)
    staging = await _scalar(
        scratch_url,
        "SELECT payload, payload_hash FROM session_staging "
        "WHERE session_id = :id AND op = 'claim'",
        {"id": str(outcome.session_id)},
    )
    assert staging is not None
    payload, payload_hash = staging
    assert payload["statement"] == MASKED_STATEMENT
    assert "\x00" not in str(payload)
    assert payload_hash == canonical_sha256(payload)

    # ── session 2 (a FRESH question): the same NUL statement must
    # DEDUPE against the stored masked claim (T7.9) ──
    qid2 = await _seed_question(scratch_url, "Умножение 6 и 7, проверь результат.")
    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": COMPLETE},
            {"content": CURATOR_NUL_STATEMENT},
        ]
    )
    orch2, gateway2, engine2 = _orch(scratch_url, fake_llm, tmp_path / "ws2")
    try:
        outcome2 = await orch2.run_session(qid2)
    finally:
        await gateway2.close()
        await engine2.dispose()

    assert outcome2.final_state is SessionState.SUCCEEDED
    count = (
        await _scalar(
            scratch_url,
            "SELECT count(*)::int FROM claims WHERE statement = :s",
            {"s": MASKED_STATEMENT},
        )
    )[0]
    assert count == 1, "the masked statement must dedupe, not create a second claim"


async def test_nul_in_question_text_commits(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, _engine = migrated_db
    qid = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": COMPLETE},
            {"content": CURATOR_NUL_QUESTION},
        ]
    )
    orch, gateway, engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED

    row = await _scalar(
        scratch_url,
        "SELECT text, origin FROM questions WHERE parent_id = :pid",
        {"pid": str(qid)},
    )
    assert row is not None, "the model-proposed question did not commit"
    assert row[0] == f"Почему произведение рав{NUL_MARKER}но 42?"
    assert row[1] == "previous_result"

    staging = await _scalar(
        scratch_url,
        "SELECT payload, payload_hash FROM session_staging "
        "WHERE session_id = :id AND op = 'question'",
        {"id": str(outcome.session_id)},
    )
    assert staging is not None
    payload, payload_hash = staging
    assert payload["text"] == f"Почему произведение рав{NUL_MARKER}но 42?"
    assert payload_hash == canonical_sha256(payload)


async def test_nul_in_complete_reason_is_stored_as_marker(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """sessions.termination_reason is TEXT. T7.47a measured on the real
    DB (UTF8): NUL is rejected in TEXT too (CharacterNotInRepertoire
    Error) — the SMOKE-V12-K2 §3.1 side-observation «NUL в бинарном
    text-параметре Postgres принимает» was refuted (ADR-0020 itself
    already said «TEXT-столбец тоже не хранит NUL»). Pre-fix the
    complete-reason NUL is written in the FENCED FINAL transaction
    (commit_finalize) and kills the whole commit; post-fix the marker
    is stored — NUL never vanishes silently.

    ``decision.reason`` is a host-vocabulary token, not free text: a
    NUL-corrupted "goal_reached\\x00" does not match the GOAL_REACHED
    value, so the session lands in the SAME bucket the host gives any
    unknown/corrupted reason (SUCCEEDED_PARTIAL — the work is
    preserved, the outcome is explainable from the stored marker; the
    host does not guess intent from a corrupted token).
    """
    scratch_url, _engine = migrated_db
    qid = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": COMPLETE_NUL_REASON},
            {"content": CURATOR_NUL_STATEMENT},
        ]
    )
    orch, gateway, engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED_PARTIAL
    row = await _scalar(
        scratch_url,
        "SELECT termination_reason FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    assert row is not None
    assert row[0] == f"goal_reached{NUL_MARKER}"
    assert outcome.termination_reason == f"goal_reached{NUL_MARKER}"
    # the work survived: the (masked) claim still committed
    count = (
        await _scalar(
            scratch_url,
            "SELECT count(*)::int FROM claims WHERE statement = :s",
            {"s": MASKED_STATEMENT},
        )
    )[0]
    assert count == 1


# ── plan / verification / extraction (online config, llm modes) ────────


def _plan_response_nul() -> dict[str, Any]:
    return {
        "public_rationale": "Сначала пересчёт, затем сверка с памятью",
        "plan": {
            "steps": [
                {
                    "observation": "Результат пересчёта 6*7 в sandbox\x00",
                    "method": "Независимый пересчёт тем же выражением",
                    "tool_hint": "python.execute",
                },
                {
                    "observation": "Совпадает ли результат с ранее зафиксированными claims",
                    "method": "Сверка с локальным корпусом утверждений",
                },
            ],
            "stopping_criteria": ["Результат пересчитан и сверен"],
            "assessment_methods": ["recompute"],
        },
    }


async def test_nul_in_llm_plan_is_stored_as_marker(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["planning"]["mode"] = "llm"
    result = await _run_online(engine, payload)
    assert result.state == "active"
    qid = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": _plan_response_nul()},
            {"content": TOOL_PYTHON},
            {"content": COMPLETE},
            {"content": CURATOR_NUL_STATEMENT},
        ]
    )
    orch, gateway, orch_engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    row = await _scalar(
        scratch_url,
        "SELECT plan, plan_sha256 FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    assert row is not None
    plan_doc, plan_sha = row
    assert plan_doc is not None, "the LLM plan was not persisted (fallback?) — check plan_fallback audit"
    steps = plan_doc["steps"]
    assert steps[0]["observation"] == f"Результат пересчёта 6*7 в sandbox{NUL_MARKER}"
    # the stored sha is over the SAME masked document
    assert canonical_sha256(plan_doc) == plan_sha


async def test_nul_in_verifier_report_is_stored_as_marker(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["verification"]["mode"] = "llm"
    result = await _run_online(engine, payload)
    assert result.state == "active"
    qid = await _seed_question(scratch_url)

    verifier = {
        "public_rationale": "Пересчёт воспроизведён; независимой сверки нет",
        "checks": [
            {
                "description": "Пересчёт 6*7 воспроизводится\x00",
                "method": "Независимый пересчёт того же выражения",
                "evidence_indexes": [0],
                "result": "pass",
            }
        ],
        "gaps": ["Независимая репликация пересчёта"],
    }
    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": COMPLETE},
            {"content": verifier},
            {"content": CURATOR_NUL_STATEMENT},
        ]
    )
    orch, gateway, orch_engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    row = await _scalar(
        scratch_url,
        "SELECT verification, verification_sha256 FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    assert row is not None
    report_doc, report_sha = row
    assert report_doc is not None, (
        "the verifier report was not persisted (fallback?) — check verification_fallback audit"
    )
    assert report_doc["checks"][0]["description"] == f"Пересчёт 6*7 воспроизводится{NUL_MARKER}"
    assert canonical_sha256(report_doc) == report_sha


async def test_postgres_rejects_only_nul_among_c0_bytes(migrated_db: tuple[str, Any]) -> None:
    """Empirical C0 scope (T7.47a, fixes the ADR-0020 scope statement
    with a test): which control bytes does Postgres (UTF8) actually
    reject in the column types the app uses?

    Measured: NUL (0x00) is the ONLY rejected C0 byte — in TEXT and
    VARCHAR alike (CharacterNotInRepertoireError: invalid byte
    sequence for encoding UTF8: 0x00) and in JSONB (Untranslatable
    CharacterError on the \\u0000 escape). All other C0 bytes
    (0x01–0x1F) are stored fine. Hence masking stays \x00-only
    (ADR-0020): extending it would alter observable data Postgres
    accepts.

    Also refutes the SMOKE-V12-K2 §3.1 side-observation that a binary
    TEXT parameter accepts NUL — on the app's path (SQLAlchemy +
    asyncpg, UTF8 database) it is rejected.
    """
    import json as _json

    scratch_url, _engine = migrated_db

    engine = create_async_engine(scratch_url)
    try:
        # one connection: the probe table is TEMP (per-connection), and
        # each probe runs in its OWN transaction — a rejected INSERT
        # aborts the transaction, and conn.begin() rolls it back
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("CREATE TEMP TABLE t747_c0_probe (t TEXT, v VARCHAR(50), j JSONB)")
                )
            for code in range(0x00, 0x20):
                ch = chr(code)
                for col, value in (
                    ("t", ch),  # raw str param, the app's TEXT shape
                    ("v", ch),
                    # JSONB production shape: a JSON-escaped string
                    # (json.dumps escapes every control byte as \u00XX)
                    ("j", _json.dumps({"v": ch})),
                ):
                    try:
                        async with conn.begin():
                            await conn.execute(
                                text(f"INSERT INTO t747_c0_probe ({col}) VALUES (:x)"),
                                {"x": value},
                            )
                        rejected = False
                    except Exception:
                        rejected = True
                    if code == 0x00:
                        assert rejected, f"0x00 must be rejected in {col}"
                    else:
                        assert not rejected, (
                            f"0x{code:02x} unexpectedly rejected in {col} — "
                            "the ADR-0020 \x00-only scope would need revisiting"
                        )
    finally:
        await engine.dispose()


DOCUMENT = (
    "Отчёт о состоянии кластера. "
    "Ключевой параметр зафиксирован на 42 узлах. "
    "Вторичный параметр: 7 узлов с отклонением. "
    "Рекомендация: пересмотреть топологию сети в третьем квартале."
)


async def test_nul_in_extraction_is_stored_as_marker(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """The quote must be verbatim in the (host-masked) document, so the
    only free model text in an extraction record is the chunk NOTE — the
    NUL goes there."""
    scratch_url, engine = migrated_db
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["extraction"]["mode"] = "llm"
    payload["extraction"]["min_document_bytes"] = 100
    result = await _run_online(engine, payload)
    assert result.state == "active"
    qid = await _seed_question(scratch_url)

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "doc.txt").write_text(DOCUMENT, encoding="utf-8")

    extraction = {
        "public_rationale": "Извлечены ключевые факты",
        "chunks": [
            {
                "quote": "Ключевой параметр зафиксирован на 42 узлах.",
                "note": "Ключевой факт отчёта\x00",
            }
        ],
    }
    tool_read: dict[str, Any] = {
        "public_rationale": "Прочитать документ",
        "expected_information": "Ключевые факты из отчёта",
        "decision": {"kind": "tool", "tool": "workspace.read", "arguments": {"path": "doc.txt"}},
    }
    curator: dict[str, Any] = {
        "summary": "Одно утверждение",
        "claims": [
            {
                "statement": "Ключевой параметр зафиксирован на 42 узлах",
                "claim_type": "document_assertion",
                "scope": {"source": "doc.txt"},
            }
        ],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        "new_questions": [],
    }
    fake_llm.script(
        [
            {"content": tool_read},
            {"content": extraction},
            {"content": COMPLETE},
            {"content": curator},
        ]
    )
    orch, gateway, orch_engine = _orch(scratch_url, fake_llm, tmp_path)
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await orch_engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    row = await _scalar(
        scratch_url,
        "SELECT extraction, extraction_sha256 FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    assert row is not None
    extraction_doc, extraction_sha = row
    assert extraction_doc is not None, (
        "the extraction was not persisted (fallback?) — check extraction_fallback audit"
    )
    assert extraction_doc["records"][0]["chunks"][0]["note"] == f"Ключевой факт отчёта{NUL_MARKER}"
    assert canonical_sha256(extraction_doc) == extraction_sha
