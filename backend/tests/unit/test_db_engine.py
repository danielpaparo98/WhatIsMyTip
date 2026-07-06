"""Tests for the SQLAlchemy engine pool settings (ME-005)."""

import pytest


def test_db_pool_settings_defaults():
    from packages.shared.config import Settings

    # When no env var is provided, the Settings should default to the
    # low-memory (2, 3, 30) values.  On a 512 MB instance every pooled
    # connection is a server-side process plus an asyncpg buffer, so a
    # tight pool keeps the baseline RSS down while remaining overridable
    # via DB_POOL_SIZE / DB_MAX_OVERFLOW on larger instances.
    s = Settings()
    assert s.db_pool_size == 2
    assert s.db_max_overflow == 3
    assert s.db_pool_timeout == 30


def test_db_pool_settings_read_env(monkeypatch):
    from packages.shared.config import Settings

    monkeypatch.setenv("DB_POOL_SIZE", "12")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "20")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "45")
    s = Settings()
    assert s.db_pool_size == 12
    assert s.db_max_overflow == 20
    assert s.db_pool_timeout == 45
