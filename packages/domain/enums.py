"""Closed enums defined by the architecture contract."""

from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    CREATED = "created"
    WAKING = "waking"
    ORIENTING = "orienting"
    SELECTING_QUESTION = "selecting_question"
    PLANNING = "planning"
    EXPLORING = "exploring"
    VERIFYING = "verifying"
    STOPPING = "stopping"
    CONSOLIDATING = "consolidating"
    REPORTING = "reporting"
    COMMITTING = "committing"
    RECONCILING_COMMIT = "reconciling_commit"
    ABORTING = "aborting"
    SUCCEEDED = "succeeded"
    SUCCEEDED_PARTIAL = "succeeded_partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            SessionState.SUCCEEDED,
            SessionState.SUCCEEDED_PARTIAL,
            SessionState.FAILED,
            SessionState.CANCELLED,
        }


class NodeState(StrEnum):
    SLEEPING = "sleeping"
    PAUSED = "paused"


class DecisionKind(StrEnum):
    TOOL = "tool"
    COMPLETE = "complete"


class IdempotencyClass(StrEnum):
    PURE = "pure"
    OBSERVATION = "observation"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


class ToolName(StrEnum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_LIST = "workspace.list"
    WORKSPACE_WRITE = "workspace.write"
    ARTIFACT_CREATE = "artifact.create"
    MEMORY_SEARCH = "memory.search"
    QUESTION_CREATE = "question.create"
    MESSAGE_REPLY = "message.reply"
    SHELL_EXECUTE = "shell.execute"
    PYTHON_EXECUTE = "python.execute"
    WEB_SEARCH = "web.search"
    WEB_FETCH = "web.fetch"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_OPERATOR = "require_operator"


class ActionState(StrEnum):
    PROPOSED = "proposed"
    POLICY_EVALUATED = "policy_evaluated"
    ACCEPTED = "accepted"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class EventType(StrEnum):
    SESSION_STATE_CHANGED = "SessionStateChanged"
    MODEL_RUN_COMPLETED = "ModelRunCompleted"
    ACTION_PROPOSED = "ActionProposed"
    POLICY_EVALUATED = "PolicyEvaluated"
    ACTION_ACCEPTED = "ActionAccepted"
    ACTION_STARTED = "ActionStarted"
    ACTION_COMPLETED = "ActionCompleted"
    ACTION_FAILED = "ActionFailed"
    ACTION_OUTCOME_UNKNOWN = "ActionOutcomeUnknown"
    COMMIT_ATTEMPT_PREPARED = "CommitAttemptPrepared"
    COMMIT_ATTEMPT_RECONCILED = "CommitAttemptReconciled"
