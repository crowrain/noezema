"""Canonical enums (T1.1).

Single source for every closed vocabulary in ARCHITECTURE.md v0.25.
Values are the wire/DB representations (lowercase snake_case / E0..E4).
"""

from __future__ import annotations

from enum import StrEnum

# ─── Session lifecycle (§6) ────────────────────────────────────────────────


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
        return self in _TERMINAL_STATES

    @property
    def allows_stop(self) -> bool:
        """waking..verifying range (§6.7)."""
        return self in _STOPPABLE_STATES

    @property
    def allows_abort(self) -> bool:
        """created..reporting, excluding committing and terminal (§6.7)."""
        return self in _ABORTABLE_STATES


_TERMINAL_STATES: frozenset[SessionState] = frozenset(
    {
        SessionState.SUCCEEDED,
        SessionState.SUCCEEDED_PARTIAL,
        SessionState.FAILED,
        SessionState.CANCELLED,
    }
)
_STOPPABLE_STATES: frozenset[SessionState] = frozenset(
    {
        SessionState.WAKING,
        SessionState.ORIENTING,
        SessionState.SELECTING_QUESTION,
        SessionState.PLANNING,
        SessionState.EXPLORING,
        SessionState.VERIFYING,
    }
)
_ABORTABLE_STATES: frozenset[SessionState] = frozenset(
    {
        SessionState.CREATED,
        SessionState.WAKING,
        SessionState.ORIENTING,
        SessionState.SELECTING_QUESTION,
        SessionState.PLANNING,
        SessionState.EXPLORING,
        SessionState.VERIFYING,
        SessionState.STOPPING,
        SessionState.CONSOLIDATING,
        SessionState.REPORTING,
    }
)


class NodeState(StrEnum):
    """Node-level state, stored separately from session state (§6)."""

    SLEEPING = "sleeping"
    PAUSED = "paused"


# ─── Decision protocol (§7) ────────────────────────────────────────────────


class DecisionKind(StrEnum):
    TOOL = "tool"
    COMPLETE = "complete"


class CompleteReason(StrEnum):
    GOAL_REACHED = "goal_reached"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_PROGRESS = "no_progress"
    BLOCKED = "blocked"
    OPERATOR_STOP = "operator_stop"


# ─── Actions, policy, idempotency (§5.6, §5.7, §7, §14.2) ─────────────────


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_OPERATOR = "require_operator"


class ActionState(StrEnum):
    """Lifecycle: ActionProposed → PolicyEvaluated → ActionAccepted →
    ActionStarted → ActionCompleted | ActionFailed | ActionOutcomeUnknown."""

    PROPOSED = "proposed"
    POLICY_EVALUATED = "policy_evaluated"
    ACCEPTED = "accepted"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class IdempotencyClass(StrEnum):
    """Tool repeatability class (§5.7)."""

    PURE = "pure"
    OBSERVATION = "observation"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


# ─── Commit boundary (§5.2.2) ──────────────────────────────────────────────


class CommitAttemptStatus(StrEnum):
    PREPARED = "prepared"
    RECONCILING = "reconciling"
    COMMITTED = "committed"
    ABORTED = "aborted"

    @property
    def is_unresolved(self) -> bool:
        return self in (CommitAttemptStatus.PREPARED, CommitAttemptStatus.RECONCILING)


# ─── Staging (§5.2.2, §5.9) ────────────────────────────────────────────────


class StagingAggregateType(StrEnum):
    QUESTION = "question"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    CLAIM_DEPENDENCY = "claim_dependency"
    IDENTITY = "identity"


class StagingOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class StagingValidationStatus(StrEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"


# ─── Evidence and assessment (§3.7, §6.4, §8.2, §14.3) ────────────────────


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


class AssessmentEvidenceRole(StrEnum):
    SUPPORT = "support"
    COUNTER = "counter"
    SCOPE_WITNESS = "scope_witness"
    CONTEXT = "context"


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
    """Lifecycle of a claim assessment head (§8.2, §14.1)."""

    CURRENT = "current"
    PENDING = "pending"
    INVALID = "invalid"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    DUE = "due"
    STALE = "stale"
    UNKNOWN = "unknown"


class ClaimType(StrEnum):
    """Closed registry v1 (§8.7)."""

    LOCAL_OBSERVATION = "local_observation"
    COMPUTED_RESULT = "computed_result"
    FORMAL_THEOREM = "formal_theorem"
    EMPIRICAL_CONJECTURE = "empirical_conjecture"
    PROCEDURAL = "procedural"
    EXTERNAL_FACT = "external_fact"
    TEMPORAL_FACT = "temporal_fact"
    SELF_MODEL = "self_model"


class DependencyKind(StrEnum):
    """claim_dependencies.kind (T4.1, §8.6).

    ``evidential`` edges form the DAG used by cascade invalidation
    (cycle check at commit, graph revision bump). ``research`` is the
    explicit marker for a research dependency (e.g. on a hypothesis,
    which never serves as sufficient evidence); research edges do not
    participate in the cycle check or the graph revision.
    """

    EVIDENTIAL = "evidential"
    RESEARCH = "research"


class BarrierStatus(StrEnum):
    """dependency_invalidation_barriers.status (T4.2, §8.6).

    ``discovering`` — (re)building the closure manifest outside the
    barrier transaction; ``active`` — applying batches from the durable
    cursor; ``closing`` — manifest exhausted, final closure check before
    ``resolved``; ``blocked`` — manifest hash mismatch / impossible
    cursor / closure-invariant violation: ancestor protection is kept
    and only an audited operator recovery may close it (it never
    auto-resolves).
    """

    DISCOVERING = "discovering"
    ACTIVE = "active"
    CLOSING = "closing"
    RESOLVED = "resolved"
    BLOCKED = "blocked"

    @property
    def is_open(self) -> bool:
        """Open barriers keep ancestor protection on their closure."""
        return self in (
            BarrierStatus.DISCOVERING,
            BarrierStatus.ACTIVE,
            BarrierStatus.CLOSING,
            BarrierStatus.BLOCKED,
        )


class ReassessmentJobStatus(StrEnum):
    """reassessment_jobs.status (T4.2 scaffold, T4.3 worker, §14.1).

    At most one ``queued|leased|retry`` job per (claim, target snapshot)
    (partial unique index); ``blocked|completed`` rows stay for history.
    """

    QUEUED = "queued"
    LEASED = "leased"
    RETRY = "retry"
    BLOCKED = "blocked"
    COMPLETED = "completed"

    @property
    def is_active(self) -> bool:
        return self in (
            ReassessmentJobStatus.QUEUED,
            ReassessmentJobStatus.LEASED,
            ReassessmentJobStatus.RETRY,
        )


# ─── Questions (§9) ────────────────────────────────────────────────────────


class QuestionState(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    RESEARCHING = "researching"
    PARTIALLY_ANSWERED = "partially_answered"
    VERIFIED = "verified"
    REJECTED = "rejected"
    DEFERRED = "deferred"

    @property
    def is_resolved(self) -> bool:
        return self in (QuestionState.VERIFIED, QuestionState.REJECTED)


class QuestionOrigin(StrEnum):
    SEEDED = "seeded"
    MESSAGE = "message"
    CONFLICT = "conflict"
    UNKNOWN_TERM = "unknown_term"
    UNVERIFIED_CLAIM = "unverified_claim"
    PREVIOUS_RESULT = "previous_result"
    LOCAL_CORPUS = "local_corpus"
    MODEL_PROPOSAL = "model_proposal"
    INVALID_ASSESSMENT = "invalid_assessment"


# ─── Messages and operator commands (§13.2, §13.6, §13.7) ─────────────────


class MessageState(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    ANSWERED = "answered"
    EXPIRED = "expired"


class OperatorCommandType(StrEnum):
    """Closed enum (§13.2). Free text is never parsed into this."""

    PAUSE = "pause"
    RESUME = "resume"
    WAKE_NOW = "wake_now"
    STOP_GRACEFULLY = "stop_gracefully"
    ABORT_SESSION = "abort_session"
    SET_BUDGET = "set_budget"
    SET_ACCESS_PROFILE = "set_access_profile"
    RESTORE_CHECKPOINT = "restore_checkpoint"


class OperatorCommandState(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WAITING_SAFE_BOUNDARY = "waiting_safe_boundary"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


# ─── Config snapshots and activation (§8.7, §14.1) ────────────────────────


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
    POST_PUBLISH_BLOCKED = "post_publish_blocked"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


# ─── Host recovery (§8.7.1.1, §13) ────────────────────────────────────────


class HostTransitionState(StrEnum):
    CHECKING = "checking"
    RETRY_WAIT = "retry_wait"
    READY_TO_START = "ready_to_start"
    RESOLVED = "resolved"
    RESUME_DEGRADED = "resume_degraded"
    RESUME_BLOCKED = "resume_blocked"


class HostPolicyChangeState(StrEnum):
    PREPARED = "prepared"
    INCONSISTENT = "inconsistent"
    RESOLUTION_PREPARED = "resolution_prepared"
    COMMITTED = "committed"
    ABORTED = "aborted"
    RESOLVED = "resolved"


# ─── Audit / outbox (§5.10, §14.4) ────────────────────────────────────────


class AuditEventType(StrEnum):
    WAKE_SKIPPED = "wake_skipped"
    SESSION_STARTED = "session_started"
    SESSION_STATE_CHANGED = "session_state_changed"
    SESSION_COMMITTED = "session_committed"
    SESSION_FAILED = "session_failed"
    SESSION_CANCELLED = "session_cancelled"
    QUESTION_SELECTED = "question_selected"
    CONTEXT_PACKED = "context_packed"
    ACTION_PROPOSED = "action_proposed"
    POLICY_EVALUATED = "policy_evaluated"
    ACTION_ACCEPTED = "action_accepted"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    ACTION_OUTCOME_UNKNOWN = "action_outcome_unknown"
    STAGING_RECORDED = "staging_recorded"
    STAGING_VALIDATED = "staging_validated"
    STAGING_REJECTED = "staging_rejected"
    CLAIM_CREATED = "claim_created"
    CLAIM_REVISION = "claim_revision"
    CLAIM_ASSESSED = "claim_assessed"
    CLAIM_INVALIDATED = "claim_invalidated"
    DEPENDENCY_EDGE_REJECTED = "dependency_edge_rejected"
    CASCADE_STARTED = "cascade_started"
    BARRIER_GENERATION_PUBLISHED = "barrier_generation_published"
    BARRIER_BATCH_APPLIED = "barrier_batch_applied"
    BARRIER_RESOLVED = "barrier_resolved"
    BARRIER_BLOCKED = "barrier_blocked"
    REASSESSMENT_JOB_COMPLETED = "reassessment_job_completed"
    REASSESSMENT_JOB_RETRY = "reassessment_job_retry"
    REASSESSMENT_JOB_BLOCKED = "reassessment_job_blocked"
    COMMIT_ATTEMPT_PREPARED = "commit_attempt_prepared"
    COMMIT_ATTEMPT_COMMITTED = "commit_attempt_committed"
    COMMIT_ATTEMPT_ABORTED = "commit_attempt_aborted"
    COMMIT_RECONCILED = "commit_reconciled"
    MESSAGE_CREATED = "message_created"
    MESSAGE_DELIVERED = "message_delivered"
    MESSAGE_ANSWERED = "message_answered"
    MESSAGE_EXPIRED = "message_expired"
    OPERATOR_COMMAND_RECEIVED = "operator_command_received"
    OPERATOR_COMMAND_COMPLETED = "operator_command_completed"
    OPERATOR_COMMAND_REJECTED = "operator_command_rejected"
    CONFIG_SNAPSHOT_CREATED = "config_snapshot_created"
    CONFIG_ACTIVATED = "config_activated"
    # T4.5 (§8.7.2): the online activation lifecycle
    ACTIVATION_ACQUIRED = "activation_acquired"
    ACTIVATION_TAKEOVER = "activation_takeover"
    ACTIVATION_PUBLISHED = "activation_published"
    ACTIVATION_POST_PUBLISH_BATCH = "activation_post_publish_batch"
    ACTIVATION_POST_PUBLISH_COMPLETED = "activation_post_publish_completed"
    ACTIVATION_POST_PUBLISH_BLOCKED = "activation_post_publish_blocked"
    ACTIVATION_CLEANED_UP = "activation_cleaned_up"
    ACTIVATION_SUPERSEDED = "activation_superseded"
    # T4.7 (§11.3, §14): the full source graph
    SOURCE_GRAPH_CHANGED = "source_graph_changed"
    # T4.8 (§8.7.4): counterevidence resolutions
    COUNTER_RESOLUTION_CREATED = "counter_resolution_created"
    # T5.2 (stage 4): the plan as an observable artifact
    PLAN_PROPOSED = "plan_proposed"
    PLAN_FALLBACK = "plan_fallback"
    # T5.3 (stage 4): the verifier role (organizes deterministic
    # checks; never assigns grade/confidence — §3.7)
    VERIFICATION_COMPLETED = "verification_completed"
    VERIFICATION_FALLBACK = "verification_fallback"
    # T5.4 (stage 4): protection against semantic repetition (§9)
    REPEAT_CYCLE_DETECTED = "repeat_cycle_detected"
    QUESTION_DEFERRED = "question_deferred"
    # T5.5 (stage 4): the untrusted extraction profile (§11.2)
    EXTRACTION_COMPLETED = "extraction_completed"
    EXTRACTION_FALLBACK = "extraction_fallback"
    # T6.1 (stage 5, §5.12): the research proxy egress journal
    RESEARCH_FETCH_COMPLETED = "research_fetch_completed"
    RESEARCH_FETCH_REJECTED = "research_fetch_rejected"
    COUNTER_RESOLUTION_INVALIDATED = "counter_resolution_invalidated"
    ALERT_RAISED = "alert_raised"


class AuditVisibility(StrEnum):
    OPERATOR = "operator"
    OWNER = "owner"
    DIAGNOSTIC = "diagnostic"
