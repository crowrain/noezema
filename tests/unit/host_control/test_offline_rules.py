"""Transactional and crash-retry tests for offline rules activation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.host_control.offline_rules import (
    OfflineActivationLimitError,
    OfflineActivationStatus,
    OfflineRulesActivator,
)
from packages.domain import (
    ClaimType,
    EventType,
    QuestionOrigin,
    RevisionScope,
    SessionState,
    canonical_json_sha256,
)
from packages.memory import mvp_claim_type_rules
from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    INVALID_QUESTION_NAMESPACE,
    bootstrap_payload,
)
from packages.persistence.models import (
    AuditEventRecord,
    ClaimAssessmentHeadRecord,
    ClaimRecord,
    ConfigActivationManifestRecord,
    ConfigSnapshotRecord,
    DomainRevisionRecord,
    QuestionRecord,
    RuntimeConfigHeadRecord,
    SessionRecord,
)

NOW = datetime(2026, 2, 1, 12, tzinfo=UTC)


def _payload(*, invalid_limit: int = 10) -> dict[str, object]:
    payload = bootstrap_payload()
    payload["claim_type_rules"] = mvp_claim_type_rules().model_dump(mode="json")
    payload["activation_limits"] = {
        "offline_activation_max_invalid_questions": invalid_limit
    }
    return payload


def _seed_invalid_claim(session_factory: sessionmaker[Session]) -> ClaimRecord:
    session_id = uuid4()
    claim_id = uuid4()
    budget = {"kind": "test"}
    with session_factory.begin() as db:
        db.add(
            SessionRecord(
                id=session_id,
                state=SessionState.SUCCEEDED.value,
                config_snapshot_id=BOOTSTRAP_CONFIG_SNAPSHOT_ID,
                question_id=None,
                commit_attempt_id=None,
                lease_owner=None,
                lease_expires_at=None,
                fence=0,
                next_audit_sequence=1,
                budget=budget,
                budget_sha256=canonical_json_sha256(budget),
                cognitive_deadline_at=NOW + timedelta(minutes=1),
                host_deadline_at=NOW + timedelta(minutes=2),
                stop_requested_at=None,
                abort_requested_at=None,
                soft_exhausted_at=None,
                soft_exhaustion_reason=None,
                termination_reason="test fixture",
                created_at=NOW,
                updated_at=NOW,
                terminal_at=NOW,
            )
        )
        db.flush()
        claim = ClaimRecord(
            id=claim_id,
            statement="The local runtime is healthy.",
            claim_type=ClaimType.LOCAL_OBSERVATION.value,
            freshness_status="unknown",
            as_of=None,
            observed_at=None,
            reverify_after=None,
            topic="runtime",
            created_in_session=session_id,
        )
        db.add(claim)
        db.flush()
        db.add(
            ClaimAssessmentHeadRecord(
                claim_id=claim_id,
                config_snapshot_id=BOOTSTRAP_CONFIG_SNAPSHOT_ID,
                assessment_state="invalid",
                current_assessment_id=None,
                epistemic_status=None,
                prepared_by="rules_activation",
                updated_at=NOW,
            )
        )
    return claim


def test_activation_publishes_a_complete_shadow_cohort_and_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    claim = _seed_invalid_claim(session_factory)
    activator = OfflineRulesActivator(session_factory, clock=lambda: NOW)

    result = activator.activate(_payload())

    assert result.status is OfflineActivationStatus.ACTIVATED
    assert result.previous_snapshot_id == BOOTSTRAP_CONFIG_SNAPSHOT_ID
    assert result.invalid_question_count == 1
    assert result.knowledge_revision == 1
    question_id = uuid5(
        INVALID_QUESTION_NAMESPACE,
        f"{str(result.config_snapshot_id).lower()}:{str(claim.id).lower()}",
    )
    with session_factory() as db:
        head = db.get(RuntimeConfigHeadRecord, "global")
        candidate = db.get(ConfigSnapshotRecord, result.config_snapshot_id)
        shadow = db.get(
            ClaimAssessmentHeadRecord,
            (claim.id, result.config_snapshot_id),
        )
        question = db.get(QuestionRecord, question_id)
        revision = db.get(DomainRevisionRecord, RevisionScope.KNOWLEDGE.value)
        assert head is not None and head.active_config_snapshot_id == result.config_snapshot_id
        assert candidate is not None and candidate.activation_state == "active"
        assert candidate.activation_expected_head_count == 1
        assert candidate.activation_verified_head_count == 1
        assert candidate.activation_invalid_head_count == 1
        assert shadow is not None and shadow.assessment_state == "invalid"
        assert question is not None and question.origin == QuestionOrigin.INVALID_ASSESSMENT.value
        assert revision is not None and revision.revision == 1
        assert db.scalar(select(func.count()).select_from(ConfigActivationManifestRecord)) == 1

    repeated = activator.activate(_payload())

    assert repeated.status is OfflineActivationStatus.ALREADY_EFFECTIVE
    assert repeated.config_snapshot_id == result.config_snapshot_id
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(QuestionRecord)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(AuditEventRecord.type == EventType.CONFIG_ACTIVATED.value)
            )
            == 1
        )


def test_crash_after_seal_reuses_the_same_candidate(
    session_factory: sessionmaker[Session],
) -> None:
    def fail_after_seal(name: str) -> None:
        if name == "after_seal_committed":
            raise RuntimeError("simulated crash")

    payload = _payload()
    crashing = OfflineRulesActivator(
        session_factory,
        clock=lambda: NOW,
        failpoint=fail_after_seal,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        crashing.activate(payload)

    with session_factory() as db:
        candidate = db.scalar(
            select(ConfigSnapshotRecord).where(
                ConfigSnapshotRecord.activation_mode == "offline"
            )
        )
        assert candidate is not None and candidate.activation_state == "ready"
        candidate_id = candidate.id
        head = db.get(RuntimeConfigHeadRecord, "global")
        assert head is not None
        assert head.active_config_snapshot_id == BOOTSTRAP_CONFIG_SNAPSHOT_ID

    result = OfflineRulesActivator(session_factory, clock=lambda: NOW).activate(payload)

    assert result.config_snapshot_id == candidate_id
    assert result.status is OfflineActivationStatus.ACTIVATED


def test_invalid_question_limit_blocks_publication_without_changing_pointer(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_invalid_claim(session_factory)

    with pytest.raises(OfflineActivationLimitError):
        OfflineRulesActivator(session_factory, clock=lambda: NOW).activate(
            _payload(invalid_limit=0)
        )

    with session_factory() as db:
        head = db.get(RuntimeConfigHeadRecord, "global")
        candidate = db.scalar(
            select(ConfigSnapshotRecord).where(
                ConfigSnapshotRecord.activation_mode == "offline"
            )
        )
        revision = db.get(DomainRevisionRecord, RevisionScope.KNOWLEDGE.value)
        assert head is not None
        assert head.active_config_snapshot_id == BOOTSTRAP_CONFIG_SNAPSHOT_ID
        assert candidate is not None and candidate.activation_state == "ready"
        assert revision is not None and revision.revision == 0
        assert db.scalar(select(func.count()).select_from(QuestionRecord)) == 0
