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
