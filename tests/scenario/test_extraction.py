"""Scenario: the untrusted extraction profile (T5.5, stage 4, §11.2).

- with extraction mode "llm" a workspace.read of a document above the
  high-risk threshold is first passed to the extractor (a model
  without tools); the explorer then receives ONLY the extracted
  chunks with host-computed provenance — the raw content never
  reaches the explorer's context (asserted on the actual request
  log of the fake LLM server);
- the extraction is persisted on the session (sessions.extraction +
  canonical sha256) and audited (extraction_completed);
- a non-verbatim / over-budget / invalid report falls back to the
  raw read (MVP behavior, audited — never a session failure);
- off mode and small documents bypass the profile entirely.
"""

from __future__ import annotations

import copy
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

# the marker is INSIDE a quoted chunk: it may reach the explorer
# (that is the extracted content). The no-leak string is in the
# document but in NO quoted chunk: it must never leave the extractor.
SECRET_MARKER = "SEKRET-ALPHA-7742"
NOLEAK = "список узлов доступен по запросу у оператора смены"
DOCUMENT = (
    "Отчёт о состоянии кластера. "
    f"Ключевой параметр: {SECRET_MARKER} зафиксирован на 42 узлах. "
    "Вторичный параметр: 7 узлов с отклонением. "
    "Рекомендация: пересмотреть топологию сети в третьем квартале. "
    f"Приложение: {NOLEAK}."
)

