"""Curator LLM: generate claims from evidence using LLM."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from packages.domain.llm_schemas import CuratorResponse, ClaimProposal
from packages.domain.models.orm_session import ORMModelRun
from packages.llm_gateway.client import LLMMiddleware, ModelRunRecord
from sqlalchemy.ext.asyncio import AsyncSession


CURATOR_SYSTEM_PROMPT = """You are the Curator of an autonomous research system.

Your job is to analyze exploration evidence and produce structured claims.

Rules:
1. Claims must be factual, testable, and unambiguous
2. Each claim should be a single focused statement
3. Confidence reflects evidence strength: 0.0-1.0
4. If evidence is weak or contradictory, note it in gaps
5. Claim types: empirical_conjecture, contextual_fact, model_hypothesis, operational_observation, meta_claim

Respond in JSON with "claims", "summary", and "gaps" fields.
"""


class CuratorLLM:
    """Generate claims from evidence via LLM."""

    def __init__(self, llm: LLMMiddleware):
        self.llm = llm

    async def generate_claims(
        self,
        db: AsyncSession,
        question: str,
        evidence: list[dict],
        session_id: uuid.UUID,
    ) -> tuple[CuratorResponse, ModelRunRecord]:
        """Call LLM to generate claims from evidence."""
        evidence_text = self._format_evidence(evidence)

        user_prompt = (
            f"Research question: {question}\n\n"
            f"Exploration evidence ({len(evidence)} steps):\n{evidence_text}\n\n"
            "Generate claims based on this evidence."
        )

        run_id = uuid.uuid4()
        response, record = await self.llm.chat(
            system_prompt=CURATOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=CuratorResponse,
            run_id=run_id,
        )

        # Persist model run
        db.add(ORMModelRun(
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=record.latency_ms,
            error=record.error,
        ))
        await db.flush()

        return response, record

    @staticmethod
    def _format_evidence(evidence: list[dict]) -> str:
        """Format evidence list for LLM context."""
        lines = []
        for i, ev in enumerate(evidence, 1):
            tool = ev.get("tool", "unknown")
            result = ev.get("result", {})
            lines.append(f"  [{i}] {tool}: {result}")
        return "\n".join(lines) if lines else "  (no evidence)"

    def fallback_claims(self, evidence: list[dict], question: str) -> CuratorResponse:
        """Fallback if LLM is unavailable — simple heuristic claims."""
        if not evidence:
            return CuratorResponse(
                claims=[],
                summary=f"No evidence gathered for: {question}",
                gaps=["No exploration was performed"],
            )

        claim = ClaimProposal(
            statement=f"Exploration of '{question}' yielded {len(evidence)} evidence items via {', '.join(e.get('tool', 'unknown') for e in evidence[:3])}",
            claim_type="operational_observation",
            confidence=0.5,
            rationale="Heuristic claim from evidence count",
        )
        return CuratorResponse(
            claims=[claim],
            summary=f"Gathered {len(evidence)} evidence items",
            gaps=["LLM curator unavailable — using heuristic fallback"],
        )
