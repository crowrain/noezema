"""Bounded implementations of the read-only MVP tools."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from packages.domain import (
    BoundAction,
    ClaimAssessmentId,
    ClaimId,
    EpistemicStatus,
    MemorySearchArguments,
    MemorySearchHit,
    MemorySearchPayload,
    PythonExecuteArguments,
    PythonExecutionPayload,
    ShellExecuteArguments,
    ShellExecutionPayload,
    ToolArguments,
    ToolExecutionObservation,
    ToolName,
    WorkspaceEntry,
    WorkspaceListArguments,
    WorkspaceListPayload,
    WorkspaceReadArguments,
    WorkspaceReadPayload,
    canonical_json_sha256,
    sandbox_profile_sha256,
)
from packages.persistence.models import (
    ClaimAssessmentHeadRecord,
    ClaimAssessmentRecord,
    ClaimRecord,
    SessionRecord,
)
from packages.tool_broker.errors import ToolDeadlineExceeded, ToolExecutionError
from packages.tool_broker.sandbox_runtime import SandboxRunner


class ToolExecutor(Protocol):
    def execute(
        self,
        *,
        action: BoundAction,
        arguments: ToolArguments,
        captured_at: datetime,
        timeout_ms: int,
    ) -> ToolExecutionObservation: ...


@dataclass(frozen=True, slots=True)
class _Deadline:
    expires_at: float

    @classmethod
    def after_ms(cls, timeout_ms: int) -> _Deadline:
        return cls(expires_at=time.monotonic() + timeout_ms / 1000)

    def check(self) -> None:
        if time.monotonic() > self.expires_at:
            raise ToolDeadlineExceeded


class SafeToolExecutor:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        workspace_root: Path,
        max_workspace_read_bytes: int,
        max_workspace_list_entries: int,
        max_memory_results: int,
    ) -> None:
        self._session_factory = session_factory
        self._workspace_root = workspace_root.resolve(strict=True)
        self._max_workspace_read_bytes = max_workspace_read_bytes
        self._max_workspace_list_entries = max_workspace_list_entries
        self._max_memory_results = max_memory_results

    def execute(
        self,
        *,
        action: BoundAction,
        arguments: ToolArguments,
        captured_at: datetime,
        timeout_ms: int,
    ) -> ToolExecutionObservation:
        deadline = _Deadline.after_ms(timeout_ms)
        if action.tool is ToolName.WORKSPACE_READ and isinstance(arguments, WorkspaceReadArguments):
            return self._read(action, arguments, captured_at, deadline)
        if action.tool is ToolName.WORKSPACE_LIST and isinstance(arguments, WorkspaceListArguments):
            return self._list(action, arguments, captured_at, deadline)
        if action.tool is ToolName.MEMORY_SEARCH and isinstance(arguments, MemorySearchArguments):
            return self._search(action, arguments, captured_at, deadline)
        raise ToolExecutionError(
            "tool_contract_mismatch",
            "The accepted action does not match its trusted argument schema.",
            retryable=False,
        )

    def _resolve(self, requested_path: str) -> Path:
        candidate = (self._workspace_root / requested_path).resolve(strict=False)
        if not candidate.is_relative_to(self._workspace_root):
            raise ToolExecutionError(
                "workspace_path_outside_root",
                "The requested path resolves outside the configured workspace.",
                retryable=False,
            )
        return candidate

    def _read(
        self,
        action: BoundAction,
        arguments: WorkspaceReadArguments,
        captured_at: datetime,
        deadline: _Deadline,
    ) -> ToolExecutionObservation:
        deadline.check()
        path = self._resolve(arguments.path)
        try:
            if not path.is_file():
                raise ToolExecutionError(
                    "workspace_file_not_found",
                    "The requested workspace path is not a regular file.",
                    retryable=False,
                )
            size = path.stat().st_size
            if size > self._max_workspace_read_bytes:
                raise ToolExecutionError(
                    "workspace_file_too_large",
                    "The requested workspace file exceeds the policy byte limit.",
                    retryable=False,
                )
            content_bytes = path.read_bytes()
        except ToolExecutionError:
            raise
        except OSError as exc:
            raise ToolExecutionError(
                "workspace_read_failed",
                "The workspace file could not be read.",
                retryable=True,
            ) from exc
        deadline.check()
        if len(content_bytes) > self._max_workspace_read_bytes:
            raise ToolExecutionError(
                "workspace_file_too_large",
                "The requested workspace file exceeds the policy byte limit.",
                retryable=False,
            )
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolExecutionError(
                "workspace_file_not_utf8",
                "Only UTF-8 text files are available to workspace.read in the MVP.",
                retryable=False,
            ) from exc
        payload = WorkspaceReadPayload(
            path=arguments.path,
            size_bytes=len(content_bytes),
            content_sha256=hashlib.sha256(content_bytes).hexdigest(),
            content=content,
        )
        return ToolExecutionObservation.build(
            action_id=action.action_id,
            tool=action.tool,
            source=f"workspace:{arguments.path}",
            captured_at=captured_at,
            payload=payload,
        )

    def _list(
        self,
        action: BoundAction,
        arguments: WorkspaceListArguments,
        captured_at: datetime,
        deadline: _Deadline,
    ) -> ToolExecutionObservation:
        root = self._resolve(arguments.path)
        if not root.is_dir():
            raise ToolExecutionError(
                "workspace_directory_not_found",
                "The requested workspace path is not a directory.",
                retryable=False,
            )
        pending = [root]
        entries: list[WorkspaceEntry] = []
        truncated = False
        try:
            while pending:
                deadline.check()
                directory = pending.pop()
                children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
                for child in children:
                    deadline.check()
                    if child.is_symlink():
                        continue
                    relative = child.relative_to(self._workspace_root).as_posix()
                    if child.is_dir():
                        entry = WorkspaceEntry(path=relative, kind="directory")
                        if arguments.recursive:
                            pending.append(child)
                    elif child.is_file():
                        entry = WorkspaceEntry(
                            path=relative,
                            kind="file",
                            size_bytes=child.stat().st_size,
                        )
                    else:
                        continue
                    if len(entries) == self._max_workspace_list_entries:
                        truncated = True
                        pending.clear()
                        break
                    entries.append(entry)
        except ToolExecutionError:
            raise
        except OSError as exc:
            raise ToolExecutionError(
                "workspace_list_failed",
                "The workspace directory could not be listed.",
                retryable=True,
            ) from exc
        entries.sort(key=lambda item: item.path)
        payload = WorkspaceListPayload(
            path=arguments.path,
            recursive=arguments.recursive,
            entries=tuple(entries),
            truncated=truncated,
        )
        return ToolExecutionObservation.build(
            action_id=action.action_id,
            tool=action.tool,
            source=f"workspace:{arguments.path}",
            captured_at=captured_at,
            payload=payload,
        )

    def _search(
        self,
        action: BoundAction,
        arguments: MemorySearchArguments,
        captured_at: datetime,
        deadline: _Deadline,
    ) -> ToolExecutionObservation:
        deadline.check()
        limit = min(arguments.limit, self._max_memory_results)
        with self._session_factory() as db:
            session_record = db.get(SessionRecord, action.session_id.root)
            if session_record is None:
                raise ToolExecutionError(
                    "session_not_found",
                    "The action session no longer exists.",
                    retryable=False,
                )
            query = arguments.query.lower()
            rows = db.execute(
                select(
                    ClaimRecord,
                    ClaimAssessmentHeadRecord,
                    ClaimAssessmentRecord,
                )
                .join(
                    ClaimAssessmentHeadRecord,
                    ClaimAssessmentHeadRecord.claim_id == ClaimRecord.id,
                )
                .join(
                    ClaimAssessmentRecord,
                    ClaimAssessmentRecord.id == ClaimAssessmentHeadRecord.current_assessment_id,
                )
                .where(
                    ClaimAssessmentHeadRecord.config_snapshot_id
                    == session_record.config_snapshot_id,
                    ClaimAssessmentHeadRecord.assessment_state == "current",
                    or_(
                        func.lower(ClaimRecord.statement).contains(query, autoescape=True),
                        func.lower(ClaimRecord.topic).contains(query, autoescape=True),
                    ),
                )
                .order_by(ClaimRecord.created_in_session, ClaimRecord.id)
                .limit(limit + 1)
            ).all()
        deadline.check()
        truncated = len(rows) > limit
        hits = tuple(
            MemorySearchHit(
                claim_id=ClaimId(root=claim.id),
                assessment_id=ClaimAssessmentId(root=assessment.id),
                statement=claim.statement,
                topic=claim.topic,
                epistemic_status=EpistemicStatus(head.epistemic_status),
                confidence_basis_points=assessment.confidence_basis_points,
            )
            for claim, head, assessment in rows[:limit]
        )
        payload = MemorySearchPayload(
            query=arguments.query,
            hits=hits,
            truncated=truncated,
        )
        return ToolExecutionObservation.build(
            action_id=action.action_id,
            tool=action.tool,
            source="memory:current-assessment-heads",
            captured_at=captured_at,
            payload=payload,
        )


class SandboxToolExecutor:
    """Adapt OCI sandbox results to trusted, typed Tool Broker observations."""

    def __init__(self, *, runner: SandboxRunner) -> None:
        self._runner = runner

    def execute(
        self,
        *,
        action: BoundAction,
        arguments: ToolArguments,
        captured_at: datetime,
        timeout_ms: int,
    ) -> ToolExecutionObservation:
        if not isinstance(arguments, ShellExecuteArguments | PythonExecuteArguments):
            raise ToolExecutionError(
                "tool_contract_mismatch",
                "The sandbox executor received a non-execution tool.",
                retryable=False,
            )
        result = self._runner.execute(
            action_id=action.action_id,
            session_id=str(action.session_id),
            tool=action.tool,
            arguments=arguments,
            timeout_ms=timeout_ms,
        )
        environment = result.environment
        if environment.profile_sha256 != sandbox_profile_sha256(self._runner.profile):
            raise ToolExecutionError(
                "sandbox_environment_mismatch",
                "The sandbox result does not match the pinned capability profile.",
                retryable=False,
                outcome_known=False,
            )
        payload_type = (
            ShellExecutionPayload
            if isinstance(arguments, ShellExecuteArguments)
            else PythonExecutionPayload
        )
        payload = payload_type(
            exit_code=result.captured.exit_code,
            successful=(result.captured.exit_code == 0 and not result.workspace_quota_exceeded),
            duration_ms=result.captured.duration_ms,
            stdout=result.captured.stdout,
            stderr=result.captured.stderr,
            stdout_bytes=result.captured.stdout_bytes,
            stderr_bytes=result.captured.stderr_bytes,
            stdout_sha256=result.captured.stdout_sha256,
            stderr_sha256=result.captured.stderr_sha256,
            stdout_truncated=result.captured.stdout_truncated,
            stderr_truncated=result.captured.stderr_truncated,
            workspace_bytes_before=result.workspace_bytes_before,
            workspace_bytes_after=result.workspace_bytes_after,
            workspace_quota_exceeded=result.workspace_quota_exceeded,
            arguments_sha256=action.arguments_sha256,
            environment=environment,
            environment_sha256=canonical_json_sha256(environment.model_dump(mode="json")),
        )
        return ToolExecutionObservation.build(
            action_id=action.action_id,
            tool=action.tool,
            source=(f"sandbox:{environment.runtime}:{environment.image}:{action.action_id}"),
            captured_at=captured_at,
            payload=payload,
        )


class DispatchingToolExecutor:
    """Route closed tool names to trusted adapters without model-controlled fallback."""

    def __init__(
        self,
        *,
        safe: SafeToolExecutor,
        sandbox: SandboxToolExecutor | None,
    ) -> None:
        self._safe = safe
        self._sandbox = sandbox

    def execute(
        self,
        *,
        action: BoundAction,
        arguments: ToolArguments,
        captured_at: datetime,
        timeout_ms: int,
    ) -> ToolExecutionObservation:
        if action.tool in {
            ToolName.WORKSPACE_READ,
            ToolName.WORKSPACE_LIST,
            ToolName.MEMORY_SEARCH,
        }:
            return self._safe.execute(
                action=action,
                arguments=arguments,
                captured_at=captured_at,
                timeout_ms=timeout_ms,
            )
        if action.tool in {ToolName.SHELL_EXECUTE, ToolName.PYTHON_EXECUTE}:
            if self._sandbox is None:
                raise ToolExecutionError(
                    "sandbox_not_configured",
                    "The capability policy did not configure a sandbox runtime.",
                    retryable=False,
                )
            return self._sandbox.execute(
                action=action,
                arguments=arguments,
                captured_at=captured_at,
                timeout_ms=timeout_ms,
            )
        raise ToolExecutionError(
            "tool_not_implemented",
            "No trusted adapter exists for the accepted tool.",
            retryable=False,
        )
