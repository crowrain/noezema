"""Unit tests: the verifier report schema (T5.3, stage 4, §3.7).

The central gate property: the schema CANNOT express a grade or a
confidence — the verifier never assigns them (§3.7: the verifier's
judgment never changes a claim's status).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.domain.canonical import canonical_sha256
from packages.domain.schemas.verification import (
    VerificationCheck,
    VerificationError,
    VerifierReport,
    render_verification,
    validate_verifier_report,
    verification_payload,
)

pytestmark = pytest.mark.unit


def _check(**kw) -> VerificationCheck:
    base: dict = {"description": "Пересчёт сверен", "method": "Независимый пересчёт", "result": "pass"}
    base.update(kw)
    return VerificationCheck(**base)


def _report(**kw) -> VerifierReport:
    base: dict = {
        "public_rationale": "Проверено пересчётом",
        "checks": [_check(evidence_indexes=[0])],
        "gaps": ["Нужна сверка с другим источником"],
    }
    base.update(kw)
    return VerifierReport(**base)


def test_valid_report_parses():
    report = _report()
    assert report.checks[0].result == "pass"
    assert report.gaps


def test_schema_cannot_express_grade_or_confidence():
    """Gate M5: the verifier does not assign grade/confidence — the
    closed schema has no field for them at all."""
    for model in (VerifierReport, VerificationCheck):
        assert "grade" not in model.model_fields
        assert "effective_grade" not in model.model_fields
        assert "confidence" not in model.model_fields
        assert "status" not in model.model_fields
        assert "epistemic_status" not in model.model_fields
    with pytest.raises(ValidationError):
        _report(grade="E4")
    with pytest.raises(ValidationError):
        _report(confidence=0.99)
    with pytest.raises(ValidationError):
        _check(grade="E3")


def test_check_fields():
    with pytest.raises(ValidationError):
        _check(description="")
    with pytest.raises(ValidationError):
        _check(method="")
    with pytest.raises(ValidationError):
        _check(result="maybe")
    for result in ("pass", "fail", "not_applicable"):
        assert _check(result=result).result == result
    with pytest.raises(ValidationError):
        _check(note="x" * 401)


def test_evidence_indexes():
    with pytest.raises(ValidationError):
        _check(evidence_indexes=[-1])
    with pytest.raises(ValidationError):
        _check(evidence_indexes=[0, 0])
    assert _check(evidence_indexes=[0, 2, 1]).evidence_indexes == [0, 2, 1]
    with pytest.raises(ValidationError):
        _check(evidence_indexes=[0] * 17)


def test_report_bounds():
    with pytest.raises(ValidationError):
        _report(public_rationale="")
    with pytest.raises(ValidationError):
        _report(checks=[])
    with pytest.raises(ValidationError):
        _report(checks=[_check()] * 17)
    with pytest.raises(ValidationError):
        _report(gaps=["", "x"])
    with pytest.raises(ValidationError):
        _report(gaps=["x" * 401])
    with pytest.raises(ValidationError):
        _report(gaps=["x"] * 17)
    assert _report(gaps=[]).gaps == []


def test_host_validation_budget_and_references():
    report = _report(checks=[_check()] * 3)
    validate_verifier_report(report, evidence_count=5, max_checks=3)
    with pytest.raises(VerificationError, match="max_checks"):
        validate_verifier_report(report, evidence_count=5, max_checks=2)
    with pytest.raises(VerificationError, match="evidence\\[7\\]"):
        validate_verifier_report(_report(checks=[_check(evidence_indexes=[7])]),
                                 evidence_count=2, max_checks=8)
    with pytest.raises(VerificationError, match=">= 1"):
        validate_verifier_report(report, evidence_count=5, max_checks=0)


def test_render_is_a_proposal_without_grade():
    rendered = render_verification(_report())
    assert "метод:" in rendered
    assert "[0]" in rendered
    assert "→ pass" in rendered
    assert "Верификатор (предложение, не оценка):" in rendered
    assert "Пробелы:" in rendered
    assert "rules engine" in rendered
    # no grade wording can leak in from the model side
    assert "E0" not in rendered and "E1" not in rendered and "E2" not in rendered and "E4" not in rendered


def test_payload_stable_and_hashable():
    doc1, doc2 = verification_payload(_report()), verification_payload(_report())
    assert doc1 == doc2
    assert canonical_sha256(doc1) == canonical_sha256(doc2)
    assert set(doc1) == {"checks", "gaps", "rationale"}
