"""Crash-idempotent offline rules activation while cognitive writers are stopped."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from packages.domain import (
    EventType,
    QuestionOrigin,
    QuestionState,
    RevisionScope,
    SessionState,
    canonical_json_sha256,
)
from packages.domain.assessments import ClaimTypeRulesSnapshot
from packages.persistence import INVALID_QUESTION_NAMESPACE, append_global_audit
from packages.persistence.models import (
    ClaimAssessmentHeadRecord,
    ClaimAssessmentRecord,
    ClaimRecord,
    CommitAttemptRecord,
    ConfigActivationManifestRecord,
    ConfigSnapshotRecord,
    DomainRevisionRecord,
    QuestionRecord,
    RuntimeConfigHeadRecord,
    SessionRecord,
)

_CONFIG_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/crowrain/noezema/offline-config")
_TERMINAL_SESSION_STATES = tuple(state.value for state in SessionState if state.is_terminal)
_UNRESOLVED_COMMIT_STATES = ("prepared", "reconciling")


class OfflineActivationError(RuntimeError):
    """Base class for an offline activation that cannot safely publish."""


class OfflineActivationBlockedError(OfflineActivationError):
    """Operational state proves cognitive writers have not quiesced."""


class OfflineActivationConflictError(OfflineActivationError):
    """The frozen cohort or runtime pointer changed before publication."""


class OfflineActivationLimitError(OfflineActivationError):
    """The bounded invalid-question write set exceeds the candidate limit."""


class OfflineActivationStatus(StrEnum):
    ALREADY_EFFECTIVE = "already_effective"
    ACTIVATED = "activated"


@dataclass(frozen=True, slots=True)
class OfflineActivationResult:
    status: OfflineActivationStatus
    config_snapshot_id: UUID
    previous_snapshot_id: UUID
    invalid_question_count: int
    knowledge_revision: int


class OfflineRulesActivator:
    """Prepare and publish a conservative complete shadow cohort in three commits."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._failpoint = failpoint or (lambda _name: None)

    def describe_candidate(self, payload: dict[str, Any]) -> tuple[UUID, UUID]:
        """Derive the candidate/base tuple before the host transition is published."""

        validate_offline_rules_payload(payload)
        payload_sha256 = canonical_json_sha256(payload)
        with self._session_factory() as db:
            head = db.get(RuntimeConfigHeadRecord, "global")
            active = (
                db.get(ConfigSnapshotRecord, head.active_config_snapshot_id)
                if head is not None
                else None
            )
            if active is None or canonical_json_sha256(active.payload) != active.payload_sha256:
                raise OfflineActivationConflictError("effective config fingerprint is invalid")
            if active.payload_sha256 == payload_sha256:
                return active.id, active.id
            revision_sha256 = canonical_json_sha256(
                {
                    "base_snapshot_id": str(active.id),
                    "payload_sha256": payload_sha256,
                }
            )
            return uuid5(_CONFIG_NAMESPACE, revision_sha256), active.id

    def activate(self, payload: dict[str, Any]) -> OfflineActivationResult:
        timestamp = _aware(self._clock())
        payload_copy = dict(payload)
        rules, invalid_limit = _validate_payload(payload_copy)
        payload_sha256 = canonical_json_sha256(payload_copy)

        with _hold_session_advisory_lock(self._session_factory):
            candidate_id, base_id, already_effective = self._ensure_candidate(
                payload_copy,
                payload_sha256=payload_sha256,
                occurred_at=timestamp,
            )
            if already_effective:
                revision = self._knowledge_revision()
                return OfflineActivationResult(
                    status=OfflineActivationStatus.ALREADY_EFFECTIVE,
                    config_snapshot_id=candidate_id,
                    previous_snapshot_id=base_id,
                    invalid_question_count=0,
                    knowledge_revision=revision,
                )
            self._failpoint("after_candidate_committed")

            self._prepare_and_seal(
                candidate_id,
                base_id=base_id,
                rules=rules,
                occurred_at=timestamp,
            )
            self._failpoint("after_seal_committed")

            result = self._publish(
                candidate_id,
                base_id=base_id,
                invalid_limit=invalid_limit,
                occurred_at=timestamp,
            )
            self._failpoint("after_publish_committed")
            return result

    def _ensure_candidate(
        self,
        payload: dict[str, Any],
        *,
        payload_sha256: str,
        occurred_at: datetime,
    ) -> tuple[UUID, UUID, bool]:
        with self._session_factory.begin() as db:
            head = _locked_runtime_head(db)
            _assert_offline_admission(db, head)
            active = db.get(ConfigSnapshotRecord, head.active_config_snapshot_id)
            if active is None or canonical_json_sha256(active.payload) != active.payload_sha256:
                raise OfflineActivationConflictError("effective config fingerprint is invalid")
            if active.payload_sha256 == payload_sha256:
                return active.id, active.base_snapshot_id or active.id, True

            revision_sha256 = canonical_json_sha256(
                {
                    "base_snapshot_id": str(active.id),
                    "payload_sha256": payload_sha256,
                }
            )
            candidate_id = uuid5(_CONFIG_NAMESPACE, revision_sha256)
            candidate = db.scalar(
                select(ConfigSnapshotRecord)
                .where(ConfigSnapshotRecord.sha == revision_sha256)
                .with_for_update()
            )
            if candidate is None:
                candidate = ConfigSnapshotRecord(
                    id=candidate_id,
                    base_snapshot_id=active.id,
                    payload=payload,
                    payload_sha256=payload_sha256,
                    sha=revision_sha256,
                    activation_mode="offline",
                    activation_state="draft",
                    activation_manifest_sha256=None,
                    activation_cohort_revision=None,
                    activation_expected_head_count=None,
                    activation_verified_head_count=None,
                    activation_heads_sha256=None,
                    activation_invalid_head_count=None,
                    activation_verified_at=None,
                    created_at=occurred_at,
                )
                db.add(candidate)
                db.flush()
            elif (
                candidate.id != candidate_id
                or candidate.base_snapshot_id != active.id
                or candidate.payload_sha256 != payload_sha256
                or candidate.payload != payload
                or candidate.activation_mode != "offline"
            ):
                raise OfflineActivationConflictError("candidate revision identity conflicts")
            elif candidate.activation_state == "failed":
                candidate.activation_state = "draft"
                _clear_seal(candidate)
            return candidate.id, active.id, False

    def _prepare_and_seal(
        self,
        candidate_id: UUID,
        *,
        base_id: UUID,
        rules: ClaimTypeRulesSnapshot,
        occurred_at: datetime,
    ) -> None:
        with self._session_factory.begin() as db:
            head = _locked_runtime_head(db)
            _assert_offline_admission(db, head)
            revision = _locked_knowledge_revision(db)
            if head.active_config_snapshot_id != base_id:
                raise OfflineActivationConflictError("effective config changed before preparation")
            candidate = db.get(ConfigSnapshotRecord, candidate_id)
            if candidate is None or candidate.base_snapshot_id != base_id:
                raise OfflineActivationConflictError("offline candidate is missing")
            if (
                candidate.activation_state == "ready"
                and candidate.activation_cohort_revision == revision.revision
            ):
                return
            if candidate.activation_state not in {"draft", "preparing_heads", "ready"}:
                raise OfflineActivationConflictError("offline candidate lifecycle is invalid")

            candidate.activation_state = "preparing_heads"
            _clear_seal(candidate)
            db.execute(
                delete(ClaimAssessmentHeadRecord).where(
                    ClaimAssessmentHeadRecord.config_snapshot_id == candidate_id
                )
            )
            db.execute(
                delete(ConfigActivationManifestRecord).where(
                    ConfigActivationManifestRecord.config_snapshot_id == candidate_id
                )
            )
            db.flush()

            claims = tuple(db.scalars(select(ClaimRecord).order_by(ClaimRecord.id)))
            manifest_entries: list[str] = []
            head_entries: list[dict[str, object]] = []
            invalid_count = 0
            for claim in claims:
                manifest_entries.append(str(claim.id))
                db.add(
                    ConfigActivationManifestRecord(
                        config_snapshot_id=candidate_id,
                        claim_id=claim.id,
                    )
                )
                effective_head = db.get(
                    ClaimAssessmentHeadRecord,
                    (claim.id, base_id),
                )
                assessment = (
                    db.get(ClaimAssessmentRecord, effective_head.current_assessment_id)
                    if effective_head is not None
                    and effective_head.current_assessment_id is not None
                    else None
                )
                compatible = (
                    effective_head is not None
                    and effective_head.assessment_state == "current"
                    and assessment is not None
                    and assessment.valid
                    and assessment.rules_hash == rules.sha256
                )
                assessment_state = "current" if compatible else "invalid"
                current_assessment_id = assessment.id if compatible else None
                epistemic_status = assessment.epistemic_status if compatible else None
                if not compatible:
                    invalid_count += 1
                db.add(
                    ClaimAssessmentHeadRecord(
                        claim_id=claim.id,
                        config_snapshot_id=candidate_id,
                        assessment_state=assessment_state,
                        current_assessment_id=current_assessment_id,
                        epistemic_status=epistemic_status,
                        prepared_by="rules_activation",
                        updated_at=occurred_at,
                    )
                )
                head_entries.append(
                    {
                        "claim_id": str(claim.id),
                        "assessment_state": assessment_state,
                        "current_assessment_id": (
                            str(current_assessment_id) if current_assessment_id is not None else None
                        ),
                        "epistemic_status": epistemic_status,
                        "prepared_by": "rules_activation",
                    }
                )
            candidate.activation_manifest_sha256 = canonical_json_sha256(manifest_entries)
            candidate.activation_cohort_revision = revision.revision
            candidate.activation_expected_head_count = len(claims)
            candidate.activation_verified_head_count = len(head_entries)
            candidate.activation_heads_sha256 = canonical_json_sha256(head_entries)
            candidate.activation_invalid_head_count = invalid_count
            candidate.activation_verified_at = occurred_at
            candidate.activation_state = "ready"
            db.flush()

    def _publish(
        self,
        candidate_id: UUID,
        *,
        base_id: UUID,
        invalid_limit: int,
        occurred_at: datetime,
    ) -> OfflineActivationResult:
        with self._session_factory.begin() as db:
            head = _locked_runtime_head(db)
            _assert_offline_admission(db, head)
            revision = _locked_knowledge_revision(db)
            candidate = db.get(ConfigSnapshotRecord, candidate_id)
            if head.active_config_snapshot_id == candidate_id:
                if candidate is None or candidate.activation_state != "active":
                    raise OfflineActivationConflictError("effective candidate state is inconsistent")
                return OfflineActivationResult(
                    status=OfflineActivationStatus.ALREADY_EFFECTIVE,
                    config_snapshot_id=candidate_id,
                    previous_snapshot_id=base_id,
                    invalid_question_count=candidate.activation_invalid_head_count or 0,
                    knowledge_revision=revision.revision,
                )
            if head.active_config_snapshot_id != base_id:
                raise OfflineActivationConflictError("effective config changed before publish")
            previous = db.get(ConfigSnapshotRecord, base_id)
            if previous is None or candidate is None:
                raise OfflineActivationConflictError("publish config tuple is incomplete")
            if (
                candidate.activation_state != "ready"
                or candidate.activation_cohort_revision != revision.revision
                or candidate.activation_expected_head_count
                != candidate.activation_verified_head_count
                or candidate.activation_manifest_sha256 is None
                or candidate.activation_heads_sha256 is None
                or candidate.activation_invalid_head_count is None
                or candidate.activation_verified_at is None
            ):
                raise OfflineActivationConflictError("candidate verification seal is invalid")
            if candidate.activation_invalid_head_count > invalid_limit:
                raise OfflineActivationLimitError(
                    "offline activation exceeds offline_activation_max_invalid_questions"
                )

            invalid_heads = tuple(
                db.scalars(
                    select(ClaimAssessmentHeadRecord)
                    .where(
                        ClaimAssessmentHeadRecord.config_snapshot_id == candidate_id,
                        ClaimAssessmentHeadRecord.assessment_state == "invalid",
                    )
                    .order_by(ClaimAssessmentHeadRecord.claim_id)
                )
            )
            if len(invalid_heads) != candidate.activation_invalid_head_count:
                raise OfflineActivationConflictError("invalid-head seal count changed")
            for assessment_head in invalid_heads:
                claim = db.get(ClaimRecord, assessment_head.claim_id)
                if claim is None:
                    raise OfflineActivationConflictError("manifest claim disappeared")
                question_id = uuid5(
                    INVALID_QUESTION_NAMESPACE,
                    f"{str(candidate_id).lower()}:{str(claim.id).lower()}",
                )
                question_text = f"Переоценить после смены правил: {claim.statement}"[:4096]
                existing = db.get(QuestionRecord, question_id)
                if existing is None:
                    db.add(
                        QuestionRecord(
                            id=question_id,
                            text=question_text,
                            origin=QuestionOrigin.INVALID_ASSESSMENT.value,
                            origin_config_snapshot_id=candidate_id,
                            state=QuestionState.QUEUED.value,
                            priority=0,
                            parent_id=None,
                            score_components={"claim_id": str(claim.id)},
                            embedding_fingerprint=None,
                            created_at=occurred_at,
                        )
                    )
                elif (
                    existing.text != question_text
                    or existing.origin != QuestionOrigin.INVALID_ASSESSMENT.value
                    or existing.origin_config_snapshot_id != candidate_id
                    or existing.score_components != {"claim_id": str(claim.id)}
                ):
                    raise OfflineActivationConflictError("invalid question identity conflicts")

            head.active_config_snapshot_id = candidate_id
            head.updated_at = occurred_at
            candidate.activation_state = "active"
            previous.activation_state = "superseded"
            revision.revision += 1
            revision.updated_at = occurred_at
            append_global_audit(
                db,
                type=EventType.CONFIG_ACTIVATED,
                occurred_at=occurred_at,
                actor="offline-rules",
                public_summary="Offline rules configuration activated",
                topic="audit.config_activated.v1",
                payload={
                    "config_snapshot_id": str(candidate_id),
                    "previous_snapshot_id": str(base_id),
                    "payload_sha256": candidate.payload_sha256,
                    "manifest_sha256": candidate.activation_manifest_sha256,
                    "heads_sha256": candidate.activation_heads_sha256,
                    "head_count": candidate.activation_expected_head_count,
                    "invalid_question_count": candidate.activation_invalid_head_count,
                    "knowledge_revision": revision.revision,
                },
            )
            db.flush()
            return OfflineActivationResult(
                status=OfflineActivationStatus.ACTIVATED,
                config_snapshot_id=candidate_id,
                previous_snapshot_id=base_id,
                invalid_question_count=candidate.activation_invalid_head_count,
                knowledge_revision=revision.revision,
            )

    def _knowledge_revision(self) -> int:
        with self._session_factory() as db:
            record = db.get(DomainRevisionRecord, RevisionScope.KNOWLEDGE.value)
            if record is None:
                raise OfflineActivationConflictError("knowledge revision is missing")
            return record.revision


