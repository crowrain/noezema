"""Scenario: a full orchestrator session on the REAL one-shot sandbox (T7.58).

The switch `NOEZEMA_TOOL_EXECUTOR=sandbox` is resolved by the same
`build_tool_executor` that eval-run and the wake tick use; the container is
opened and destroyed by `Orchestrator.run_session` itself (ADR-0023).

Docker is required: skipped without an engine, same fixtures/pattern as
tests/scenario/test_tool_broker_sandbox.py. The image is `noezema-sandbox:test`,
pre-built before pytest (AGENTS.md §6). No wall-clock-sensitive assertions → no
`timing` marker (these tests run in the parallel pool).
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.orchestrator.tool_executors import (
    SANDBOX_MODE,
    TOOL_EXECUTOR_ENV,
    SandboxSessionExecutor,
    ToolExecutorUnavailableError,
    build_tool_executor,
)
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import QuestionOrigin
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.policy.profiles import effective_profile
from packages.sandbox.runtime import ContainerSandboxRuntime
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

TOOL_PYTHON: JsonDict = {
    "public_rationale": "Проверить вычисление в контейнере",
    "expected_information": "Результат 6*7",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(6*7)"}},
}
TOOL_SHELL: JsonDict = {
    "public_rationale": "Проверить shell.execute в контейнере",
    "decision": {
        "kind": "tool",
        "tool": "shell.execute",
        "arguments": {"command": "echo sb-shell-ok"},
    },
}
TOOL_WRITE: JsonDict = {
    "public_rationale": "Записать вывод в overlay сессии",
    "decision": {
        "kind": "tool",
        "tool": "workspace.write",
        "arguments": {"path": "notes.md", "content": "6*7=42"},
    },
}
COMPLETE: JsonDict = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK: JsonDict = {
    "summary": "Одно утверждение",
    "claims": [
        {"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [{"text": "Почему 42?", "origin": "previous_result"}],
}


def _use_sandbox(monkeypatch: pytest.MonkeyPatch, image: str, work_root: Path) -> None:
    """The switch exactly as an operator sets it (EnvironmentFile / shell env)."""
    monkeypatch.setenv(TOOL_EXECUTOR_ENV, SANDBOX_MODE)
    monkeypatch.setenv("NOEZEMA_SANDBOX_IMAGE", image)
    monkeypatch.setenv("NOEZEMA_SANDBOX_WORK_ROOT", str(work_root))
    monkeypatch.delenv("NOEZEMA_SANDBOX_ENGINE", raising=False)


class _RecordingExecutor(SandboxSessionExecutor):
    """Test-only wrapper: remembers which containers the orchestrator opened."""

    def __init__(self, runtime: ContainerSandboxRuntime) -> None:
        super().__init__(runtime)
        self.opened: list[str] = []

    async def open_session(self, session_id: uuid.UUID, cap_profile: Any) -> None:
        await super().open_session(session_id, cap_profile)
        handle = self.handle
        assert handle is not None
        self.opened.append(handle.container_name)


async def _seed_question(scratch_url: str, question_text: str = "Сколько будет 6*7?") -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(
                db, ORMQuestion(text=question_text, origin=QuestionOrigin.SEEDED.value)
            )
            return q.id
    finally:
        await engine.dispose()


async def _scalar(scratch_url: str, sql: str, params: dict | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _rows(scratch_url: str, sql: str, params: dict | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).fetchall())
    finally:
        await engine.dispose()


def _containers(*names: str) -> list[str]:
    """Exact-name `docker ps -a` lookup. A prefix filter would also see other
    xdist workers' containers, so names are matched one by one."""
    found: list[str] = []
    for name in names:
        out = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            timeout=30,
            text=True,
        )
        found += [line for line in out.stdout.splitlines() if line == name]
    return found


def _orchestrator(
    scratch_url: str, fake: FakeLLM, executor: Any
) -> tuple[Orchestrator, LLMMiddleware, Any]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=executor,
    )
    return orch, gateway, engine


async def _actions(scratch_url: str, session_id: uuid.UUID) -> dict[str, tuple[str, str | None]]:
    rows = await _rows(
        scratch_url,
        "SELECT tool, state, error_code FROM actions WHERE session_id=:s",
        {"s": str(session_id)},
    )
    return {row[0]: (row[1], row[2]) for row in rows}


