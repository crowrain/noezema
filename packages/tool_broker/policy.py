"""Capability-policy evaluation for host-bound tool actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from packages.domain import (
    BoundAction,
    CapabilityPolicy,
    MemorySearchArguments,
    PolicyDecision,
    PolicyEvaluation,
    PythonExecuteArguments,
    ShellExecuteArguments,
    ToolArguments,
    ToolName,
    WorkspaceListArguments,
    WorkspaceReadArguments,
    capability_policy_sha256,
)

_ARGUMENT_SCHEMAS = {
    ToolName.WORKSPACE_READ: WorkspaceReadArguments,
    ToolName.WORKSPACE_LIST: WorkspaceListArguments,
    ToolName.MEMORY_SEARCH: MemorySearchArguments,
    ToolName.SHELL_EXECUTE: ShellExecuteArguments,
    ToolName.PYTHON_EXECUTE: PythonExecuteArguments,
}


@dataclass(frozen=True, slots=True)
class EvaluatedAction:
    evaluation: PolicyEvaluation
    arguments: ToolArguments | None


class CapabilityPolicyEngine:
    """Evaluate exact typed arguments against one immutable capability snapshot."""

    def __init__(self, *, policy: CapabilityPolicy, workspace_root: Path) -> None:
        root = workspace_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        self.policy = policy
        self.workspace_root = root
        self.policy_sha256 = capability_policy_sha256(policy)

    def evaluate(self, action: BoundAction) -> EvaluatedAction:
        if action.tool not in self.policy.allowed_tools:
            return self.deny("tool_not_allowed")
        schema = _ARGUMENT_SCHEMAS.get(action.tool)
        if schema is None:
            return self.deny("tool_not_implemented")
        try:
            arguments = schema.model_validate(action.arguments)
        except ValidationError:
            return self.deny("arguments_invalid")

        if isinstance(arguments, MemorySearchArguments):
            if arguments.limit > self.policy.max_memory_results:
                return self.deny("memory_result_limit_exceeded")
        elif isinstance(arguments, WorkspaceReadArguments | WorkspaceListArguments):
            if not self._path_is_confined(arguments.path):
                return self.deny("workspace_path_outside_root")
        elif isinstance(arguments, ShellExecuteArguments | PythonExecuteArguments):
            if self.policy.sandbox is None:
                return self.deny("sandbox_not_configured")
            if not self._path_is_confined(arguments.cwd):
                return self.deny("workspace_path_outside_root")
            if (
                arguments.timeout_ms is not None
                and arguments.timeout_ms > self.policy.action_timeout_ms
            ):
                return self.deny("action_timeout_limit_exceeded")

        return EvaluatedAction(
            evaluation=PolicyEvaluation(
                decision=PolicyDecision.ALLOW,
                policy_version=self.policy.version,
                policy_sha256=self.policy_sha256,
                reason="capability_allowed",
            ),
            arguments=arguments,
        )

    def _path_is_confined(self, requested_path: str) -> bool:
        candidate = (self.workspace_root / requested_path).resolve(strict=False)
        return candidate.is_relative_to(self.workspace_root)

    def deny(self, reason: str) -> EvaluatedAction:
        return EvaluatedAction(
            evaluation=PolicyEvaluation(
                decision=PolicyDecision.DENY,
                policy_version=self.policy.version,
                policy_sha256=self.policy_sha256,
                reason=reason,
            ),
            arguments=None,
        )
