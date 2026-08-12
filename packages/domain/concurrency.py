"""Trusted-host leases, fencing tokens and revision-vector contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field, NonNegativeInt, StringConstraints, model_validator

from packages.domain._base import ContractModel
from packages.domain.ids import SessionId


class RevisionScope(StrEnum):
    KNOWLEDGE = "knowledge"
    DEPENDENCY_GRAPH = "dependency_graph"
    WORKSPACE = "workspace"
    ARTIFACT_STORE = "artifact_store"


LeaseOwner = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class RevisionVector(ContractModel):
    knowledge: NonNegativeInt = 0
    dependency_graph: NonNegativeInt = 0
    workspace: NonNegativeInt = 0
    artifact_store: NonNegativeInt = 0

    def revision_for(self, scope: RevisionScope) -> int:
        return int(getattr(self, scope.value))


class SessionLease(ContractModel):
    session_id: SessionId
    owner: LeaseOwner
    fence: int = Field(ge=1)
    expires_at: AwareDatetime


class WriterIntentLease(ContractModel):
    scope: RevisionScope
    session_id: SessionId
    operation_id: UUID
    owner: LeaseOwner
    session_fence: int = Field(ge=1)
    writer_fence: int = Field(ge=1)
    base_revision: NonNegativeInt
    expires_at: AwareDatetime


class WriterIntentSet(ContractModel):
    session_lease: SessionLease
    operation_id: UUID
    intents: tuple[WriterIntentLease, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def verify_binding(self) -> WriterIntentSet:
        scopes = [item.scope for item in self.intents]
        if scopes != sorted(scopes, key=lambda item: item.value):
            raise ValueError("writer intent scopes must use canonical ordering")
        if len(scopes) != len(set(scopes)):
            raise ValueError("writer intent scopes must be unique")
        for item in self.intents:
            if (
                item.session_id != self.session_lease.session_id
                or item.operation_id != self.operation_id
                or item.owner != self.session_lease.owner
                or item.session_fence != self.session_lease.fence
                or item.expires_at > self.session_lease.expires_at
            ):
                raise ValueError("writer intent is not fenced by its session lease")
        return self

    def intent_for(self, scope: RevisionScope) -> WriterIntentLease:
        for item in self.intents:
            if item.scope is scope:
                return item
        raise KeyError(scope)

    @property
    def base_revision_vector(self) -> RevisionVector:
        values = {item.scope.value: item.base_revision for item in self.intents}
        return RevisionVector(**values)