# ── the full session on a real container ────────────────────────────────────


async def test_full_session_on_real_sandbox(
    migrated_db, fake_llm: FakeLLM, sandbox_image: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch_url, _engine = migrated_db
    work_root = tmp_path / "sb"
    _use_sandbox(monkeypatch, sandbox_image, work_root)

    question_id = await _seed_question(scratch_url)
    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": TOOL_SHELL},  # not available in the stub mode (T7.57(b)) — real here
            {"content": TOOL_WRITE},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ]
    )

    executor = _RecordingExecutor(
        ContainerSandboxRuntime(image=sandbox_image, engine="docker", work_root=work_root)
    )
    orch, gateway, engine = _orchestrator(scratch_url, fake_llm, executor)
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state.value == "succeeded", outcome.termination_reason
    assert outcome.steps == 4
    # the observation rule is unchanged: only python.execute is evidence
    assert outcome.evidence_count == 1
    assert outcome.claims_proposed == 1

    # all three tools really executed inside the container (an infra failure
    # would have recorded failed / outcome_unknown actions instead)
    actions = await _actions(scratch_url, outcome.session_id)
    assert set(actions) == {"python.execute", "shell.execute", "workspace.write"}, actions
    assert all(state == "completed" for state, _ in actions.values()), actions

    # the container name follows the durable session id (one container per session)
    assert executor.opened == [f"noezema-sb-{str(outcome.session_id).replace('-', '')[:12]}"]

    # the writes landed in the per-session overlay and were frozen into the
    # committed workspace manifest (freeze uses executor.workspace_dir)
    entries = await _rows(
        scratch_url,
        "SELECT e.path, e.sha256, e.size FROM workspace_entries e "
        "JOIN workspace_manifests m ON m.id = e.manifest_id WHERE m.session_id=:s",
        {"s": str(outcome.session_id)},
    )
    assert [row[0] for row in entries] == ["notes.md"], entries
    manifest_id = await _scalar(
        scratch_url,
        "SELECT committed_workspace_manifest_id FROM sessions WHERE id=:id",
        {"id": str(outcome.session_id)},
    )
    assert manifest_id is not None

    # the shared HOST workspace was never used (this is what differs from the stub)
    assert not (tmp_path / "ws").exists()

    # nothing outlived the session: no container, no overlay directory
    assert _containers(*executor.opened) == []
    assert list(work_root.iterdir()) == [], f"sandbox work root not cleaned: {list(work_root.iterdir())}"


# ── the sandbox profile really has no network (sealed AND curated) ──────────


PROBE_DNS = (
    "import socket\n"
    "try:\n"
    "    socket.getaddrinfo('example.org', 80)\n"
    "except OSError as exc:\n"
    "    print('NO-NETWORK:', type(exc).__name__)\n"
    "else:\n"
    "    print('NETWORK-AVAILABLE')\n"
)


async def test_container_has_no_egress_in_sealed_and_curated_modes(
    sandbox_image: str, tmp_path: Path
) -> None:
    runtime = ContainerSandboxRuntime(image=sandbox_image, engine="docker", work_root=tmp_path / "sb")

    for access_profile in ("sealed", "curated"):
        policy = {**BOOTSTRAP_PAYLOAD["policy"], "access_profile": access_profile}
        cap = effective_profile(policy)
        executor = SandboxSessionExecutor(runtime)
        session_id = uuid.uuid4()
        await executor.open_session(session_id, cap)
        try:
            assert executor.handle is not None
            obs = await executor.execute("python.execute", {"code": PROBE_DNS})
            assert obs.ok, obs.error
            assert "NO-NETWORK" in str(obs.data["stdout"]), (access_profile, obs.data)

            interfaces = await executor.execute(
                "shell.execute", {"command": "cut -d: -f1 /proc/net/dev | tail -n +3"}
            )
            assert interfaces.ok, interfaces.error
            names = [line.strip() for line in str(interfaces.data["stdout"]).splitlines() if line.strip()]
            assert names == ["lo"], (access_profile, names)
        finally:
            await executor.close_session()
        # curated's `research_proxy` never becomes a container network (§3 invariant)
        name = f"noezema-sb-{str(session_id).replace('-', '')[:12]}"
        assert _containers(name) == []
        assert list((tmp_path / "sb").iterdir()) == []


