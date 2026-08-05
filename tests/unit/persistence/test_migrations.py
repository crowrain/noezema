"""Migration reproducibility and schema-drift tests."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from packages.domain import EventType, QuestionOrigin, QuestionState
from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    BOOTSTRAP_PAYLOAD_SHA256,
    BOOTSTRAP_REVISION_SHA256,
    INVALID_QUESTION_NAMESPACE,
    Base,
    bootstrap_payload,
    validate_bootstrap_constants,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FOUNDATION_MIGRATION = importlib.import_module("migrations.versions.0001_operational_foundation")
QUESTION_MIGRATION = importlib.import_module("migrations.versions.0002_fifo_questions")
FOUNDATION_TABLES = {
    "actions",
    "audit_events",
    "config_snapshots",
    "domain_revisions",
    "model_runs",
    "outbox_events",
    "runtime_config_heads",
    "sessions",
    "system_constants",
}
EXPECTED_TABLES = FOUNDATION_TABLES | {"questions"}


def test_application_and_immutable_migration_literals_match() -> None:
    validate_bootstrap_constants()

    assert FOUNDATION_MIGRATION.BOOTSTRAP_CONFIG_SNAPSHOT_ID == str(BOOTSTRAP_CONFIG_SNAPSHOT_ID)
    assert FOUNDATION_MIGRATION.INVALID_QUESTION_NAMESPACE == str(INVALID_QUESTION_NAMESPACE)
    assert FOUNDATION_MIGRATION.BOOTSTRAP_PAYLOAD_SHA256 == BOOTSTRAP_PAYLOAD_SHA256
    assert FOUNDATION_MIGRATION.BOOTSTRAP_REVISION_SHA256 == BOOTSTRAP_REVISION_SHA256


def test_question_migration_literals_match_domain_enums() -> None:
    assert QUESTION_MIGRATION.QUESTION_ORIGINS == tuple(item.value for item in QuestionOrigin)
    assert QUESTION_MIGRATION.QUESTION_STATES == tuple(item.value for item in QuestionState)
    assert QUESTION_MIGRATION.AUDIT_EVENT_TYPES_V2 == tuple(item.value for item in EventType)


def test_bootstrap_payload_has_no_shared_mutable_state() -> None:
    first = bootstrap_payload()
    second = bootstrap_payload()

    first["schema_version"] = "mutated"

    assert second["schema_version"] == "runtime-config/v1"


def test_metadata_exposes_the_first_operational_schema_slice() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_online_migration_requires_an_explicit_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOEZEMA_DATABASE_URL", raising=False)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    with pytest.raises(RuntimeError, match="NOEZEMA_DATABASE_URL is required"):
        command.upgrade(config, "head")


def test_migrations_render_valid_bootstrap_and_fifo_sql(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.upgrade(config, "head", sql=True)

    sql = capsys.readouterr().out
    for table in EXPECTED_TABLES:
        assert f"CREATE TABLE {table}" in sql
    assert sql.count("INSERT INTO runtime_config_heads") == 1
    assert str(BOOTSTRAP_CONFIG_SNAPSHOT_ID) in sql
    assert str(INVALID_QUESTION_NAMESPACE) in sql
    assert BOOTSTRAP_PAYLOAD_SHA256 in sql
    assert '"embeddings":null' in sql
    assert '"embeddings"NULL' not in sql
    assert "ALTER TABLE sessions ADD COLUMN question_id UUID" in sql
    assert "CREATE INDEX ix_questions_fifo" in sql
    assert "DROP CONSTRAINT ck_audit_events_type_allowed" in sql
    assert "ck_audit_events_ck_audit_events_type_allowed" not in sql

    command.downgrade(config, "head:base", sql=True)
    downgrade_sql = capsys.readouterr().out
    assert "DROP TABLE questions" in downgrade_sql
    assert "DROP COLUMN question_id" in downgrade_sql
    assert "DROP CONSTRAINT ck_audit_events_type_allowed" in downgrade_sql
