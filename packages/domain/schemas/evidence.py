"""Session evidence record (M1 in-memory; durable table lands in M3)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import EvidenceKind


class EvidenceRecord(BaseModel):
    """Typed observation produced by the host adapter (T1.18).

    ``identity_hash`` is computed by the trusted host from the canonical
    content of the observation — never by the model. The rules engine
    (M3) uses kind + identity + independence to grade claims.
    """

    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    identity_hash: str = Field(min_length=16, max_length=128)
    payload: JsonDict = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=500)
