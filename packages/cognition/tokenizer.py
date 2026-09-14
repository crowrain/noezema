"""Deterministic token estimation for context budgets (T3.8, §5.4.1).

The model's real tokenizer is backend-specific, so the host uses a
deterministic ESTIMATE that is stable across runs: it counts word tokens
(``\\w+``) and single non-space symbol tokens. The fingerprint of this
estimator is recorded in the ``ContextPacked`` audit so a later change of
tokenizer is visible in the journal.

Budget math (§5.4.1)::

    input_budget = min(context_window, backend_limit)
                   - max_output_tokens
                   - safety_margin

Section limits are ABSOLUTE token counts from the config snapshot and may
not sum to more than ``input_budget``.
"""

from __future__ import annotations

import re

TOKENIZER_FINGERPRINT = "word-symbol-v1"

_WORD_OR_SYMBOL = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate (word + symbol tokens)."""
    if not text:
        return 0
    return len(_WORD_OR_SYMBOL.findall(text))


class TokenBudgets:
    """Absolute per-section token limits + the derived input budget."""

    def __init__(
        self,
        section_limits: dict[str, int],
        *,
        context_window: int,
        backend_context_limit: int,
        max_output_tokens: int,
        safety_margin_tokens: int,
    ) -> None:
        self.section_limits = dict(section_limits)
        self.input_budget = (
            min(context_window, backend_context_limit) - max_output_tokens - safety_margin_tokens
        )

    @classmethod
    def from_snapshot(cls, model: dict[str, int], token_budgets: dict[str, int]) -> TokenBudgets:
        return cls(
            {str(k): int(v) for k, v in token_budgets.items()},
            context_window=int(model.get("context_window", 32768)),
            backend_context_limit=int(model.get("backend_context_limit", 32768)),
            max_output_tokens=int(model.get("max_output_tokens", 0)),
            safety_margin_tokens=int(model.get("safety_margin_tokens", 0)),
        )

    def validate(self) -> list[str]:
        """The section limits may not sum past the input budget (§5.4.1)."""
        problems: list[str] = []
        total = sum(self.section_limits.values())
        if total > self.input_budget:
            problems.append(f"section limits sum {total} > input_budget {self.input_budget}")
        for name, limit in self.section_limits.items():
            if limit < 0:
                problems.append(f"section {name!r} has a negative limit {limit}")
        return problems
