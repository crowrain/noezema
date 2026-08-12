"""Durable Tool Broker lifecycle with policy fencing and safe retries."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from packages.domain import (
    ActionId,
    ActionState,
    BoundAction,
    BrokerRunResult,
    CapabilityPolicy,
    EventType,
    IdempotencyClass,
    PolicyDecision,
    PolicyEvaluation,
    ToolExecutionFailure,
    ToolExecutionObservation,
    capability_policy_config_payload,
    capability_policy_sha256,
    sealed_mvp_capability_policy,
)
from packages.persistence import append_session_audit
from packages.persistence.models import (
    ActionRecord,
    ConfigSnapshotRecord,
    ModelRunRecord,
    SessionRecord,
)
from packages.tool_broker.errors import ToolExecutionError
from packages.tool_broker.executor import (
    DispatchingToolExecutor,
    SafeToolExecutor,
    SandboxToolExecutor,
    ToolExecutor,
)
from packages.tool_broker.policy import CapabilityPolicyEngine, EvaluatedAction
from packages.tool_broker.sandbox_runtime import OciSandboxRunner, SandboxRunner

_RETRYABLE_CLASSES = {IdempotencyClass.PURE, IdempotencyClass.IDEMPOTENT}


class ActionBindingConflictError(RuntimeError):
    """A durable action or model run is already bound to different trusted input."""


class PolicySnapshotMismatchError(RuntimeError):
    """An accepted action cannot resume under a different capability snapshot."""


class ToolBroker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        workspace_root: Path,
        policy: CapabilityPolicy | None = None,
        executor: ToolExecutor | None = None,
        sandbox_runner: SandboxRunner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.policy = policy or sealed_mvp_capability_policy()
        self._policy_engine = CapabilityPolicyEngine(
            policy=self.policy,
            workspace_root=workspace_root,
        )
        if executor is not None and sandbox_runner is not None:
            raise ValueError("provide either executor or sandbox_runner, not both")
        safe_executor = SafeToolExecutor(
            session_factory=session_factory,
            workspace_root=workspace_root,
            max_workspace_read_bytes=self.policy.max_workspace_read_bytes,
            max_workspace_list_entries=self.policy.max_workspace_list_entries,
            max_memory_results=self.policy.max_memory_results,
        )
        if executor is not None:
            self._executor = executor
        else:
            runner = sandbox_runner
            if self.policy.sandbox is not None:
                if runner is None:
                    runner = OciSandboxRunner(
                        profile=self.policy.sandbox,
                        workspace_root=workspace_root,
                    )
                elif runner.profile != self.policy.sandbox:
                    raise ValueError("sandbox runner does not match the capability policy")
            elif runner is not None:
                raise ValueError("sandbox runner requires a sandbox capability profile")
            self._executor = DispatchingToolExecutor(
                safe=safe_executor,
                sandbox=SandboxToolExecutor(runner=runner) if runner is not None else None,
            )
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, action: BoundAction) -> BrokerRunResult:
        """Register, authorize and execute one host-bound action with durable transitions."""

        self._register(action)
        evaluated = self._evaluate_if_proposed(action)
        if evaluated.evaluation.decision is not PolicyDecision.ALLOW:
            return self._load_result(action.action_id)
        assert evaluated.arguments is not None

        while True:
            state, attempts = self._state_and_attempts(action)
            if state in {
                ActionState.COMPLETED,
                ActionState.FAILED,
                ActionState.OUTCOME_UNKNOWN,
            }:
                return self._load_result(action.action_id)
            if state is ActionState.STARTED:
                if action.idempotency_class not in _RETRYABLE_CLASSES:
                    failure = ToolExecutionFailure(
                        code="prior_outcome_unknown",
                        message="The previous non-retryable execution did not record an outcome.",
                        retryable=False,
                        outcome_known=False,
                        attempt=max(attempts, 1),
                    )
                    return self._finish_failure(action, failure)
                if attempts >= self.policy.max_attempts:
                    failure = ToolExecutionFailure(
                        code="retry_budget_exhausted",
                        message="The action exhausted its capability-policy retry budget.",
                        retryable=False,
                        outcome_known=True,
                        attempt=max(attempts, 1),
                    )
                    return self._finish_failure(action, failure)

            attempt = self._start_attempt(action)
            try:
                observation = self._executor.execute(
                    action=action,
                    arguments=evaluated.arguments,
                    captured_at=self._clock(),
                    timeout_ms=self.policy.action_timeout_ms,
                )
            except ToolExecutionError as exc:
                failure = ToolExecutionFailure(
                    code=exc.code,
                    message=exc.public_message,
                    retryable=exc.retryable,
                    outcome_known=exc.outcome_known,
                    attempt=attempt,
                )
            except Exception as exc:
                outcome_known = action.idempotency_class in _RETRYABLE_CLASSES
                failure = ToolExecutionFailure(
                    code="tool_internal_error",
                    message=f"The tool adapter failed internally ({type(exc).__name__}).",
                    retryable=False,
                    outcome_known=outcome_known,
                    attempt=attempt,
                )
            else:
                return self._finish_completed(action, observation)

            if (
                failure.retryable
                and action.idempotency_class in _RETRYABLE_CLASSES
                and attempt < self.policy.max_attempts
            ):
                self._record_retry(action, failure)
                continue
            return self._finish_failure(action, failure)

    def _register(self, action: BoundAction) -> None:
        timestamp = self._clock()
        with self._session_factory.begin() as db:
            existing = db.get(ActionRecord, action.action_id.root)
            if existing is not None:
                self._require_same_binding(existing, action)
                return
            model_run = db.get(ModelRunRecord, action.model_run_id.root)
            if model_run is None or model_run.session_id != action.session_id.root:
                raise ActionBindingConflictError(
                    "the action model run does not exist in the bound session"
                )
            conflicting_run = db.scalar(
                select(ActionRecord).where(ActionRecord.model_run_id == action.model_run_id.root)
            )
            if conflicting_run is not None:
                raise ActionBindingConflictError(
                    "the model run is already bound to a different action"
                )
            db.add(
                ActionRecord(
                    id=action.action_id.root,
                    session_id=action.session_id.root,
                    model_run_id=action.model_run_id.root,
                    idempotency_key=action.idempotency_key.root,
                    idempotency_class=action.idempotency_class.value,
                    tool=action.tool.value,
                    arguments_json=action.arguments_json,
                    arguments_sha256=action.arguments_sha256,
                    policy_decision=None,
                    policy_version=None,
                    policy_hash=None,
                    policy_reason=None,
                    state=ActionState.PROPOSED.value,
                    attempt_count=0,
                    created_at=timestamp,
                    started_at=None,
                    completed_at=None,
                    result=None,
                    error=None,
                )
            )
            db.flush()
            append_session_audit(
                db,
                session_id=action.session_id,
                type=EventType.ACTION_PROPOSED,
                occurred_at=timestamp,
                actor="tool_broker",
                public_summary=f"Action proposed: {action.tool.value}",
                topic="audit.action_proposed.v1",
                payload={
                    "action_id": str(action.action_id),
                    "tool": action.tool.value,
                    "arguments_sha256": action.arguments_sha256,
                    "idempotency_class": action.idempotency_class.value,
                },
            )

    def _evaluate_if_proposed(self, action: BoundAction) -> EvaluatedAction:
        if self._session_policy_matches(action):
            evaluated = self._policy_engine.evaluate(action)
        else:
            evaluated = self._policy_engine.deny("session_policy_not_bound")
        timestamp = self._clock()
        with self._session_factory.begin() as db:
            record = self._locked_action(db, action.action_id)
            self._require_same_binding(record, action)
            if record.state == ActionState.PROPOSED.value:
                decision = evaluated.evaluation
                record.policy_decision = decision.decision.value
                record.policy_version = decision.policy_version
                record.policy_hash = decision.policy_sha256
                record.policy_reason = decision.reason
                record.state = ActionState.POLICY_EVALUATED.value
                append_session_audit(
                    db,
                    session_id=action.session_id,
                    type=EventType.POLICY_EVALUATED,
                    occurred_at=timestamp,
                    actor="policy_engine",
                    public_summary=f"Policy decision: {decision.decision.value}",
                    topic="audit.policy_evaluated.v1",
                    payload={
                        "action_id": str(action.action_id),
                        "decision": decision.decision.value,
                        "policy_version": decision.policy_version,
                        "policy_sha256": decision.policy_sha256,
                        "reason": decision.reason,
                    },
                )
                if decision.decision is PolicyDecision.ALLOW:
                    record.state = ActionState.ACCEPTED.value
                    append_session_audit(
                        db,
                        session_id=action.session_id,
                        type=EventType.ACTION_ACCEPTED,
                        occurred_at=timestamp,
                        actor="tool_broker",
                        public_summary=f"Action accepted: {action.tool.value}",
                        topic="audit.action_accepted.v1",
                        payload={"action_id": str(action.action_id)},
                    )
                return evaluated

            persisted = self._policy_evaluation(record)
            if persisted.policy_sha256 != capability_policy_sha256(self.policy):
                raise PolicySnapshotMismatchError(
                    "accepted action is bound to a different capability policy"
                )
            if persisted.decision is PolicyDecision.ALLOW and evaluated.arguments is None:
                raise PolicySnapshotMismatchError(
                    "accepted action no longer validates under its capability policy"
                )
            return EvaluatedAction(
                evaluation=persisted,
                arguments=evaluated.arguments,
            )

    def _session_policy_matches(self, action: BoundAction) -> bool:
        with self._session_factory() as db:
            session_record = db.get(SessionRecord, action.session_id.root)
            if session_record is None:
                return False
            config = db.get(ConfigSnapshotRecord, session_record.config_snapshot_id)
            if config is None:
                return False
            return config.payload.get("policy") == capability_policy_config_payload(self.policy)

    def _start_attempt(self, action: BoundAction) -> int:
        timestamp = self._clock()
        with self._session_factory.begin() as db:
            record = self._locked_action(db, action.action_id)
            if record.state not in {
                ActionState.ACCEPTED.value,
                ActionState.STARTED.value,
            }:
                raise ActionBindingConflictError(
                    f"cannot start action from durable state {record.state}"
                )
            record.attempt_count += 1
            record.state = ActionState.STARTED.value
            if record.started_at is None:
                record.started_at = timestamp
            append_session_audit(
                db,
                session_id=action.session_id,
                type=EventType.ACTION_STARTED,
                occurred_at=timestamp,
                actor="tool_broker",
                public_summary=f"Action attempt started: {action.tool.value}",
                topic="audit.action_started.v1",
                payload={
                    "action_id": str(action.action_id),
                    "attempt": record.attempt_count,
                    "retry": record.attempt_count > 1,
                },
            )
            return record.attempt_count

    def _record_retry(self, action: BoundAction, failure: ToolExecutionFailure) -> None:
        timestamp = self._clock()
        with self._session_factory.begin() as db:
            record = self._locked_action(db, action.action_id)
            if record.state != ActionState.STARTED.value:
                raise ActionBindingConflictError("retry requires a started action")
            append_session_audit(
                db,
                session_id=action.session_id,
                type=EventType.ACTION_FAILED,
                occurred_at=timestamp,
                actor="tool_broker",
                public_summary=f"Action attempt failed; retry scheduled: {action.tool.value}",
                topic="audit.action_attempt_failed.v1",
                payload={
                    "action_id": str(action.action_id),
                    "attempt": failure.attempt,
                    "code": failure.code,
                    "will_retry": True,
                },
            )

    def _finish_completed(
        self,
        action: BoundAction,
        observation: ToolExecutionObservation,
    ) -> BrokerRunResult:
        if observation.provenance.action_id != action.action_id:
            raise ActionBindingConflictError("executor returned an observation for another action")
        timestamp = self._clock()
        with self._session_factory.begin() as db:
            record = self._locked_action(db, action.action_id)
            if record.state == ActionState.COMPLETED.value:
                return self._result_from_record(record)
            if record.state != ActionState.STARTED.value:
                raise ActionBindingConflictError("completion requires a started action")
            record.state = ActionState.COMPLETED.value
            record.completed_at = timestamp
            record.result = {
                "schema": "tool-result/v1",
                "observation": observation.model_dump(mode="json"),
            }
            record.error = None
            append_session_audit(
                db,
                session_id=action.session_id,
                type=EventType.ACTION_COMPLETED,
                occurred_at=timestamp,
                actor="tool_broker",
                public_summary=f"Action completed: {action.tool.value}",
                topic="audit.action_completed.v1",
                payload={
                    "action_id": str(action.action_id),
                    "observation_id": str(observation.id),
                    "payload_sha256": observation.payload_sha256,
                    "attempt": record.attempt_count,
                },
            )
            db.flush()
            return self._result_from_record(record)

    def _finish_failure(
        self,
        action: BoundAction,
        failure: ToolExecutionFailure,
    ) -> BrokerRunResult:
        timestamp = self._clock()
        state = ActionState.FAILED if failure.outcome_known else ActionState.OUTCOME_UNKNOWN
        event_type = (
            EventType.ACTION_FAILED
            if state is ActionState.FAILED
            else EventType.ACTION_OUTCOME_UNKNOWN
        )
        with self._session_factory.begin() as db:
            record = self._locked_action(db, action.action_id)
            if record.state in {
                ActionState.FAILED.value,
                ActionState.OUTCOME_UNKNOWN.value,
            }:
                return self._result_from_record(record)
            if record.state != ActionState.STARTED.value:
                raise ActionBindingConflictError("failure requires a started action")
            record.state = state.value
            record.completed_at = timestamp
            record.result = None
            record.error = failure.model_dump(mode="json")
            append_session_audit(
                db,
                session_id=action.session_id,
                type=event_type,
                occurred_at=timestamp,
                actor="tool_broker",
                public_summary=f"Action ended as {state.value}: {action.tool.value}",
                topic=f"audit.action_{state.value}.v1",
                payload={
                    "action_id": str(action.action_id),
                    "attempt": failure.attempt,
                    "code": failure.code,
                    "outcome_known": failure.outcome_known,
                },
            )
            db.flush()
            return self._result_from_record(record)

    def _state_and_attempts(self, action: BoundAction) -> tuple[ActionState, int]:
        with self._session_factory() as db:
            record = db.get(ActionRecord, action.action_id.root)
            if record is None:
                raise LookupError(f"action does not exist: {action.action_id}")
            return ActionState(record.state), record.attempt_count

    def _load_result(self, action_id: ActionId) -> BrokerRunResult:
        with self._session_factory() as db:
            record = db.get(ActionRecord, action_id.root)
            if record is None:
                raise LookupError(f"action does not exist: {action_id}")
            return self._result_from_record(record)

    @staticmethod
    def _locked_action(db: Session, action_id: ActionId) -> ActionRecord:
        record = db.scalar(
            select(ActionRecord).where(ActionRecord.id == action_id.root).with_for_update()
        )
        if record is None:
            raise LookupError(f"action does not exist: {action_id}")
        return record

    @staticmethod
    def _policy_evaluation(record: ActionRecord) -> PolicyEvaluation:
        if (
            record.policy_decision is None
            or record.policy_version is None
            or record.policy_hash is None
            or record.policy_reason is None
        ):
            raise ActionBindingConflictError("action policy snapshot is incomplete")
        return PolicyEvaluation(
            decision=PolicyDecision(record.policy_decision),
            policy_version=record.policy_version,
            policy_sha256=record.policy_hash,
            reason=record.policy_reason,
        )

    @classmethod
    def _result_from_record(cls, record: ActionRecord) -> BrokerRunResult:
        policy = cls._policy_evaluation(record)
        observation = None
        failure = None
        if record.result is not None:
            observation = ToolExecutionObservation.model_validate_json(
                json.dumps(record.result["observation"])
            )
        if record.error is not None:
            failure = ToolExecutionFailure.model_validate(record.error)
        return BrokerRunResult(
            action_id=ActionId(root=record.id),
            state=ActionState(record.state),
            attempt_count=record.attempt_count,
            policy=policy,
            observation=observation,
            error=failure,
        )

    @staticmethod
    def _require_same_binding(record: ActionRecord, action: BoundAction) -> None:
        actual = (
            record.session_id,
            record.model_run_id,
            record.idempotency_key,
            record.idempotency_class,
            record.tool,
            record.arguments_json,
            record.arguments_sha256,
        )
        expected = (
            action.session_id.root,
            action.model_run_id.root,
            action.idempotency_key.root,
            action.idempotency_class.value,
            action.tool.value,
            action.arguments_json,
            action.arguments_sha256,
        )
        if actual != expected:
            raise ActionBindingConflictError(
                "durable action does not match the supplied host binding"
            )
