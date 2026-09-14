"""Cognitive session runner (T1.14-T1.16, T1.18).

Flow: waking → orienting → selecting_question → planning → exploring
(LLM decision loop with typed tool execution) → verifying → consolidating
(curator staging proposal, host-validated) → reporting → committing
(M1 "simple commit": terminal state + audit + outbox in one transaction;
fenced commit with staging lands in M2).

M2 state (after this PR):
  - every tool decision is authorized by the Policy Engine against the
    capability profile from the config snapshot; the decision is recorded
    as PolicyEvaluated (allow / deny / require_operator);
  - a denied or require_operator action is never executed; the model gets
    the denial reason back as an observation (interactive operator approval
    lands with the full web in M7);
  - only profile-allowed tools appear in the model's context (T2.6);
  - still deferred: lease/heartbeat/reconciliation (PR #15), durable
    knowledge tables (M3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.orchestrator.evidence import observation_to_evidence
from apps.orchestrator.executor import arguments_hash
from apps.orchestrator.state_machine import transition
from packages.broker import ToolExecutor, check_idempotency
from packages.cognition.question_selector import FIFOQuestionSelector
from packages.domain.canonical import canonical_json_bytes
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import (
    ActionState,
    AuditEventType,
    CompleteReason,
    DecisionKind,
    IdempotencyClass,
    MessageState,
    QuestionOrigin,
    QuestionState,
    SessionState,
)
from packages.domain.models.questions import ORMQuestion
from packages.domain.models.sessions import ORMAction, ORMModelRun, ORMSession
from packages.domain.repositories.inbox import MessageRepository
from packages.domain.repositories.questions import QuestionRepository
from packages.domain.repositories.sessions import ActionRepository, ModelRunRepository, SessionRepository
from packages.domain.schemas.decision import ModelResponse
from packages.domain.schemas.evidence import EvidenceRecord
from packages.domain.schemas.staging import CuratorProposal
from packages.domain.services.audit import AuditService
from packages.domain.services.config import ConfigService
from packages.llm_gateway.client import LLMError, LLMMiddleware
from packages.llm_gateway.config import ModelProfile
from packages.llm_gateway.fingerprint import build_model_fingerprint
from packages.llm_gateway.roles import Role, load_prompt, tool_schema_hash
from packages.policy.engine import PolicyEngine
from packages.policy.profiles import CapabilityProfile, ProfileError, effective_profile
from packages.policy.tools import get_tool

PLAN_TEMPLATE = "Исследовать вопрос, собрать evidence инструментами, предложить claims."


@dataclass(slots=True)
class SessionContext:
    question_text: str
    plan: str
    observations: list[str] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    complete_reason: str | None = None


@dataclass(frozen=True)
class SessionOutcome:
    session_id: uuid.UUID
    final_state: SessionState
    question_id: uuid.UUID | None
    steps: int
    evidence_count: int
    claims_proposed: int
    questions_created: int
    termination_reason: str | None


class Orchestrator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: LLMMiddleware,
        profile: ModelProfile,
        executor: ToolExecutor,
        selector: FIFOQuestionSelector | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.profile = profile
        self.executor = executor
        self.selector = selector if selector is not None else FIFOQuestionSelector()
        self.prompts = {Role.EXPLORER: load_prompt(Role.EXPLORER), Role.CURATOR: load_prompt(Role.CURATOR)}

    async def run_session(self, question_id: uuid.UUID | None = None) -> SessionOutcome:
        async with self.session_factory() as db, transaction(db):
            return await self._run(db, question_id)

    # ── main flow ─────────────────────────────────────────────────────────

    async def _run(self, db: AsyncSession, question_id: uuid.UUID | None) -> SessionOutcome:
        audit = AuditService(db)

        # waking: single-session enforcement (M1)
        active = await SessionRepository.list_nonterminal(db)
        if active:
            raise RuntimeError(
                f"session {active[0].id} is still nonterminal; single session at a time (M1)"
            )

        snapshot = await ConfigService.get_effective(db)
        limits = snapshot.session_limits

        # capability profile from the config snapshot (fail-closed):
        # the snapshot may only narrow the YAML ceiling
        try:
            cap_profile = effective_profile(snapshot.policy)
        except ProfileError as exc:
            raise RuntimeError(f"capability profile unavailable: {exc}") from exc
        policy_engine = PolicyEngine(cap_profile)

        session = ORMSession(state=SessionState.CREATED.value, config_snapshot_id=snapshot.id)
        await SessionRepository.create(db, session)
        await audit.record(
            AuditEventType.SESSION_STARTED, session_id=session.id, public_summary="session started"
        )
        for state in (SessionState.WAKING, SessionState.ORIENTING):
            await self._transition(db, audit, session, state)

        ctx = SessionContext(question_text="", plan=PLAN_TEMPLATE)

        # orienting: deliver pending messages
        for message in await MessageRepository.list_pending_delivery(db, limit=5):
            await MessageRepository.set_state(db, message.id, MessageState.DELIVERED)
            ctx.messages.append(message.body)
            await audit.record(
                AuditEventType.MESSAGE_DELIVERED,
                session_id=session.id,
                payload={"message_id": str(message.id), "body": message.body[:500]},
            )

        # selecting_question
        await self._transition(db, audit, session, SessionState.SELECTING_QUESTION)
        question = await self._select_question(db, question_id)
        if question is None:
            return await self._finish(
                db, audit, session, SessionState.FAILED, None, 0, 0, 0, termination_reason="no_question"
            )
        session.question_id = question.id
        question.state = QuestionState.RESEARCHING.value
        await audit.record(
            AuditEventType.QUESTION_SELECTED,
            session_id=session.id,
            payload={"question_id": str(question.id)},
            public_summary=f"question selected: {question.text[:120]}",
        )
        ctx.question_text = question.text

        # planning (MVP: fixed template)
        await self._transition(db, audit, session, SessionState.PLANNING)

        # exploring
        await self._transition(db, audit, session, SessionState.EXPLORING)
        max_steps = int(limits.get("max_explorer_steps", 10))
        steps, stopped, aborted = await self._explorer_loop(
            db, audit, session, ctx, max_steps, cap_profile, policy_engine
        )
        if aborted:
            # ABORTING was set in the loop; finish as cancelled in the SAME
            # transaction so the audit trail of the aborted session survives.
            return await self._finish(
                db,
                audit,
                session,
                SessionState.CANCELLED,
                question.id,
                steps,
                len(ctx.evidence),
                0,
                termination_reason="operator_abort",
            )
        if stopped:
            await self._transition(db, audit, session, SessionState.STOPPING)

        # verifying (MVP: no-op; verifier profile lands in M5)
        await self._transition(db, audit, session, SessionState.VERIFYING)

        # consolidating: curator
        await self._transition(db, audit, session, SessionState.CONSOLIDATING)
        claims, _questions_created = await self._curator(db, audit, session, ctx, snapshot)

        # reporting
        await self._transition(db, audit, session, SessionState.REPORTING)
        await audit.record(
            AuditEventType.SESSION_STATE_CHANGED,
            session_id=session.id,
            public_summary=f"report: {ctx.complete_reason or 'budget_exhausted'} after {steps} steps",
            payload={
                "steps": steps,
                "evidence": [e.model_dump() for e in ctx.evidence[:20]],
                "observations": ctx.observations[:20],
            },
        )

        # committing (M1 simple commit)
        await self._transition(db, audit, session, SessionState.COMMITTING)
        if ctx.complete_reason == CompleteReason.GOAL_REACHED.value:
            final = SessionState.SUCCEEDED
        else:
            final = SessionState.SUCCEEDED_PARTIAL
        question.state = (
            QuestionState.VERIFIED.value
            if final is SessionState.SUCCEEDED
            else QuestionState.PARTIALLY_ANSWERED.value
        )
        return await self._finish(
            db,
            audit,
            session,
            final,
            question.id,
            steps,
            len(ctx.evidence),
            claims,
            termination_reason=ctx.complete_reason,
        )

    # ── explorer loop ─────────────────────────────────────────────────────

    async def _explorer_loop(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        ctx: SessionContext,
        max_steps: int,
        cap_profile: CapabilityProfile,
        policy_engine: PolicyEngine,
    ) -> tuple[int, bool, bool]:
        """Returns (steps_done, stop_requested, abort_requested)."""
        explorer = self.prompts[Role.EXPLORER]
        # only profile-allowed tools are in the model's schema (T2.6)
        allowed_tools = sorted(cap_profile.tools)
        steps = 0
        stop_requested = False
        abort_requested = False
        for step in range(1, max_steps + 1):
            steps = step

            # operator abort check between steps
            await db.refresh(session)
            if session.abort_requested_at is not None:
                await self._transition(db, audit, session, SessionState.ABORTING)
                abort_requested = True
                break
            if session.stop_requested_at is not None:
                stop_requested = True
                ctx.complete_reason = CompleteReason.OPERATOR_STOP.value
                break

            user_ctx = self._explorer_context(ctx, allowed_tools)
            fingerprint = build_model_fingerprint(
                self.profile,
                prompt_version=explorer.version,
                tool_schema_hash=tool_schema_hash(allowed_tools),
                policy_version=cap_profile.policy_version,
            )
            try:
                response, record = await self.gateway.chat(
                    system=explorer.text, user=user_ctx, response_schema=ModelResponse, fingerprint=fingerprint
                )
            except LLMError as exc:
                await audit.record(
                    AuditEventType.SESSION_FAILED,
                    session_id=session.id,
                    payload={"error": str(exc)[:500], "phase": "exploring"},
                    public_summary="LLM unavailable; host failure report",
                )
                raise

            turn_id = uuid.uuid4()  # host-generated causal id (§20.10)
            run = ORMModelRun(
                session_id=session.id,
                turn_id=turn_id,
                phase=session.state,
                model_fingerprint=fingerprint,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                latency_ms=record.latency_ms,
                finish_reason=record.finish_reason,
                output_schema_valid=record.output_schema_valid,
            )
            await ModelRunRepository.create(db, run)

            decision = response.decision
            if decision.kind is DecisionKind.COMPLETE:
                ctx.complete_reason = decision.reason
                await audit.record(
                    AuditEventType.SESSION_STATE_CHANGED,
                    session_id=session.id,
                    payload={
                        "complete_reason": decision.reason,
                        "rationale": response.public_rationale[:500],
                    },
                    public_summary=f"explorer complete: {decision.reason}",
                )
                break

            # tool decision — authorized by the Policy Engine (§5.6)
            tool_name = decision.tool
            if tool_name is None:  # schema validation makes this unreachable
                raise LLMError("tool decision without tool name")
            args: JsonDict = decision.arguments
            args_hash = arguments_hash(args)
            spec = get_tool(tool_name)
            action_key = f"{turn_id}:{tool_name}:{args_hash[:32]}"
            evaluation = policy_engine.evaluate(tool_name, args, external_texts=ctx.messages)
            await audit.record(
                AuditEventType.POLICY_EVALUATED,
                session_id=session.id,
                actor="policy-engine",
                payload={
                    "tool": tool_name,
                    "decision": evaluation.decision.value,
                    "reasons": list(evaluation.reasons),
                    "arguments_hash": evaluation.arguments_hash,
                    "profile_version": evaluation.profile_version,
                    "similarity_signal": evaluation.similarity_signal,
                },
                public_summary=f"policy {evaluation.decision.value}: {tool_name}",
            )

            # T2.8: the host key is bound to tool + arguments hash forever
            existing = await ActionRepository.get_by_idempotency_key(db, session.id, action_key)
            verdict = check_idempotency(
                {"arguments_hash": existing.arguments_hash, "state": existing.state}
                if existing is not None
                else None,
                args_hash,
            )
            if verdict.status == "conflict" and existing is not None:
                # SECURITY INCIDENT: the key was reused with other arguments
                await audit.record(
                    AuditEventType.ALERT_RAISED,
                    session_id=session.id,
                    actor="tool-broker",
                    payload={
                        "kind": "idempotency_key_conflict",
                        "action_id": str(existing.id),
                        "tool": tool_name,
                        "key": action_key,
                        "stored_hash": existing.arguments_hash,
                        "presented_hash": args_hash,
                    },
                    public_summary="SECURITY: idempotency key reused with different arguments",
                )
                ctx.observations.append(
                    f"[{step}] {tool_name}: инцидент — повтор idempotency key с другими аргументами (отклонено)"
                )
                continue
            if verdict.status == "replay" and existing is not None:
                # same key + same hash: no blind re-execution
                ctx.observations.append(f"[{step}] {tool_name}: повтор (replay), действие уже зафиксировано")
                continue

            action = ORMAction(
                session_id=session.id,
                model_run_id=run.id,
                idempotency_key=action_key,
                idempotency_class=(
                    spec.idempotency_class.value if spec is not None else IdempotencyClass.NON_IDEMPOTENT.value
                ),
                tool=tool_name,
                arguments_hash=args_hash,
                policy_decision=evaluation.decision.value,
                state=ActionState.POLICY_EVALUATED.value,
            )
            await ActionRepository.create(db, action)

            if not evaluation.allowed:
                # deny / require_operator: the action is NEVER executed;
                # the model gets the reason back as an observation.
                action.state = ActionState.FAILED.value
                action.error_code = f"policy:{evaluation.decision.value}"
                await audit.record(
                    AuditEventType.ACTION_FAILED,
                    session_id=session.id,
                    payload={
                        "action_id": str(action.id),
                        "denied": True,
                        "reasons": list(evaluation.reasons),
                    },
                    public_summary=f"action denied by policy: {tool_name}",
                )
                ctx.observations.append(
                    f"[{step}] {tool_name} ОТКЛОНЕНО политикой: {'; '.join(evaluation.reasons)[:300]}"
                )
                continue

            # allow: proposed → accepted → started
            now = datetime.now(UTC)
            action.state = ActionState.ACCEPTED.value
            action.state = ActionState.STARTED.value
            action.started_at = now
            await audit.record(
                AuditEventType.ACTION_STARTED,
                session_id=session.id,
                payload={
                    "action_id": str(action.id),
                    "tool": tool_name,
                    "arguments": _cap_args(args),
                },
                public_summary=f"action: {tool_name}",
            )

            obs = await self.executor.execute(tool_name, args, db=db)
            action.state = ActionState.COMPLETED.value if obs.ok else ActionState.FAILED.value
            action.error_code = obs.error
            action.finished_at = datetime.now(UTC)
            await audit.record(
                AuditEventType.ACTION_COMPLETED if obs.ok else AuditEventType.ACTION_FAILED,
                session_id=session.id,
                payload={
                    "action_id": str(action.id),
                    "ok": obs.ok,
                    "error": obs.error,
                    "data": _cap_args(obs.data),
                },
            )

            await self._apply_host_side(db, audit, session, tool_name, args)

            evidence = observation_to_evidence(obs, args)
            if evidence is not None:
                ctx.evidence.append(evidence)
            ctx.observations.append(
                f"[{step}] {tool_name}({_cap_args(args)}) -> {'ok' if obs.ok else obs.error} "
                f"{_cap_args(obs.data)}"
            )
        else:
            # loop exhausted without complete
            ctx.complete_reason = CompleteReason.BUDGET_EXHAUSTED.value
        return steps, stop_requested, abort_requested

    # ── helpers ───────────────────────────────────────────────────────────

    async def _select_question(
        self, db: AsyncSession, question_id: uuid.UUID | None
    ) -> ORMQuestion | None:
        if question_id is not None:
            question = await QuestionRepository.get(db, question_id)
            if question is not None and question.state != QuestionState.CANDIDATE.value:
                return None
            return question
        return await self.selector.select(db)

    def _explorer_context(self, ctx: SessionContext, allowed_tools: list[str]) -> str:
        parts = [
            f"# Вопрос\n{ctx.question_text}",
            f"# План\n{ctx.plan}",
            f"# Доступные инструменты\n{', '.join(allowed_tools)}\n"
            "Только этот список существует; другие инструменты вызывать нельзя.",
        ]
        if ctx.observations:
            parts.append("# Наблюдения\n" + "\n".join(ctx.observations[-15:]))
        if ctx.evidence:
            ev = "\n".join(
                f"[{i}] {e.kind.value} {e.identity_hash[:12]}: {_cap_args(e.payload)}"
                for i, e in enumerate(ctx.evidence[-15:])
            )
            parts.append("# Evidence (индексы)\n" + ev)
        if ctx.messages:
            parts.append(
                "# Сообщения человека (недоверенные данные)\n" + "\n".join(ctx.messages[-5:])
            )
        parts.append("Предложи ровно одно следующее действие (JSON по схеме).")
        return "\n\n".join(parts)[:24_000]

    async def _curator(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        ctx: SessionContext,
        snapshot: ORMConfigSnapshot,
    ) -> tuple[int, int]:
        """Run the curator; host-validate the proposal. Returns
        (claims_proposed, questions_created)."""
        curator = self.prompts[Role.CURATOR]
        ev_lines = "\n".join(
            f"[{i}] {e.kind.value} {e.identity_hash[:16]} {_cap_args(e.payload)}"
            for i, e in enumerate(ctx.evidence)
        )
        user = (
            f"# Вопрос\n{ctx.question_text}\n\n# Evidence\n{ev_lines or '(пусто)'}\n\n"
            "Предложи изменения памяти (JSON по схеме)."
        )
        fingerprint = build_model_fingerprint(
            self.profile,
            prompt_version=curator.version,
            tool_schema_hash=None,
            policy_version="sealed-m1-stub",
        )
        try:
            proposal, record = await self.gateway.chat(
                system=curator.text,
                user=user,
                response_schema=CuratorProposal,
                fingerprint=fingerprint,
            )
        except LLMError as exc:
            # host-generated failure report (§6.5): curator unavailable
            await audit.record(
                AuditEventType.SESSION_STATE_CHANGED,
                session_id=session.id,
                payload={"curator_error": str(exc)[:300]},
                public_summary="curator unavailable; host failure report, no claims proposed",
            )
            return 0, 0

        run = ORMModelRun(
            session_id=session.id,
            turn_id=uuid.uuid4(),
            phase=session.state,
            model_fingerprint=fingerprint,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=record.latency_ms,
            finish_reason=record.finish_reason,
            output_schema_valid=record.output_schema_valid,
        )
        await ModelRunRepository.create(db, run)

        max_questions = int(snapshot.session_limits.get("max_new_questions_per_session", 4))
        problems = proposal.validate_against(len(ctx.evidence), questions_max=max_questions)
        if problems:
            await audit.record(
                AuditEventType.SESSION_STATE_CHANGED,
                session_id=session.id,
                payload={"curator_rejected": problems[:10]},
                public_summary="curator proposal rejected by host validation",
            )
            return 0, 0

        questions_created = 0
        for q in proposal.new_questions:
            await QuestionRepository.create(
                db,
                ORMQuestion(
                    text=q.text,
                    origin=q.origin.value,
                    origin_config_snapshot_id=snapshot.id,
                    parent_id=session.question_id,
                ),
            )
            questions_created += 1

        await audit.record(
            AuditEventType.CLAIM_CREATED,
            session_id=session.id,
            payload={
                "claims": [c.model_dump(mode="json") for c in proposal.claims],
                "evidence_links": [link.model_dump() for link in proposal.evidence_links],
                "new_questions": [q.model_dump(mode="json") for q in proposal.new_questions],
                "summary": proposal.summary,
            },
            public_summary=f"curator: {len(proposal.claims)} claims, {questions_created} questions",
        )
        return len(proposal.claims), questions_created

    async def _apply_host_side(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        tool: str,
        args: JsonDict,
    ) -> None:
        """Host-side effects of host-deferred tools (T1.18)."""
        if tool == "question.create":
            text = str(args.get("text", "")).strip()
            if text:
                origin = str(args.get("origin", QuestionOrigin.MODEL_PROPOSAL.value))
                try:
                    origin_enum = QuestionOrigin(origin)
                except ValueError:
                    origin_enum = QuestionOrigin.MODEL_PROPOSAL
                question = ORMQuestion(
                    text=text[:2000],
                    origin=origin_enum.value,
                    origin_config_snapshot_id=session.config_snapshot_id,
                    parent_id=session.question_id,
                )
                await QuestionRepository.create(db, question)
                await audit.record(
                    AuditEventType.SESSION_STATE_CHANGED,
                    session_id=session.id,
                    payload={"question_text": text[:300]},
                    public_summary="model proposed a new question",
                )
        elif tool == "message.reply":
            message_id = args.get("message_id")
            body = str(args.get("body", ""))[:2000]
            if message_id:
                try:
                    message = await MessageRepository.get(db, uuid.UUID(str(message_id)))
                except ValueError:
                    message = None
                if message is not None:
                    await MessageRepository.set_state(db, message.id, MessageState.ANSWERED)
                    await audit.record(
                        AuditEventType.MESSAGE_ANSWERED,
                        session_id=session.id,
                        payload={"message_id": str(message.id), "reply": body[:300]},
                    )

    async def _transition(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        dst: SessionState,
    ) -> None:
        src = SessionState(session.state)
        transition(src, dst)
        session.state = dst.value
        await db.flush()
        await audit.record(
            AuditEventType.SESSION_STATE_CHANGED,
            session_id=session.id,
            payload={"from": src.value, "to": dst.value},
        )

    async def _finish(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        final: SessionState,
        question_id: uuid.UUID | None,
        steps: int,
        evidence_count: int,
        claims: int,
        *,
        termination_reason: str | None,
    ) -> SessionOutcome:
        transition(SessionState(session.state), final)
        session.state = final.value
        session.termination_reason = termination_reason
        await db.flush()
        if final is SessionState.SUCCEEDED:
            event = AuditEventType.SESSION_COMMITTED
        elif final is SessionState.FAILED:
            event = AuditEventType.SESSION_FAILED
        elif final is SessionState.CANCELLED:
            event = AuditEventType.SESSION_CANCELLED
        else:
            event = AuditEventType.SESSION_STATE_CHANGED
        await audit.record(
            event,
            session_id=session.id,
            payload={"steps": steps, "evidence": evidence_count, "claims": claims},
            public_summary=f"session {final.value} ({termination_reason or 'ok'})",
        )
        return SessionOutcome(
            session_id=session.id,
            final_state=final,
            question_id=question_id,
            steps=steps,
            evidence_count=evidence_count,
            claims_proposed=claims,
            questions_created=0,
            termination_reason=termination_reason,
        )


def _cap_args(value: object) -> str:
    """Compact canonical representation for audit payloads (≤1000 chars)."""
    if isinstance(value, str):
        value = {"value": value}
    text = canonical_json_bytes(value).decode("utf-8", "replace")
    return text[:1000]
