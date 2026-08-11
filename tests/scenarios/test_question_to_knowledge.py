"""First vertical scenario: question -> evidence -> assessment -> commit."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
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
    ActionId,
    ChunkId,
    ClaimType,
    CommitAttemptId,
    ConfigSnapshotId,
    EpistemicStatus,
    EvidenceAdapterBudget,
    EvidenceGrade,
    EvidenceKind,
    ObservationId,
    ObservationProvenance,
    QuestionDraft,
    ScopeDimension,
    SessionId,
    SessionState,
    SourceId,
    SourceObservation,
    SourceRange,
    ToolName,
    VerifiedEvidenceMetadata,
    canonical_json_sha256,
)
from packages.memory import (
    KnowledgeRevisionConflictError,
    build_knowledge_commit_batch,
    finalize_knowledge_commit,
    mvp_claim_type_rules,
    prepare_knowledge_commit,
)
from packages.persistence import BOOTSTRAP_CONFIG_SNAPSHOT_ID, bootstrap_payload
from packages.persistence.models import (
    AssessmentEvidenceRecord,
    AuditEventRecord,
    CheckpointRecord,
    ClaimAssessmentHeadRecord,
    ClaimAssessmentRecord,
    ClaimRecord,
    CommitAttemptRecord,
    ConfigSnapshotRecord,
    DomainRevisionRecord,
    EvidenceRecord,
    OutboxEventRecord,
    QuestionRecord,
    RuntimeConfigHeadRecord,
    SessionRecord,
    SessionStagingRecord,
)

NOW = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)


def _count(db: Session, record: type[object]) -> int:
    value = db.scalar(select(func.count()).select_from(record))
    assert value is not None
    return value


def _install_mvp_rules(db: Session) -> ConfigSnapshotId:
    rules = mvp_claim_type_rules()
    config_id = ConfigSnapshotId.new()
    payload = bootstrap_payload()
    payload["claim_type_rules"] = rules.model_dump(mode="json")
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
    head = db.get(RuntimeConfigHeadRecord, "global")
    assert head is not None
    head.active_config_snapshot_id = config_id.root
    head.updated_at = NOW
    return config_id


def _start_question(session_factory: sessionmaker[Session]) -> tuple[SessionId, QuestionDraft]:
    session_id = SessionId.new()
    with session_factory.begin() as db:
        config_id = _install_mvp_rules(db)
        question = QuestionDraft.seeded(
            text="Is the NOEZEMA rules engine deterministic?",
            origin_config_snapshot_id=config_id,
            created_at=NOW,
        )
        enqueue_question(db, question)
        started = start_next_session(db, session_id=session_id, occurred_at=NOW)
        assert isinstance(started, SessionStarted)
    return session_id, question


def _source_observation(seed: int) -> SourceObservation:
    marker = "123456789abcdef"[seed]
    return SourceObservation(
        id=ObservationId.new(),
        kind=EvidenceKind.SOURCE_ASSERTION,
        payload_sha256=marker * 64,
        provenance=ObservationProvenance(
            action_id=ActionId.new(),
            tool=ToolName.WEB_FETCH,
            source=f"https://publisher-{seed}.example.test/noezema",
            captured_at=NOW,
        ),
        source_id=SourceId.new(),
        chunk_id=ChunkId.new(),
        source_content_sha256=marker * 64,
        chunk_sha256="123456789abcdef"[seed + 1] * 64,
        normalized_range=SourceRange(
            unit="text",
            start=0,
            end=100,
            normalization_version="text/v1",
        ),
    )


def _build_external_fact_batch(
    question: QuestionDraft,
    *,
    support_count: int = 2,
    include_counter: bool = False,
):
    observations = tuple(_source_observation(index * 2) for index in range(support_count))
    counter = _source_observation(8) if include_counter else None
    all_observations = observations + ((counter,) if counter is not None else ())
    references = tuple(
        EvidenceReference(
            observation_id=item.id,
            relation=EvidenceRelation.SUPPORTS,
            scope="The exact claim statement.",
        )
        for item in observations
    )
    if counter is not None:
        references += (
            EvidenceReference(
                observation_id=counter.id,
                relation=EvidenceRelation.COUNTERS,
                scope="The exact claim statement.",
            ),
        )
    proposal = CuratorProposal(
        public_summary="Source observations were converted into one bounded claim.",
        outcome=CuratorOutcome.PROGRESS,
        claims=(
            CuratorClaimProposal(
                ref="claim_1",
                statement="The NOEZEMA rules engine is deterministic for a fixed input.",
                claim_type=ClaimType.EXTERNAL_FACT,
                topic="rules-engine",
                evidence=references,
            ),
        ),
    )
    context = CuratorContext(
        question=ProtocolQuestion(id=question.id, text=question.text),
        observations=tuple(
            ProtocolObservation.from_typed(item, public_summary="A source assertion.")
            for item in all_observations
        ),
        allowed_claim_types=(ClaimType.EXTERNAL_FACT,),
        remaining_claim_budget=1,
        remaining_evidence_link_budget=len(all_observations),
        remaining_handoff_budget=0,
    )
    validate_curator_proposal(proposal, context=context)
    metadata = tuple(
        VerifiedEvidenceMetadata(
            observation_id=item.id,
            covered_scope=(ScopeDimension.CLAIM,),
            integrity_checked=True,
            independence_group=f"publisher-{index}",
        )
        for index, item in enumerate(all_observations)
    )
    return build_knowledge_commit_batch(
        proposal,
        observations=all_observations,
        metadata=metadata,
        evidence_budget=EvidenceAdapterBudget(remaining_evidence_items=len(all_observations)),
        rules=mvp_claim_type_rules(),
        validated_knowledge_revision=0,
        validated_dependency_graph_revision=0,
    )


def test_supported_question_is_assessed_and_committed_atomically(
    session_factory: sessionmaker[Session],
) -> None:
    session_id, question = _start_question(session_factory)
    batch = _build_external_fact_batch(question)
    attempt_id = CommitAttemptId.new()

    assert batch.claims[0].assessment.effective_grade is EvidenceGrade.E3
    assert batch.claims[0].assessment.epistemic_status is EpistemicStatus.SUPPORTED

    with session_factory.begin() as db:
        prepared = prepare_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            batch=batch,
            occurred_at=NOW,
        )
        assert prepared.status == "prepared"
        assert (
            prepare_knowledge_commit(
                db,
                session_id=session_id,
                attempt_id=attempt_id,
                batch=batch,
                occurred_at=NOW,
            )
            == prepared
        )

    with session_factory() as db:
        assert _count(db, ClaimRecord) == 0
        assert _count(db, EvidenceRecord) == 0
        assert _count(db, ClaimAssessmentRecord) == 0
        assert _count(db, SessionStagingRecord) == 1

    with session_factory.begin() as db:
        committed = finalize_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            occurred_at=NOW,
        )

    with session_factory() as db:
        session_record = db.get(SessionRecord, session_id.root)
        question_record = db.get(QuestionRecord, question.id.root)
        attempt = db.get(CommitAttemptRecord, attempt_id.root)
        assessment = db.get(ClaimAssessmentRecord, committed.assessment_ids[0].root)
        head = db.get(
            ClaimAssessmentHeadRecord,
            (committed.claim_ids[0].root, session_record.config_snapshot_id),
        )
        revision = db.get(DomainRevisionRecord, "knowledge")

        assert session_record is not None
        assert question_record is not None
        assert attempt is not None
        assert assessment is not None
        assert head is not None
        assert revision is not None
        assert session_record.state == SessionState.SUCCEEDED.value
        assert question_record.state == "answered"
        assert attempt.status == "committed"
        assert assessment.effective_grade == EvidenceGrade.E3.value
        assert assessment.epistemic_status == EpistemicStatus.SUPPORTED.value
        assert head.current_assessment_id == assessment.id
        assert head.epistemic_status == EpistemicStatus.SUPPORTED.value
        assert revision.revision == 1
        assert _count(db, ClaimRecord) == 1
        assert _count(db, EvidenceRecord) == 2
        assert _count(db, AssessmentEvidenceRecord) == 2
        assert _count(db, CheckpointRecord) == 1
        assert _count(db, SessionStagingRecord) == 0
        assert _count(db, AuditEventRecord) == 6
        assert _count(db, OutboxEventRecord) == 6

    with session_factory.begin() as db:
        replayed = finalize_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            occurred_at=NOW,
        )
    assert replayed == committed
    with session_factory() as db:
        assert _count(db, ClaimRecord) == 1
        assert _count(db, EvidenceRecord) == 2
        assert _count(db, ClaimAssessmentRecord) == 1
        assert _count(db, CheckpointRecord) == 1
        assert db.get(DomainRevisionRecord, "knowledge").revision == 1


def test_failure_inside_final_transaction_leaves_only_prepared_staging(
    session_factory: sessionmaker[Session],
) -> None:
    session_id, question = _start_question(session_factory)
    batch = _build_external_fact_batch(question)
    attempt_id = CommitAttemptId.new()
    with session_factory.begin() as db:
        prepare_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            batch=batch,
            occurred_at=NOW,
        )

    def failpoint() -> None:
        raise RuntimeError("simulated finalizer crash")

    with pytest.raises(RuntimeError, match="simulated"), session_factory.begin() as db:
        finalize_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            occurred_at=NOW,
            fail_after_domain_apply=failpoint,
        )

    with session_factory() as db:
        attempt = db.get(CommitAttemptRecord, attempt_id.root)
        session_record = db.get(SessionRecord, session_id.root)
        assert attempt is not None
        assert session_record is not None
        assert attempt.status == "prepared"
        assert session_record.state == SessionState.COMMITTING.value
        assert _count(db, SessionStagingRecord) == 1
        assert _count(db, ClaimRecord) == 0
        assert _count(db, EvidenceRecord) == 0
        assert _count(db, ClaimAssessmentRecord) == 0
        assert _count(db, ClaimAssessmentHeadRecord) == 0
        assert _count(db, CheckpointRecord) == 0
        assert db.get(DomainRevisionRecord, "knowledge").revision == 0


def test_insufficient_and_counter_evidence_never_look_supported(
    session_factory: sessionmaker[Session],
) -> None:
    session_id, question = _start_question(session_factory)
    batch = _build_external_fact_batch(question, support_count=1, include_counter=True)
    assessment = batch.claims[0].assessment
    attempt_id = CommitAttemptId.new()

    assert assessment.effective_grade is EvidenceGrade.E2
    assert assessment.epistemic_status is EpistemicStatus.DISPUTED
    with session_factory.begin() as db:
        prepare_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            batch=batch,
            occurred_at=NOW,
        )
    with session_factory.begin() as db:
        committed = finalize_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            occurred_at=NOW,
        )
    with session_factory() as db:
        head = db.scalar(
            select(ClaimAssessmentHeadRecord).where(
                ClaimAssessmentHeadRecord.claim_id == committed.claim_ids[0].root
            )
        )
        assert head is not None
        assert head.epistemic_status == EpistemicStatus.DISPUTED.value
        assert head.epistemic_status != EpistemicStatus.SUPPORTED.value


def test_revision_change_rejects_finalization_without_partial_visibility(
    session_factory: sessionmaker[Session],
) -> None:
    session_id, question = _start_question(session_factory)
    batch = _build_external_fact_batch(question)
    attempt_id = CommitAttemptId.new()
    with session_factory.begin() as db:
        prepare_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            batch=batch,
            occurred_at=NOW,
        )
    with session_factory.begin() as db:
        revision = db.get(DomainRevisionRecord, "knowledge")
        assert revision is not None
        revision.revision += 1

    with pytest.raises(KnowledgeRevisionConflictError), session_factory.begin() as db:
        finalize_knowledge_commit(
            db,
            session_id=session_id,
            attempt_id=attempt_id,
            occurred_at=NOW,
        )

    with session_factory() as db:
        attempt = db.get(CommitAttemptRecord, attempt_id.root)
        assert attempt is not None
        assert attempt.status == "prepared"
        assert _count(db, SessionStagingRecord) == 1
        assert _count(db, ClaimRecord) == 0
        assert _count(db, EvidenceRecord) == 0
        assert _count(db, ClaimAssessmentRecord) == 0
        assert _count(db, CheckpointRecord) == 0
