"""Migration reproducibility and schema-drift tests."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from packages.domain import (
    ClaimType,
    EpistemicStatus,
    EventType,
    EvidenceKind,
    MessageState,
    OperatorCommandState,
    OperatorCommandType,
    QuestionOrigin,
    QuestionState,
    RevisionScope,
)
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
KNOWLEDGE_MIGRATION = importlib.import_module("migrations.versions.0003_knowledge_commit_slice")
TOOL_BROKER_MIGRATION = importlib.import_module("migrations.versions.0004_tool_broker")
CONCURRENCY_MIGRATION = importlib.import_module("migrations.versions.0006_concurrency_control")
ORCHESTRATOR_MIGRATION = importlib.import_module(
    "migrations.versions.0007_durable_orchestrator_turns"
)
BUDGET_MIGRATION = importlib.import_module("migrations.versions.0008_session_budgets_and_controls")
INBOX_MIGRATION = importlib.import_module("migrations.versions.0009_human_input_and_operator_inbox")
FOUNDATION_TABLES = {
    "actions",
    "audit_events",
    "config_snapshots",
    "domain_revisions",
    "model_runs",
    "outbox_events",
    "orchestrator_turns",
    "runtime_config_heads",
    "runtime_controls",
    "sessions",
    "system_constants",
}
EXPECTED_TABLES = FOUNDATION_TABLES | {
    "artifact_blobs",
    "artifacts",
    "assessment_evidence",
    "checkpoints",
    "claim_assessment_heads",
    "claim_assessments",
    "claims",
    "commit_attempts",
    "evidence",
    "messages",
    "operator_commands",
    "questions",
    "session_staging",
    "workspace_files",
    "workspace_versions",
    "writer_intents",
}


def test_application_and_immutable_migration_literals_match() -> None:
    validate_bootstrap_constants()

    assert FOUNDATION_MIGRATION.BOOTSTRAP_CONFIG_SNAPSHOT_ID == str(BOOTSTRAP_CONFIG_SNAPSHOT_ID)
    assert FOUNDATION_MIGRATION.INVALID_QUESTION_NAMESPACE == str(INVALID_QUESTION_NAMESPACE)
    assert FOUNDATION_MIGRATION.BOOTSTRAP_PAYLOAD_SHA256 == BOOTSTRAP_PAYLOAD_SHA256
    assert FOUNDATION_MIGRATION.BOOTSTRAP_REVISION_SHA256 == BOOTSTRAP_REVISION_SHA256


def test_question_migration_literals_match_domain_enums() -> None:
    assert QUESTION_MIGRATION.QUESTION_ORIGINS == tuple(item.value for item in QuestionOrigin)
    assert QUESTION_MIGRATION.QUESTION_STATES == tuple(item.value for item in QuestionState)
    assert QUESTION_MIGRATION.AUDIT_EVENT_TYPES_V2 == BUDGET_MIGRATION.AUDIT_EVENT_TYPES_V2


def test_knowledge_migration_literals_match_closed_domain_registries() -> None:
    assert KNOWLEDGE_MIGRATION.CLAIM_TYPES == tuple(item.value for item in ClaimType)
    assert KNOWLEDGE_MIGRATION.EVIDENCE_KINDS == tuple(item.value for item in EvidenceKind)
    assert KNOWLEDGE_MIGRATION.EPISTEMIC_STATUSES == tuple(item.value for item in EpistemicStatus)
    assert CONCURRENCY_MIGRATION.REVISION_SCOPES == tuple(item.value for item in RevisionScope)
    assert ORCHESTRATOR_MIGRATION.TURN_PHASES == (
        "planning",
        "exploration",
        "verification",
        "consolidation",
    )
    assert BUDGET_MIGRATION.AUDIT_EVENT_TYPES_V3 == INBOX_MIGRATION.AUDIT_EVENT_TYPES_V3
    assert INBOX_MIGRATION.AUDIT_EVENT_TYPES_V4 == tuple(item.value for item in EventType)
    assert INBOX_MIGRATION.MESSAGE_STATES == tuple(item.value for item in MessageState)
    assert INBOX_MIGRATION.OPERATOR_COMMAND_TYPES == tuple(
        item.value for item in OperatorCommandType
    )
    assert INBOX_MIGRATION.OPERATOR_COMMAND_STATES == tuple(
        item.value for item in OperatorCommandState
    )


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
    assert "ALTER TABLE sessions ADD COLUMN commit_attempt_id UUID" in sql
    assert "ALTER TABLE actions ADD COLUMN arguments_json TEXT" in sql
    assert "ALTER TABLE actions ADD COLUMN attempt_count INTEGER" in sql
    assert "CREATE TABLE artifact_blobs" in sql
    assert "CREATE TABLE workspace_versions" in sql
    assert "CREATE TABLE writer_intents" in sql
    assert "CREATE TABLE orchestrator_turns" in sql
    assert "ALTER TABLE sessions ADD COLUMN budget JSONB" in sql
    assert "ALTER TABLE sessions ADD COLUMN stop_requested_at" in sql
    assert "CREATE TABLE runtime_controls" in sql
    assert "CREATE TABLE messages" in sql
    assert "CREATE TABLE operator_commands" in sql
    assert "SessionBudgetExhausted" in sql
    assert "ALTER TABLE commit_attempts ADD COLUMN session_fence BIGINT" in sql
    assert "CREATE INDEX ix_questions_fifo" in sql
    assert "CREATE INDEX ix_audit_events_public_timeline" in sql
    assert "CREATE INDEX ix_sessions_query" in sql
    assert "CREATE INDEX ix_messages_query" in sql
    assert "CREATE INDEX ix_operator_commands_query" in sql
    assert "DROP CONSTRAINT ck_audit_events_type_allowed" in sql
    assert "ck_audit_events_ck_audit_events_type_allowed" not in sql

    command.downgrade(config, "head:base", sql=True)
    downgrade_sql = capsys.readouterr().out
    assert "DROP TABLE questions" in downgrade_sql
    assert "DROP TABLE claim_assessments" in downgrade_sql
    assert "DROP TABLE commit_attempts" in downgrade_sql
    assert "DROP COLUMN question_id" in downgrade_sql
    assert "DROP COLUMN commit_attempt_id" in downgrade_sql
    assert "DROP COLUMN arguments_json" in downgrade_sql
    assert "DROP TABLE workspace_versions" in downgrade_sql
    assert "DROP TABLE artifact_blobs" in downgrade_sql
    assert "DROP TABLE writer_intents" in downgrade_sql
    assert "DROP TABLE orchestrator_turns" in downgrade_sql
    assert "DROP COLUMN budget" in downgrade_sql
    assert "DROP COLUMN abort_requested_at" in downgrade_sql
    assert "DROP TABLE operator_commands" in downgrade_sql
    assert "DROP TABLE messages" in downgrade_sql
    assert "DROP TABLE runtime_controls" in downgrade_sql
    assert "DROP INDEX ix_audit_events_public_timeline" in downgrade_sql
    assert "DROP CONSTRAINT ck_audit_events_type_allowed" in downgrade_sql
