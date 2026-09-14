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
            self.SUCCEEDED, self.SUCCEEDED_PARTIAL, self.FAILED, self.CANCELLED,
        }

    @property
    def allows_stop(self) -> bool:
        """waking..verifying range"""
        return self in {
            self.WAKING, self.ORIENTING, self.SELECTING_QUESTION,
            self.PLANNING, self.EXPLORING, self.VERIFYING,
        }

    @property
    def allows_abort(self) -> bool:
        """created..reporting (excluding committing+)"""
        return self not in {
            self.COMMITTING, self.RECONCILING_COMMIT,
        } and not self.is_terminal


class DecisionKind(StrEnum):
    TOOL = "tool"
    COMPLETE = "complete"


class EvidenceKind(StrEnum):
    SOURCE_ASSERTION = "source_assertion"
    QUOTE_INTEGRITY = "quote_integrity"
    EXPERIMENT_RUN = "experiment_run"
    COMPUTATION = "computation"
    FORMAL_CHECK = "formal_check"
    LOCAL_OBSERVATION = "local_observation"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    COUNTERS = "counters"


class EffectiveGrade(StrEnum):
    E0 = "E0"  # unverified
    E1 = "E1"  # integrity_checked
    E2 = "E2"  # single_method_supported_in_scope
    E3 = "E3"  # independently_corroborated_or_replicated_in_scope
    E4 = "E4"  # formally_verified_or_repeatedly_independently_replicated

    @property
    def level(self) -> int:
        return int(self.value[1:])


class EpistemicStatus(StrEnum):
    HYPOTHESIS = "hypothesis"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    REFUTED = "refuted"
    DEFERRED = "deferred"


class AssessmentState(StrEnum):
    CURRENT = "current"
    PENDING = "pending"
    INVALID = "invalid"


class ClaimType(StrEnum):
    LOCAL_OBSERVATION = "local_observation"
    COMPUTED_RESULT = "computed_result"
    FORMAL_THEOREM = "formal_theorem"
    EMPIRICAL_CONJECTURE = "empirical_conjecture"
    PROCEDURAL = "procedural"
    EXTERNAL_FACT = "external_fact"
    TEMPORAL_FACT = "temporal_fact"
    SELF_MODEL = "self_model"


class IdempotencyClass(StrEnum):
    PURE = "pure"
    OBSERVATION = "observation"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


class ActionStatus(StrEnum):
    PROPOSED = "action_proposed"
    POLICY_EVALUATED = "policy_evaluated"
    ACCEPTED = "action_accepted"
    STARTED = "action_started"
    COMPLETED = "action_completed"
    FAILED = "action_failed"
    OUTCOME_UNKNOWN = "action_outcome_unknown"


class CommitAttemptStatus(StrEnum):
    PREPARED = "prepared"
    RECONCILING = "reconciling"
    COMMITTED = "committed"
    ABORTED = "aborted"

    @property
    def is_unresolved(self) -> bool:
        return self in {self.PREPARED, self.RECONCILING}


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_OPERATOR = "require_operator"


class QuestionSource(StrEnum):
    SEEDED = "seeded"
    MESSAGE = "message"
    COUNTEREVIDENCE = "counterevidence"
    MODEL_PROPOSAL = "model_proposal"


