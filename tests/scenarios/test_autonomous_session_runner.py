"""Full autonomous question -> evidence -> assessed checkpoint scenarios."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import (
    AutonomousSessionRunner,
    SessionRunnerLimits,
    SessionStarted,
    request_session_abort,
    start_next_session,
)
from packages.cognition import (
    CuratorClaimProposal,
    CuratorOutcome,
    CuratorProposal,
    EvidenceReference,
    EvidenceRelation,
    PromptBundle,
    PromptKind,
    PromptSnapshot,
    enqueue_question,
)
from packages.domain import (
    ClaimType,
    CompleteDecision,
    ConfigSnapshotId,
    DecisionEnvelope,
    ObservationId,
    QuestionDraft,
    SessionBudget,
    SessionId,
    SessionState,
    ToolDecision,
    ToolName,
    canonical_json_sha256,
    capability_policy_config_payload,
    sealed_mvp_capability_policy,
)
from packages.llm_gateway import (
    ModelRunResult,
    StructuredRunResult,
    TokenUsage,
)
from packages.memory import mvp_claim_type_rules
from packages.persistence import BOOTSTRAP_CONFIG_SNAPSHOT_ID, bootstrap_payload
from packages.persistence.models import (
    ClaimAssessmentRecord,
    ClaimRecord,
    CommitAttemptRecord,
    ConfigSnapshotRecord,
    EvidenceRecord,
    ModelRunRecord,
    OrchestratorTurnRecord,
    QuestionRecord,
    RuntimeConfigHeadRecord,
    SessionRecord,
)
from packages.tool_broker import ToolBroker

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _Gateway:
    def __init__(
        self,
        *,
        decisions: tuple[ModelRunResult, ...] = (),
        curator: StructuredRunResult[CuratorProposal] | None = None,
    ) -> None:
        self.decisions = deque(decisions)
        self.curator = curator
        self.decision_calls = 0
        self.curator_calls = 0

    def generate_decision(self, _request) -> ModelRunResult:
        self.decision_calls += 1
        if not self.decisions:
            raise AssertionError("unexpected Explorer model call")
        return self.decisions.popleft()

    def generate_structured(self, _request, *, response_model, schema_name):
        self.curator_calls += 1
        assert response_model is CuratorProposal
        assert schema_name == "noezema_curator_proposal_v1"
        if self.curator is None:
            raise AssertionError("unexpected Curator model call")
        return self.curator


def _prompt(kind: PromptKind) -> PromptSnapshot:
    path = PROJECT_ROOT / "prompts" / f"{kind.value}.md"
    return PromptSnapshot.load(
        path,
        kind=kind,
        version=f"{kind.value}/v1",
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _prompts(kind: PromptKind) -> PromptBundle:
    return PromptBundle(identity=_prompt(PromptKind.IDENTITY), role=_prompt(kind))


def _install_runtime_config(db: Session) -> ConfigSnapshotId:
    policy = sealed_mvp_capability_policy()
    rules = mvp_claim_type_rules()
    payload = bootstrap_payload()
    payload["policy"] = capability_policy_config_payload(policy)
    payload["claim_type_rules"] = rules.model_dump(mode="json")
    payload_sha256 = canonical_json_sha256(payload)
    config_id = ConfigSnapshotId.new()
    db.add(
        ConfigSnapshotRecord(
            id=config_id.root,
            base_snapshot_id=BOOTSTRAP_CONFIG_SNAPSHOT_ID,
            payload=payload,
            payload_sha256=payload_sha256,
            sha=canonical_json_sha256(
                {
                    "base_snapshot_id": str(BOOTSTRAP_CONFIG_SNAPSHOT_ID),
                    "payload_sha256": payload_sha256,
                }
            ),
            activation_mode="offline",
            activation_state="active",
            created_at=NOW,
        )
    )
    head = db.get(RuntimeConfigHeadRecord, "global")
    assert head is not None
    head.active_config_snapshot_id = config_id.root
    head.updated_at = NOW
    return config_id


def _enqueue(db: Session, config_id: ConfigSnapshotId) -> QuestionDraft:
    question = QuestionDraft.seeded(
        text="What does the local corpus say about NOEZEMA?",
        origin_config_snapshot_id=config_id,
        created_at=NOW,
    )
    enqueue_question(db, question)
    return question


def _explorer_result(*, complete: bool) -> ModelRunResult:
    decision = (
        CompleteDecision(kind="complete", reason="the source was read")
        if complete
        else ToolDecision(
            kind="tool",
            tool=ToolName.WORKSPACE_READ,
            arguments={"path": "corpus.txt"},
        )
    )
    return ModelRunResult(
        decision=DecisionEnvelope(
            public_rationale=(
                "The bounded local source is sufficient."
                if complete
                else "Read the exact bounded local source."
            ),
            expected_information=None if complete else "The exact source text.",
            decision=decision,
        ),
        finish_reason="stop",
        backend_model="local-test-model",
        usage=TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        latency_ms=5,
        attempts=1,
        model_fingerprint_sha256="a" * 64,
        invocation_fingerprint_sha256="b" * 64,
        tool_schema_sha256="c" * 64,
    )


class _ContextBoundGateway(_Gateway):
    def generate_structured(self, request, *, response_model, schema_name):
        self.curator_calls += 1
        assert response_model is CuratorProposal
        assert schema_name == "noezema_curator_proposal_v1"
        context = json.loads(request.messages[-1].content)["context"]
        observation_id = ObservationId.model_validate(context["observations"][0]["id"])
        proposal = CuratorProposal(
            public_summary="The local source was converted into an assessed claim.",
            outcome=CuratorOutcome.COMPLETED,
            claims=(
                CuratorClaimProposal(
                    ref="local_corpus_statement",
                    statement="NOEZEMA keeps evidence provenance with learned claims.",
                    claim_type=ClaimType.EXTERNAL_FACT,
                    topic="noezema-memory",
                    evidence=(
                        EvidenceReference(
                            observation_id=observation_id,
                            relation=EvidenceRelation.SUPPORTS,
                            scope="The exact claim statement.",
                        ),
                    ),
                ),
            ),
        )
        return StructuredRunResult[CuratorProposal](
            output=proposal,
            finish_reason="stop",
            backend_model="local-test-model",
            usage=TokenUsage(input_tokens=30, output_tokens=20, total_tokens=50),
            latency_ms=7,
            attempts=1,
            model_fingerprint_sha256="d" * 64,
            invocation_fingerprint_sha256="e" * 64,
            output_schema_sha256="f" * 64,
        )


def _runner(
    session_factory: sessionmaker[Session],
    workspace: Path,
    gateway: _Gateway,
) -> AutonomousSessionRunner:
    policy = sealed_mvp_capability_policy()
    return AutonomousSessionRunner(
        session_factory=session_factory,
        gateway=gateway,
        tool_broker=ToolBroker(
            session_factory=session_factory,
            workspace_root=workspace,
            artifact_store_root=workspace.parent / "artifacts",
            policy=policy,
            lease_owner="runner/incarnation-1",
            clock=lambda: NOW,
        ),
        explorer_prompts=_prompts(PromptKind.EXPLORER),
        curator_prompts=_prompts(PromptKind.CURATOR),
        lease_owner="runner/incarnation-1",
        limits=SessionRunnerLimits(max_steps=32, max_claims=4, max_evidence_links=8),
        clock=lambda: NOW,
    )


def _prepared_session(
    session_factory: sessionmaker[Session],
    *,
    budget: SessionBudget | None = None,
) -> SessionId:
    session_id = SessionId.new()
    with session_factory.begin() as db:
        config_id = _install_runtime_config(db)
        _enqueue(db, config_id)
        started = start_next_session(
            db,
            session_id=session_id,
            budget=budget,
            occurred_at=NOW,
        )
        assert isinstance(started, SessionStarted)
    return session_id


def test_runner_completes_full_local_cognitive_cycle(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "corpus.txt").write_text(
        "NOEZEMA keeps evidence provenance with learned claims.",
        encoding="utf-8",
    )
    with session_factory.begin() as db:
        config_id = _install_runtime_config(db)
        question = _enqueue(db, config_id)
    gateway = _ContextBoundGateway(
        decisions=(_explorer_result(complete=False), _explorer_result(complete=True)),
    )

    result = _runner(session_factory, workspace, gateway).run_once()

    assert result.terminal_state is SessionState.SUCCEEDED
    assert result.question_id == question.id
    assert result.checkpoint_id is not None
    assert result.model_turns == 3
    assert result.tool_actions == 1
    assert result.claims_committed == 1
    assert gateway.decision_calls == 2
    assert gateway.curator_calls == 1
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(EvidenceRecord)) == 1
        assert db.scalar(select(func.count()).select_from(ClaimAssessmentRecord)) == 1
        question_record = db.get(QuestionRecord, question.id.root)
        assert question_record is not None and question_record.state == "answered"


def test_soft_exhaustion_reserves_curator_and_commits_partial_progress(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "corpus.txt").write_text(
        "NOEZEMA keeps evidence provenance with learned claims.",
        encoding="utf-8",
    )
    budget = SessionBudget(
        max_model_turns=3,
        max_tool_actions=2,
        max_input_tokens=10_000,
        max_output_tokens=10_000,
        cognitive_reserve_model_turns=1,
        cognitive_reserve_input_tokens=1_000,
        cognitive_reserve_output_tokens=1_000,
    )
    session_id = _prepared_session(session_factory, budget=budget)
    gateway = _ContextBoundGateway(
        decisions=(_explorer_result(complete=False), _explorer_result(complete=True)),
    )

    result = _runner(session_factory, workspace, gateway).run_session(session_id)

    assert result.terminal_state is SessionState.SUCCEEDED_PARTIAL
    assert result.model_turns == 3
    assert result.tool_actions == 1
    assert result.claims_committed == 1
    with session_factory() as db:
        session = db.get(SessionRecord, session_id.root)
        assert session is not None
        assert session.soft_exhaustion_reason == "model_turns"
        question = db.get(QuestionRecord, session.question_id)
        assert question is not None and question.state == "queued"


def test_unknown_commit_resumes_without_repeating_models_or_actions(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "corpus.txt").write_text(
        "NOEZEMA keeps evidence provenance with learned claims.",
        encoding="utf-8",
    )
    session_id = _prepared_session(session_factory)
    initial_gateway = _ContextBoundGateway(
        decisions=(_explorer_result(complete=False), _explorer_result(complete=True)),
    )
    initial = _runner(session_factory, workspace, initial_gateway)
    for _ in range(8):
        initial.run_step(session_id)

    with session_factory.begin() as db:
        session = db.get(SessionRecord, session_id.root)
        attempt = db.scalar(
            select(CommitAttemptRecord).where(CommitAttemptRecord.session_id == session_id.root)
        )
        assert session is not None and session.state == SessionState.COMMITTING.value
        assert attempt is not None and attempt.status == "prepared"
        attempt.status = "reconciling"
        turns_before = db.scalar(select(func.count()).select_from(OrchestratorTurnRecord))
        runs_before = db.scalar(select(func.count()).select_from(ModelRunRecord))
        claims_before = db.scalar(select(func.count()).select_from(ClaimRecord))
        assert claims_before == 0

    recovery_gateway = _Gateway()
    recovered = _runner(session_factory, workspace, recovery_gateway).run_session(session_id)

    assert recovered.terminal_state is SessionState.SUCCEEDED
    assert recovered.claims_committed == 1
    assert recovery_gateway.decision_calls == 0
    assert recovery_gateway.curator_calls == 0
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(OrchestratorTurnRecord)) == turns_before
        assert db.scalar(select(func.count()).select_from(ModelRunRecord)) == runs_before
        attempt = db.scalar(
            select(CommitAttemptRecord).where(CommitAttemptRecord.session_id == session_id.root)
        )
        assert attempt is not None and attempt.status == "committed"


def test_runner_applies_durable_abort_before_any_model_call(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_id = _prepared_session(session_factory)
    gateway = _Gateway()
    runner = _runner(session_factory, workspace, gateway)
    runner.run_step(session_id)
    with session_factory.begin() as db:
        request_session_abort(
            db,
            session_id=session_id,
            actor="scenario-test",
            requested_at=NOW,
        )

    result = runner.run_session(session_id)

    assert result.terminal_state is SessionState.CANCELLED
    assert result.checkpoint_id is None
    assert result.model_turns == 0
    assert result.tool_actions == 0
    assert gateway.decision_calls == 0
    assert gateway.curator_calls == 0