# ── failure paths ───────────────────────────────────────────────────────────


async def test_unavailable_image_refuses_the_session_start(
    migrated_db, fake_llm: FakeLLM, docker_engine: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No silent fallback to the unisolated stub: the session does not start,
    phase 1 rolls back (no session row), nothing runs on the host."""
    scratch_url, _engine = migrated_db
    work_root = tmp_path / "sb"
    monkeypatch.setenv(TOOL_EXECUTOR_ENV, SANDBOX_MODE)

    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    question_id = await _seed_question(scratch_url)

    executor = _RecordingExecutor(
        ContainerSandboxRuntime(
            image="noezema-sandbox:no-such-tag-t758", engine=docker_engine, work_root=work_root
        )
    )
    orch, gateway, engine = _orchestrator(scratch_url, fake_llm, executor)
    try:
        with pytest.raises(ToolExecutorUnavailableError):
            await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    row = await _scalar(scratch_url, "SELECT count(*)::int FROM sessions")
    assert row is not None and row[0] == 0, "a refused start must not leave a session row"
    assert executor.opened == [], "no container may be created"
    assert list(work_root.iterdir()) == [], "a refused start leaves no sandbox directories"


async def test_exception_inside_session_still_destroys_the_container(
    migrated_db, fake_llm: FakeLLM, sandbox_image: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 1 raises AFTER a tool already ran in the container (here: the model
    call fails for good). The finally of run_session still destroys the container."""
    scratch_url, _engine = migrated_db
    work_root = tmp_path / "sb"
    _use_sandbox(monkeypatch, sandbox_image, work_root)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"error": 500},
            {"error": 500},
            {"error": 500},
            {"error": 500},
        ]
    )
    question_id = await _seed_question(scratch_url)
    executor = _RecordingExecutor(
        ContainerSandboxRuntime(image=sandbox_image, engine="docker", work_root=work_root)
    )
    orch, gateway, engine = _orchestrator(scratch_url, fake_llm, executor)
    try:
        with pytest.raises(Exception) as excinfo:  # LLMTransientError after retries
            await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert "500" in str(excinfo.value) or "transient" in str(excinfo.value).lower()
    # the container WAS opened (the hook ran) and IS gone (the finally ran)
    assert len(executor.opened) == 1, executor.opened
    assert _containers(*executor.opened) == []
    assert list(work_root.iterdir()) == [], f"sandbox work root not cleaned: {list(work_root.iterdir())}"

    row = await _scalar(scratch_url, "SELECT count(*)::int FROM sessions")
    assert row is not None and row[0] == 0, "phase-1 rollback must leave no session row"


# ── the stub path through the same switch is unchanged ──────────────────────


async def test_stub_mode_session_is_unchanged(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (unset) switch → the pre-T7.58 behavior: shared host workspace,
    shell.execute refused with `tool_not_supported:` (T7.57(b)), no container."""
    scratch_url, _engine = migrated_db
    monkeypatch.delenv(TOOL_EXECUTOR_ENV, raising=False)

    executor = build_tool_executor(tmp_path / "ws")
    assert isinstance(executor, StubToolExecutor)
    assert not hasattr(executor, "open_session")  # the sandbox hook is absent

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": TOOL_SHELL},
            {"content": TOOL_WRITE},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ]
    )
    question_id = await _seed_question(scratch_url)
    orch, gateway, engine = _orchestrator(scratch_url, fake_llm, executor)
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    actions = await _actions(scratch_url, outcome.session_id)
    assert set(actions) == {"python.execute", "shell.execute", "workspace.write"}, actions
    assert actions["python.execute"][0] == "completed"
    assert actions["workspace.write"][0] == "completed"
    assert actions["shell.execute"][0] == "failed"
    assert (actions["shell.execute"][1] or "").startswith("tool_not_supported:")

    # the stub wrote into the shared host workspace; no sandbox work root exists
    assert (tmp_path / "ws" / "notes.md").read_text(encoding="utf-8") == "6*7=42"
    assert not (tmp_path / "sb").exists()
