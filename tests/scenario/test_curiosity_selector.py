"""Scenario: curiosity ranking end-to-end (T5.1, §5.3.1).

- the score-based selector beats FIFO (a rephrased/known question that
  FIFO would pick first loses to a genuinely novel one);
- the normalized score inputs + the similarity fingerprint are stored
  on the selected question and in the QUESTION_SELECTED audit;
- the selector is config-driven: the effective snapshot's
  ``curiosity.selector`` switch turns it on (an online config change,
  not a code change);
- ε-exploration stays inside the top-M / δ pool (never the whole
  registry) and is reproducible from the recorded seed.
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
from packages.cognition.curiosity import CuriosityQuestionSelector
from packages.cognition.question_selector import FIFOQuestionSelector
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType, QuestionOrigin, SessionState
from packages.domain.models.memory import ORMClaim
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]


async def _seed(scratch_url: str, rows: list[ORMQuestion]) -> list[uuid.UUID]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            ids = []
            for row in rows:
                q = await QuestionRepository.create(db, row)
                ids.append(q.id)
            return ids
    finally:
        await engine.dispose()


async def _seed_claim(scratch_url: str, statement: str) -> None:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            db.add(ORMClaim(id=uuid.uuid4(), statement=statement, claim_type="external_fact"))
            await db.flush()
    finally:
        await engine.dispose()


async def _row(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _question_row(
    scratch_url: str, question_id: uuid.UUID
) -> tuple[str, dict[str, Any] | None, str | None]:
    row = await _row(
        scratch_url,
        "SELECT state, score_components, embedding_fingerprint FROM questions WHERE id=:id",
        {"id": str(question_id)},
    )
    assert row is not None
    return row[0], row[1], row[2]


def _curiosity_section(**overrides: Any) -> dict[str, Any]:
    section = copy.deepcopy(BOOTSTRAP_PAYLOAD["curiosity"])
    section["selector"] = "curiosity"
    section.update(overrides)
    return section


async def test_ranking_beats_fifo_and_persists_score(
    migrated_db: tuple[str, Any]
) -> None:
    scratch_url, _engine = migrated_db
    # existing knowledge: a claim about the rain
    await _seed_claim(scratch_url, "Дождь в этом регионе идёт чаще всего летом из-за конвекции")
    # q_dup: created FIRST (the FIFO head) but a rephrase of the claim
    # topic → its novelty is low; q_new: a genuinely fresh topic.
    q_dup, q_new = await _seed(
        scratch_url,
        [
            ORMQuestion(
                text="Почему дождь в регионе идёт чаще летом из-за конвекции?",
                origin=QuestionOrigin.SEEDED.value,
                priority=1,
            ),
            ORMQuestion(
                text="Как работает фотосинтез в хлоропластах растений?",
                origin=QuestionOrigin.SEEDED.value,
            ),
        ],
    )

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # FIFO would take the older q_dup...
        async with factory() as db:
            fifo_choice = await FIFOQuestionSelector().select(db)
        assert fifo_choice is not None and fifo_choice.id == q_dup

        # ...the curiosity selector takes the novel one instead
        session_id = uuid.uuid4()
        selector = CuriosityQuestionSelector(_curiosity_section())
        async with factory() as db, transaction(db):
            chosen, record = await selector.select(db, session_id=session_id)
            assert chosen is not None and record is not None
            assert chosen.id == q_new
            # all components are normalized to [0, 1]
            for qid in (q_dup, q_new):
                for value in record["components"][qid].values():
                    assert 0.0 <= value <= 1.0
            assert record["components"][q_dup]["novelty"] < 1.0  # the rephrase penalty
            assert record["components"][q_new]["novelty"] == 1.0
            assert record["score"][q_new] > record["score"][q_dup]
            assert set(record["candidates_considered"]) == {str(q_dup), str(q_new)}
            CuriosityQuestionSelector.persist(db, chosen, record, selector.config.similarity_fingerprint)

        state, components, fingerprint = await _question_row(scratch_url, q_new)
        assert state == "candidate"  # selection does not change the state
        assert components is not None
        assert components["fingerprint"] == "token-jaccard-v1"
        assert fingerprint == "token-jaccard-v1"
        assert components["score"] == record["score"][q_new]
        assert components["selected"] == "argmax"
        assert components["rng_seed"] == record["rng_seed"]
        # the unselected question keeps an empty record
        _, dup_components, dup_fp = await _question_row(scratch_url, q_dup)
        assert dup_components is None and dup_fp is None
    finally:
        await engine.dispose()


async def test_epsilon_explore_stays_in_pool(migrated_db: tuple[str, Any]) -> None:
    scratch_url, _engine = migrated_db
    texts = [
        "Как устроены нейронные сети?",
        "Почему небо голубое?",
        "Как варить правильный бульон из костей?",
        "Что такое квантовая запутанность?",
    ]
    await _seed(
        scratch_url,
        [ORMQuestion(text=t, origin=QuestionOrigin.SEEDED.value) for t in texts],
    )
    section = _curiosity_section(epsilon=1.0, top_m=2, delta=0.0)  # pool = top 2 only
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            selector = CuriosityQuestionSelector(section)
            session_id = uuid.uuid4()
            chosen, record = await selector.select(db, session_id=session_id)
            assert chosen is not None and record is not None
            scores: dict[str, float] = {str(k): v for k, v in record["score"].items()}
            ranked = sorted(scores.items(), key=lambda kv: -kv[1])
            top_two = {ranked[0][0], ranked[1][0]}
            # ε = 1.0: the pick always comes from the top-M pool
            assert str(chosen.id) in top_two
            assert record["selected"] in ("argmax", "explore")
            # reproducible: the same (session, candidates) gives the same pick
            again, record2 = await selector.select(db, session_id=session_id)
            assert again is not None and again.id == chosen.id
            assert record2 is not None and record2["rng_seed"] == record["rng_seed"]
            # a different session id re-rolls the deterministic draw
            other, _ = await selector.select(db, session_id=uuid.uuid4())
            assert other is not None and str(other.id) in top_two
    finally:
        await engine.dispose()


TOOL_PYTHON: dict[str, Any] = {
    "public_rationale": "Проверить",
    "expected_information": "Результат",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(1)"}},
}
COMPLETE: dict[str, Any] = {
    "public_rationale": "Готово",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK: dict[str, Any] = {
    "summary": "Утверждение",
    "claims": [{"statement": "1 есть единица", "claim_type": "computed_result", "scope": {"expr": "1"}}],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


async def test_orchestrator_switches_selector_by_config(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    # the config switch: an online change enables the curiosity selector
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["curiosity"] = _curiosity_section()
    result = await _run_online(engine, payload)
    assert result.state == "active"

    await _seed_claim(scratch_url, "Дождь в этом регионе идёт чаще всего летом из-за конвекции")
    _q_dup, q_new = await _seed(
        scratch_url,
        [
            ORMQuestion(
                text="Почему дождь в регионе идёт чаще летом из-за конвекции?",
                origin=QuestionOrigin.SEEDED.value,
                priority=1,
            ),
            ORMQuestion(
                text="Как работает фотосинтез в хлоропластах растений?",
                origin=QuestionOrigin.SEEDED.value,
            ),
        ],
    )

    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
    )
    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    try:
        outcome = await orch.run_session(None)  # no explicit question → the selector decides
    finally:
        await gateway.close()

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.question_id == q_new  # NOT the FIFO head q_dup
    state, components, fingerprint = await _question_row(scratch_url, q_new)
    assert state == "verified"
    assert components is not None and fingerprint == "token-jaccard-v1"
    row = await _row(
        scratch_url,
        "SELECT payload->'curiosity'->>'score' FROM audit_events "
        "WHERE type = :t AND payload->>'question_id' = :q",
        {"t": AuditEventType.QUESTION_SELECTED.value, "q": str(q_new)},
    )
    assert row is not None and row[0] is not None  # the score is in the selection audit
