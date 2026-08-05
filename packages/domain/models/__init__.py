"""Domain models package — Pydantic schemas + SQLAlchemy ORM."""

# Pydantic schemas (existing)
from packages.domain.models.enums import (
    SessionState,
    DecisionKind,
    EvidenceKind,
    EvidenceRelation,
    EffectiveGrade,
    EpistemicStatus,
    ClaimType,
    AuditEventType,
    ToolName,
)

# Pydantic business models
from packages.domain.models.session import Session
from packages.domain.models.decision import Decision, ModelResponse
from packages.domain.models.claim import (
    Claim,
    Evidence,
    ClaimAssessment,
    ClaimAssessmentHead,
)

# SQLAlchemy ORM
from packages.domain.models.base import Base
from packages.domain.models.orm_session import (
    ORMQuestion,
    ORMSession,
    ORMAction,
    ORMModelRun,
    ORMAuditEvent,
    ORMMessage,
    ORMCommitAttempt,
)
from packages.domain.models.orm_claim import (
    ORMClaim,
    ORMClaimDependency,
    ORMCounterevidenceResolution,
    ORMEvidence,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
    ORMConfigSnapshot,
    ORMDomainRevision,
)

__all__ = [
    # Enums
    "SessionState", "DecisionKind", "EvidenceKind", "EvidenceRelation",
    "EffectiveGrade", "EpistemicStatus", "ClaimType", "AuditEventType", "ToolName",
    # Pydantic
    "Session", "Decision", "ModelResponse",
    "Claim", "Evidence", "ClaimAssessment", "ClaimAssessmentHead",
    # ORM Base
    "Base",
    # ORM Session
    "ORMQuestion", "ORMSession", "ORMAction", "ORMModelRun",
    "ORMAuditEvent", "ORMMessage", "ORMCommitAttempt",
    # ORM Claim
    "ORMClaim", "ORMClaimDependency", "ORMCounterevidenceResolution",
    "ORMEvidence", "ORMClaimAssessment", "ORMClaimAssessmentHead",
    "ORMConfigSnapshot", "ORMDomainRevision",
]