COMPLETE: dict[str, Any] = {
    "public_rationale": "Документ изучен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
TOOL_READ: dict[str, Any] = {
    "public_rationale": "Прочитать документ",
    "expected_information": "Ключевые факты из отчёта",
    "decision": {"kind": "tool", "tool": "workspace.read", "arguments": {"path": "doc.txt"}},
}
CURATOR_OK: dict[str, Any] = {
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


def _extraction_report(quotes: list[str] | None = None) -> dict[str, Any]:
    if quotes is None:
        quotes = [
            f"Ключевой параметр: {SECRET_MARKER} зафиксирован на 42 узлах.",
            "Рекомендация: пересмотреть топологию сети в третьем квартале.",
        ]
    return {
        "public_rationale": "Извлечены ключевые факты",
        "chunks": [
            {"quote": q, "note": "факт"} for q in quotes
        ],
    }


def _extraction_section(**overrides: Any) -> dict[str, Any]:
    section = copy.deepcopy(BOOTSTRAP_PAYLOAD["extraction"])
    section.update(overrides)
    return section


async def _enable_extraction(engine: Any, **overrides: Any) -> None:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    section = _extraction_section()
    section["mode"] = "llm"
    section.update(overrides)
    if "min_document_bytes" not in overrides:
        section["min_document_bytes"] = 100
    payload["extraction"] = section
    result = await _run_online(engine, payload)
    assert result.state == "active"


async def _seed_question(scratch_url: str, text_: str) -> None:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await QuestionRepository.create(
                db, ORMQuestion(text=text_, origin=QuestionOrigin.SEEDED.value)
            )
    finally:
        await engine.dispose()


async def _scalar(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _run_session(
    fake: FakeLLM,
    script: list[dict[str, Any]],
    tmp_path: Any,
    scratch_url: str,
    write_doc: bool = True,
) -> tuple[Any, list[dict[str, Any]]]:
    workspace = tmp_path / "ws"
    if write_doc:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "doc.txt").write_text(DOCUMENT, encoding="utf-8")
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
    try:
        fake.script(script)
        outcome = await orch.run_session(None)
    finally:
        await gateway.close()
        await engine.dispose()
    return outcome, fake.requests()


async def test_extraction_replaces_raw_content_in_explorer_context(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Gate: the raw untrusted document never reaches the main
    explorer — only the extracted chunks with host provenance do."""
    scratch_url, engine = migrated_db
    await _enable_extraction(engine)
    await _seed_question(scratch_url, "Что зафиксировано в отчёте о кластере?")

    outcome, requests = await _run_session(
        fake_llm,
        [
            {"content": TOOL_READ},
            {"content": _extraction_report()},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED

    # the raw document is in the extractor's input (it must see the
    # document) but NOT in any explorer/curator input: the no-leak
    # string (present in the document, absent from the quotes) must
    # never leave the extractor
    extractor_reqs = [r for r in requests if "<<<UNTRUSTED DATA BEGIN>>>" in r["last_user"]]
    assert len(extractor_reqs) == 1
    assert all(NOLEAK in r["last_user"] for r in extractor_reqs)
    other_reqs = [r for r in requests if r not in extractor_reqs]
    for req in other_reqs:
        assert NOLEAK not in req["last_user"], "raw content leaked into the explorer/curator context"

    # the extracted chunks ARE in the explorer's second request
    explorer_after_read = [r for r in requests if "42 узлах" in r["last_user"]]
    assert len(explorer_after_read) >= 1

    # persisted with the host provenance
    row = await _scalar(
        scratch_url,
        "SELECT extraction, extraction_sha256 FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    doc, doc_sha = row[0], row[1]
    assert doc is not None
    assert canonical_sha256(doc) == doc_sha
    records = doc["records"]
    assert len(records) == 1
    assert records[0]["path"] == "doc.txt"
    assert records[0]["document_sha256"]
    assert len(records[0]["chunks"]) == 2
    for chunk in records[0]["chunks"]:
        assert "quote_sha256" in chunk
    assert DOCUMENT not in str(doc)  # raw content is never stored

    # audited
    audit_row = await _scalar(
        scratch_url,
        "SELECT payload->'chunks', payload->'path' FROM audit_events "
        "WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.EXTRACTION_COMPLETED.value, "s": str(outcome.session_id)},
    )
    assert audit_row is not None
    assert audit_row[0] == 2
    assert audit_row[1] == "doc.txt"


async def test_non_verbatim_quote_falls_back_to_raw_read(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    await _enable_extraction(engine)
    await _seed_question(scratch_url, "Что зафиксировано в отчёте о кластере?")

    bad = _extraction_report(quotes=["параметр был зафиксирован на сорока двух узлах"])  # paraphrase
    outcome, requests = await _run_session(
        fake_llm,
        [
            {"content": TOOL_READ},
            {"content": bad},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED  # fallback, not failure
    # the raw read was served to the explorer (MVP behavior)
    assert any(SECRET_MARKER in r["last_user"] for r in requests)
    row = await _scalar(
        scratch_url,
        "SELECT extraction FROM sessions WHERE id = :id",
        {"id": str(outcome.session_id)},
    )
    assert row[0] is None
    fb = await _scalar(
        scratch_url,
        "SELECT payload->>'reason' FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.EXTRACTION_FALLBACK.value, "s": str(outcome.session_id)},
    )
    assert fb is not None and "verbatim" in fb[0]


async def test_overbudget_report_falls_back(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    await _enable_extraction(engine, max_chunks=1)
    await _seed_question(scratch_url, "Что зафиксировано в отчёте о кластере?")

    over = _extraction_report(quotes=[
        f"Ключевой параметр: {SECRET_MARKER} зафиксирован на 42 узлах.",
        "Вторичный параметр: 7 узлов с отклонением.",
    ])
    outcome, _requests = await _run_session(
        fake_llm,
        [
            {"content": TOOL_READ},
            {"content": over},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    fb = await _scalar(
        scratch_url,
        "SELECT payload->>'reason' FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.EXTRACTION_FALLBACK.value, "s": str(outcome.session_id)},
    )
    assert fb is not None and "max_chunks" in fb[0]


async def test_off_mode_and_small_document_bypass_profile(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Bootstrap default (mode=off): the raw read is served as-is and
    no extractor call happens at all."""
    scratch_url, engine = migrated_db
    await _seed_question(scratch_url, "Что зафиксировано в отчёте о кластере?")

    outcome, requests = await _run_session(
        fake_llm,
        [
            {"content": TOOL_READ},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    assert not any("<<<UNTRUSTED DATA BEGIN>>>" in r["last_user"] for r in requests)
    assert any(SECRET_MARKER in r["last_user"] for r in requests)  # raw read as-is
    row = await _scalar(
        scratch_url,
        "SELECT count(*) FROM model_runs WHERE session_id = :s",
        {"s": str(outcome.session_id)},
    )
    # model runs = explorer read decision + curator; the complete
    # decision exits the loop and is not a separate recorded run
    # (the extraction profile is off: no extractor call)
    assert row[0] == 2

    # a small document bypasses the profile even in llm mode
    await _enable_extraction(engine, min_document_bytes=10_000)
    small_workspace = tmp_path / "ws_small"
    small_workspace.mkdir(parents=True, exist_ok=True)
    (small_workspace / "doc.txt").write_text("маленький документ", encoding="utf-8")
    await _seed_question(scratch_url, "Что в маленьком документе?")
    engine2 = create_async_engine(scratch_url)
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)
    gateway2 = LLMMiddleware(
        LLMGatewayConfig(base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    orch2 = Orchestrator(
        session_factory=factory2,
        gateway=gateway2,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(small_workspace),
    )
    try:
        fake_llm.script([{"content": TOOL_READ}, {"content": COMPLETE}, {"content": CURATOR_OK}])
        outcome2 = await orch2.run_session(None)
        reqs2 = fake_llm.requests()
    finally:
        await gateway2.close()
        await engine2.dispose()
    assert outcome2.final_state is SessionState.SUCCEEDED
    assert not any("<<<UNTRUSTED DATA BEGIN>>>" in r["last_user"] for r in reqs2)
