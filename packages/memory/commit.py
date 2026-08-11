"""Prepared and atomic finalization of one validated knowledge batch."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain import (
    CheckpointId,
    ClaimAssessmentId,
    ClaimId,
    ClaimType,
    ClaimTypeRulesSnapshot,
    CommitAttemptId,
    EpistemicStatus,
    EventType,
    KnowledgeCommitBatch,
    QuestionState,
    SessionId,
    SessionStagingId,
    SessionState,
    assessment_role,
    knowledge_commit_sha256,
)
from packages.persistence import append_session_audit
from packages.persistence.models import (
    AssessmentEvidenceRecord,
    CheckpointRecord,
    ClaimAssessmentHeadRecord,
    ClaimAssessmentRecord,
    ClaimRecord,
    CommitAttemptRecord,
    ConfigSnapshotRecord,
    DomainRevisionRecord,
    EvidenceRecord,
    QuestionRecord,
    SessionRecord,
    SessionStagingRecord,
)


@dataclass(frozen=True, slots=True)
class PreparedKnowledgeCommit:
    attempt_id: CommitAttemptId
    staging_id: SessionStagingId | None
    staging_sha256: str
    status: str


@dataclass(frozen=True, slots=True)
class CommittedKnowledge:
    attempt_id: CommitAttemptId
    session_id: SessionId
    checkpoint_id: CheckpointId
    knowledge_revision: int
    dependency_graph_revision: int
    claim_ids: tuple[ClaimId, ...]
    assessment_ids: tuple[ClaimAssessmentId, ...]
    epistemic_statuses: tuple[EpistemicStatus, ...]


class KnowledgeCommitError(RuntimeError):
    """Base class for a rejected prepared or final knowledge commit."""


class CommitAttemptConflictError(KnowledgeCommitError):
    """A session or attempt ID was already bound to different staged content."""


class KnowledgeRevisionConflictError(KnowledgeCommitError):
    """The validated domain revision vector is no longer current."""


class ConfigRulesMismatchError(KnowledgeCommitError):
    """The batch was not assessed with the session's immutable rules snapshot."""


class InvalidCommitStateError(KnowledgeCommitError):
    """The session/attempt tuple cannot cross the commit boundary."""


def prepare_knowledge_commit(
    db: Session,
    *,
    session_id: SessionId,
    attempt_id: CommitAttemptId,
    batch: KnowledgeCommitBatch,
    occurred_at: datetime | None = None,
) -> PreparedKnowledgeCommit:
    """Persist the immutable prepared boundary; the caller owns COMMIT/rollback."""

    timestamp = occurred_at or datetime.now(UTC)
    staging_hash = knowledge_commit_sha256(batch)
    session_record = db.scalar(
        select(SessionRecord).where(SessionRecord.id == session_id.root).with_for_update()
    )
    if session_record is None:
        raise InvalidCommitStateError(f"session does not exist: {session_id}")

    existing = db.scalar(
        select(CommitAttemptRecord).where(CommitAttemptRecord.session_id == session_id.root)
    )
    if existing is not None:
        staging = db.scalar(
            select(SessionStagingRecord).where(SessionStagingRecord.attempt_id == existing.id)
        )
        if (
            existing.id != attempt_id.root
            or existing.staging_hash != staging_hash
            or (staging is not None and staging.payload_hash != staging_hash)
            or (staging is None and existing.status != "committed")
        ):
            raise CommitAttemptConflictError(
                "session commit attempt is already bound to different content"
            )
        return PreparedKnowledgeCommit(
            attempt_id=attempt_id,
            staging_id=SessionStagingId(root=staging.id) if staging is not None else None,
            staging_sha256=staging_hash,
            status=existing.status,
        )

    if session_record.state in {state.value for state in SessionState if state.is_terminal}:
        raise InvalidCommitStateError("terminal session cannot prepare a knowledge commit")
    if session_record.question_id is None:
        raise InvalidCommitStateError("knowledge commit session has no bound question")

    _verify_config_rules(db, session_record, batch)
    _verify_revision_vector(db, batch, lock=False)

    staging_id = SessionStagingId.new()
    previous_state = session_record.state
    db.add(
        CommitAttemptRecord(
            id=attempt_id.root,
            session_id=session_id.root,
            status="prepared",
            validated_knowledge_revision=batch.validated_knowledge_revision,
            validated_dependency_graph_revision=batch.validated_dependency_graph_revision,
            staging_hash=staging_hash,
            terminal_state=batch.terminal_state.value,
            prepared_at=timestamp,
            resolved_at=None,
            last_error=None,
        )
    )
    db.flush()
    session_record.state = SessionState.COMMITTING.value
    session_record.commit_attempt_id = attempt_id.root
    session_record.updated_at = timestamp
    db.add(
        SessionStagingRecord(
            id=staging_id.root,
            session_id=session_id.root,
            attempt_id=attempt_id.root,
            aggregate_type="knowledge_batch",
            operation="insert",
            payload=batch.model_dump(mode="json"),
            payload_hash=staging_hash,
            schema_version=1,
            validation_status="verified",
            validated_knowledge_revision=batch.validated_knowledge_revision,
            validated_dependency_graph_revision=batch.validated_dependency_graph_revision,
            validation_rules_hash=batch.rules_sha256,
            created_at=timestamp,
        )
    )
    db.flush()
    append_session_audit(
        db,
        session_id=session_id,
        type=EventType.SESSION_STATE_CHANGED,
        occurred_at=timestamp,
        actor="orchestrator",
        public_summary="Session entered commit boundary",
        topic="audit.session_state_changed.v1",
        payload={
            "from": previous_state,
            "to": SessionState.COMMITTING.value,
            "attempt_id": str(attempt_id),
        },
    )
    append_session_audit(
        db,
        session_id=session_id,
        type=EventType.COMMIT_ATTEMPT_PREPARED,
        occurred_at=timestamp,
        actor="orchestrator",
        public_summary="Knowledge commit prepared",
        topic="audit.commit_attempt_prepared.v1",
        payload={
            "attempt_id": str(attempt_id),
            "staging_sha256": staging_hash,
            "validated_knowledge_revision": batch.validated_knowledge_revision,
            "validated_dependency_graph_revision": (batch.validated_dependency_graph_revision),
        },
    )
    return PreparedKnowledgeCommit(
        attempt_id=attempt_id,
        staging_id=staging_id,
        staging_sha256=staging_hash,
        status="prepared",
    )


