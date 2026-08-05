import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from packages.domain.models.enums import (
    ClaimType, EpistemicStatus, AssessmentState, EffectiveGrade,
    EvidenceKind, EvidenceRelation, PreparedBy,
)


class Claim(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    statement: str
    claim_type: ClaimType
    freshness_status: str = "fresh"
    valid_from: datetime = Field(default_factory=datetime.now)
    valid_to: datetime | None = None
    reverify_after: datetime | None = None
    created_in_session: uuid.UUID | None = None


class Evidence(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    claim_id: uuid.UUID
    relation: EvidenceRelation
    evidence_kind: EvidenceKind
    identity_hash: str
    scope: str | None = None
    source_id: uuid.UUID | None = None
    created_in_session: uuid.UUID | None = None


class ClaimAssessment(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    claim_id: uuid.UUID
    effective_grade: EffectiveGrade
    epistemic_status: EpistemicStatus | None = None
    rules_version: int = 1
    rules_hash: str
    evidence_set_hash: str
    confidence: float | None = None
    valid: bool = True
    created_in_session: uuid.UUID | None = None


class ClaimAssessmentHead(BaseModel):
    claim_id: uuid.UUID
    config_snapshot_id: uuid.UUID
    assessment_state: AssessmentState
    current_assessment_id: uuid.UUID | None = None
    epistemic_status: EpistemicStatus | None = None
    prepared_by: PreparedBy | None = None
