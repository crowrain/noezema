"""Opt-in verification against a disposable PostgreSQL database."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from packages.persistence import BOOTSTRAP_CONFIG_SNAPSHOT_ID, INVALID_QUESTION_NAMESPACE

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get("NOEZEMA_TEST_DATABASE_URL")

pytestmark = pytest.mark.postgresql_integration


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="NOEZEMA_TEST_DATABASE_URL is not set to a disposable PostgreSQL database",
)
def test_upgrade_seeds_exactly_one_global_head(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DATABASE_URL is not None
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", DATABASE_URL)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.upgrade(config, "head")
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            global_heads = connection.scalar(
                text("SELECT count(*) FROM runtime_config_heads WHERE scope = 'global'")
            )
            active_snapshot = connection.scalar(
                text(
                    "SELECT active_config_snapshot_id::text FROM runtime_config_heads "
                    "WHERE scope = 'global'"
                )
            )
            invalid_question_namespace = connection.scalar(
                text(
                    "SELECT value FROM system_constants "
                    "WHERE key = 'invalid_question_uuid5_namespace'"
                )
            )
            questions_table = connection.scalar(text("SELECT to_regclass('questions')::text"))
            session_question_column = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'sessions' AND column_name = 'question_id'"
                )
            )

        assert global_heads == 1
        assert active_snapshot == str(BOOTSTRAP_CONFIG_SNAPSHOT_ID)
        assert invalid_question_namespace == str(INVALID_QUESTION_NAMESPACE)
        assert questions_table == "questions"
        assert session_question_column == 1
    finally:
        engine.dispose()
        command.downgrade(config, "base")
