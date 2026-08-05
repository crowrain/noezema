"""Question-ingestion contract tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.domain import (
    ConfigSnapshotId,
    QuestionDraft,
    QuestionId,
    QuestionOrigin,
)

CONFIG_ID = ConfigSnapshotId.new()
NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def test_seeded_and_message_constructors_assign_trusted_origin_and_id() -> None:
    seeded = QuestionDraft.seeded(
        text="Что можно проверить в локальном корпусе?",
        origin_config_snapshot_id=CONFIG_ID,
        created_at=NOW,
    )
    message = QuestionDraft.from_message(
        text="Исследуй причинность этого сбоя",
        origin_config_snapshot_id=CONFIG_ID,
        created_at=NOW,
    )

    assert seeded.origin is QuestionOrigin.SEEDED
    assert message.origin is QuestionOrigin.MESSAGE
    assert seeded.id != message.id
    assert type(seeded.id) is QuestionId


def test_question_requires_aware_time_nonempty_text_and_bounded_priority() -> None:
    with pytest.raises(ValidationError, match="timezone info"):
        QuestionDraft.seeded(
            text="Вопрос",
            origin_config_snapshot_id=CONFIG_ID,
            created_at=datetime(2026, 8, 5),
        )

    with pytest.raises(ValidationError):
        QuestionDraft.seeded(
            text=" ",
            origin_config_snapshot_id=CONFIG_ID,
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="less than or equal to 1000"):
        QuestionDraft.from_message(
            text="Вопрос",
            origin_config_snapshot_id=CONFIG_ID,
            created_at=NOW,
            priority=1001,
        )
