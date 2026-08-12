"""Durable policy and execution lifecycle tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from packages.domain import (
    ActionState,
    CapabilityPolicy,
    ClaimAssessmentId,
    ClaimId,
    ConfigSnapshotId,
    EpistemicStatus,
    EventType,
    ModelRunId,
    SandboxEnvironmentManifest,
    SandboxProfile,
    SessionId,
    ToolDecision,
    ToolExecutionObservation,
    ToolName,
    TurnId,
    WorkspaceListPayload,
    WorkspaceReadPayload,
    bind_action,
    canonical_json_sha256,
    capability_policy_config_payload,
    sandbox_mvp_capability_policy,
    sealed_mvp_capability_policy,
)
from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    bootstrap_payload,
    create_session_with_audit,
)
from packages.persistence.models import (
    ActionRecord,
    AuditEventRecord,
    ClaimAssessmentHeadRecord,
    ClaimAssessmentRecord,
    ClaimRecord,
    ConfigSnapshotRecord,
    ModelRunRecord,
    OutboxEventRecord,
)
from packages.tool_broker import (
    CapturedCommand,
    SandboxProcessResult,
    ToolBroker,
    ToolExecutionError,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _seed_model_run(
    session_factory: sessionmaker[Session],
    *,
    bind_policy: bool = True,
    policy: CapabilityPolicy | None = None,
) -> tuple[SessionId, ModelRunId, ConfigSnapshotId]:
    session_id = SessionId.new()
    model_run_id = ModelRunId.new()
    config_id = ConfigSnapshotId(root=BOOTSTRAP_CONFIG_SNAPSHOT_ID)
    with session_factory.begin() as db:
        if bind_policy:
            selected_policy = policy or sealed_mvp_capability_policy()
            config_id = ConfigSnapshotId.new()
            payload = bootstrap_payload()
            payload["policy"] = capability_policy_config_payload(selected_policy)
            payload_sha256 = canonical_json_sha256(payload)
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
        create_session_with_audit(
            db,
            session_id=session_id,
            config_snapshot_id=config_id,
            occurred_at=NOW,
        )
        db.add(
            ModelRunRecord(
                id=model_run_id.root,
                session_id=session_id.root,
                turn_id=TurnId.new().root,
                phase="exploration",
                model_fingerprint="a" * 64,
                context_manifest_sha256="b" * 64,
                context_manifest={},
                prompt_version="explorer/v1",
                tool_schema_sha256="c" * 64,
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                finish_reason="stop",
                output_schema_valid=True,
                raw_response_artifact=None,
                created_at=NOW,
            )
        )
    return session_id, model_run_id, config_id


def _bound_action(
    session_id: SessionId,
    model_run_id: ModelRunId,
    tool: ToolName,
    arguments: dict[str, object],
):
    return bind_action(
        session_id=session_id,
        model_run_id=model_run_id,
        decision=ToolDecision(kind="tool", tool=tool, arguments=arguments),
    )


def test_workspace_read_is_persisted_with_full_lifecycle_and_replays(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    (tmp_path / "facts.txt").write_text("local evidence", encoding="utf-8")
    session_id, model_run_id, _ = _seed_model_run(session_factory)
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.WORKSPACE_READ,
        {"path": "facts.txt"},
    )
    broker = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        clock=lambda: NOW,
    )

    completed = broker.run(action)
    replayed = broker.run(action)

    assert completed.state is ActionState.COMPLETED
    assert completed.attempt_count == 1
    assert completed.observation == replayed.observation
    assert isinstance(completed.observation.payload, WorkspaceReadPayload)
    assert completed.observation.payload.content == "local evidence"
    with session_factory() as db:
        record = db.get(ActionRecord, action.action_id.root)
        events = db.scalars(
            select(AuditEventRecord)
            .where(AuditEventRecord.session_id == session_id.root)
            .order_by(AuditEventRecord.sequence)
        ).all()
        outbox = db.scalars(select(OutboxEventRecord)).all()

        assert record is not None
        assert record.arguments_json == action.arguments_json
        assert record.state == ActionState.COMPLETED.value
        assert record.policy_hash is not None
        assert [item.type for item in events] == [
            EventType.SESSION_STATE_CHANGED.value,
            EventType.ACTION_PROPOSED.value,
            EventType.POLICY_EVALUATED.value,
            EventType.ACTION_ACCEPTED.value,
            EventType.ACTION_STARTED.value,
            EventType.ACTION_COMPLETED.value,
        ]
        assert len(outbox) == len(events)


def test_unsafe_tool_is_denied_before_execution(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    session_id, model_run_id, _ = _seed_model_run(session_factory)
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.SHELL_EXECUTE,
        {"command": "whoami"},
    )

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.POLICY_EVALUATED
    assert result.policy.reason == "tool_not_allowed"
    assert result.attempt_count == 0
    with session_factory() as db:
        record = db.get(ActionRecord, action.action_id.root)
        assert record is not None
        assert record.state == ActionState.POLICY_EVALUATED.value
        assert record.started_at is None
        assert record.result is None


def test_traversal_argument_is_denied_by_typed_policy(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    session_id, model_run_id, _ = _seed_model_run(session_factory)
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.WORKSPACE_READ,
        {"path": "../secret.txt"},
    )

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.POLICY_EVALUATED
    assert result.policy.reason == "arguments_invalid"


def test_session_config_snapshot_must_pin_the_exact_capability_policy(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    session_id, model_run_id, _ = _seed_model_run(
        session_factory,
        bind_policy=False,
    )
    action = _bound_action(session_id, model_run_id, ToolName.WORKSPACE_LIST, {})

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.POLICY_EVALUATED
    assert result.policy.reason == "session_policy_not_bound"


class _FlakyListExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *, action, arguments, captured_at, timeout_ms):
        self.calls += 1
        if self.calls == 1:
            raise ToolExecutionError(
                "temporary_io_error",
                "Temporary read-only I/O failure.",
                retryable=True,
            )
        return ToolExecutionObservation.build(
            action_id=action.action_id,
            tool=action.tool,
            source="workspace:.",
            captured_at=captured_at,
            payload=WorkspaceListPayload(
                path=".",
                recursive=False,
                entries=(),
                truncated=False,
            ),
        )


def test_pure_action_retries_transient_failure_with_auditable_attempts(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    session_id, model_run_id, _ = _seed_model_run(session_factory)
    action = _bound_action(session_id, model_run_id, ToolName.WORKSPACE_LIST, {})
    executor = _FlakyListExecutor()

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        executor=executor,
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.COMPLETED
    assert result.attempt_count == 2
    assert executor.calls == 2
    with session_factory() as db:
        events = db.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.session_id == session_id.root,
                AuditEventRecord.type == EventType.ACTION_FAILED.value,
            )
        ).all()
        assert len(events) == 1
        assert events[0].payload["will_retry"] is True


def test_memory_search_returns_only_current_assessment_heads(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    session_id, model_run_id, config_id = _seed_model_run(session_factory)
    claim_id = ClaimId.new()
    assessment_id = ClaimAssessmentId.new()
    with session_factory.begin() as db:
        db.add(
            ClaimRecord(
                id=claim_id.root,
                statement="The Tool Broker enforces a capability policy.",
                claim_type="external_fact",
                freshness_status="fresh",
                as_of=None,
                observed_at=None,
                reverify_after=None,
                topic="tool-broker",
                created_in_session=session_id.root,
            )
        )
        db.flush()
        db.add(
            ClaimAssessmentRecord(
                id=assessment_id.root,
                claim_id=claim_id.root,
                effective_grade="E2",
                epistemic_status=EpistemicStatus.SUPPORTED.value,
                rules_version="test-rules/v1",
                rules_hash="d" * 64,
                evidence_set_hash="e" * 64,
                assessed_scope=[],
                confidence_basis_points=5500,
                valid=True,
                invalidation_reason=None,
                created_in_session=session_id.root,
                created_at=NOW,
            )
        )
        db.flush()
        db.add(
            ClaimAssessmentHeadRecord(
                claim_id=claim_id.root,
                config_snapshot_id=config_id.root,
                assessment_state="current",
                current_assessment_id=assessment_id.root,
                epistemic_status=EpistemicStatus.SUPPORTED.value,
                prepared_by="session",
                updated_at=NOW,
            )
        )
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.MEMORY_SEARCH,
        {"query": "capability", "limit": 5},
    )

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.COMPLETED
    assert result.observation is not None
    assert len(result.observation.payload.hits) == 1
    assert result.observation.payload.hits[0].claim_id == claim_id


class _FakeSandboxRunner:
    def __init__(self, profile: SandboxProfile) -> None:
        self.profile = profile
        self.calls = 0

    def execute(self, **_kwargs: object) -> SandboxProcessResult:
        self.calls += 1
        stdout = b"42\n"
        return SandboxProcessResult(
            captured=CapturedCommand(
                exit_code=0,
                stdout=stdout.decode(),
                stderr="",
                stdout_bytes=len(stdout),
                stderr_bytes=0,
                stdout_sha256=hashlib.sha256(stdout).hexdigest(),
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stderr_truncated=False,
                duration_ms=3,
            ),
            environment=SandboxEnvironmentManifest.from_profile(
                self.profile,
                runtime_version="5.4.2",
            ),
            workspace_bytes_before=0,
            workspace_bytes_after=0,
            workspace_quota_exceeded=False,
        )


def test_python_action_executes_only_through_policy_bound_sandbox(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    profile = SandboxProfile(image=f"localhost/noezema-sandbox@sha256:{'a' * 64}")
    policy = sandbox_mvp_capability_policy(sandbox=profile)
    session_id, model_run_id, _ = _seed_model_run(
        session_factory,
        policy=policy,
    )
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.PYTHON_EXECUTE,
        {"code": "print(6 * 7)"},
    )
    runner = _FakeSandboxRunner(profile)
    broker = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        policy=policy,
        sandbox_runner=runner,
        clock=lambda: NOW,
    )

    completed = broker.run(action)
    replayed = broker.run(action)

    assert completed.state is ActionState.COMPLETED
    assert completed.observation is not None
    assert completed.observation.payload.kind == "python_execution"
    assert completed.observation.payload.stdout == "42\n"
    assert completed.observation.payload.environment.network == "none"
    assert completed.observation == replayed.observation
    assert runner.calls == 1


def test_model_cannot_raise_the_sandbox_timeout_above_policy_limit(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    profile = SandboxProfile(image=f"localhost/noezema-sandbox@sha256:{'a' * 64}")
    policy = sandbox_mvp_capability_policy(sandbox=profile)
    session_id, model_run_id, _ = _seed_model_run(
        session_factory,
        policy=policy,
    )
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.PYTHON_EXECUTE,
        {"code": "print('never runs')", "timeout_ms": policy.action_timeout_ms + 1},
    )
    runner = _FakeSandboxRunner(profile)

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        policy=policy,
        sandbox_runner=runner,
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.POLICY_EVALUATED
    assert result.policy.reason == "action_timeout_limit_exceeded"
    assert runner.calls == 0