def _validate_payload(payload: dict[str, Any]) -> tuple[ClaimTypeRulesSnapshot, int]:
    if payload.get("schema_version") != "runtime-config/v1":
        raise ValueError("offline config must use runtime-config/v1")
    rules = ClaimTypeRulesSnapshot.model_validate_json(
        json.dumps(payload.get("claim_type_rules"), separators=(",", ":"))
    )
    limits = payload.get("activation_limits")
    if not isinstance(limits, dict):
        raise ValueError("activation_limits must be an object")
    invalid_limit = limits.get("offline_activation_max_invalid_questions")
    if isinstance(invalid_limit, bool) or not isinstance(invalid_limit, int):
        raise ValueError("offline_activation_max_invalid_questions must be an integer")
    if not 0 <= invalid_limit <= 100_000:
        raise ValueError("offline_activation_max_invalid_questions is out of range")
    return rules, invalid_limit


def validate_offline_rules_payload(payload: dict[str, Any]) -> None:
    """Fail before quiescing the runtime when a requested payload is malformed."""

    _validate_payload(payload)
    canonical_json_sha256(payload)


def _locked_runtime_head(db: Session) -> RuntimeConfigHeadRecord:
    head = db.scalar(
        select(RuntimeConfigHeadRecord)
        .where(RuntimeConfigHeadRecord.scope == "global")
        .with_for_update()
    )
    if head is None:
        raise OfflineActivationConflictError("global runtime config head is missing")
    return head


