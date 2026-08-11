"""Host-owned capability policy and typed Tool Broker observations."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, TypeAlias
from uuid import uuid5

from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints, model_validator

from packages.domain._base import ContractModel, JsonObject, NonEmptyText, Sha256Hex, ShortReason
from packages.domain.actions import canonical_json_sha256
from packages.domain.assessments import EpistemicStatus
from packages.domain.enums import ActionState, PolicyDecision, ToolName
from packages.domain.evidence import (
    EvidenceKind,
    ObservationProvenance,
    SourceObservation,
    SourceRange,
)
from packages.domain.ids import (
    ActionId,
    ChunkId,
    ClaimAssessmentId,
    ClaimId,
    ObservationId,
    SourceId,
)


def _validate_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError("tool path must be relative to the configured workspace")
    if ".." in posix_path.parts:
        raise ValueError("tool path must not traverse outside the configured workspace")
    return normalized


RelativeToolPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
    AfterValidator(_validate_relative_path),
]


class WorkspaceReadArguments(ContractModel):
    path: RelativeToolPath


class WorkspaceListArguments(ContractModel):
    path: RelativeToolPath = "."
    recursive: bool = False


class MemorySearchArguments(ContractModel):
    query: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
    ]
    limit: int = Field(default=10, ge=1, le=50)


SafeToolArguments: TypeAlias = (
    WorkspaceReadArguments | WorkspaceListArguments | MemorySearchArguments
)


class CapabilityPolicy(ContractModel):
    """Immutable limits selected by the trusted host, never by the model."""

    schema_version: Literal["capability-policy/v1"] = "capability-policy/v1"
    version: ShortReason
    allowed_tools: tuple[ToolName, ...] = Field(max_length=32)
    max_workspace_read_bytes: int = Field(default=262_144, ge=1, le=8_388_608)
    max_workspace_list_entries: int = Field(default=256, ge=1, le=10_000)
    max_memory_results: int = Field(default=20, ge=1, le=50)
    action_timeout_ms: int = Field(default=5_000, ge=1, le=300_000)
    max_attempts: int = Field(default=2, ge=1, le=10)

    @model_validator(mode="after")
    def require_unique_tools(self) -> CapabilityPolicy:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("capability policy tools must be unique")
        return self


def capability_policy_sha256(policy: CapabilityPolicy) -> str:
    return canonical_json_sha256(policy.model_dump(mode="json"))


def capability_policy_config_payload(policy: CapabilityPolicy) -> JsonObject:
    """Serialize the exact policy material pinned by an immutable config snapshot."""

    return {
        "version": policy.version,
        "sha256": capability_policy_sha256(policy),
        "capability_policy": policy.model_dump(mode="json"),
    }


def sealed_mvp_capability_policy() -> CapabilityPolicy:
    """Allow only bounded, read-only operations implemented before the sandbox stage."""

    return CapabilityPolicy(
        version="sealed-mvp/read-only/v1",
        allowed_tools=(
            ToolName.WORKSPACE_LIST,
            ToolName.WORKSPACE_READ,
            ToolName.MEMORY_SEARCH,
        ),
    )


class PolicyEvaluation(ContractModel):
    decision: PolicyDecision
    policy_version: ShortReason
    policy_sha256: Sha256Hex
    reason: ShortReason


class WorkspaceEntry(ContractModel):
    path: RelativeToolPath
    kind: Literal["file", "directory"]
    size_bytes: int | None = Field(default=None, ge=0)


class WorkspaceReadPayload(ContractModel):
    kind: Literal["workspace_read"] = "workspace_read"
    path: RelativeToolPath
    media_type: Literal["text/plain"] = "text/plain"
    encoding: Literal["utf-8"] = "utf-8"
    size_bytes: int = Field(ge=0)
    content_sha256: Sha256Hex
    content: str

    @model_validator(mode="after")
    def verify_exact_content(self) -> WorkspaceReadPayload:
        content_bytes = self.content.encode("utf-8")
        if self.size_bytes != len(content_bytes):
            raise ValueError("size_bytes does not match UTF-8 content")
        if self.content_sha256 != hashlib.sha256(content_bytes).hexdigest():
            raise ValueError("content_sha256 does not match UTF-8 content")
        return self


class WorkspaceListPayload(ContractModel):
    kind: Literal["workspace_list"] = "workspace_list"
    path: RelativeToolPath
    recursive: bool
    entries: tuple[WorkspaceEntry, ...]
    truncated: bool


class MemorySearchHit(ContractModel):
    claim_id: ClaimId
    assessment_id: ClaimAssessmentId
    statement: NonEmptyText
    topic: ShortReason
    epistemic_status: EpistemicStatus
    confidence_basis_points: int = Field(ge=0, le=10_000)


class MemorySearchPayload(ContractModel):
    kind: Literal["memory_search"] = "memory_search"
    query: NonEmptyText
    hits: tuple[MemorySearchHit, ...]
    truncated: bool


ToolResultPayload: TypeAlias = Annotated[
    WorkspaceReadPayload | WorkspaceListPayload | MemorySearchPayload,
    Field(discriminator="kind"),
]

_PAYLOAD_TO_TOOL = {
    WorkspaceReadPayload: ToolName.WORKSPACE_READ,
    WorkspaceListPayload: ToolName.WORKSPACE_LIST,
    MemorySearchPayload: ToolName.MEMORY_SEARCH,
}


class ToolExecutionObservation(ContractModel):
    """A bounded, content-addressed result emitted only by the Tool Broker."""

    id: ObservationId
    payload_sha256: Sha256Hex
    provenance: ObservationProvenance
    payload: ToolResultPayload

    @classmethod
    def build(
        cls,
        *,
        action_id: ActionId,
        tool: ToolName,
        source: str,
        captured_at: AwareDatetime,
        payload: ToolResultPayload,
    ) -> ToolExecutionObservation:
        return cls(
            id=ObservationId.new(),
            payload_sha256=canonical_json_sha256(payload.model_dump(mode="json")),
            provenance=ObservationProvenance(
                action_id=action_id,
                tool=tool,
                source=source,
                captured_at=captured_at,
            ),
            payload=payload,
        )

    @model_validator(mode="after")
    def verify_binding(self) -> ToolExecutionObservation:
        expected_tool = _PAYLOAD_TO_TOOL[type(self.payload)]
        if self.provenance.tool is not expected_tool:
            raise ValueError("observation payload does not match provenance tool")
        expected_hash = canonical_json_sha256(self.payload.model_dump(mode="json"))
        if self.payload_sha256 != expected_hash:
            raise ValueError("payload_sha256 does not match the typed tool payload")
        return self


class ToolExecutionFailure(ContractModel):
    code: ShortReason
    message: NonEmptyText
    retryable: bool
    outcome_known: bool
    attempt: int = Field(ge=1, le=10_000)


class BrokerRunResult(ContractModel):
    action_id: ActionId
    state: ActionState
    attempt_count: int = Field(ge=0)
    policy: PolicyEvaluation
    observation: ToolExecutionObservation | None = None
    error: ToolExecutionFailure | None = None

    @model_validator(mode="after")
    def require_terminal_payload(self) -> BrokerRunResult:
        if self.state is ActionState.COMPLETED:
            if self.observation is None or self.error is not None:
                raise ValueError("completed action requires only an observation")
        elif self.state in {ActionState.FAILED, ActionState.OUTCOME_UNKNOWN}:
            if self.error is None or self.observation is not None:
                raise ValueError("failed action requires only an execution error")
        elif self.state is ActionState.POLICY_EVALUATED:
            if self.policy.decision is PolicyDecision.ALLOW:
                raise ValueError("allowed action cannot stop after policy evaluation")
            if self.observation is not None or self.error is not None:
                raise ValueError("denied action must not contain an execution result")
        else:
            raise ValueError("broker result must describe a denied or terminal action")
        return self


def workspace_read_as_source_observation(
    observation: ToolExecutionObservation,
    *,
    kind: Literal[EvidenceKind.SOURCE_ASSERTION, EvidenceKind.QUOTE_INTEGRITY] = (
        EvidenceKind.SOURCE_ASSERTION
    ),
) -> SourceObservation:
    """Promote an exact non-empty workspace read to evidence without circular memory use."""

    payload = observation.payload
    if not isinstance(payload, WorkspaceReadPayload):
        raise ValueError("only workspace.read can become a source observation")
    if not payload.content:
        raise ValueError("an empty workspace file cannot provide source evidence")
    action_namespace = observation.provenance.action_id.root
    return SourceObservation(
        id=observation.id,
        kind=kind,
        payload_sha256=payload.content_sha256,
        provenance=observation.provenance,
        source_id=SourceId(root=uuid5(action_namespace, f"source:{payload.path}")),
        chunk_id=ChunkId(
            root=uuid5(action_namespace, f"chunk:{payload.path}:{payload.content_sha256}")
        ),
        source_content_sha256=payload.content_sha256,
        chunk_sha256=payload.content_sha256,
        normalized_range=SourceRange(
            unit="text",
            start=0,
            end=len(payload.content),
            normalization_version="utf-8-text/v1",
        ),
    )
