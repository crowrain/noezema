"""Runtime-distinct UUID identifiers created by the trusted host."""

from __future__ import annotations

from typing import Self
from uuid import UUID, uuid4

from pydantic import ConfigDict, RootModel


class _UuidIdentifier(RootModel[UUID]):
    """A UUID root model that serializes as one JSON string."""

    model_config = ConfigDict(frozen=True)

    @classmethod
    def new(cls) -> Self:
        """Create an unpredictable identifier inside a trusted host component."""

        return cls(root=uuid4())

    def __str__(self) -> str:
        return str(self.root)


class SessionId(_UuidIdentifier):
    """Identity of one cognitive session."""


class TurnId(_UuidIdentifier):
    """Identity of one orchestrator turn."""


class ModelRunId(_UuidIdentifier):
    """Identity of one LLM invocation."""


class ActionId(_UuidIdentifier):
    """Identity assigned by the Tool Broker after decision validation."""


class IdempotencyKey(_UuidIdentifier):
    """Host-generated key bound to one tool and canonical arguments hash."""


class CommitAttemptId(_UuidIdentifier):
    """Identity of a durable commit reconciliation record."""


class AuditEventId(_UuidIdentifier):
    """Identity of one immutable audit event."""


class ConfigSnapshotId(_UuidIdentifier):
    """Identity of one immutable runtime configuration snapshot."""


class OutboxEventId(_UuidIdentifier):
    """Identity of one event awaiting delivery from the transactional outbox."""


class QuestionId(_UuidIdentifier):
    """Identity of one durable research question."""


class ObservationId(_UuidIdentifier):
    """Identity assigned to one typed tool observation by the trusted host."""


class ArtifactId(_UuidIdentifier):
    """Identity assigned to one content-addressed artifact by the trusted host."""


class SourceId(_UuidIdentifier):
    """Identity of one provenance source registered by the trusted host."""


class ChunkId(_UuidIdentifier):
    """Identity of one exact source chunk registered by the trusted host."""


class EnvironmentManifestId(_UuidIdentifier):
    """Identity of one immutable execution-environment manifest."""


class ClaimId(_UuidIdentifier):
    """Stable identity of one knowledge claim."""


class EvidenceId(_UuidIdentifier):
    """Identity of one deduplicated evidence record."""


class ClaimAssessmentId(_UuidIdentifier):
    """Identity of one immutable rules assessment."""


class SessionStagingId(_UuidIdentifier):
    """Identity of one immutable session staging batch."""


class CheckpointId(_UuidIdentifier):
    """Identity of one committed database checkpoint."""
