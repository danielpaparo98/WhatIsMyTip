"""Tests for the SQLAlchemy engine pool settings (ME-005)."""

import pytest


def test_db_pool_settings_defaults():
    from packages.shared.config import Settings

    # When no env var is provided, the Settings should default to
    # the documented (5, 10, 30) values (ME-005).
    s = Settings()
    assert s.db_pool_size == 5
    assert s.db_max_overflow == 10
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
