"""Public results of one trusted-host wake attempt."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from packages.domain import ConfigSnapshotId, QuestionId, QuestionOrigin, SessionId
from packages.domain._base import ContractModel, NonEmptyText


class WakeSkipReason(StrEnum):
    ACTIVE_SESSION = "active_session"
    CONFIG_ACTIVATION_IN_PROGRESS = "config_activation_in_progress"
    NO_ELIGIBLE_QUESTION = "no_eligible_question"


class SessionStarted(ContractModel):
    status: Literal["started"] = "started"
    session_id: SessionId
    question_id: QuestionId
    question_text: NonEmptyText
    question_origin: QuestionOrigin
    config_snapshot_id: ConfigSnapshotId


class WakeSkipped(ContractModel):
    status: Literal["skipped"] = "skipped"
    reason: WakeSkipReason


WakeResult = SessionStarted | WakeSkipped
