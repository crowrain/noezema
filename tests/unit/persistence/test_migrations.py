"""Migration reproducibility and schema-drift tests."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

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
MIGRATION = importlib.import_module("migrations.versions.0001_operational_foundation")
EXPECTED_TABLES = {
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


def test_application_and_immutable_migration_literals_match() -> None:
    validate_bootstrap_constants()

    assert MIGRATION.BOOTSTRAP_CONFIG_SNAPSHOT_ID == str(BOOTSTRAP_CONFIG_SNAPSHOT_ID)
    assert MIGRATION.INVALID_QUESTION_NAMESPACE == str(INVALID_QUESTION_NAMESPACE)
    assert MIGRATION.BOOTSTRAP_PAYLOAD_SHA256 == BOOTSTRAP_PAYLOAD_SHA256
    assert MIGRATION.BOOTSTRAP_REVISION_SHA256 == BOOTSTRAP_REVISION_SHA256


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


def test_first_migration_renders_valid_pinned_bootstrap_sql(
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
