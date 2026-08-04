"""Minimal orchestrator: question → explorer → typed evidence → curator staging → rules assessment → commit."""

import uuid
from datetime import datetime, timedelta

from packages.domain.models.session import Session
from packages.domain.models.enums import SessionState, AuditEventType
from packages.domain.models.decision import ModelResponse
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig
from packages.cognition.question_selector import FIFOQuestionSelector
from packages.tool_broker.sandbox import SandboxExecutor
from packages.memory.rules_engine import RulesEngine


class Orchestrator:
    """Minimal orchestrator for MVP."""

    def __init__(self, llm_config: LLMGatewayConfig):
        self.llm = LLMMiddleware(llm_config)
        self.llm_config = llm_config
        self.selector = FIFOQuestionSelector()
        self.sandbox = SandboxExecutor()

    async def run_session(self, question_id: uuid.UUID | None = None) -> uuid.UUID:
        """Run one cognitive session."""
        session = Session(state=SessionState.WAKING)
        session_id = session.id

        # Select question
        if question_id:
            question = await self._get_question(question_id)
        else:
            question = await self.selector.select_next()

        if not question:
            session.state = SessionState.FAILED
            return session_id

        session.question_id = question["id"]
        session.state = SessionState.ORIENTING

        try:
            # Explorer loop
            evidence = await self._explorer_loop(session, question)

            # Curator: assess with rules engine
            await self._curator_commit(session, evidence)

            session.state = SessionState.SUCCEEDED
        except Exception as e:
            session.state = SessionState.FAILED
            raise

        return session_id

    async def _explorer_loop(
        self, session: Session, question: dict
    ) -> list[dict]:
        """Bounded tool loop: LLM proposes actions, we execute them."""
        max_steps = 10  # MVP limit
        evidence = []

        system_prompt = self._explorer_prompt(question)
        current_context = f"Question: {question['statement']}\nPrevious evidence: {evidence}"

        for step in range(max_steps):
            session.state = SessionState.EXPLORING
            run_id = uuid.uuid4()
            turn_id = uuid.uuid4()

            # Call LLM
            llm_response, record = await self.llm.chat(
                system_prompt=system_prompt,
                user_prompt=current_context,
                response_schema=ModelResponse,
                run_id=run_id,
            )
            # Narrow type
            assert isinstance(llm_response, ModelResponse)

            if llm_response.decision.is_complete:
                break

            # Execute tool
            result = await self._execute_tool(llm_response.decision)

            evidence.append({
                "tool": llm_response.decision.tool,
                "result": result,
                "step": step,
            })

            current_context = f"Question: {question['statement']}\nEvidence so far: {evidence}"

        return evidence

    async def _curator_commit(
        self, session: Session, evidence: list[dict]
    ):
        """Curator proposes claims, rules engine assesses, commit."""
        session.state = SessionState.COMMITTING
        # MVP: simple success — rules engine validates in memory service
        pass

    def _explorer_prompt(self, question: dict) -> str:
        return (
            "You are an autonomous researcher. Investigate:\n\n"
            f"Question: {question['statement']}\n\n"
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

    async def _get_question(self, question_id: uuid.UUID) -> dict | None:
        """MVP: placeholder — real impl queries DB."""
        return None

    async def close(self):
        await self.llm.close()
