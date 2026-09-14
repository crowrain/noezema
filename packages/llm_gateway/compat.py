"""Model compatibility suite (T1.11).

A fixed battery of structured-output checks run against any
OpenAI-compatible endpoint before a model profile is selected for
production (ADR-0010). In CI the suite runs against the deterministic
fake LLM; against a real model it is invoked manually
(``noezemactl compat-run``, hostctl) and the report is archived.

The suite measures reliability of the *protocol*, not the model's
knowledge: every check asks for a valid ModelResponse and records whether
the endpoint honored the schema.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import DecisionKind
from packages.domain.schemas.decision import ModelResponse
from packages.llm_gateway.client import LLMMiddleware

SYSTEM_PROMPT = (
    "You are a research assistant. Respond ONLY with a valid JSON object matching "
    "the required schema: {public_rationale, expected_information, decision}."
)


@dataclass(frozen=True)
class CompatCheckResult:
    name: str
    passed: bool
    attempts: int
    detail: str


@dataclass
class CompatReport:
    model: str
    checks: list[CompatCheckResult]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> JsonDict:
        return {"model": self.model, "passed": self.passed, "checks": [asdict(c) for c in self.checks]}


async def run_compat_suite(gateway: LLMMiddleware) -> CompatReport:
    checks: list[CompatCheckResult] = []

    cases: list[tuple[str, str]] = [
        (
            "schema_basic",
            "Исследуй вопрос: что такое квантовая запутанность? Предложи следующий шаг.",
        ),
        (
            "tool_choice",
            "Доступны инструменты web.search и workspace.read. Выбери один шаг исследования.",
        ),
        (
            "complete_decision",
            "Все шаги выполнены, цель достигнута. Заверши исследование.",
        ),
        (
            "long_context",
            "КОНТЕКСТ: " + ("локальный автономный мыслитель. " * 1500) + "Теперь предложи следующий шаг.",
        ),
    ]

    for name, user in cases:
        try:
            _resp, record = await gateway.chat(
                system=SYSTEM_PROMPT, user=user, response_schema=ModelResponse
            )
            passed = record.output_schema_valid
            checks.append(
                CompatCheckResult(name, passed, record.attempts, "ok" if passed else "schema invalid")
            )
        except Exception as exc:  # the suite reports, never raises
            checks.append(CompatCheckResult(name, False, 0, str(exc)[:200]))

    return CompatReport(model=gateway.config.model, checks=checks)


def expected_decisions() -> dict[str, DecisionKind]:
    """Reference mapping used by model_compatibility tests."""
    return {
        "schema_basic": DecisionKind.TOOL,
        "tool_choice": DecisionKind.TOOL,
        "complete_decision": DecisionKind.COMPLETE,
    }
