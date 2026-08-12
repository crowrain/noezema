"""Fenced phase transitions and resumable Explorer turn tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import (
    DurableSessionOrchestrator,
    IllegalSessionTransitionError,
    InconsistentSessionRuntimeError,
    SessionStarted,
    SessionWorkKind,
    TurnFenceRejectedError,
    claim_session,
    start_next_session,
    terminate_session,
    transition_session_phase,
)
from packages.cognition import (
    ExplorerContext,
    PromptBundle,
    PromptKind,
    PromptSnapshot,
    ProtocolQuestion,
    enqueue_question,
)
from packages.domain import (
    ActionState,
    CompleteDecision,
    ConfigSnapshotId,
    DecisionEnvelope,
    EventType,
    QuestionDraft,
    SessionId,
    SessionState,
    ToolDecision,
    ToolName,
    TurnId,
    canonical_json_sha256,
    capability_policy_config_payload,
    sealed_mvp_capability_policy,
)
from packages.llm_gateway import ModelRunResult, TokenUsage
from packages.persistence import FenceMismatchError, bootstrap_payload
from packages.persistence.models import (
    ActionRecord,
    AuditEventRecord,
    ConfigSnapshotRecord,
    ModelRunRecord,
    OrchestratorTurnRecord,
    RuntimeConfigHeadRecord,
    SessionRecord,
)
from packages.tool_broker import ToolBroker

NOW = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _Gateway:
    def __init__(self, result: ModelRunResult) -> None:
        self.result = result
        self.calls = 0

    def generate_decision(self, _request) -> ModelRunResult:
        self.calls += 1
        return self.result


class _CrashingBroker:
    def run(self, _action):
        raise RuntimeError("simulated crash before action registration")


class _TakeoverGateway(_Gateway):
    def __init__(
        self,
        result: ModelRunResult,
        *,
        session_factory: sessionmaker[Session],
        session_id: SessionId,
    ) -> None:
        super().__init__(result)
        self._session_factory = session_factory
        self._session_id = session_id

    def generate_decision(self, request) -> ModelRunResult:
        result = super().generate_decision(request)
        with self._session_factory.begin() as db:
            claim_session(
                db,
                session_id=self._session_id,
                owner="worker-b/incarnation-1",
                ttl_seconds=60,
                occurred_at=NOW + timedelta(seconds=6),
            )
        return result


def _prompt(kind: PromptKind) -> PromptSnapshot:
    path = PROJECT_ROOT / "prompts" / f"{kind.value}.md"
    return PromptSnapshot.load(
        path,
        kind=kind,
        version=f"{kind.value}/v1",
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _prompts() -> PromptBundle:
    return PromptBundle(
        identity=_prompt(PromptKind.IDENTITY),
        role=_prompt(PromptKind.EXPLORER),
    )


def _install_policy(db: Session) -> ConfigSnapshotId:
    policy = sealed_mvp_capability_policy()
    payload = bootstrap_payload()
    payload["policy"] = capability_policy_config_payload(policy)
    payload_sha256 = canonical_json_sha256(payload)
    config_id = ConfigSnapshotId.new()
    db.add(
        ConfigSnapshotRecord(
            id=config_id.root,
            base_snapshot_id=None,
            payload=payload,
            payload_sha256=payload_sha256,
            sha=canonical_json_sha256({"base_snapshot_id": None, "payload_sha256": payload_sha256}),
            activation_mode="offline",
            activation_state="active",
            created_at=NOW,
        )
    )
    head = db.get(RuntimeConfigHeadRecord, "global")
    assert head is not None
    head.active_config_snapshot_id = config_id.root
    return config_id


def _start(session_factory: sessionmaker[Session]) -> tuple[SessionId, ProtocolQuestion]:
    session_id = SessionId.new()
    with session_factory.begin() as db:
        config_id = _install_policy(db)
        question = QuestionDraft.seeded(
            text="What can be learned from corpus.txt?",
            origin_config_snapshot_id=config_id,
            created_at=NOW,
        )
        enqueue_question(db, question)
        started = start_next_session(db, session_id=session_id, occurred_at=NOW)
        assert isinstance(started, SessionStarted)
    return session_id, ProtocolQuestion(id=question.id, text=question.text)


def _advance_to_exploring(
    session_factory: sessionmaker[Session],
    session_id: SessionId,
    *,
    owner: str = "worker-a/incarnation-1",
    ttl_seconds: int = 300,
) -> None:
    with session_factory.begin() as db:
        claimed = claim_session(
            db,
            session_id=session_id,
            owner=owner,
            ttl_seconds=ttl_seconds,
            occurred_at=NOW,
        )
        assert claimed.directive.kind is SessionWorkKind.WAKE
        for target in (
            SessionState.ORIENTING,
            SessionState.PLANNING,
            SessionState.EXPLORING,
        ):
            transition_session_phase(
                db,
                lease=claimed.lease,
                target=target,
                reason="phase_completed",
                occurred_at=NOW,
            )


def _context(question: ProtocolQuestion) -> ExplorerContext:
    return ExplorerContext(
        question=question,
        remaining_actions=2,
        allowed_tools=(ToolName.WORKSPACE_READ,),
    )


def _result(*, complete: bool = False) -> ModelRunResult:
    if complete:
        decision = DecisionEnvelope(
            public_rationale="The available exploration is sufficient.",
            expected_information=None,
            decision=CompleteDecision(kind="complete", reason="enough evidence"),
        )
    else:
        decision = DecisionEnvelope(
            public_rationale="Read the bounded local corpus.",
            expected_information="Exact source text.",
            decision=ToolDecision(
                kind="tool",
                tool=ToolName.WORKSPACE_READ,
                arguments={"path": "corpus.txt"},
            ),
        )
    return ModelRunResult(
        decision=decision,
        finish_reason="stop",
        backend_model="local-test-model",
        usage=TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        latency_ms=5,
        attempts=1,
        model_fingerprint_sha256="a" * 64,
        invocation_fingerprint_sha256="b" * 64,
        tool_schema_sha256="c" * 64,
    )


def _broker(
    session_factory: sessionmaker[Session],
    workspace: Path,
    *,
    owner: str = "worker-a/incarnation-1",
) -> ToolBroker:
    return ToolBroker(
        session_factory=session_factory,
        workspace_root=workspace,
        artifact_store_root=workspace.parent / "artifacts",
        policy=sealed_mvp_capability_policy(),
        lease_owner=owner,
        clock=lambda: NOW,
    )


def test_phase_graph_is_atomic_and_expired_owner_is_fenced(
    session_factory: sessionmaker[Session],
) -> None:
    session_id, _ = _start(session_factory)
    with session_factory.begin() as db:
        claimed = claim_session(
            db,
            session_id=session_id,
            owner="worker-a/incarnation-1",
            ttl_seconds=5,
            occurred_at=NOW,
        )
        assert claimed.directive.kind is SessionWorkKind.WAKE
        transition_session_phase(
            db,
            lease=claimed.lease,
            target=SessionState.ORIENTING,
            reason="woke",
            occurred_at=NOW,
        )
        transition_session_phase(
            db,
            lease=claimed.lease,
            target=SessionState.PLANNING,
            reason="oriented",
            occurred_at=NOW,
        )
        transition_session_phase(
            db,
            lease=claimed.lease,
            target=SessionState.EXPLORING,
            reason="planned",
            occurred_at=NOW,
        )
        with pytest.raises(IllegalSessionTransitionError):
            transition_session_phase(
                db,
                lease=claimed.lease,
                target=SessionState.REPORTING,
                reason="illegal shortcut",
                occurred_at=NOW,
            )

    takeover_at = NOW + timedelta(seconds=6)
    with session_factory.begin() as db:
        takeover = claim_session(
            db,
            session_id=session_id,
            owner="worker-b/incarnation-1",
            ttl_seconds=60,
            occurred_at=takeover_at,
        )
        assert takeover.directive.kind is SessionWorkKind.EXPLORE
        assert takeover.lease.fence == claimed.lease.fence + 1
        with pytest.raises(FenceMismatchError):
            transition_session_phase(
                db,
                lease=claimed.lease,
                target=SessionState.VERIFYING,
                reason="stale worker",
                occurred_at=takeover_at,
            )

    with session_factory() as db:
        events = db.scalars(
            select(AuditEventRecord)
            .where(
                AuditEventRecord.session_id == session_id.root,
                AuditEventRecord.type == EventType.SESSION_STATE_CHANGED.value,
            )
            .order_by(AuditEventRecord.sequence)
        ).all()
        assert [event.payload["to"] for event in events] == [
            "created",
            "waking",
            "orienting",
            "planning",
            "exploring",
        ]


def test_explorer_tool_turn_persists_once_and_replays_downstream_action(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "corpus.txt").write_text("Local evidence.", encoding="utf-8")
    session_id, question = _start(session_factory)
    _advance_to_exploring(session_factory, session_id)
    gateway = _Gateway(_result())
    runtime = DurableSessionOrchestrator(
        session_factory=session_factory,
        gateway=gateway,
        tool_broker=_broker(session_factory, workspace),
        lease_owner="worker-a/incarnation-1",
        clock=lambda: NOW,
    )
    turn_id = TurnId.new()

    first = runtime.run_explorer_turn(
        session_id=session_id,
        turn_id=turn_id,
        context=_context(question),
        prompts=_prompts(),
        policy_version=sealed_mvp_capability_policy().version,
    )
    replayed = runtime.run_explorer_turn(
        session_id=session_id,
        turn_id=turn_id,
        context=_context(question),
        prompts=_prompts(),
        policy_version=sealed_mvp_capability_policy().version,
    )

    assert gateway.calls == 1
    assert first.replayed_model_run is False
    assert replayed.replayed_model_run is True
    assert first.model_run_id == replayed.model_run_id
    assert first.action_result == replayed.action_result
    assert first.action_result is not None
    assert first.action_result.state is ActionState.COMPLETED
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(OrchestratorTurnRecord)) == 1
        assert db.scalar(select(func.count()).select_from(ModelRunRecord)) == 1
        assert db.scalar(select(func.count()).select_from(ActionRecord)) == 1
        model_events = db.scalar(
            select(func.count())
            .select_from(AuditEventRecord)
            .where(AuditEventRecord.type == EventType.MODEL_RUN_COMPLETED.value)
        )
        assert model_events == 1


def test_completed_model_turn_resumes_after_crash_before_action_registration(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "corpus.txt").write_text("Recovered evidence.", encoding="utf-8")
    session_id, question = _start(session_factory)
    _advance_to_exploring(session_factory, session_id)
    gateway = _Gateway(_result())
    turn_id = TurnId.new()
    crashing = DurableSessionOrchestrator(
        session_factory=session_factory,
        gateway=gateway,
        tool_broker=_CrashingBroker(),  # type: ignore[arg-type]
        lease_owner="worker-a/incarnation-1",
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        crashing.run_explorer_turn(
            session_id=session_id,
            turn_id=turn_id,
            context=_context(question),
            prompts=_prompts(),
            policy_version=sealed_mvp_capability_policy().version,
        )

    with session_factory.begin() as db:
        claimed = claim_session(
            db,
            session_id=session_id,
            owner="worker-a/incarnation-1",
            ttl_seconds=300,
            occurred_at=NOW,
        )
        assert claimed.directive.kind is SessionWorkKind.RESUME_TURN
        assert claimed.directive.turn_id == turn_id

    resumed = DurableSessionOrchestrator(
        session_factory=session_factory,
        gateway=gateway,
        tool_broker=_broker(session_factory, workspace),
        lease_owner="worker-a/incarnation-1",
        clock=lambda: NOW,
    ).resume_explorer_turn(session_id=session_id, turn_id=turn_id)

    assert resumed.replayed_model_run is True
    assert resumed.action_result is not None
    assert resumed.action_result.state is ActionState.COMPLETED
    assert gateway.calls == 1


def test_complete_decision_moves_to_verifying_once(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_id, question = _start(session_factory)
    _advance_to_exploring(session_factory, session_id)
    gateway = _Gateway(_result(complete=True))
    runtime = DurableSessionOrchestrator(
        session_factory=session_factory,
        gateway=gateway,
        tool_broker=_broker(session_factory, workspace),
        lease_owner="worker-a/incarnation-1",
        clock=lambda: NOW,
    )
    turn_id = TurnId.new()

    runtime.run_explorer_turn(
        session_id=session_id,
        turn_id=turn_id,
        context=_context(question),
        prompts=_prompts(),
        policy_version=sealed_mvp_capability_policy().version,
    )
    replayed = runtime.resume_explorer_turn(session_id=session_id, turn_id=turn_id)

    assert replayed.replayed_model_run is True
    assert replayed.action_result is None
    assert gateway.calls == 1
    with session_factory() as db:
        session = db.get(SessionRecord, session_id.root)
        assert session is not None
        assert session.state == SessionState.VERIFYING.value
        verifying_events = db.scalar(
            select(func.count())
            .select_from(AuditEventRecord)
            .where(
                AuditEventRecord.session_id == session_id.root,
                AuditEventRecord.payload["to"].as_string() == SessionState.VERIFYING.value,
            )
        )
        assert verifying_events == 1


def test_model_result_from_expired_incarnation_is_discarded_and_regenerated(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_id, question = _start(session_factory)
    _advance_to_exploring(session_factory, session_id, ttl_seconds=5)
    gateway = _TakeoverGateway(
        _result(complete=True),
        session_factory=session_factory,
        session_id=session_id,
    )
    clock_values = iter((NOW, NOW + timedelta(seconds=6)))
    stale_runtime = DurableSessionOrchestrator(
        session_factory=session_factory,
        gateway=gateway,
        tool_broker=_broker(session_factory, workspace),
        lease_owner="worker-a/incarnation-1",
        lease_ttl_seconds=5,
        clock=lambda: next(clock_values),
    )
    turn_id = TurnId.new()

    with pytest.raises(TurnFenceRejectedError):
        stale_runtime.run_explorer_turn(
            session_id=session_id,
            turn_id=turn_id,
            context=_context(question),
            prompts=_prompts(),
            policy_version=sealed_mvp_capability_policy().version,
        )

    with session_factory() as db:
        turn = db.get(OrchestratorTurnRecord, turn_id.root)
        assert turn is not None and turn.status == "prepared"
        assert db.scalar(select(func.count()).select_from(ModelRunRecord)) == 0

    replacement_gateway = _Gateway(_result(complete=True))
    replacement_runtime = DurableSessionOrchestrator(
        session_factory=session_factory,
        gateway=replacement_gateway,
        tool_broker=_broker(
            session_factory,
            workspace,
            owner="worker-b/incarnation-1",
        ),
        lease_owner="worker-b/incarnation-1",
        clock=lambda: NOW + timedelta(seconds=6),
    )
    with pytest.raises(InconsistentSessionRuntimeError, match="prepared model turn"):
        replacement_runtime.run_explorer_turn(
            session_id=session_id,
            turn_id=TurnId.new(),
            context=_context(question),
            prompts=_prompts(),
            policy_version=sealed_mvp_capability_policy().version,
        )
    recovered = replacement_runtime.resume_explorer_turn(
        session_id=session_id,
        turn_id=turn_id,
    )

    assert recovered.replayed_model_run is False
    assert gateway.calls == 1
    assert replacement_gateway.calls == 1
    with session_factory() as db:
        turn = db.get(OrchestratorTurnRecord, turn_id.root)
        assert turn is not None and turn.status == "completed"
        assert turn.lease_owner == "worker-b/incarnation-1"
        assert turn.session_fence == 2


def test_failure_termination_is_two_audited_edges_and_releases_lease(
    session_factory: sessionmaker[Session],
) -> None:
    session_id, _ = _start(session_factory)
    with session_factory.begin() as db:
        claimed = claim_session(
            db,
            session_id=session_id,
            owner="worker-a/incarnation-1",
            ttl_seconds=60,
            occurred_at=NOW,
        )
        terminate_session(
            db,
            lease=claimed.lease,
            terminal_state=SessionState.FAILED,
            reason="model_backend_unavailable",
            occurred_at=NOW,
        )

    with session_factory() as db:
        session = db.get(SessionRecord, session_id.root)
        assert session is not None
        assert session.state == SessionState.FAILED.value
        assert session.lease_owner is None
        assert session.lease_expires_at is None