def finalize_knowledge_commit(
    db: Session,
    *,
    session_id: SessionId,
    attempt_id: CommitAttemptId,
    occurred_at: datetime | None = None,
    fail_after_domain_apply: Callable[[], None] | None = None,
) -> CommittedKnowledge:
    """Apply a prepared batch and terminal records in one caller-owned transaction."""

    timestamp = occurred_at or datetime.now(UTC)
    session_record = db.scalar(
        select(SessionRecord).where(SessionRecord.id == session_id.root).with_for_update()
    )
    if session_record is None:
        raise InvalidCommitStateError(f"session does not exist: {session_id}")

    revisions = _revision_records(db, lock=True)
    attempt = db.scalar(
        select(CommitAttemptRecord)
        .where(CommitAttemptRecord.id == attempt_id.root)
        .with_for_update()
    )
    if attempt is None or attempt.session_id != session_id.root:
        raise InvalidCommitStateError("commit attempt is not bound to the session")
    if attempt.status == "committed":
        return _load_committed_result(db, attempt_id=attempt_id, session_id=session_id)
    if attempt.status != "prepared":
        raise InvalidCommitStateError(f"commit attempt is not finalizable: {attempt.status}")
    if (
        session_record.state != SessionState.COMMITTING.value
        or session_record.commit_attempt_id != attempt_id.root
    ):
        raise InvalidCommitStateError("session is outside the prepared commit fence")

    staging = db.scalar(
        select(SessionStagingRecord).where(SessionStagingRecord.attempt_id == attempt_id.root)
    )
    if staging is None or staging.validation_status != "verified":
        raise InvalidCommitStateError("verified session staging is missing")
    batch = KnowledgeCommitBatch.model_validate_json(
        json.dumps(staging.payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    if staging.payload_hash != knowledge_commit_sha256(batch):
        raise InvalidCommitStateError("staging payload hash does not match its content")
    if attempt.staging_hash != staging.payload_hash:
        raise InvalidCommitStateError("attempt and staging hashes do not match")

    knowledge_revision, dependency_revision = revisions
    if (
        knowledge_revision.revision != batch.validated_knowledge_revision
        or dependency_revision.revision != batch.validated_dependency_graph_revision
    ):
        raise KnowledgeRevisionConflictError("domain revision vector changed after validation")
    _verify_config_rules(db, session_record, batch)

    rules = _session_rules(db, session_record)
    for claim in batch.claims:
        rule = next(item for item in rules.rules if item.claim_type is claim.claim_type)
        captured_at = tuple(
            item.facts.proposal.observation.provenance.captured_at for item in claim.evidence
        )
        observed_at = (
            max(captured_at)
            if captured_at
            and claim.claim_type in {ClaimType.LOCAL_OBSERVATION, ClaimType.SELF_MODEL}
            else None
        )
        reverify_after = (
            timestamp + timedelta(seconds=rule.reverify_after_seconds)
            if rule.reverify_after_seconds is not None
            else None
        )
        db.add(
            ClaimRecord(
                id=claim.id.root,
                statement=claim.statement,
                claim_type=claim.claim_type.value,
                freshness_status="fresh" if reverify_after is not None else "unknown",
                as_of=claim.assessment.as_of,
                observed_at=observed_at,
                reverify_after=reverify_after,
                topic=claim.topic,
                created_in_session=session_id.root,
            )
        )
    db.flush()

    for claim in batch.claims:
        for evidence in claim.evidence:
            facts = evidence.facts
            db.add(
                EvidenceRecord(
                    id=evidence.id.root,
                    claim_id=claim.id.root,
                    relation=facts.proposal.relation.value,
                    evidence_kind=facts.proposal.evidence_kind.value,
                    identity_hash=facts.proposal.identity_sha256,
                    scope=facts.proposal.scope,
                    observation=facts.proposal.observation.model_dump(mode="json"),
                    covered_scope=[item.value for item in facts.covered_scope],
                    integrity_checked=facts.integrity_checked,
                    independence_group=facts.independence_group,
                    successful=facts.successful,
                    created_in_session=session_id.root,
                )
            )
    db.flush()

    for claim in batch.claims:
        assessment = claim.assessment
        db.add(
            ClaimAssessmentRecord(
                id=claim.assessment_id.root,
                claim_id=claim.id.root,
                effective_grade=assessment.effective_grade.value,
                epistemic_status=assessment.epistemic_status.value,
                rules_version=assessment.rules_version,
                rules_hash=assessment.rules_sha256,
                evidence_set_hash=assessment.evidence_set_sha256,
                assessed_scope=[item.value for item in assessment.assessed_scope],
                confidence_basis_points=assessment.confidence_basis_points,
                valid=True,
                invalidation_reason=None,
                created_in_session=session_id.root,
                created_at=timestamp,
            )
        )
    db.flush()

    for claim in batch.claims:
        for evidence in claim.evidence:
            db.add(
                AssessmentEvidenceRecord(
                    assessment_id=claim.assessment_id.root,
                    evidence_id=evidence.id.root,
                    role=assessment_role(evidence, claim.assessment),
                )
            )
        db.add(
            ClaimAssessmentHeadRecord(
                claim_id=claim.id.root,
                config_snapshot_id=session_record.config_snapshot_id,
                assessment_state="current",
                current_assessment_id=claim.assessment_id.root,
                epistemic_status=claim.assessment.epistemic_status.value,
                prepared_by="session",
                updated_at=timestamp,
            )
        )
    db.flush()

    if fail_after_domain_apply is not None:
        fail_after_domain_apply()

    knowledge_revision.revision += 1
    knowledge_revision.updated_at = timestamp
    if session_record.question_id is not None:
        question = db.get(QuestionRecord, session_record.question_id)
        if question is None:
            raise InvalidCommitStateError("bound research question disappeared")
        question.state = QuestionState.ANSWERED.value

    checkpoint_id = CheckpointId.new()
    db.add(
        CheckpointRecord(
            id=checkpoint_id.root,
            session_id=session_id.root,
            database_commit_id=attempt_id.root,
            knowledge_revision=knowledge_revision.revision,
            dependency_graph_revision=dependency_revision.revision,
            created_at=timestamp,
        )
    )
    attempt.status = "committed"
    attempt.resolved_at = timestamp
    session_record.state = batch.terminal_state.value
    session_record.updated_at = timestamp
    session_record.terminal_at = timestamp
    db.delete(staging)
    db.flush()
    append_session_audit(
        db,
        session_id=session_id,
        type=EventType.COMMIT_ATTEMPT_RECONCILED,
        occurred_at=timestamp,
        actor="orchestrator",
        public_summary="Knowledge commit completed",
        topic="audit.commit_attempt_reconciled.v1",
        payload={
            "attempt_id": str(attempt_id),
            "status": "committed",
            "knowledge_revision": knowledge_revision.revision,
            "dependency_graph_revision": dependency_revision.revision,
            "claims_committed": len(batch.claims),
        },
    )
    append_session_audit(
        db,
        session_id=session_id,
        type=EventType.SESSION_STATE_CHANGED,
        occurred_at=timestamp,
        actor="orchestrator",
        public_summary="Session completed",
        topic="audit.session_state_changed.v1",
        payload={
            "from": SessionState.COMMITTING.value,
            "to": batch.terminal_state.value,
            "attempt_id": str(attempt_id),
        },
    )
    db.flush()
    ordered_claims = tuple(sorted(batch.claims, key=lambda item: str(item.id)))
    return CommittedKnowledge(
        attempt_id=attempt_id,
        session_id=session_id,
        checkpoint_id=checkpoint_id,
        knowledge_revision=knowledge_revision.revision,
        dependency_graph_revision=dependency_revision.revision,
        claim_ids=tuple(claim.id for claim in ordered_claims),
        assessment_ids=tuple(claim.assessment_id for claim in ordered_claims),
        epistemic_statuses=tuple(claim.assessment.epistemic_status for claim in ordered_claims),
    )


def _verify_revision_vector(
    db: Session,
    batch: KnowledgeCommitBatch,
    *,
    lock: bool,
) -> None:
    knowledge, dependency = _revision_records(db, lock=lock)
    if (
        knowledge.revision != batch.validated_knowledge_revision
        or dependency.revision != batch.validated_dependency_graph_revision
    ):
        raise KnowledgeRevisionConflictError("batch was validated against stale revisions")


def _revision_records(
    db: Session,
    *,
    lock: bool,
) -> tuple[DomainRevisionRecord, DomainRevisionRecord]:
    records: list[DomainRevisionRecord] = []
    for scope in ("knowledge", "dependency_graph"):
        statement = select(DomainRevisionRecord).where(DomainRevisionRecord.scope == scope)
        if lock:
            statement = statement.with_for_update()
        record = db.scalar(statement)
        if record is None:
            raise InvalidCommitStateError("domain revision vector is incomplete")
        records.append(record)
    return records[0], records[1]


def _session_rules(db: Session, session_record: SessionRecord) -> ClaimTypeRulesSnapshot:
    config = db.get(ConfigSnapshotRecord, session_record.config_snapshot_id)
    if config is None:
        raise ConfigRulesMismatchError("session configuration snapshot is missing")
    raw_rules = config.payload.get("claim_type_rules")
    try:
        return ClaimTypeRulesSnapshot.model_validate_json(
            json.dumps(raw_rules, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
    except (TypeError, ValueError) as error:
        raise ConfigRulesMismatchError("session configuration has no valid claim rules") from error


def _verify_config_rules(
    db: Session,
    session_record: SessionRecord,
    batch: KnowledgeCommitBatch,
) -> None:
    rules = _session_rules(db, session_record)
    if rules.version != batch.rules_version or rules.sha256 != batch.rules_sha256:
        raise ConfigRulesMismatchError("batch rules do not match the session configuration")


def _load_committed_result(
    db: Session,
    *,
    attempt_id: CommitAttemptId,
    session_id: SessionId,
) -> CommittedKnowledge:
    checkpoint = db.scalar(
        select(CheckpointRecord).where(CheckpointRecord.database_commit_id == attempt_id.root)
    )
    if checkpoint is None:
        raise InvalidCommitStateError("committed attempt has no checkpoint")
    claims = tuple(
        db.scalars(
            select(ClaimRecord)
            .where(ClaimRecord.created_in_session == session_id.root)
            .order_by(ClaimRecord.id)
        )
    )
    assessments = tuple(
        db.scalars(
            select(ClaimAssessmentRecord)
            .where(ClaimAssessmentRecord.created_in_session == session_id.root)
            .order_by(ClaimAssessmentRecord.claim_id)
        )
    )
    return CommittedKnowledge(
        attempt_id=attempt_id,
        session_id=session_id,
        checkpoint_id=CheckpointId(root=checkpoint.id),
        knowledge_revision=checkpoint.knowledge_revision,
        dependency_graph_revision=checkpoint.dependency_graph_revision,
        claim_ids=tuple(ClaimId(root=claim.id) for claim in claims),
        assessment_ids=tuple(ClaimAssessmentId(root=assessment.id) for assessment in assessments),
        epistemic_statuses=tuple(
            EpistemicStatus(assessment.epistemic_status) for assessment in assessments
        ),
    )
