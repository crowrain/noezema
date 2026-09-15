"""Verifier report schema (T5.3, stage 4, §3.7, §5.5, §6.4).

The verifier role organizes deterministic checks over the session's
typed evidence and interprets their results. The report is a proposal:

- it carries NO grade, NO confidence and NO claim-status field by
  construction — the closed schema cannot express them. Grade and
  confidence are produced only by the rules engine, and the verifier's
  own judgment never changes a claim's status (§3.7);
- it creates no evidence: ``evidence_indexes`` only refer to the
  typed observations the Tool Broker already recorded (the model's
  text alone never creates evidence);
- an invalid or over-budget report falls back to the MVP no-op
  verifying phase (audit, never a session failure); transport LLM
  errors fail the session, like any other phase.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: closed set of deterministic check outcomes
CheckResult = Literal["pass", "fail", "not_applicable"]


class VerificationError(ValueError):
    """Host-side validation of the verifier report failed."""


class VerificationCheck(BaseModel):
    """One organized deterministic check with its interpretation."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=400)
    method: str = Field(min_length=1, max_length=200)
    evidence_indexes: list[int] = Field(default_factory=list, max_length=16)
    result: CheckResult
    note: str | None = Field(default=None, max_length=400)

    @field_validator("evidence_indexes")
    @classmethod
    def _validate_indexes(cls, value: list[int]) -> list[int]:
        for item in value:
            if item < 0:
                raise ValueError("evidence indexes must be >= 0")
        if len(set(value)) != len(value):
            raise ValueError("evidence_indexes must not repeat")
        return value


class VerifierReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_rationale: str = Field(min_length=1, max_length=2000)
    checks: list[VerificationCheck] = Field(min_length=1, max_length=16)
    gaps: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("gaps")
    @classmethod
    def _validate_gaps(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item) > 400:
                raise ValueError("each gap must be a non-empty string <= 400 chars")
        return value


def validate_verifier_report(report: VerifierReport, evidence_count: int, max_checks: int) -> None:
    """Host-side budget and referential validation. Raises
    VerificationError — the caller falls back to the MVP no-op phase."""
    if max_checks < 1:
        raise VerificationError(f"max_checks must be >= 1, got {max_checks}")
    if len(report.checks) > max_checks:
        raise VerificationError(f"report has {len(report.checks)} checks; max_checks={max_checks}")
    for check in report.checks:
        for index in check.evidence_indexes:
            if index >= evidence_count:
                raise VerificationError(
                    f"check {check.description[:40]!r} references evidence[{index}] "
                    f"but the session has {evidence_count} evidence items"
                )


def render_verification(report: VerifierReport) -> str:
    """The report rendered into the curator context (a PROPOSAL of what
    was checked; it deliberately contains no grade/confidence)."""
    lines: list[str] = []
    for i, check in enumerate(report.checks, start=1):
        refs = ", ".join(f"[{j}]" for j in check.evidence_indexes) or "—"
        note = f" — {check.note}" if check.note else ""
        lines.append(f"{i}. {check.description} (метод: {check.method}; evidence: {refs}) → {check.result}{note}")
    lines.append("Верификатор (предложение, не оценка): " + report.public_rationale)
    if report.gaps:
        lines.append("Пробелы: " + "; ".join(report.gaps))
    lines.append(
        "Внимание: grade/confidence назначает только rules engine; "
        "вывод верификатора статус утверждений не меняет."
    )
    return "\n".join(lines)


def verification_payload(report: VerifierReport) -> dict[str, Any]:
    """The JSON stored on sessions.verification / sent to the audit
    (stable structure, canonical for the sha256)."""
    return {
        "checks": [check.model_dump() for check in report.checks],
        "gaps": list(report.gaps),
        "rationale": report.public_rationale,
    }
