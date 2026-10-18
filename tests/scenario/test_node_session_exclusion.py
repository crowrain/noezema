"""Scenario: one node must not run two sessions at once (T7.61(а), §5.2.1).

Reproducer of the dev-stand race, reproduced from the stand's own sequence: the operator pressed
«wake now» while a scheduled `noezema-dev-tick.service` (a separate systemd process) fired 36 s
later. Both started a session on the same node and both asked the same question.

The mechanism, in the order the code executes it:

1. `hostctl wake-tick` calls :meth:`WakeScheduler.decide` (`apps/orchestrator/scheduler.py`): its
   session-lane check is `SELECT id FROM sessions WHERE state NOT IN (<terminal>)` — it only sees
   COMMITTED rows.
2. The web's «wake now» started earlier and is inside `run_session`: phase 1
   (`apps/orchestrator/orchestrator.py`) creates its `sessions` row in a transaction that stays open
   until the session reaches COMMITTING, so at the moment of the tick's admission that row does not
   exist for any other connection. The web marker `node_state='session_running'` is written on the
   web side, and `_admission` honours only `paused`.
3. So the tick is admitted and starts a second session → «одна сессия на узел» broken.

The test drives BOTH entry points through their real code: the web one via the stand's own entry
(`build_standalone_app`) and the tick as a real subprocess (`python -m hostctl.cli wake-tick`), i.e.
as two processes with their own connections to one DB — exactly the stand's shape. The overlap window
is created by a slow scripted model answer (no wall-clock assertions in the test itself).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.web.api import build_standalone_app
from hostctl.unit_state import publish_unit_state
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import QuestionOrigin
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from tests.conftest import REPO_ROOT, FakeLLM

pytestmark = [pytest.mark.scenario]

TOOL_PYTHON: JsonDict = {
    "public_rationale": "Проверить вычисление",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(6*7)"}},
}
COMPLETE: JsonDict = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK: JsonDict = {
    "summary": "Одно утверждение",
    "claims": [{"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}

# The first answer of the session is deliberately slow: it keeps the web session inside phase 1 for
# the whole time the tick needs to run its admission. It is a window, never an assertion.
SESSION_A_SCRIPT: list[JsonDict] = [
    {"content": TOOL_PYTHON, "delay_seconds": 6.0},
    {"content": COMPLETE},
    {"content": CURATOR_OK},
]


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@asynccontextmanager
async def _own_session(scratch_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


async def _seed_question(scratch_url: str, qtext: str) -> None:
    async with _own_session(scratch_url) as db, transaction(db):
        await QuestionRepository.create(db, ORMQuestion(text=qtext, origin=QuestionOrigin.SEEDED.value))


async def _session_rows(scratch_url: str) -> tuple[int, int]:
    """(total session rows, nonterminal session rows) as another connection sees them."""
    async with _own_session(scratch_url) as db:
        total = (await db.execute(text("SELECT count(*) FROM sessions"))).scalar_one()
        nonterminal = (
            await db.execute(
                text(
                    "SELECT count(*) FROM sessions "
                    "WHERE state NOT IN ('succeeded','succeeded_partial','failed','cancelled')"
                )
            )
        ).scalar_one()
    return int(total), int(nonterminal)


def _run_wake_tick(scratch_url: str, data_root: Path, fake_llm: FakeLLM) -> subprocess.CompletedProcess[str]:
    """`hostctl wake-tick` as the stand runs it: a separate process, its own connections."""
    env = dict(os.environ)
    env.update(
        {
            "NOEZEMA_DATABASE_URL": scratch_url,
            "NOEZEMA_DATA_ROOT": str(data_root),
            "NOEZEMA_TOOL_EXECUTOR": "stub",
            "NOEZEMA_LLM_BASE_URL": fake_llm.base_url,
            "NOEZEMA_LLM_MODEL": "fake-thinker",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "hostctl.cli", "wake-tick"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.asyncio
async def test_scheduled_tick_cannot_start_a_second_session_next_to_web_wake(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch_url, _fixture_engine = migrated_db
    data_root = tmp_path / "var-lib-noezema-dev"
    host_lib = data_root / "host"
    unit_state = host_lib / "unit-state.json"
    host_lib.mkdir(parents=True)

    monkeypatch.setenv("NOEZEMA_DATABASE_URL", scratch_url)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(data_root))
    monkeypatch.setenv("NOEZEMA_HOST_LIB", str(host_lib))
    monkeypatch.setenv("NOEZEMA_UNIT_STATE", str(unit_state))
    monkeypatch.setenv("NOEZEMA_LLM_BASE_URL", fake_llm.base_url)
    monkeypatch.setenv("NOEZEMA_LLM_MODEL", "fake-thinker")
    monkeypatch.setenv("NOEZEMA_TOOL_EXECUTOR", "stub")
    monkeypatch.delenv("NOEZEMA_ADMIN_TOKEN", raising=False)

    await _seed_question(scratch_url, "Сколько будет 6*7?")
    fake_llm.script(SESSION_A_SCRIPT)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})

    app = build_standalone_app()
    try:
        async with app.router.lifespan_context(app):
            assert app.state.orchestrator is not None
            async with _client(app) as client:
                r = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "w-a"})
                assert r.status_code == 202, r.text
                assert r.json()["state"] == "completed", r.json()

                # The web session is inside phase 1: the model call has been made...
                for _ in range(200):
                    if len(fake_llm.requests()) >= 1:
                        break
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("сессия веб-wake не дошла до модели")

                # ...and its session row is still invisible to every other connection. This is the
                # window in which the scheduled tick evaluates its admission.
                total, nonterminal = await _session_rows(scratch_url)
                assert nonterminal == 0, (
                    "фаза 1 должна держать строку сессии невидимой до COMMITTING — иначе сам репродюсер"
                    f" теряет смысл: sessions={total}, nonterminal={nonterminal}"
                )

                tick = _run_wake_tick(scratch_url, data_root, fake_llm)
                assert "wake-tick: skip (session_in_progress)" in tick.stdout, (
                    "запланированный тик не должен стартовать вторую сессию на узле, у которого уже "
                    f"идёт session: stdout={tick.stdout!r} stderr={tick.stderr!r} code={tick.returncode}"
                )
                assert tick.returncode == 0, (
                    "skip — не ошибка: тик обязан выйти 0 (как wait/skip по другим причинам): "
                    f"code={tick.returncode} stdout={tick.stdout!r}"
                )

                # The invariant the stand broke: one session on one node.
                total, nonterminal = await _session_rows(scratch_url)
                assert total <= 1, f"вторая сессия всё равно создана: sessions={total}, tick={tick.stdout!r}"

                # and the node is not wedged by it: after the web session finishes, wake works again
                data: JsonDict = {}
                for _ in range(300):
                    data = (await client.get("/api/v1/status")).json()
                    if data["node_state"] == "idle" and data["session"] is None:
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail(f"сессия веб-wake не завершилась: {data}")

                fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
                r2 = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "w-b"})
                assert r2.status_code == 202, r2.text
                assert r2.json()["state"] == "completed", (
                    f"скип тика не должен наследовать блокировку узла: {r2.json()}"
                )
                for _ in range(300):
                    data = (await client.get("/api/v1/status")).json()
                    if data["node_state"] == "idle" and data["session"] is None:
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail(f"вторая сессия не завершилась: {data}")

                total, _ = await _session_rows(scratch_url)
                assert total == 2, f"ожидались ровно две последовательные сессии: sessions={total}"
    finally:
        await app.state.engine.dispose()
