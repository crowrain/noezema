"""COW workspace and content-addressed artifact publication tests."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from packages.artifacts import ContentAddressedArtifactStore
from packages.domain import (
    ActionState,
    ArtifactCreatePayload,
    CapabilityPolicy,
    EventType,
    ModelRunId,
    SandboxProfile,
    ToolName,
    TurnId,
    WorkspaceReadPayload,
    WorkspaceWritePayload,
    transactional_workspace_mvp_capability_policy,
)
from packages.persistence.models import (
    ActionRecord,
    ArtifactBlobRecord,
    ArtifactRecord,
    AuditEventRecord,
    DomainRevisionRecord,
    ModelRunRecord,
    WorkspaceFileRecord,
    WorkspaceVersionRecord,
    WriterIntentRecord,
)
from packages.tool_broker import ToolBroker, ToolExecutionError, WorkspaceArtifactPublisher
from tests.unit.tool_broker.test_service import (
    NOW,
    _bound_action,
    _FakeSandboxRunner,
    _seed_model_run,
)


def _add_model_run(
    session_factory: sessionmaker[Session],
    *,
    session_id,
) -> ModelRunId:
    model_run_id = ModelRunId.new()
    with session_factory.begin() as db:
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
    return model_run_id


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    store = tmp_path / "artifact-store"
    workspace.mkdir()
    return workspace, store


def test_workspace_write_publishes_manifest_atomically_and_replays(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace, store = _roots(tmp_path)
    policy = transactional_workspace_mvp_capability_policy()
    session_id, model_run_id, _ = _seed_model_run(session_factory, policy=policy)
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.WORKSPACE_WRITE,
        {
            "path": "notes/insight.txt",
            "content": "A durable insight.",
            "expected_content_sha256": "absent",
        },
    )
    broker = ToolBroker(
        session_factory=session_factory,
        workspace_root=workspace,
        artifact_store_root=store,
        policy=policy,
        clock=lambda: NOW,
    )

    completed = broker.run(action)
    replayed = broker.run(action)

    assert completed.state is ActionState.COMPLETED
    assert completed.observation == replayed.observation
    assert isinstance(completed.observation.payload, WorkspaceWritePayload)
    assert not (workspace / "notes" / "insight.txt").exists()
    payload = completed.observation.payload
    assert (store / "blobs" / payload.content_sha256[:2] / payload.content_sha256).is_file()
    with session_factory() as db:
        manifest = db.get(WorkspaceFileRecord, "notes/insight.txt")
        versions = db.scalars(select(WorkspaceVersionRecord)).all()
        artifacts = db.scalars(select(ArtifactRecord)).all()
        completion = db.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.session_id == session_id.root,
                AuditEventRecord.type == EventType.ACTION_COMPLETED.value,
            )
        )
        assert manifest is not None
        assert manifest.revision == 1
        assert manifest.content_sha256 == payload.content_sha256
        assert len(versions) == 1
        assert len(artifacts) == 1
        assert completion is not None
        assert completion.payload["published_effect"]["workspace_revision"] == 1
        revision_vector = completion.payload["published_effect"]["revision_vector"]
        assert revision_vector["workspace"] == 1
        assert revision_vector["artifact_store"] == 1
        workspace_intent = db.get(WriterIntentRecord, "workspace")
        artifact_intent = db.get(WriterIntentRecord, "artifact_store")
        assert workspace_intent is not None
        assert artifact_intent is not None
        assert workspace_intent.fence == 1
        assert workspace_intent.holder_operation_id is None
        assert artifact_intent.fence == 1
        assert artifact_intent.holder_operation_id is None

    read_run_id = _add_model_run(session_factory, session_id=session_id)
    read = broker.run(
        _bound_action(
            session_id,
            read_run_id,
            ToolName.WORKSPACE_READ,
            {"path": "notes/insight.txt"},
        )
    )
    assert isinstance(read.observation.payload, WorkspaceReadPayload)
    assert read.observation.payload.content == "A durable insight."


def test_compare_and_swap_conflict_leaves_only_an_unreachable_blob(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace, store = _roots(tmp_path)
    (workspace / "claim.txt").write_text("newer content", encoding="utf-8")
    policy = transactional_workspace_mvp_capability_policy()
    session_id, model_run_id, _ = _seed_model_run(session_factory, policy=policy)
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.WORKSPACE_WRITE,
        {
            "path": "claim.txt",
            "content": "stale overwrite",
            "expected_content_sha256": "absent",
        },
    )

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=workspace,
        artifact_store_root=store,
        policy=policy,
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.FAILED
    assert result.error.code == "workspace_revision_conflict"
    assert (workspace / "claim.txt").read_text(encoding="utf-8") == "newer content"
    assert len(list((store / "blobs").rglob("*"))) > 0
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ArtifactRecord)) == 0
        assert db.scalar(select(func.count()).select_from(WorkspaceFileRecord)) == 0
        assert db.scalar(select(func.count()).select_from(WorkspaceVersionRecord)) == 0


def test_artifact_create_deduplicates_blob_but_not_logical_artifacts(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace, store = _roots(tmp_path)
    policy = transactional_workspace_mvp_capability_policy()
    session_id, first_run_id, _ = _seed_model_run(session_factory, policy=policy)
    second_run_id = _add_model_run(session_factory, session_id=session_id)
    broker = ToolBroker(
        session_factory=session_factory,
        workspace_root=workspace,
        artifact_store_root=store,
        policy=policy,
        clock=lambda: NOW,
    )

    first = broker.run(
        _bound_action(
            session_id,
            first_run_id,
            ToolName.ARTIFACT_CREATE,
            {"name": "reports/one.md", "media_type": "text/markdown", "content": "same"},
        )
    )
    second = broker.run(
        _bound_action(
            session_id,
            second_run_id,
            ToolName.ARTIFACT_CREATE,
            {"name": "reports/two.md", "media_type": "text/markdown", "content": "same"},
        )
    )

    assert isinstance(first.observation.payload, ArtifactCreatePayload)
    assert isinstance(second.observation.payload, ArtifactCreatePayload)
    assert first.observation.payload.content_sha256 == second.observation.payload.content_sha256
    assert first.observation.payload.artifact_id != second.observation.payload.artifact_id
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ArtifactBlobRecord)) == 1
        assert db.scalar(select(func.count()).select_from(ArtifactRecord)) == 2


class _FailAfterPublish:
    def __init__(self, inner: WorkspaceArtifactPublisher) -> None:
        self._inner = inner

    def publish(self, db, *, action, observation, session_lease, published_at):
        self._inner.publish(
            db,
            action=action,
            observation=observation,
            session_lease=session_lease,
            published_at=published_at,
        )
        raise ToolExecutionError(
            "publication_failpoint",
            "Injected failure after staging database effects.",
            retryable=False,
        )


def test_failure_after_effect_staging_rolls_back_manifest_artifact_and_audit(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace, store_root = _roots(tmp_path)
    policy = transactional_workspace_mvp_capability_policy()
    session_id, model_run_id, _ = _seed_model_run(session_factory, policy=policy)
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.WORKSPACE_WRITE,
        {
            "path": "partial.txt",
            "content": "must not become visible",
            "expected_content_sha256": "absent",
        },
    )
    store = ContentAddressedArtifactStore(store_root)
    publisher = WorkspaceArtifactPublisher(
        store=store,
        workspace_root=workspace,
        max_workspace_total_bytes=policy.max_workspace_total_bytes,
        max_artifact_store_bytes=policy.max_artifact_store_bytes,
    )

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=workspace,
        artifact_store_root=store_root,
        policy=policy,
        effect_publisher=_FailAfterPublish(publisher),
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.FAILED
    assert result.error.code == "publication_failpoint"
    with session_factory() as db:
        record = db.get(ActionRecord, action.action_id.root)
        assert record is not None and record.state == ActionState.FAILED.value
        assert db.scalar(select(func.count()).select_from(ArtifactBlobRecord)) == 0
        assert db.scalar(select(func.count()).select_from(ArtifactRecord)) == 0
        assert db.scalar(select(func.count()).select_from(WorkspaceFileRecord)) == 0
        completion_count = db.scalar(
            select(func.count())
            .select_from(AuditEventRecord)
            .where(
                AuditEventRecord.session_id == session_id.root,
                AuditEventRecord.type == EventType.ACTION_COMPLETED.value,
            )
        )
        assert completion_count == 0
        assert db.get(DomainRevisionRecord, "workspace").revision == 0
        assert db.get(DomainRevisionRecord, "artifact_store").revision == 0
        workspace_intent = db.get(WriterIntentRecord, "workspace")
        artifact_intent = db.get(WriterIntentRecord, "artifact_store")
        assert workspace_intent is not None
        assert artifact_intent is not None
        assert workspace_intent.fence == 0
        assert workspace_intent.holder_operation_id is None
        assert artifact_intent.fence == 0
        assert artifact_intent.holder_operation_id is None
    assert any(path.is_file() for path in (store_root / "blobs").rglob("*"))


def test_workspace_logical_quota_includes_read_only_seed_files(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace, store = _roots(tmp_path)
    (workspace / "seed.bin").write_bytes(b"x" * 1_048_570)
    policy = transactional_workspace_mvp_capability_policy().model_copy(
        update={"max_workspace_total_bytes": 1_048_576}
    )
    session_id, model_run_id, _ = _seed_model_run(session_factory, policy=policy)
    action = _bound_action(
        session_id,
        model_run_id,
        ToolName.WORKSPACE_WRITE,
        {
            "path": "overflow.txt",
            "content": "ten-bytes!",
            "expected_content_sha256": "absent",
        },
    )

    result = ToolBroker(
        session_factory=session_factory,
        workspace_root=workspace,
        artifact_store_root=store,
        policy=policy,
        clock=lambda: NOW,
    ).run(action)

    assert result.state is ActionState.FAILED
    assert result.error.code == "workspace_quota_exceeded"
    with session_factory() as db:
        assert db.get(WorkspaceFileRecord, "overflow.txt") is None


class _WorkspaceInspectingRunner(_FakeSandboxRunner):
    observed_content: str | None = None

    def execute(self, **kwargs: object):
        workspace_root = kwargs["workspace_root"]
        assert isinstance(workspace_root, Path)
        self.observed_content = (workspace_root / "generated" / "result.txt").read_text(
            encoding="utf-8"
        )
        return super().execute(**kwargs)


def test_sandbox_receives_a_read_only_snapshot_of_the_published_manifest(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace, store = _roots(tmp_path)
    profile = SandboxProfile(image=f"localhost/noezema-sandbox@sha256:{'a' * 64}")
    policy = CapabilityPolicy(
        version="sealed-mvp/transactional-workspace-sandbox/v1",
        allowed_tools=(ToolName.WORKSPACE_WRITE, ToolName.PYTHON_EXECUTE),
        sandbox=profile,
    )
    session_id, write_run_id, _ = _seed_model_run(session_factory, policy=policy)
    compute_run_id = _add_model_run(session_factory, session_id=session_id)
    runner = _WorkspaceInspectingRunner(profile)
    broker = ToolBroker(
        session_factory=session_factory,
        workspace_root=workspace,
        artifact_store_root=store,
        policy=policy,
        sandbox_runner=runner,
        clock=lambda: NOW,
    )
    written = broker.run(
        _bound_action(
            session_id,
            write_run_id,
            ToolName.WORKSPACE_WRITE,
            {
                "path": "generated/result.txt",
                "content": "visible only through COW snapshot",
                "expected_content_sha256": "absent",
            },
        )
    )
    computed = broker.run(
        _bound_action(
            session_id,
            compute_run_id,
            ToolName.PYTHON_EXECUTE,
            {"code": "print(open('generated/result.txt').read())"},
        )
    )

    assert written.state is ActionState.COMPLETED
    assert computed.state is ActionState.COMPLETED
    assert runner.observed_content == "visible only through COW snapshot"
    assert not (workspace / "generated" / "result.txt").exists()