class AuditEventType(StrEnum):
    SESSION_STARTED = "session_started"
    SESSION_STATE_CHANGED = "session_state_changed"
    ACTION_LIFECYCLE = "action_lifecycle"
    MODEL_RUN = "model_run"
    POLICY_EVALUATED = "policy_evaluated"
    CONTEXT_PACKED = "context_packed"
    EVIDENCE_CREATED = "evidence_created"
    CLAIM_ASSESSED = "claim_assessed"
    SESSION_COMMITTED = "session_committed"
    SESSION_FAILED = "session_failed"
    RECONCILIATION = "reconciliation"
    OPERATOR_COMMAND = "operator_command"
    HOST_EVENT = "host_event"
    # Stage 3b: reassessment, dependency barriers, online activation
    REASSESSMENT_JOB_CREATED = "reassessment_job_created"
    REASSESSMENT_JOB_LEASED = "reassessment_job_leased"
    REASSESSMENT_JOB_COMPLETED = "reassessment_job_completed"
    REASSESSMENT_JOB_RETRY = "reassessment_job_retry"
    REASSESSMENT_JOB_BLOCKED = "reassessment_job_blocked"
    DEPENDENCY_INVALIDATED = "dependency_invalidated"
    CLOSURE_MANIFEST_CREATED = "closure_manifest_created"
    BARRIER_CREATED = "barrier_created"
    BARRIER_BATCH_APPLIED = "barrier_batch_applied"
    BARRIER_GENERATION_ADVANCED = "barrier_generation_advanced"
    BARRIER_RESOLVED = "barrier_resolved"
    BARRIER_BLOCKED = "barrier_blocked"
    CONFIG_ACTIVATION_ACQUIRED = "config_activation_acquired"
    CONFIG_ACTIVATION_PUBLISHED = "config_activation_published"
    CONFIG_ACTIVATION_FAILED = "config_activation_failed"


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


class MessageState(StrEnum):
    QUEUED = "queued"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    EXPIRED = "expired"


class MessagePriority(StrEnum):
    NORMAL = "normal"
    HIGH = "high"


class NodeState(StrEnum):
    SLEEPING = "sleeping"
    PAUSED = "paused"


class PreparedBy(StrEnum):
    SESSION = "session"
    RULES_ACTIVATION = "rules_activation"
    REASSESSMENT_WORKER = "reassessment_worker"


class ActivationMode(StrEnum):
    BOOTSTRAP = "bootstrap"
    OFFLINE = "offline"
    ONLINE = "online"


class ActivationState(StrEnum):
    DRAFT = "draft"
    PREPARING_HEADS = "preparing_heads"
    READY = "ready"
    PUBLISHING = "publishing"
    POST_PUBLISH = "post_publish"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class ClaimRelation(StrEnum):
    SUPPORTS = "supports"
    COUNTERS = "counters"
    DEPENDS_ON = "depends_on"


class ReassessmentJobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RETRY = "retry"
    BLOCKED = "blocked"
    COMPLETED = "completed"

    @property
    def is_active(self) -> bool:
        """queued | leased | retry — one per claim/target config (unique constraint)."""
        return self in {self.QUEUED, self.LEASED, self.RETRY}

    @property
    def is_runnable(self) -> bool:
        """runnable subset: scheduled and within retry budget (checked in code)."""
        return self in {self.QUEUED}


class ReassessmentErrorClass(StrEnum):
    TRANSIENT = "transient"
    DETERMINISTIC = "deterministic"
    PERMANENT = "permanent"


class ReassessmentJobReason(StrEnum):
    INVALIDATION = "invalidation"
    RULES_CHANGE = "rules_change"
    REVERIFY_DEADLINE = "reverify_deadline"
    OPERATOR = "operator"
    REPAIR = "repair"


# Priority bands (lower = first). Spec §5.9.1:
# 0 — reverse dependencies of active questions
# 1 — external / temporal facts
# 2 — everything else
JOB_PRIORITY_ACTIVE_QUESTION_DEP = 0
JOB_PRIORITY_EXTERNAL_TEMPORAL = 1
JOB_PRIORITY_DEFAULT = 2


class BarrierStatus(StrEnum):
    DISCOVERING = "discovering"
    ACTIVE = "active"
    CLOSING = "closing"
    RESOLVED = "resolved"
    BLOCKED = "blocked"

    @property
    def is_open(self) -> bool:
        """GC roots that must survive crash recovery."""
        return self in {self.DISCOVERING, self.ACTIVE, self.CLOSING}
