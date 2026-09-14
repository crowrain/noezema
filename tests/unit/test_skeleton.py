"""Smoke tests for the M0 skeleton (T0.3)."""

from __future__ import annotations

import pytest

from packages.domain.db.engine import DEFAULT_DATABASE_URL, Database, DatabaseSettings


@pytest.mark.unit
def test_settings_defaults() -> None:
    settings = DatabaseSettings()
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.echo_sql is False


@pytest.mark.unit
def test_settings_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", "postgresql+asyncpg://u:p@h:1/d")
    monkeypatch.setenv("NOEZEMA_ECHO_SQL", "true")
    settings = DatabaseSettings()
    assert settings.database_url == "postgresql+asyncpg://u:p@h:1/d"
    assert settings.echo_sql is True


@pytest.mark.unit
def test_settings_ignore_unknown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOEZEMA_UNRELATED", "1")
    settings = DatabaseSettings()
    assert settings.database_url == DEFAULT_DATABASE_URL


@pytest.mark.unit
def test_database_lazily_creates_engine() -> None:
    db = Database(DatabaseSettings())
    assert db._engine is None
    engine = db.engine
    assert engine.url.drivername == "postgresql+asyncpg"
    assert db.session_factory is not None
    # engine is cached
    assert db.engine is engine
