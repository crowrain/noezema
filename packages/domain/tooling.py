"""Host-owned capability policy and typed Tool Broker observations."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, TypeAlias
from uuid import uuid5

from pydantic import (
    AfterValidator,
    AwareDatetime,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

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
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError("tool path contains forbidden control characters")
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


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("executable text must not contain a NUL byte")
    return value


def _validate_oci_image_digest(value: str) -> str:
    if value.startswith("sha256:"):
        digest = value.removeprefix("sha256:")
        name = "local-image-id"
    else:
        name, separator, digest = value.rpartition("@sha256:")
        if not separator:
            raise ValueError("sandbox image must be pinned by an OCI SHA-256 digest")
    if (
        not name
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or any(character.isspace() or character == "\x00" for character in value)
    ):
        raise ValueError("sandbox image must be pinned by a valid OCI SHA-256 digest")
    return value


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


class ShellExecuteArguments(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    command: Annotated[
        str,
        StringConstraints(min_length=1, max_length=16_384),
        AfterValidator(_reject_nul),
    ]
    cwd: RelativeToolPath = "."
    timeout_ms: int | None = Field(default=None, ge=1, le=300_000)


class PythonExecuteArguments(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    code: Annotated[
        str,
        StringConstraints(min_length=1, max_length=65_536),
        AfterValidator(_reject_nul),
    ]
    cwd: RelativeToolPath = "."
    timeout_ms: int | None = Field(default=None, ge=1, le=300_000)


ToolArguments: TypeAlias = (
    WorkspaceReadArguments
    | WorkspaceListArguments
    | MemorySearchArguments
    | ShellExecuteArguments
    | PythonExecuteArguments
)


class SandboxProfile(ContractModel):
    """Pinned OCI isolation requirements enforced outside the model."""

    schema_version: Literal["sandbox-profile/v1"] = "sandbox-profile/v1"
    runtime: Literal["podman", "docker"] = "podman"
    image: Annotated[
        str,
        StringConstraints(min_length=71, max_length=512),
        AfterValidator(_validate_oci_image_digest),
    ]
    user_id: int = Field(default=65_532, ge=1, le=2_147_483_647)
    group_id: int = Field(default=65_532, ge=1, le=2_147_483_647)
    cpu_millis: int = Field(default=1_000, ge=100, le=16_000)
    memory_bytes: int = Field(default=536_870_912, ge=67_108_864, le=17_179_869_184)
    pids_limit: int = Field(default=128, ge=16, le=4_096)
    tmpfs_bytes: int = Field(default=67_108_864, ge=1_048_576, le=1_073_741_824)
    workspace_max_bytes: int = Field(
        default=1_073_741_824,
        ge=1_048_576,
        le=68_719_476_736,
    )
    max_output_bytes: int = Field(default=262_144, ge=1_024, le=8_388_608)


def sandbox_profile_sha256(profile: SandboxProfile) -> str:
    return canonical_json_sha256(profile.model_dump(mode="json"))


class CapabilityPolicy(ContractModel):
    """Immutable limits selected by the trusted host, never by the model."""

    schema_version: Literal["capability-policy/v2"] = "capability-policy/v2"
    version: ShortReason
    allowed_tools: tuple[ToolName, ...] = Field(max_length=32)
    max_workspace_read_bytes: int = Field(default=262_144, ge=1, le=8_388_608)
    max_workspace_list_entries: int = Field(default=256, ge=1, le=10_000)
    max_memory_results: int = Field(default=20, ge=1, le=50)
    action_timeout_ms: int = Field(default=5_000, ge=1, le=300_000)
    max_attempts: int = Field(default=2, ge=1, le=10)
    sandbox: SandboxProfile | None = None

    @model_validator(mode="after")
    def require_unique_tools(self) -> CapabilityPolicy:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("capability policy tools must be unique")
        execution_tools = {ToolName.SHELL_EXECUTE, ToolName.PYTHON_EXECUTE}
        if execution_tools.intersection(self.allowed_tools) and self.sandbox is None:
            raise ValueError("shell/python capabilities require a pinned sandbox profile")
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


def sandbox_mvp_capability_policy(*, sandbox: SandboxProfile) -> CapabilityPolicy:
    """Enable local computation only inside the pinned, networkless OCI sandbox."""

    return CapabilityPolicy(
        version="sealed-mvp/sandboxed-compute/v1",
        allowed_tools=(
            ToolName.WORKSPACE_LIST,
            ToolName.WORKSPACE_READ,
            ToolName.MEMORY_SEARCH,
            ToolName.SHELL_EXECUTE,
            ToolName.PYTHON_EXECUTE,
        ),
        action_timeout_ms=30_000,
        max_attempts=2,
        sandbox=sandbox,
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
    model_config = ConfigDict(str_strip_whitespace=False)

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


class SandboxEnvironmentManifest(ContractModel):
    schema_version: Literal["sandbox-environment/v1"] = "sandbox-environment/v1"
    runtime: Literal["podman", "docker"]
    runtime_version: ShortReason
    image: NonEmptyText
    profile_sha256: Sha256Hex
    rootless: Literal[True] = True
    network: Literal["none"] = "none"
    root_filesystem: Literal["read_only"] = "read_only"
    workspace_mount: Literal["read_only"] = "read_only"
    dropped_capabilities: tuple[Literal["ALL"], ...] = ("ALL",)
    no_new_privileges: Literal[True] = True
    user_id: int = Field(ge=1)
    group_id: int = Field(ge=1)
    cpu_millis: int = Field(ge=100)
    memory_bytes: int = Field(ge=1)
    pids_limit: int = Field(ge=1)
    tmpfs_bytes: int = Field(ge=1)
    workspace_max_bytes: int = Field(ge=1)

    @classmethod
    def from_profile(
        cls,
        profile: SandboxProfile,
        *,
        runtime_version: str,
    ) -> SandboxEnvironmentManifest:
        return cls(
            runtime=profile.runtime,
            runtime_version=runtime_version,
            image=profile.image,
            profile_sha256=sandbox_profile_sha256(profile),
            user_id=profile.user_id,
            group_id=profile.group_id,
            cpu_millis=profile.cpu_millis,
            memory_bytes=profile.memory_bytes,
            pids_limit=profile.pids_limit,
            tmpfs_bytes=profile.tmpfs_bytes,
            workspace_max_bytes=profile.workspace_max_bytes,
        )


class _SandboxExecutionPayload(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    exit_code: int = Field(ge=0, le=255)
    successful: bool
    duration_ms: int = Field(ge=0)
    stdout: str
    stderr: str
    stdout_bytes: int = Field(ge=0)
    stderr_bytes: int = Field(ge=0)
    stdout_sha256: Sha256Hex
    stderr_sha256: Sha256Hex
    stdout_truncated: bool
    stderr_truncated: bool
    workspace_bytes_before: int = Field(ge=0)
    workspace_bytes_after: int = Field(ge=0)
    workspace_quota_exceeded: bool
    arguments_sha256: Sha256Hex
    environment: SandboxEnvironmentManifest
    environment_sha256: Sha256Hex

    @model_validator(mode="after")
    def verify_execution_result(self) -> _SandboxExecutionPayload:
        expected_environment = canonical_json_sha256(self.environment.model_dump(mode="json"))
        if self.environment_sha256 != expected_environment:
            raise ValueError("environment_sha256 does not match the sandbox manifest")
        expected_success = self.exit_code == 0 and not self.workspace_quota_exceeded
        if self.successful is not expected_success:
            raise ValueError("successful does not match exit code and quota state")
        return self


class ShellExecutionPayload(_SandboxExecutionPayload):
    kind: Literal["shell_execution"] = "shell_execution"


class PythonExecutionPayload(_SandboxExecutionPayload):
    kind: Literal["python_execution"] = "python_execution"


ToolResultPayload: TypeAlias = Annotated[
    WorkspaceReadPayload
    | WorkspaceListPayload
    | MemorySearchPayload
    | ShellExecutionPayload
    | PythonExecutionPayload,
    Field(discriminator="kind"),
]

_PAYLOAD_TO_TOOL = {
    WorkspaceReadPayload: ToolName.WORKSPACE_READ,
    WorkspaceListPayload: ToolName.WORKSPACE_LIST,
    MemorySearchPayload: ToolName.MEMORY_SEARCH,
    ShellExecutionPayload: ToolName.SHELL_EXECUTE,
    PythonExecutionPayload: ToolName.PYTHON_EXECUTE,
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
