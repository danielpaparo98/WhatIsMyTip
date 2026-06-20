"""Tests for credential redaction in database URL logging.

Ensures that DATABASE_URL and connection strings are never logged with
their password component visible.  This is a defence-in-depth measure
against accidental secret leakage via application logs.
"""

import logging

import pytest

from packages.shared.db import _redact_url


class TestRedactUrl:
    """Verify that _redact_url masks passwords in connection strings."""

    def test_redacts_postgres_url_with_password(self):
        url = "postgresql+asyncpg://user:supersecret@host:5432/dbname"
        redacted = _redact_url(url)
        assert "supersecret" not in redacted
        assert "***" in redacted
        assert "user" in redacted
        assert "host:5432" in redacted

    def test_redacts_redis_url_with_password(self):
        url = "rediss://default:fake_redis_pass_98765@cache.example.com:25061"
        redacted = _redact_url(url)
        assert "fake_redis_pass_98765" not in redacted
        assert "***" in redacted
        assert "cache.example.com:25061" in redacted

    def test_preserves_url_without_credentials(self):
        url = "postgresql+asyncpg://localhost/whatismytip"
        redacted = _redact_url(url)
        assert redacted == url

    def test_preserves_url_with_user_only_no_password(self):
        url = "postgresql://user@localhost/db"
        redacted = _redact_url(url)
        assert "user@localhost" in redacted
        assert "***" not in redacted

    def test_handles_empty_string(self):
        assert _redact_url("") == "<empty>"

    def test_handles_malformed_url_gracefully(self):
        """Malformed URLs should not raise — return a safe placeholder."""
        redacted = _redact_url("not://a valid: ://url with spaces")
        # Should not raise and should not leak anything
        assert isinstance(redacted, str)

    def test_redacts_cloud_managed_db_format(self):
        """Cloud managed-db DSN format with long password gets masked."""
        url = (
            "postgresql://myuser:fake_cloud_db_password_12345"
            "@fake-host.example.com:25060/mydb"
        )
        redacted = _redact_url(url)
        assert "fake_cloud_db_password_12345" not in redacted
        assert "***" in redacted


class TestNoCredentialLoggingInDbModule:
    """Ensure get_engine() never logs the raw DATABASE_URL."""

    def test_engine_creation_logs_redacted_url(
        self, monkeypatch, caplog
    ):
        """When engine creation succeeds, the logged URL must be redacted."""
        from packages.shared import db as _db

        # Reset the singleton engine so get_engine() exercises the creation path
        monkeypatch.setattr(_db, "_engine", None)

        # Use a URL with a fake password
        test_url = "postgresql+asyncpg://testuser:testpass123@localhost/testdb"
        monkeypatch.setattr(_db.settings, "database_url", test_url)

        # Mock create_async_engine to avoid a real connection attempt
        class FakeEngine:
            driver = "postgresql+asyncpg"

        def fake_create(*args, **kwargs):
            return FakeEngine()

        monkeypatch.setattr(_db, "create_async_engine", fake_create)

        with caplog.at_level(logging.INFO):
            _db.get_engine()

        # Verify no log record contains the raw password
        all_log_output = " ".join(r.getMessage() for r in caplog.records)
        assert "testpass123" not in all_log_output, (
            "Raw database password was found in log output!"
        )

        # Clean up the singleton
        monkeypatch.setattr(_db, "_engine", None)
