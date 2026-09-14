"""Context Builder — the bounded context pack (§5.4, §5.4.1, T3.8).

Instead of sending the whole history, the builder assembles a bounded
pack from fixed sections, each with an ABSOLUTE token limit from the
config snapshot:

- the HARD protocol sections (protocol, tool schemas, rules) are reserved
  FIRST and never truncated below their reservation;
- the other sections are ranked by usefulness and truncated to fit;
- pending/invalid claims live in their own small limit and carry the
  §5.4.2 label on the same line.

The ``ContextPacked`` audit event stores the actual token counts, the
included chunk IDs, the exclusion reasons and the tokenizer fingerprint,
including a separate list of the non-current claims the model saw.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from packages.cognition.retrieval import RetrievedClaim, retrieve
from packages.cognition.tokenizer import TOKENIZER_FINGERPRINT, TokenBudgets, estimate_tokens
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import AuditEventType
from packages.domain.services.audit import AuditService

#: sections reserved FIRST (hard protocol) — never truncated below limit
HARD_SECTIONS = ("protocol",)

#: the separate small limit for non-current claims (§5.4.2)
PENDING_SECTION = "pending_claims"


@dataclass(frozen=True)
class ContextSection:
    name: str
    content: str
    tokens: int
    budget: int
    truncated: bool = False
    chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextPack:
    sections: dict[str, ContextSection]
    total_tokens: int
    input_budget: int
    tokenizer_fingerprint: str
    exclusions: list[JsonDict] = field(default_factory=list)
    pending_claim_ids: list[uuid.UUID] = field(default_factory=list)

    def section(self, name: str) -> ContextSection | None:
        return self.sections.get(name)

    def render(self, names: list[str] | None = None) -> str:
        names = names or list(self.sections)
        parts = []
        for name in names:
            sec = self.sections.get(name)
            if sec is not None and sec.content:
                parts.append(f"# {name}\n{sec.content}")
        return "\n\n".join(parts)


def _fit_to_budget(lines: list[tuple[str, str]], budget: int) -> tuple[str, list[str], bool]:
    """Greedily fit (chunk_id, line) pairs into ``budget`` tokens.
    A line that does not fit is excluded (never partially sent)."""
    chosen: list[str] = []
    used = 0
    excluded: list[str] = []
    truncated = False
    for chunk_id, line in lines:
        cost = estimate_tokens(line)
        if used + cost > budget:
            excluded.append(chunk_id)
            truncated = True
            continue
        chosen.append(line)
        used += cost
    return "\n".join(chosen), excluded, truncated


class ContextBuilder:
    def __init__(self, snapshot: ORMConfigSnapshot) -> None:
        self.snapshot = snapshot
        model = dict(snapshot.model)
        token_budgets = dict(snapshot.token_budgets)
        # the §5.4.2 pending/invalid limit is SEPARATE from the §5.4.1
        # section sum: it cannot displace current knowledge, so it is kept
        # outside ``section_limits`` (and out of the sum validation)
        self.pending_budget = int(token_budgets.pop(PENDING_SECTION, 1024))
        self.budgets = TokenBudgets.from_snapshot(model, token_budgets)

    def _limit(self, name: str, default: int) -> int:
        return int(self.budgets.section_limits.get(name, default))

    async def build(
        self,
        db: AsyncSession,
        audit: AuditService,
        session_id: uuid.UUID,
        *,
        protocol: str,
        identity: str,
        question_text: str,
        plan: str,
        last_session: str,
        messages: list[str],
        recent_errors: list[str],
    ) -> ContextPack:
        """Assemble the pack for one session (caller's transaction)."""
        problems = self.budgets.validate()
        if problems:
            # fail-closed: the configured budgets are inconsistent
            raise ValueError(f"token budgets invalid: {problems}")

        exclusions: list[JsonDict] = []

        # 1. hard protocol section is reserved FIRST (never truncated)
        protocol_budget = self._limit("protocol", 4096)
        proto_tokens = estimate_tokens(protocol)
        proto_truncated = proto_tokens > protocol_budget
        protocol_sec = ContextSection(
            "protocol",
            protocol[: protocol_budget * 4],
            min(proto_tokens, protocol_budget),
            protocol_budget,
            truncated=proto_truncated,
        )
        if proto_truncated:
            exclusions.append({"section": "protocol", "reason": "exceeded_hard_reservation"})

        sections: dict[str, ContextSection] = {"protocol": protocol_sec}

        # 2. simple fixed sections, truncated to their budget
        sections["identity"] = self._fit_section("identity", identity, self._limit("identity", 2048), exclusions)
        question_plan = f"# Вопрос\n{question_text}\n\n# План\n{plan}"
        sections["question_plan"] = self._fit_section(
            "question_plan", question_plan, self._limit("question_plan", 3072), exclusions
        )
        sections["last_session"] = self._fit_section(
            "last_session", last_session, self._limit("last_session", 2048), exclusions
        )
        sections["messages"] = self._fit_section(
            "messages", "\n".join(messages), self._limit("messages", 2048), exclusions
        )
        sections["recent_errors"] = self._fit_section(
            "recent_errors", "\n".join(recent_errors), self._limit("recent_errors", 2048), exclusions
        )

        # 3. retrieval: current claims spend claims_evidence; the
        # contradictions section is filled from disputed claims
        result = await retrieve(db, question_text, snapshot_id=self.snapshot.id)
        claims_budget = self._limit("claims_evidence", 8192)
        claim_lines = [
            (f"claim:{c.claim_id}", c.line) for c in result.current
        ]
        claims_content, claim_excluded, claims_truncated = _fit_to_budget(claim_lines, claims_budget)
        for chunk_id in claim_excluded:
            exclusions.append({"section": "claims_evidence", "chunk": chunk_id, "reason": "budget"})
        sections["claims_evidence"] = ContextSection(
            "claims_evidence",
            claims_content,
            estimate_tokens(claims_content),
            claims_budget,
            truncated=claims_truncated,
            chunk_ids=tuple(
                f"claim:{c.claim_id}" for c in result.current if c.line in claims_content.splitlines()
            ),
        )

        disputed = [
            c
            for c in result.current
            if c.epistemic_status is not None and c.epistemic_status.value == "disputed"
        ]
        contra_lines = [(f"claim:{c.claim_id}", c.line) for c in disputed]
        contra_content, contra_excluded, contra_truncated = _fit_to_budget(
            contra_lines, self._limit("contradictions", 3072)
        )
        for chunk_id in contra_excluded:
            exclusions.append({"section": "contradictions", "chunk": chunk_id, "reason": "budget"})
        sections["contradictions"] = ContextSection(
            "contradictions",
            contra_content,
            estimate_tokens(contra_content),
            self._limit("contradictions", 3072),
            truncated=contra_truncated,
        )

        # 4. §5.4.2: pending/invalid in their OWN small limit, labeled on
        # the same line; the label counts toward the budget
        pending_budget = self.pending_budget
        pending_lines = [(f"claim:{c.claim_id}", c.line) for c in result.pending_invalid]
        pending_content, pending_excluded, pending_truncated = _fit_to_budget(pending_lines, pending_budget)
        for chunk_id in pending_excluded:
            # excluded ENTIRELY, never unlabeled (§5.4.2)
            exclusions.append(
                {"section": PENDING_SECTION, "chunk": chunk_id, "reason": "no_budget_for_labeled_line"}
            )
        sections[PENDING_SECTION] = ContextSection(
            PENDING_SECTION,
            pending_content,
            estimate_tokens(pending_content),
            pending_budget,
            truncated=pending_truncated,
            chunk_ids=tuple(
                f"claim:{c.claim_id}"
                for c in result.pending_invalid
                if c.line in pending_content.splitlines()
            ),
        )

        total = sum(s.tokens for s in sections.values())
        pack = ContextPack(
            sections=sections,
            total_tokens=total,
            input_budget=self.budgets.input_budget,
            tokenizer_fingerprint=TOKENIZER_FINGERPRINT,
            exclusions=exclusions,
            pending_claim_ids=[c.claim_id for c in result.pending_invalid],
        )
        await self._audit(audit, session_id, pack)
        return pack

    def _fit_section(
        self, name: str, content: str, budget: int, exclusions: list[JsonDict]
    ) -> ContextSection:
        tokens = estimate_tokens(content)
        truncated = tokens > budget
        if truncated:
            # truncate whole lines to fit the budget
            lines = content.splitlines()
            kept: list[str] = []
            used = 0
            for line in lines:
                cost = estimate_tokens(line)
                if used + cost > budget:
                    break
                kept.append(line)
                used += cost
            content = "\n".join(kept)
            exclusions.append({"section": name, "reason": "budget"})
        return ContextSection(name, content, estimate_tokens(content), budget, truncated=truncated)

    async def _audit(self, audit: AuditService, session_id: uuid.UUID, pack: ContextPack) -> None:
        await audit.record(
            AuditEventType.CONTEXT_PACKED,
            session_id=session_id,
            payload={
                "tokenizer_fingerprint": pack.tokenizer_fingerprint,
                "total_tokens": pack.total_tokens,
                "input_budget": pack.input_budget,
                "token_counts": {name: s.tokens for name, s in pack.sections.items()},
                "included_chunks": {
                    name: list(s.chunk_ids) for name, s in pack.sections.items() if s.chunk_ids
                },
                "exclusions": pack.exclusions,
                "pending_claim_ids": [str(c) for c in pack.pending_claim_ids],
            },
            public_summary=f"context packed: {pack.total_tokens} tokens",
        )


def render_claim(c: RetrievedClaim) -> str:
    return c.line
