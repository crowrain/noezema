"""Multi-step session plan schema (T5.2, stage 4, §6.2).

The plan is a structured, host-validated artifact: it formulates the
observations that can change confidence, the stopping criteria and the
assessment methods. The verification METHOD is a structured field,
separate from the free-form observation — a new check method is
distinguishable from a rephrasing (gate M5).

The plan is a proposal: it creates no evidence by itself, it does not
grant capabilities and it does not touch the host-owned
evidence/assessment boundary — the explorer still works the bounded
tool loop and the rules engine still grades. An invalid proposal is a
fallback to the MVP fixed-template plan, never a session failure
(LLM transport errors are host failures and fail the session, like
any other phase).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: closed set of assessment methods a plan may declare (§6.2:
#: "assessment methods" are host-owned, not model-invented)
ASSESSMENT_METHODS = ("recompute", "cross_source_check", "rules_only", "no_change")

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class PlanError(ValueError):
    """The host-side plan validation failed (budget, closed sets)."""


class PlanStep(BaseModel):
    """One planned step: what observation it would produce and HOW it
    will be verified (the method is a separate structured field)."""

    model_config = ConfigDict(extra="forbid")

    observation: str = Field(
        min_length=1, max_length=1000,
        description="Какое наблюдение изменит уверенность (что именно узнаем).",
    )
    method: str = Field(
        min_length=1, max_length=200,
        description="Метод проверки — как наблюдение будет получено/сверено (отдельно от формулировки).",
    )
    tool_hint: str | None = Field(
        default=None,
        description="Опционально: инструмент namespace.name, которым шаг планируется выполнить.",
    )

    @field_validator("tool_hint")
    @classmethod
    def _validate_tool_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _TOOL_NAME_RE.fullmatch(value):
            raise ValueError(f"tool_hint must match namespace.name, got {value!r}")
        return value


class SessionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[PlanStep] = Field(min_length=1, max_length=16)
    stopping_criteria: list[str] = Field(min_length=1, max_length=16)
    assessment_methods: list[str] = Field(min_length=1, max_length=8)

    @field_validator("stopping_criteria")
    @classmethod
    def _validate_criteria(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item) > 400:
                raise ValueError("each stopping criterion must be a non-empty string <= 400 chars")
        return value

    @field_validator("assessment_methods")
    @classmethod
    def _validate_methods(cls, value: list[str]) -> list[str]:
        for item in value:
            if item not in ASSESSMENT_METHODS:
                raise ValueError(
                    f"assessment method must be one of {ASSESSMENT_METHODS}, got {item!r}"
                )
        if len(set(value)) != len(value):
            raise ValueError("assessment_methods must not repeat")
        return value


class PlanResponse(BaseModel):
    """The planner's structured response envelope (mirrors ModelResponse:
    one public rationale + machine-checked structure)."""

    model_config = ConfigDict(extra="forbid")

    public_rationale: str = Field(min_length=1, max_length=2000)
    plan: SessionPlan


def validate_plan_budget(plan: SessionPlan, max_steps: int) -> None:
    """Host-side budget: the plan may not exceed the session step budget
    (session_limits.max_plan_steps). Raises PlanError — the caller
    falls back to the template plan."""
    if max_steps < 1:
        raise PlanError(f"max_plan_steps must be >= 1, got {max_steps}")
    if len(plan.steps) > max_steps:
        raise PlanError(f"plan has {len(plan.steps)} steps; max_plan_steps={max_steps}")


def render_plan(plan: SessionPlan) -> str:
    """The plan rendered into the explorer's context (question_plan
    section). The method is rendered as a separate labeled line, so the
    check method stays distinct from the observation wording."""
    lines: list[str] = []
    for i, step in enumerate(plan.steps, start=1):
        hint = f" (инструмент: {step.tool_hint})" if step.tool_hint else ""
        lines.append(f"{i}. Наблюдение: {step.observation}")
        lines.append(f"   Метод проверки: {step.method}{hint}")
    lines.append("Критерии остановки: " + "; ".join(plan.stopping_criteria))
    lines.append("Assessment methods: " + ", ".join(plan.assessment_methods))
    return "\n".join(lines)


def plan_payload(plan: SessionPlan) -> dict[str, Any]:
    """The JSON stored on sessions.plan / sent to the audit (stable
    structure, canonical for the sha256)."""
    return {
        "steps": [step.model_dump() for step in plan.steps],
        "stopping_criteria": list(plan.stopping_criteria),
        "assessment_methods": list(plan.assessment_methods),
    }