def _locked_knowledge_revision(db: Session) -> DomainRevisionRecord:
    revision = db.scalar(
        select(DomainRevisionRecord)
        .where(DomainRevisionRecord.scope == RevisionScope.KNOWLEDGE.value)
        .with_for_update()
    )
    if revision is None:
        raise OfflineActivationConflictError("knowledge revision is missing")
    return revision


def _assert_offline_admission(db: Session, head: RuntimeConfigHeadRecord) -> None:
    if (
        head.activating_config_snapshot_id is not None
        or head.lease_owner is not None
        or head.lease_expires_at is not None
    ):
        raise OfflineActivationBlockedError("runtime config activation is already owned")
    active_session = db.scalar(
        select(SessionRecord.id)
        .where(SessionRecord.state.not_in(_TERMINAL_SESSION_STATES))
        .limit(1)
    )
    if active_session is not None:
        raise OfflineActivationBlockedError("an active session still exists")
    unresolved = db.scalar(
        select(CommitAttemptRecord.id)
        .where(CommitAttemptRecord.status.in_(_UNRESOLVED_COMMIT_STATES))
        .limit(1)
    )
    if unresolved is not None:
        raise OfflineActivationBlockedError("an unresolved commit attempt exists")


@contextmanager
def _hold_session_advisory_lock(
    session_factory: Callable[[], Session],
) -> Iterator[None]:
    """Own one dedicated PostgreSQL connection for the complete offline run."""

    probe = session_factory()
    bind = probe.get_bind()
    probe.close()
    if bind.dialect.name != "postgresql":
        yield
        return
    with bind.connect() as connection:  # type: ignore[union-attr]
        connection.execute(
            text(
                "SELECT pg_advisory_lock("
                "hashtextextended('noezema:offline_rules:global', 0))"
            )
        )
        connection.commit()
        try:
            yield
        finally:
            connection.execute(
                text(
                    "SELECT pg_advisory_unlock("
                    "hashtextextended('noezema:offline_rules:global', 0))"
                )
            )
            connection.commit()


def _clear_seal(candidate: ConfigSnapshotRecord) -> None:
    candidate.activation_manifest_sha256 = None
    candidate.activation_cohort_revision = None
    candidate.activation_expected_head_count = None
    candidate.activation_verified_head_count = None
    candidate.activation_heads_sha256 = None
    candidate.activation_invalid_head_count = None
    candidate.activation_verified_at = None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("offline activation clock must be timezone-aware")
    return value.astimezone(UTC)
