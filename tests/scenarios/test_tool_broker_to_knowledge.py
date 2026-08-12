"""Explorer action -> Tool Broker -> evidence -> assessment -> commit scenario."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import SessionStarted, start_next_session
from packages.cognition import (
    CuratorClaimProposal,
    CuratorContext,
    CuratorOutcome,
    CuratorProposal,
    EvidenceReference,
    EvidenceRelation,
    ProtocolObservation,
    ProtocolQuestion,
    enqueue_question,
    validate_curator_proposal,
)
from packages.domain import (
    ClaimType,
    CommitAttemptId,
    ConfigSnapshotId,
    EpistemicStatus,
    EvidenceAdapterBudget,
    ModelRunId,
    QuestionDraft,
    ScopeDimension,
    SessionId,
    ToolDecision,
    ToolName,
    TurnId,
    VerifiedEvidenceMetadata,
    bind_action,
    canonical_json_sha256,
    capability_policy_config_payload,
    sealed_mvp_capability_policy,
    workspace_read_as_source_observation,
)
from packages.memory import (
    build_knowledge_commit_batch,
    finalize_knowledge_commit,
    mvp_claim_type_rules,
    prepare_knowledge_commit,
)
from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    acquire_session_lease,
    bootstrap_payload,
)
from packages.persistence.models import (
    ActionRecord,
    ClaimAssessmentRecord,
    ClaimRecord,
    ConfigSnapshotRecord,
    EvidenceRecord,
    ModelRunRecord,
    RuntimeConfigHeadRecord,
)
from packages.tool_broker import ToolBroker

NOW = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)


def _start_session_with_rules(
    session_factory: sessionmaker[Session],
) -> tuple[SessionId, QuestionDraft, ModelRunId]:
    session_id = SessionId.new()
    model_run_id = ModelRunId.new()
    rules = mvp_claim_type_rules()
    config_id = ConfigSnapshotId.new()
    payload = bootstrap_payload()
    payload["claim_type_rules"] = rules.model_dump(mode="json")
    payload["policy"] = capability_policy_config_payload(sealed_mvp_capability_policy())
    payload_sha256 = canonical_json_sha256(payload)
    with session_factory.begin() as db:
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
        question = QuestionDraft.seeded(
            text="Which capabilities does the MVP Tool Broker expose?",
            origin_config_snapshot_id=config_id,
            created_at=NOW,
        )
        enqueue_question(db, question)
        started = start_next_session(db, session_id=session_id, occurred_at=NOW)
        assert isinstance(started, SessionStarted)
        db.add(
            ModelRunRecord(
                id=model_run_id.root,
                session_id=session_id.root,
                turn_id=TurnId.new().root,
                phase="exploration",
                model_fingerprint="a" * 64,
                context_manifest_sha256="b" * 64,
                context_manifest={"question_id": str(question.id)},
                prompt_version="explorer/v1",
                tool_schema_sha256="c" * 64,
                input_tokens=20,
                output_tokens=10,
                latency_ms=1,
                finish_reason="stop",
                output_schema_valid=True,
                raw_response_artifact=None,
                created_at=NOW,
            )
        )
    return session_id, question, model_run_id


def test_workspace_read_flows_through_assessment_and_atomic_commit(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    statement = "The MVP Tool Broker exposes three read-only tools."
    (tmp_path / "tool-policy.txt").write_text(statement, encoding="utf-8")
    session_id, question, model_run_id = _start_session_with_rules(session_factory)
    action = bind_action(
        session_id=session_id,
        model_run_id=model_run_id,
        decision=ToolDecision(
            kind="tool",
            tool=ToolName.WORKSPACE_READ,
            arguments={"path": "tool-policy.txt"},
        ),
    )
    broker_result = ToolBroker(
        session_factory=session_factory,
        workspace_root=tmp_path,
        clock=lambda: NOW,
    ).run(action)
    assert broker_result.observation is not None
    source_observation = workspace_read_as_source_observation(broker_result.observation)

    proposal = CuratorProposal(
        public_summary="A local policy document was converted into a bounded claim.",
        outcome=CuratorOutcome.PROGRESS,
        claims=(
            CuratorClaimProposal(
                ref="tool_policy",
                statement=statement,
                claim_type=ClaimType.EXTERNAL_FACT,
                topic="tool-broker",
                evidence=(
                    EvidenceReference(
                        observation_id=source_observation.id,
                        relation=EvidenceRelation.SUPPORTS,
                        scope="The exact claim statement.",
                    ),
                ),
            ),
        ),
    )
    curator_context = CuratorContext(
        question=ProtocolQuestion(id=question.id, text=question.text),
        observations=(
            ProtocolObservation.from_typed(
                source_observation,
                public_summary="An exact UTF-8 workspace source.",
            ),
        ),
        allowed_claim_types=(ClaimType.EXTERNAL_FACT,),
        remaining_claim_budget=1,
        remaining_evidence_link_budget=1,
        remaining_handoff_budget=0,
    )
    validate_curator_proposal(proposal, context=curator_context)
    batch = build_knowledge_commit_batch(
        proposal,
        observations=(source_observation,),
        metadata=(
            VerifiedEvidenceMetadata(
                observation_id=source_observation.id,
                covered_scope=(ScopeDimension.CLAIM,),
                integrity_checked=True,
                independence_group="local-policy-document",
            ),
        ),
        evidence_budget=EvidenceAdapterBudget(remaining_evidence_items=1),
        rules=mvp_claim_type_rules(),
        validated_knowledge_revision=0,
        validated_dependency_graph_revision=0,
    )
    assert batch.claims[0].assessment.epistemic_status is EpistemicStatus.HYPOTHESIS

    attempt_id = CommitAttemptId.new()
    with session_factory.begin() as db:
        lease = acquire_session_lease(
            db,
            session_id=session_id,
            owner="orchestrator",
            ttl_seconds=300,
            occurred_at=NOW,
        )
        prepare_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            batch=batch,
            session_lease=lease,
            occurred_at=NOW,
        )
    with session_factory.begin() as db:
        committed = finalize_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            session_lease=lease,
            occurred_at=NOW,
        )

    with session_factory() as db:
        action_record = db.get(ActionRecord, action.action_id.root)
        claim = db.get(ClaimRecord, committed.claim_ids[0].root)
        assessment = db.get(
            ClaimAssessmentRecord,
            committed.assessment_ids[0].root,
        )
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.claim_id == committed.claim_ids[0].root)
        )

        assert action_record is not None
        assert claim is not None
        assert assessment is not None
        assert evidence is not None
        assert action_record.state == "completed"
        assert claim.statement == statement
        assert assessment.epistemic_status == EpistemicStatus.HYPOTHESIS.value
        assert evidence.observation["provenance"]["action_id"] == str(action.action_id)
        assert evidence.observation["provenance"]["tool"] == ToolName.WORKSPACE_READ.value
