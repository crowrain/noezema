"""Orchestrator: question → explorer → typed evidence → curator staging → rules assessment → commit.

DB-aware: persists sessions, actions, evidence, claims to PostgreSQL via SQLAlchemy.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.db_config import Database
from packages.domain.models.orm_session import ORMSession, ORMQuestion
from packages.domain.repositories import (
    SessionRepository,
    QuestionRepository,
    ActionRepository,
    AuditEventRepository,
)
from packages.domain.models.session import Session
from packages.domain.models.enums import SessionState, AuditEventType, ClaimType
from packages.domain.models.decision import ModelResponse
from packages.domain.services.memory_service import MemoryService
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig
from packages.cognition.question_selector import FIFOQuestionSelector
from packages.tool_broker.sandbox import SandboxExecutor
from packages.memory.rules_engine import RulesEngine


class Orchestrator:
    """DB-backed orchestrator for cognitive sessions."""

    def __init__(self, llm_config: LLMGatewayConfig):
        self.llm = LLMMiddleware(llm_config)
        self.llm_config = llm_config
        self.selector = FIFOQuestionSelector()
        self.sandbox = SandboxExecutor()
        self.rules_engine = RulesEngine()

    async def run_session(self, question_id: uuid.UUID | None = None) -> uuid.UUID:
        """Run one cognitive session, persisting to DB."""
        session_factory = Database.get_session_factory()

        async with session_factory() as db:
            # 1. Create session in DB
            session_id = uuid.uuid4()
            orm_session = ORMSession(id=session_id, state=SessionState.WAKING.value)
            await SessionRepository.create(db, orm_session)
            await self._audit(db, session_id, AuditEventType.SESSION_STARTED)

            # 2. Select question
            question = await self._resolve_question(db, question_id)
            if not question:
                await SessionRepository.update_state(db, session_id, SessionState.FAILED.value)
                await self._audit(db, session_id, AuditEventType.SESSION_FAILED, payload={"reason": "no_question"})
                return session_id

            orm_session.question_id = question.id
            orm_session.state = SessionState.ORIENTING.value
            await db.flush()

            try:
                # 3. Explorer loop
                evidence = await self._explorer_loop(db, session_id, question)

                # 4. Curator: create claims, attach evidence, assess
                await self._curator_commit(db, session_id, evidence)

                orm_session.state = SessionState.SUCCEEDED.value
                await db.commit()
                await self._audit(db, session_id, AuditEventType.SESSION_COMMITTED)
            except Exception as e:
                await SessionRepository.update_state(db, session_id, SessionState.FAILED.value)
                await db.commit()
                await self._audit(db, session_id, AuditEventType.SESSION_FAILED, payload={"error": str(e)})
                raise

        return session_id

    async def _explorer_loop(
        self, db: AsyncSession, session_id: uuid.UUID, question: ORMQuestion
    ) -> list[dict]:
        """Bounded tool loop: LLM proposes actions, we execute them, persist to DB."""
        max_steps = 10
        evidence = []

        system_prompt = self._explorer_prompt(question.statement)
        current_context = f"Question: {question.statement}\nPrevious evidence: {evidence}"

        for step in range(max_steps):
            await SessionRepository.update_state(db, session_id, SessionState.EXPLORING.value)
            run_id = uuid.uuid4()
            turn_id = uuid.uuid4()

            # Call LLM
            llm_response, record = await self.llm.chat(
                system_prompt=system_prompt,
                user_prompt=current_context,
                response_schema=ModelResponse,
                run_id=run_id,
            )
            assert isinstance(llm_response, ModelResponse)

            if llm_response.decision.is_complete:
                # Persist model run
                from packages.domain.models.orm_session import ORMModelRun
                db.add(ORMModelRun(
                    model=record.model,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    latency_ms=record.latency_ms,
                ))
                await db.flush()
                break

            # Execute tool
            result = await self._execute_tool(llm_response.decision)

            # Persist action
            next_step = await ActionRepository.next_step_for_session(db, session_id)
            from packages.domain.models.orm_session import ORMAction
            action = ORMAction(
                session_id=session_id,
                tool=llm_response.decision.tool,
                arguments=str(llm_response.decision.arguments or {}),
                result=str(result),
                status="completed",
                step=next_step,
                run_id=run_id,
                turn_id=turn_id,
            )
            await ActionRepository.create(db, action)

            # Persist model run
            from packages.domain.models.orm_session import ORMModelRun
            db.add(ORMModelRun(
                model=record.model,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                latency_ms=record.latency_ms,
            ))

            evidence.append({
                "tool": llm_response.decision.tool,
                "result": result,
                "step": step,
            })

            current_context = f"Question: {question.statement}\nEvidence so far: {evidence}"

            await db.flush()

        return evidence

    async def _curator_commit(
        self, db: AsyncSession, session_id: uuid.UUID, evidence: list[dict]
    ):
        """Create claim from explored evidence, run rules engine assessment."""
        orm_session = await SessionRepository.get_by_id(db, session_id)
        if orm_session is None:
            return

        orm_session.state = SessionState.COMMITTING.value
        await db.flush()

        memory_service = MemoryService(db, self.rules_engine)

        # Build claim statement from evidence summary
        if evidence:
            claim_statement = (
                f"Based on {len(evidence)} exploration steps, "
                f"evidence gathered via: {', '.join(e['tool'] for e in evidence[:5])}"
            )
        else:
            claim_statement = "No evidence gathered — exploratory session completed without findings."

        # Create claim
        claim = await memory_service.create_claim(
            statement=claim_statement,
            claim_type=ClaimType.EMPIRICAL_CONJECTURE.value,
            session_id=session_id,
        )

        # Attach evidence
        for ev in evidence:
            import hashlib
            identity_hash = hashlib.sha256(
                f"{ev['tool']}:{str(ev['result'])}".encode()
            ).hexdigest()
            await memory_service.add_evidence(
                claim_id=claim.id,
                relation="supports",
                evidence_kind=ev.get("tool", "unknown"),
                identity_hash=identity_hash,
                session_id=session_id,
            )

        # Assess
        assessment = await memory_service.assess_claim(claim.id, session_id=session_id)

        await self._audit(
            db, session_id, AuditEventType.CLAIM_ASSESSED,
            payload={
                "claim_id": str(claim.id),
                "grade": assessment.effective_grade,
                "evidence_count": len(evidence),
            },
        )

    def _explorer_prompt(self, question: str) -> str:
        return (
            "You are an autonomous researcher. Investigate:\n\n"
            f"Question: {question}\n\n"
            "Return a structured decision for each step. Use tools to gather evidence.\n"
            "When done, return decision.kind = 'complete' with reason = 'goal_reached'.\n\n"
            "Available tools: workspace.read, workspace.list, workspace.write, "
            "memory.search, shell.execute, python.execute\n\n"
            "Respond in JSON with public_rationale and decision fields."
        )

    async def _execute_tool(self, decision) -> dict:
        """Execute a tool action (MVP: workspace operations + sandbox)."""
        tool = decision.tool
        args = decision.arguments or {}

        if tool == "workspace.read":
            content = self.sandbox.read_file(args.get("path", ""))
            return {"content": content}
        elif tool == "workspace.list":
            entries = self.sandbox.list_dir(args.get("path", "."))
            return {"entries": entries}
        elif tool == "workspace.write":
            written = self.sandbox.write_file(
                args.get("path", ""), args.get("content", "")
            )
            return {"written": written}
        elif tool == "memory.search":
            return {"results": [], "note": "memory empty — first session"}
        elif tool == "shell.execute":
            return {"output": "", "note": "sandbox shell not available yet — dry run"}
        elif tool == "python.execute":
            return {"output": "", "note": "sandbox python not available yet — dry run"}
        else:
            return {"error": f"unknown tool: {tool}"}

    async def _resolve_question(
        self, db: AsyncSession, question_id: uuid.UUID | None
    ) -> ORMQuestion | None:
        """Resolve a question from DB or queue."""
        if question_id:
            return await db.get(ORMQuestion, question_id)

        # Select from unresolved queue
        unresolved = await QuestionRepository.list_unresolved(db, limit=1)
        return unresolved[0] if unresolved else None

    async def _audit(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        event_type: AuditEventType,
        payload: dict | None = None,
    ):
        from packages.domain.models.orm_session import ORMAuditEvent
        event = ORMAuditEvent(
            event_type=event_type.value,
            session_id=session_id,
            payload=str(payload) if payload else None,
        )
        await AuditEventRepository.create(db, event)

    async def close(self):
        await self.llm.close()
        await Database.close()
