"""Scenario: host-tracked coverage of the sources NAMED IN THE QUESTION
(T7.21, EVAL-3d post-mortem, §3.7, §5.4, §11.2, ADR-0010).

The EVAL-3d finding (ADR-0010): when the question names two sources, the
session must fetch (or get a definitive egress error for) EACH of them
before the host releases it to consolidation — the grade-relevant input
is produced by the trusted host (§3.7/§11.2), not the model's
`complete` decision. The mechanism under test:

- the host extracts the named URLs (``extract_question_urls``, T7.17)
  and tracks per named source: pending → fetched | errored;
- a `complete` while the coverage is incomplete is REJECTED by the host
  (audited; the model gets a host observation and another step);
- the step budget has a host floor of `len(named) + 3`, and a
  budget-exhausted session records the uncovered sources in the report
  audit (fail-closed and explainable);
- the explorer prompt carries the same rule as a backstop only (the
  gate is host-side and does not depend on the model's compliance).

The network is the faked part (FakeFetchClient, the same approach as
test_research_evidence.py); everything else — the orchestrator, the
proxy, the rules engine — is the real code.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.fetch import FetchError, FetchResult
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import FakeFetchClient, _section

pytestmark = [pytest.mark.scenario]

URL_A = "http://alpha.example/apollo"
URL_B = "http://beta.example/landing"

QUESTION_TWO_URLS = (
    "Подтверди первую пилотируемую посадку на Луну строго по этим "
    "двум источникам: " + URL_A + " и " + URL_B
)
QUESTION_ONE_URL = (
    "Подтверди первую пилотируемую посадку на Луну по этому "
    "источнику: " + URL_A
)
QUESTION_NO_URL = "Вычисли: сколько будет 2 в степени 5?"


# ── plumbing (the same shape as test_scope_coverage.py) ────────────────


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch the research-proxy network layer with
    ``FakeFetchClient`` (the same approach as
    ``tests/scenario/test_research_evidence.py``): the only faked part
    is the HTTP client, the rest of the proxy path is real."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)


def _fetch_response(url: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": "Скачиваю источник, названный вопросом",
            "expected_information": "Текст страницы",
            "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": url}},
        }
    }


def _tool_response(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": "Шаг исследования",
            "expected_information": "Результат шага",
            "decision": {"kind": "tool", "tool": tool, "arguments": arguments},
        }
    }


def _complete_response(reason: str = "goal_reached") -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": "Исследование завершено",
            "decision": {"kind": "complete", "reason": reason},
        }
    }


def _curator_response(
    statement: str | None, links: list[dict[str, Any]]
) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    if statement is not None:
        claims.append(
            {
                "statement": statement,
                "claim_type": "external_fact",
                "scope": {"объект": "Аполлон-11"},
            }
        )
    return {
        "content": {
            "summary": "Итог сессии",
            "claims": claims,
            "evidence_links": links,
            "new_questions": [],
        }
    }


async def _scalar(scratch_url: str, sql: str, params: dict | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _all(scratch_url: str, sql: str, params: dict | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).mappings().all())
    finally:
        await engine.dispose()


async def _set_section(scratch_url: str, section: dict) -> None:
    import json

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await db.execute(
                text("UPDATE config_snapshots SET research_proxy = CAST(:sec AS jsonb)"),
                {"sec": json.dumps(section)},
            )
            await db.commit()
    finally:
        await engine.dispose()


async def _seed_question(scratch_url: str, text_: str) -> Any:
    from packages.domain.db.uow import transaction
    from packages.domain.models.questions import ORMQuestion
    from packages.domain.repositories.questions import QuestionRepository

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(
                db, ORMQuestion(text=text_, origin="seeded", priority=1)
            )
            return q.id
    finally:
        await engine.dispose()


async def _run_session(
    scratch_url: str,
    engine: AsyncEngine,
    fake_llm: FakeLLM,
    tmp_path: Path,
    script: list[dict[str, Any]],
    *,
    question_text: str,
    payload_overrides: dict[str, Any] | None = None,
) -> Any:
    """Activate the curated profile, seed the question, script the fake
    LLM and run ONE full session through the real orchestrator."""
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = "http://127.0.0.1:8888"  # unused: the fetch is faked
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], "research.fetch"]
    for key, value in (payload_overrides or {}).items():
        payload[key] = value
    result = await _run_online(engine, payload)
    assert result.state == "active"

    await _set_section(scratch_url, _section())
    question_id = await _seed_question(scratch_url, question_text)

    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=service,
    )
    fake_llm.script(script)
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
    return outcome


async def _complete_rejections(scratch_url: str) -> list[Any]:
    return await _all(
        scratch_url,
        "SELECT payload FROM audit_events "
        "WHERE type = 'session_state_changed' AND payload->>'complete_rejected' = 'true'",
    )


async def _report_coverage(scratch_url: str) -> Any:
    """The source_coverage block of the session's report audit (the
    last report event — one session per scratch DB here)."""
    row = await _scalar(
        scratch_url,
        "SELECT payload->'source_coverage' FROM audit_events "
        "WHERE type = 'session_state_changed' AND payload->'source_coverage' IS NOT NULL "
        "ORDER BY sequence DESC LIMIT 1",
    )
    return row[0] if row is not None else None


# ── 1. the required scenario: complete blocked until the 2nd fetch ────


@pytest.mark.asyncio
async def test_complete_withheld_until_both_named_sources_fetched(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """Question names two URLs; the model fetches the first and tries
    to CONSOLIDATE. The host gate withholds the release (audited
    `complete_rejected`), the model fetches the second source, the
    second `complete` passes; the curator links BOTH source_assertions
    to one claim and the rules engine grades it E3 supported from two
    independent groups."""
    scratch_url, engine = migrated_db
    script = [
        _fetch_response(URL_A),
        _complete_response(),  # → REJECTED by the host gate (coverage 1/2)
        _fetch_response(URL_B),
        _complete_response(),  # → passes (coverage 2/2)
        _curator_response(
            "Аполлон-11 совершил первую пилотируемую посадку на Луну 20 июля 1969 года",
            [
                {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                {"evidence_index": 1, "claim_index": 0, "relation": "supports"},
            ],
        ),
    ]
    outcome = await _run_session(
        scratch_url, engine, fake_llm, tmp_path, script, question_text=QUESTION_TWO_URLS
    )

    assert outcome.final_state.value == "succeeded"
    assert outcome.evidence_count == 2, "both fetches must produce evidence records"

    # exactly ONE rejected completion — the first complete (1/2 covered)
    rejections = await _complete_rejections(scratch_url)
    assert len(rejections) == 1, f"expected exactly one host rejection, got {len(rejections)}"
    rejected = rejections[0]["payload"]
    assert rejected["reason"] == "source_coverage_incomplete"
    assert rejected["coverage"]["uncovered"] == [URL_B]
    assert rejected["coverage"]["fetched"] == [URL_A]

    # the claim: E3 supported from two distinct sources (the rules
    # engine is the only grade producer)
    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status, "
        "       count(DISTINCT e.source_id) AS srcs "
        "FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "LEFT JOIN evidence e ON e.claim_id = c.id "
        "WHERE c.statement LIKE 'Аполлон-11 совершил%' "
        "GROUP BY a.effective_grade, a.epistemic_status",
    )
    assert row is not None, "no assessment for the external claim"
    assert row[0] == "E3", f"expected E3, got {row[0]}"
    assert row[1] == "supported"
    assert row[2] == 2, "both evidence rows must reference two distinct sources"

    # the report audit records the complete coverage (explainable run)
    coverage = await _report_coverage(scratch_url)
    assert coverage is not None
    assert coverage["uncovered"] == []
    assert sorted(coverage["fetched"]) == sorted([URL_A, URL_B])


# ── 2. regression: a single named source must NOT be over-blocked ─────


@pytest.mark.asyncio
async def test_single_url_question_not_blocked(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """The question names ONE URL: the model fetches it and completes —
    the first `complete` passes (no rejection), the session succeeds.
    The claim stays E1 (one source, one group — the rules engine never
    fakes independence); the gate must not manufacture a second fetch."""
    scratch_url, engine = migrated_db
    script = [
        _fetch_response(URL_A),
        _complete_response(),  # → passes on the FIRST try
        _curator_response(
            "Аполлон-11 посадка (один источник)",
            [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        ),
    ]
    outcome = await _run_session(
        scratch_url, engine, fake_llm, tmp_path, script, question_text=QUESTION_ONE_URL
    )

    assert outcome.final_state.value == "succeeded"
    assert outcome.evidence_count == 1
    assert await _complete_rejections(scratch_url) == [], (
        "a single named source must not be over-blocked"
    )

    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 посадка (один источник)'",
    )
    assert row is not None
    assert row[0] == "E1", f"one source cannot reach E3, got {row[0]}"
    assert row[1] == "hypothesis"


# ── 3. regression: a question without named URLs is unaffected ────────


@pytest.mark.asyncio
async def test_no_url_question_not_blocked(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """A question that names no URLs: the coverage tracker does not
    exist, `complete` passes on the first try, no coverage audits."""
    scratch_url, engine = migrated_db
    script = [
        _tool_response(
            "python.execute", {"code": "print(2 ** 5)"}
        ),
        _complete_response(),  # → passes on the first try
        _curator_response(None, []),
    ]
    outcome = await _run_session(
        scratch_url, engine, fake_llm, tmp_path, script, question_text=QUESTION_NO_URL
    )

    assert outcome.final_state.value == "succeeded"
    assert await _complete_rejections(scratch_url) == []
    assert await _report_coverage(scratch_url) is None, (
        "no coverage tracking for a question that names no sources"
    )
    named = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events "
        "WHERE type = 'session_state_changed' AND payload->'named_sources' IS NOT NULL",
    )
    assert named[0] == 0


# ── 4. the budget case: fail-closed and explainable ────────────────────


@pytest.mark.asyncio
async def test_budget_floor_and_fail_closed_report(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """The configured budget (2 steps) is SMALLER than the named-source
    count (2 URLs) — the host raises the budget to the floor
    `len(named) + 3 = 5` (the budget accounts for the named sources).
    The model never fetches the named sources (it probes memory
    instead) and never completes: the loop exhausts the floor and the
    session ends budget-exhausted / succeeded_partial — FAIL-CLOSED —
    with the uncovered sources recorded in the report audit
    (explainable, not a silent gap)."""
    scratch_url, engine = migrated_db
    script = [
        _tool_response("memory.search", {"query": "посадка на Луну"}),
        _tool_response("memory.search", {"query": "Аполлон-11"}),
        _tool_response("memory.search", {"query": "Луна 1969"}),
        _tool_response("memory.search", {"query": "лунная программа"}),
        _tool_response("memory.search", {"query": "космос"}),  # step 5 = the floor
        _curator_response(None, []),
    ]
    outcome = await _run_session(
        scratch_url,
        engine,
        fake_llm,
        tmp_path,
        script,
        question_text=QUESTION_TWO_URLS,
        payload_overrides={"session_limits": {**BOOTSTRAP_PAYLOAD["session_limits"], "max_explorer_steps": 2}},
    )

    assert outcome.final_state.value == "succeeded_partial", (
        f"budget exhaustion must end the session partial, got {outcome.final_state}"
    )
    assert outcome.termination_reason == "budget_exhausted"
    assert outcome.steps == 5, (
        f"the host floor len(named)+3=5 must lift the configured 2, got {outcome.steps}"
    )
    assert await _complete_rejections(scratch_url) == []  # the model never completed

    # the report audit carries the coverage state: BOTH sources uncovered
    coverage = await _report_coverage(scratch_url)
    assert coverage is not None, "the report must record the coverage state"
    assert coverage["named"] == [URL_A, URL_B]
    assert coverage["uncovered"] == [URL_A, URL_B], (
        "a budget-exhausted session that never fetched the named sources "
        "must be explainable from the audit"
    )
    assert coverage["fetched"] == [] and coverage["errored"] == []


# ── 5. an errored named source counts as covered ───────────────────────


class FailingBetaFetchClient(FakeFetchClient):
    """The beta.example egress times out (a definitive egress answer)."""

    async def fetch(self, url: str) -> FetchResult:
        if "beta.example" in url:
            raise FetchError("timeout: simulated egress failure")
        return await super().fetch(url)


@pytest.mark.asyncio
async def test_errored_named_source_covers_and_claim_stays_e1(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The second named source's fetch ERRORS (timeout): the host marks
    it covered (the egress answered) and the `complete` PASSES — no
    retry spiral, no infinite gate. The claim gets ONE source_assertion
    and the rules engine grades it E1 (it never fakes the missing
    independent group): the honest outcome of a dead source."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FailingBetaFetchClient)

    scratch_url, engine = migrated_db
    script = [
        _fetch_response(URL_A),
        _fetch_response(URL_B),  # → fails (timeout) → covered as errored
        _complete_response(),  # → passes (fetched 1 + errored 1 = 2/2)
        _curator_response(
            "Аполлон-11 посадка (второй источник упал)",
            [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        ),
    ]
    outcome = await _run_session(
        scratch_url, engine, fake_llm, tmp_path, script, question_text=QUESTION_TWO_URLS
    )

    assert outcome.final_state.value == "succeeded"
    assert outcome.evidence_count == 1, "only the successful fetch produces evidence"
    assert await _complete_rejections(scratch_url) == [], (
        "an errored named source must not block the completion"
    )

    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id "
        "WHERE c.statement LIKE 'Аполлон-11 посадка (второй источник упал)'",
    )
    assert row is not None
    assert row[0] == "E1", f"one live source cannot reach E3, got {row[0]}"
    assert row[1] == "hypothesis"

    coverage = await _report_coverage(scratch_url)
    assert coverage is not None
    assert coverage["fetched"] == [URL_A]
    assert coverage["errored"] == [URL_B]
    assert coverage["uncovered"] == []
