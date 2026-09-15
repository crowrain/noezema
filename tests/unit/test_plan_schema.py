"""Unit tests: the multi-step plan schema (T5.2, stage 4, §6.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.domain.canonical import canonical_sha256
from packages.domain.schemas.plan import (
    ASSESSMENT_METHODS,
    PlanError,
    PlanResponse,
    PlanStep,
    SessionPlan,
    plan_payload,
    render_plan,
    validate_plan_budget,
)

pytestmark = pytest.mark.unit


def _step(observation: str = "Результат пересчёта", method: str = "Пересчёт в sandbox", **kw):
    return PlanStep(observation=observation, method=method, **kw)


def _plan(**kw) -> SessionPlan:
    base: dict = {
        "steps": [_step()],
        "stopping_criteria": ["Ответ пересчитан и сверен"],
        "assessment_methods": ["recompute"],
    }
    base.update(kw)
    return SessionPlan(**base)


def test_valid_plan_parses():
    plan = _plan()
    assert plan.steps[0].tool_hint is None
    assert plan.assessment_methods == ["recompute"]


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        SessionPlan(
            steps=[_step()],
            stopping_criteria=["x"],
            assessment_methods=["recompute"],
            confidence=0.9,  # the plan never carries a grade (§3.7)
        )


def test_step_requires_observation_and_method():
    with pytest.raises(ValidationError):
        PlanStep(observation="", method="m")
    with pytest.raises(ValidationError):
        PlanStep(observation="o", method="")
    with pytest.raises(ValidationError):
        PlanStep(observation="o", method="m", extra="x")


def test_tool_hint_namespace_name():
    step = _step(tool_hint="python.execute")
    assert step.tool_hint == "python.execute"
    for bad in ("python", "Python.execute", "python..execute", "python execute"):
        with pytest.raises(ValidationError):
            PlanStep(observation="o", method="m", tool_hint=bad)


def test_steps_and_criteria_bounds():
    with pytest.raises(ValidationError):
        _plan(steps=[])
    with pytest.raises(ValidationError):
        _plan(stopping_criteria=[])
    with pytest.raises(ValidationError):
        _plan(stopping_criteria=["x"] + ["y"] * 16)
    with pytest.raises(ValidationError):
        _plan(steps=[_step()] * 17)


def test_stopping_criteria_must_be_nonempty_strings():
    for bad in ([""], ["   "], ["x" * 401]):
        with pytest.raises(ValidationError):
            _plan(stopping_criteria=bad)


def test_assessment_methods_closed_set():
    with pytest.raises(ValidationError):
        _plan(assessment_methods=["my_own_method"])
    with pytest.raises(ValidationError):
        _plan(assessment_methods=[])
    with pytest.raises(ValidationError):
        _plan(assessment_methods=["recompute", "recompute"])
    ok = list(ASSESSMENT_METHODS)
    assert _plan(assessment_methods=ok).assessment_methods == ok


def test_plan_response_envelope():
    resp = PlanResponse(public_rationale="план", plan=_plan())
    assert resp.plan.steps[0].method
    with pytest.raises(ValidationError):
        PlanResponse(plan=_plan())  # public_rationale required
    with pytest.raises(ValidationError):
        PlanResponse(public_rationale="x", plan=_plan(), extra=1)


def test_budget_validation():
    plan = _plan(steps=[_step()] * 3)
    validate_plan_budget(plan, max_steps=3)
    with pytest.raises(PlanError):
        validate_plan_budget(plan, max_steps=2)
    with pytest.raises(PlanError):
        validate_plan_budget(plan, max_steps=0)


def test_render_keeps_method_separate_from_observation():
    """Gate M5: the check method stays distinguishable from a rephrasing
    — it is rendered as its own labeled line."""
    plan = _plan(steps=[_step(observation="Сумма двух чисел", method="Независимый пересчёт")])
    rendered = render_plan(plan)
    assert "Наблюдение: Сумма двух чисел" in rendered
    assert "Метод проверки: Независимый пересчёт" in rendered
    assert "Критерии остановки:" in rendered
    assert "Assessment methods: recompute" in rendered
    plan2 = _plan(steps=[_step(tool_hint="python.execute")])
    assert "(инструмент: python.execute)" in render_plan(plan2)


def test_payload_stable_and_hashable():
    plan = _plan()
    doc1, doc2 = plan_payload(plan), plan_payload(plan)
    assert doc1 == doc2
    assert canonical_sha256(doc1) == canonical_sha256(doc2)
    assert set(doc1) == {"steps", "stopping_criteria", "assessment_methods"}
    assert doc1["steps"][0]["tool_hint"] is None
