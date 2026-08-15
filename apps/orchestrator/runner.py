"""Crash-resumable vertical coordinator for one autonomous MVP session."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator.commands import (
    dispatch_next_operator_command,
    reconcile_operator_commands_for_session,
)
from apps.orchestrator.models import (
    ClaimedSession,
    CuratorTurnResult,
    SessionRunResult,
    SessionStarted,
    SessionWorkKind,
    WakeSkipped,
)
from apps.orchestrator.runtime import (
    CognitiveGateway,
    DurableSessionOrchestrator,
    FailedTurnError,
    GracefulStopRequestedError,
    InconsistentSessionRuntimeError,
    SessionAbortedError,
    SessionRuntimeError,
    SoftBudgetExhaustedError,
    apply_session_safe_boundary,
    claim_session,
    terminate_session,
    transition_session_phase,
)
from apps.orchestrator.service import start_next_session
from packages.cognition import (
    CuratorContext,
    CuratorOutcome,
    ExplorerContext,
    PromptBundle,
    ProtocolObservation,
    ProtocolQuestion,
    validate_curator_proposal,
)
from packages.domain import (
    ActionId,
    ActionState,
    CheckpointId,
    ClaimTypeRulesSnapshot,
    CommitAttemptId,
    ComputationObservation,
    EvidenceAdapterBudget,
    ExperimentObservation,
    KnowledgeCommitBatch,
    MessageId,
    MessageState,
    QuestionId,
    RevisionVector,
    ScopeDimension,
    SessionBudget,
    SessionBudgetUsage,
    SessionId,
    SessionState,
    SourceObservation,
    ToolExecutionObservation,
    TurnId,
    TypedObservation,
    VerifiedEvidenceMetadata,
    WorkspaceReadPayload,
    workspace_read_as_source_observation,
)
from packages.llm_gateway import ModelPhase, ModelRunResult
from packages.memory import (
    build_knowledge_commit_batch,
    finalize_knowledge_commit,
    prepare_knowledge_commit,
)
from packages.persistence import (
    acknowledge_session_messages,
    deliver_messages_for_session,
    load_revision_vector,
)
from packages.persistence.models import (
    ActionRecord,
    CheckpointRecord,
    ClaimRecord,
    CommitAttemptRecord,
    ConfigSnapshotRecord,
    MessageRecord,
    ModelRunRecord,
    OrchestratorTurnRecord,
    QuestionRecord,
    SessionRecord,
    SessionStagingRecord,
)
from packages.tool_broker import ToolBroker


class SessionStepLimitExceededError(SessionRuntimeError):
    """A caller-provided fairness bound stopped the runner between durable steps."""


@dataclass(frozen=True, slots=True)
class SessionRunnerLimits:
    """Trusted coordinator bounds; cognitive limits remain pinned on the session."""

    max_steps: int = 128
    max_claims: int = 8
    max_evidence_links: int = 64

    def __post_init__(self) -> None:
        if not 1 <= self.max_steps <= 10_000:
            raise ValueError("max_steps must be between 1 and 10000")
        if not 1 <= self.max_claims <= 32:
            raise ValueError("max_claims must be between 1 and 32")
        if not 1 <= self.max_evidence_links <= 1_000:
            raise ValueError("max_evidence_links must be between 1 and 1000")


@dataclass(frozen=True, slots=True)
class _EvidenceView:
    observation: TypedObservation
    public_summary: str


class AutonomousSessionRunner:
    """Drive one session from durable work directives to a terminal checkpoint."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        gateway: CognitiveGateway,
        tool_broker: ToolBroker,
        explorer_prompts: PromptBundle,
        curator_prompts: PromptBundle,
        lease_owner: str,
        lease_ttl_seconds: int = 300,
        limits: SessionRunnerLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tool_broker = tool_broker
        self._explorer_prompts = explorer_prompts
        self._curator_prompts = curator_prompts
        self._lease_owner = lease_owner
        self._lease_ttl_seconds = lease_ttl_seconds
        self._limits = limits or SessionRunnerLimits()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._turns = DurableSessionOrchestrator(
            session_factory=session_factory,
            gateway=gateway,
            tool_broker=tool_broker,
            lease_owner=lease_owner,
            lease_ttl_seconds=lease_ttl_seconds,
            clock=self._clock,
        )

    def run_once(
        self,
        *,
        budget: SessionBudget | None = None,
        max_steps: int | None = None,
    ) -> SessionRunResult | WakeSkipped:
        """Resume the single active session or admit the next FIFO question."""

        self._dispatch_commands()
        terminal_values = tuple(state.value for state in SessionState if state.is_terminal)
        with self._session_factory() as db:
            active = tuple(
                db.scalars(
                    select(SessionRecord.id)
                    .where(SessionRecord.state.not_in(terminal_values))
                    .order_by(SessionRecord.created_at, SessionRecord.id)
                )
            )
        if len(active) > 1:
            raise InconsistentSessionRuntimeError("more than one active session exists")
        if active:
            return self.run_session(SessionId(root=active[0]), max_steps=max_steps)

        session_id = SessionId.new()
        with self._session_factory.begin() as db:
            admitted = start_next_session(
                db,
                session_id=session_id,
                budget=budget,
                occurred_at=self._clock(),
            )
        if isinstance(admitted, WakeSkipped):
            return admitted
        assert isinstance(admitted, SessionStarted)
        return self.run_session(session_id, max_steps=max_steps)

    def run_session(
        self,
        session_id: SessionId,
        *,
        max_steps: int | None = None,
    ) -> SessionRunResult:
        """Run bounded durable steps; a later invocation can resume at any boundary."""

        step_limit = max_steps if max_steps is not None else self._limits.max_steps
        if not 1 <= step_limit <= 10_000:
            raise ValueError("max_steps must be between 1 and 10000")
        for _ in range(step_limit):
            terminal = self._terminal_projection(session_id)
            if terminal is not None:
                return terminal
            self.run_step(session_id)
        terminal = self._terminal_projection(session_id)
        if terminal is not None:
            return terminal
        raise SessionStepLimitExceededError(
            f"session {session_id} remains non-terminal after {step_limit} durable steps"
        )

    def run_step(self, session_id: SessionId) -> None:
        """Interpret exactly one recovery directive and commit its durable boundary."""

        self._dispatch_commands(session_id=session_id)
        if self._terminal_projection(session_id) is not None:
            self._reconcile_commands(session_id)
            return
        timestamp = self._clock()
        with self._session_factory.begin() as db:
            claimed = claim_session(
                db,
                session_id=session_id,
                owner=self._lease_owner,
                ttl_seconds=self._lease_ttl_seconds,
                occurred_at=timestamp,
            )
        work = claimed.directive.kind
        if work is SessionWorkKind.WAKE:
            self._transition(claimed, SessionState.ORIENTING, "wake_completed")
        elif work is SessionWorkKind.ORIENT:
            self._require_bound_question(session_id)
            self._transition(claimed, SessionState.PLANNING, "orientation_completed")
        elif work is SessionWorkKind.SELECT_QUESTION:
            self._require_bound_question(session_id)
            self._transition(claimed, SessionState.PLANNING, "question_already_bound")
        elif work is SessionWorkKind.PLAN:
            self._transition(claimed, SessionState.EXPLORING, "minimal_plan_ready")
        elif work is SessionWorkKind.EXPLORE:
            self._explore(session_id)
        elif work is SessionWorkKind.RESUME_TURN:
            assert claimed.directive.turn_id is not None
            self._resume_turn(session_id, claimed.directive.turn_id)
        elif work is SessionWorkKind.RECOVER_ACTION:
            assert claimed.directive.action_id is not None
            self._recover_action(session_id, claimed.directive.action_id)
        elif work is SessionWorkKind.VERIFY:
            self._transition(
                claimed,
                SessionState.CONSOLIDATING,
                "deterministic_evidence_validation_ready",
            )
        elif work is SessionWorkKind.STOP:
            self._stop(claimed)
        elif work is SessionWorkKind.CONSOLIDATE:
            self._consolidate(claimed)
        elif work is SessionWorkKind.FINALIZE_COMMIT:
            self._finalize_commit(claimed)
        elif work is SessionWorkKind.RECONCILE_COMMIT:
            if claimed.directive.state is SessionState.COMMITTING:
                self._transition(
                    claimed,
                    SessionState.RECONCILING_COMMIT,
                    "commit_outcome_requires_reconciliation",
                )
            else:
                self._finalize_commit(claimed)
        elif work is SessionWorkKind.REPORT:
            self._prepare_empty_commit(claimed)
        elif work is SessionWorkKind.ABORT:
            self._abort(claimed)
        else:
            raise InconsistentSessionRuntimeError(f"unsupported session work: {work.value}")
        self._reconcile_commands(session_id)

    def _transition(
        self,
        claimed: ClaimedSession,
        target: SessionState,
        reason: str,
    ) -> None:
        with self._session_factory.begin() as db:
            transition_session_phase(
                db,
                lease=claimed.lease,
                target=target,
                reason=reason,
                occurred_at=self._clock(),
            )

    def _explore(self, session_id: SessionId) -> None:
        try:
            self._turns.run_explorer_turn(
                session_id=session_id,
                turn_id=TurnId.new(),
                context=self._explorer_context(session_id),
                prompts=self._explorer_prompts,
                policy_version=self._tool_broker.policy.version,
            )
        except (GracefulStopRequestedError, SoftBudgetExhaustedError, SessionAbortedError):
            return
        self._acknowledge_messages(session_id)

    def _resume_turn(self, session_id: SessionId, turn_id: TurnId) -> None:
        with self._session_factory() as db:
            turn = db.get(OrchestratorTurnRecord, turn_id.root)
            if turn is None or turn.session_id != session_id.root:
                raise InconsistentSessionRuntimeError("recovery turn is missing")
            phase = ModelPhase(turn.phase)
        try:
            if phase is ModelPhase.EXPLORATION:
                self._turns.resume_explorer_turn(session_id=session_id, turn_id=turn_id)
                self._acknowledge_messages(session_id)
            elif phase is ModelPhase.CONSOLIDATION:
                self._turns.resume_curator_turn(session_id=session_id, turn_id=turn_id)
            else:
                raise InconsistentSessionRuntimeError(
                    f"MVP runner cannot resume a {phase.value} model turn"
                )
        except (GracefulStopRequestedError, SoftBudgetExhaustedError, SessionAbortedError):
            return

    def _recover_action(self, session_id: SessionId, action_id: ActionId) -> None:
        try:
            self._turns.recover_action(session_id=session_id, action_id=action_id)
        except (GracefulStopRequestedError, SoftBudgetExhaustedError, SessionAbortedError):
            return

    def _stop(self, claimed: ClaimedSession) -> None:
        if claimed.directive.state is not SessionState.STOPPING:
            with self._session_factory.begin() as db:
                apply_session_safe_boundary(
                    db,
                    lease=claimed.lease,
                    occurred_at=self._clock(),
                )
            return
        self._transition(claimed, SessionState.CONSOLIDATING, "safe_stop_completed")

    def _consolidate(self, claimed: ClaimedSession) -> None:
        turn = self._consolidation_turn(claimed.session_id)
        if turn is None:
            evidence = self._evidence_views(claimed.session_id)
            if not evidence:
                self._prepare_empty_commit(claimed)
                return
            try:
                self._turns.run_curator_turn(
                    session_id=claimed.session_id,
                    turn_id=TurnId.new(),
                    context=self._curator_context(claimed.session_id, evidence),
                    prompts=self._curator_prompts,
                    policy_version=self._tool_broker.policy.version,
                )
            except SessionAbortedError:
                return
            return
        turn_id = TurnId(root=turn.id)
        if turn.status == "failed":
            raise FailedTurnError(turn.error_code or "Curator turn failed")
        if turn.status == "prepared":
            self._turns.resume_curator_turn(
                session_id=claimed.session_id,
                turn_id=turn_id,
            )
            return
        result = self._turns.resume_curator_turn(
            session_id=claimed.session_id,
            turn_id=turn_id,
        )
        self._prepare_curator_commit(claimed, result)

    def _prepare_curator_commit(
        self,
        claimed: ClaimedSession,
        curator: CuratorTurnResult,
    ) -> None:
        evidence = self._evidence_views(claimed.session_id)
        context = self._curator_context(claimed.session_id, evidence)
        validate_curator_proposal(curator.proposal, context=context)
        rules, revisions, partial = self._commit_inputs(claimed.session_id)
        terminal_state = (
            SessionState.SUCCEEDED
            if not partial
            and curator.proposal.outcome is CuratorOutcome.COMPLETED
            and bool(curator.proposal.claims)
            else SessionState.SUCCEEDED_PARTIAL
        )
        observations = tuple(item.observation for item in evidence)
        batch = build_knowledge_commit_batch(
            curator.proposal,
            observations=observations,
            metadata=tuple(self._metadata(item) for item in observations),
            evidence_budget=EvidenceAdapterBudget(
                remaining_evidence_items=self._limits.max_evidence_links
            ),
            rules=rules,
            validated_knowledge_revision=revisions.knowledge,
            validated_dependency_graph_revision=revisions.dependency_graph,
            terminal_state=terminal_state,
        )
        self._prepare_commit(claimed, batch)

    def _prepare_empty_commit(self, claimed: ClaimedSession) -> None:
        rules, revisions, _ = self._commit_inputs(claimed.session_id)
        batch = KnowledgeCommitBatch.empty(
            rules=rules,
            validated_knowledge_revision=revisions.knowledge,
            validated_dependency_graph_revision=revisions.dependency_graph,
        )
        self._prepare_commit(claimed, batch)

    def _prepare_commit(
        self,
        claimed: ClaimedSession,
        batch: KnowledgeCommitBatch,
    ) -> None:
        with self._session_factory.begin() as db:
            prepare_knowledge_commit(
                db,
                session_id=claimed.session_id,
                attempt_id=CommitAttemptId.new(),
                batch=batch,
                session_lease=claimed.lease,
                occurred_at=self._clock(),
            )

    def _finalize_commit(self, claimed: ClaimedSession) -> None:
        with self._session_factory.begin() as db:
            session = db.get(SessionRecord, claimed.session_id.root)
            if session is None or session.commit_attempt_id is None:
                raise InconsistentSessionRuntimeError("commit work has no bound attempt")
            attempt_id = CommitAttemptId(root=session.commit_attempt_id)
            attempt = db.get(CommitAttemptRecord, attempt_id.root)
            staging = db.scalar(
                select(SessionStagingRecord).where(
                    SessionStagingRecord.attempt_id == attempt_id.root
                )
            )
            if attempt is None:
                raise InconsistentSessionRuntimeError("bound commit attempt is missing")
            if attempt.status == "committed":
                return
            if staging is None:
                raise InconsistentSessionRuntimeError("prepared commit staging is missing")
            batch = KnowledgeCommitBatch.model_validate_json(_canonical_json(staging.payload))
            prepare_knowledge_commit(
                db,
                session_id=claimed.session_id,
                attempt_id=attempt_id,
                batch=batch,
                session_lease=claimed.lease,
                occurred_at=self._clock(),
            )
            finalize_knowledge_commit(
                db,
                session_id=claimed.session_id,
                attempt_id=attempt_id,
                session_lease=claimed.lease,
                occurred_at=self._clock(),
            )

    def _abort(self, claimed: ClaimedSession) -> None:
        with self._session_factory.begin() as db:
            record = db.get(SessionRecord, claimed.session_id.root)
            if record is None:
                raise InconsistentSessionRuntimeError("session disappeared during abort")
            terminal = (
                SessionState.CANCELLED
                if record.abort_requested_at is not None
                else SessionState.FAILED
            )
            terminate_session(
                db,
                lease=claimed.lease,
                terminal_state=terminal,
                reason=record.termination_reason or "session_aborted",
                occurred_at=self._clock(),
            )

    def _explorer_context(self, session_id: SessionId) -> ExplorerContext:
        evidence = self._evidence_views(session_id)
        usage = self._budget_usage(session_id)
        with self._session_factory.begin() as db:
            messages = deliver_messages_for_session(
                db,
                session_id=session_id,
                occurred_at=self._clock(),
            )
        return ExplorerContext(
            question=self._protocol_question(session_id),
            remaining_actions=usage.remaining_tool_actions,
            allowed_tools=self._tool_broker.policy.allowed_tools,
            prior_summary=self._prior_explorer_summary(session_id),
            new_messages=tuple(item.body for item in messages),
            recent_errors=self._recent_errors(session_id),
            observations=tuple(
                ProtocolObservation.from_typed(
                    item.observation,
                    public_summary=item.public_summary,
                )
                for item in evidence[-64:]
            ),
        )

    def _acknowledge_messages(self, session_id: SessionId) -> None:
        with self._session_factory.begin() as db:
            ids = tuple(
                MessageId(root=item)
                for item in db.scalars(
                    select(MessageRecord.id)
                    .where(
                        MessageRecord.delivered_session_id == session_id.root,
                        MessageRecord.state == MessageState.DELIVERED.value,
                    )
                    .order_by(MessageRecord.id)
                )
            )
            acknowledge_session_messages(
                db,
                session_id=session_id,
                message_ids=ids,
                occurred_at=self._clock(),
            )

    def _dispatch_commands(self, session_id: SessionId | None = None) -> None:
        for _ in range(32):
            with self._session_factory.begin() as db:
                dispatched = dispatch_next_operator_command(
                    db,
                    occurred_at=self._clock(),
                    session_id=session_id,
                )
            if dispatched is None:
                return
        raise InconsistentSessionRuntimeError("operator command dispatch batch exceeded 32 items")

    def _reconcile_commands(self, session_id: SessionId) -> None:
        with self._session_factory.begin() as db:
            reconcile_operator_commands_for_session(
                db,
                session_id=session_id,
                occurred_at=self._clock(),
            )

    def _curator_context(
        self,
        session_id: SessionId,
        evidence: tuple[_EvidenceView, ...],
    ) -> CuratorContext:
        rules, _, _ = self._commit_inputs(session_id)
        return CuratorContext(
            question=self._protocol_question(session_id),
            prior_summary=self._prior_explorer_summary(session_id),
            observations=tuple(
                ProtocolObservation.from_typed(
                    item.observation,
                    public_summary=item.public_summary,
                )
                for item in evidence[-128:]
            ),
            allowed_claim_types=tuple(rule.claim_type for rule in rules.rules),
            remaining_claim_budget=self._limits.max_claims,
            remaining_evidence_link_budget=self._limits.max_evidence_links,
            remaining_handoff_budget=0,
        )

    def _commit_inputs(
        self,
        session_id: SessionId,
    ) -> tuple[ClaimTypeRulesSnapshot, RevisionVector, bool]:
        with self._session_factory() as db:
            session = db.get(SessionRecord, session_id.root)
            if session is None:
                raise InconsistentSessionRuntimeError("session does not exist")
            config = db.get(ConfigSnapshotRecord, session.config_snapshot_id)
            if config is None:
                raise InconsistentSessionRuntimeError("session config snapshot is missing")
            raw_rules = config.payload.get("claim_type_rules")
            try:
                rules = ClaimTypeRulesSnapshot.model_validate_json(_canonical_json(raw_rules))
            except (TypeError, ValueError) as exc:
                raise InconsistentSessionRuntimeError(
                    "session config has no valid claim rules"
                ) from exc
            revisions = load_revision_vector(db)
            partial = session.stop_requested_at is not None or session.soft_exhausted_at is not None
        return rules, revisions, partial

    def _budget_usage(self, session_id: SessionId) -> SessionBudgetUsage:
        from apps.orchestrator.runtime import session_budget_usage

        with self._session_factory() as db:
            return session_budget_usage(
                db,
                session_id=session_id,
                occurred_at=self._clock(),
            )

    def _protocol_question(self, session_id: SessionId) -> ProtocolQuestion:
        with self._session_factory() as db:
            session = db.get(SessionRecord, session_id.root)
            if session is None or session.question_id is None:
                raise InconsistentSessionRuntimeError("session has no bound question")
            question = db.get(QuestionRecord, session.question_id)
            if question is None:
                raise InconsistentSessionRuntimeError("bound question is missing")
            return ProtocolQuestion(id=QuestionId(root=question.id), text=question.text)

    def _require_bound_question(self, session_id: SessionId) -> None:
        self._protocol_question(session_id)

    def _consolidation_turn(self, session_id: SessionId) -> OrchestratorTurnRecord | None:
        with self._session_factory() as db:
            turns = tuple(
                db.scalars(
                    select(OrchestratorTurnRecord)
                    .where(
                        OrchestratorTurnRecord.session_id == session_id.root,
                        OrchestratorTurnRecord.phase == ModelPhase.CONSOLIDATION.value,
                    )
                    .order_by(OrchestratorTurnRecord.ordinal)
                )
            )
            if len(turns) > 1:
                raise InconsistentSessionRuntimeError("session has multiple Curator turns")
            if not turns:
                return None
            turn = turns[0]
            db.expunge(turn)
            return turn

    def _evidence_views(self, session_id: SessionId) -> tuple[_EvidenceView, ...]:
        views: list[_EvidenceView] = []
        with self._session_factory() as db:
            actions = tuple(
                db.scalars(
                    select(ActionRecord)
                    .where(
                        ActionRecord.session_id == session_id.root,
                        ActionRecord.state == ActionState.COMPLETED.value,
                    )
                    .order_by(ActionRecord.created_at, ActionRecord.id)
                )
            )
            for action in actions:
                if action.result is None:
                    raise InconsistentSessionRuntimeError(
                        "completed action has no durable observation"
                    )
                try:
                    raw = action.result["observation"]
                    observation = ToolExecutionObservation.model_validate_json(_canonical_json(raw))
                except (KeyError, ValueError) as exc:
                    raise InconsistentSessionRuntimeError(
                        "completed action observation is invalid"
                    ) from exc
                try:
                    typed = workspace_read_as_source_observation(observation)
                except ValueError:
                    continue
                payload = observation.payload
                assert isinstance(payload, WorkspaceReadPayload)
                prefix = f"Exact UTF-8 content from {payload.path}:\n"
                summary = (prefix + payload.content)[:4096].strip()
                if not summary:
                    summary = f"Exact empty-looking UTF-8 content from {payload.path}"
                views.append(_EvidenceView(observation=typed, public_summary=summary))
        return tuple(views)

    @staticmethod
    def _metadata(observation: TypedObservation) -> VerifiedEvidenceMetadata:
        if isinstance(observation, SourceObservation):
            return VerifiedEvidenceMetadata(
                observation_id=observation.id,
                covered_scope=(ScopeDimension.CLAIM,),
                integrity_checked=True,
                independence_group=f"source-{observation.source_content_sha256[:32]}",
            )
        if isinstance(observation, ExperimentObservation):
            return VerifiedEvidenceMetadata(
                observation_id=observation.id,
                covered_scope=(ScopeDimension.CLAIM, ScopeDimension.ENVIRONMENT),
                integrity_checked=True,
                independence_group=f"environment-{observation.environment.sha256[:32]}",
                successful=True,
            )
        assert isinstance(observation, ComputationObservation)
        return VerifiedEvidenceMetadata(
            observation_id=observation.id,
            covered_scope=(
                ScopeDimension.CLAIM,
                ScopeDimension.INPUTS,
                ScopeDimension.ALGORITHM,
                ScopeDimension.ENVIRONMENT,
            ),
            integrity_checked=True,
            independence_group=f"environment-{observation.environment.sha256[:32]}",
            successful=True,
        )

    def _prior_explorer_summary(self, session_id: SessionId) -> str | None:
        with self._session_factory() as db:
            turn = db.scalar(
                select(OrchestratorTurnRecord)
                .where(
                    OrchestratorTurnRecord.session_id == session_id.root,
                    OrchestratorTurnRecord.phase == ModelPhase.EXPLORATION.value,
                    OrchestratorTurnRecord.status == "completed",
                )
                .order_by(OrchestratorTurnRecord.ordinal.desc())
                .limit(1)
            )
            if turn is None or turn.result is None:
                return None
            result = ModelRunResult.model_validate_json(_canonical_json(turn.result))
            return result.decision.public_rationale

    def _recent_errors(self, session_id: SessionId) -> tuple[str, ...]:
        errors: list[str] = []
        with self._session_factory() as db:
            records = tuple(
                db.scalars(
                    select(ActionRecord)
                    .where(
                        ActionRecord.session_id == session_id.root,
                        ActionRecord.state.in_(
                            (
                                ActionState.FAILED.value,
                                ActionState.POLICY_EVALUATED.value,
                            )
                        ),
                    )
                    .order_by(ActionRecord.created_at.desc(), ActionRecord.id.desc())
                    .limit(32)
                )
            )
            for record in records:
                if record.error is not None and isinstance(record.error.get("message"), str):
                    errors.append(record.error["message"][:4096])
                elif record.policy_reason:
                    errors.append(f"{record.tool}: {record.policy_reason}"[:4096])
        return tuple(reversed(errors))

    def _terminal_projection(self, session_id: SessionId) -> SessionRunResult | None:
        with self._session_factory() as db:
            session = db.get(SessionRecord, session_id.root)
            if session is None:
                raise LookupError(f"session does not exist: {session_id}")
            state = SessionState(session.state)
            if not state.is_terminal:
                return None
            checkpoint = db.scalar(
                select(CheckpointRecord).where(CheckpointRecord.session_id == session_id.root)
            )
            model_turns = db.scalar(
                select(func.count(ModelRunRecord.id)).where(
                    ModelRunRecord.session_id == session_id.root
                )
            )
            tool_actions = db.scalar(
                select(func.count(ActionRecord.id)).where(
                    ActionRecord.session_id == session_id.root
                )
            )
            claims = db.scalar(
                select(func.count(ClaimRecord.id)).where(
                    ClaimRecord.created_in_session == session_id.root
                )
            )
            return SessionRunResult(
                session_id=session_id,
                question_id=(
                    QuestionId(root=session.question_id)
                    if session.question_id is not None
                    else None
                ),
                terminal_state=state,
                checkpoint_id=(
                    CheckpointId(root=checkpoint.id) if checkpoint is not None else None
                ),
                model_turns=int(model_turns or 0),
                tool_actions=int(tool_actions or 0),
                claims_committed=int(claims or 0),
                termination_reason=session.termination_reason,
            )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
