"""Tests for production configuration fail-fast validation.

Verifies that the app refuses to start in production with:
- Localhost DATABASE_URL defaults
- Missing or too-short ADMIN_API_KEY
- Missing +asyncpg driver in DATABASE_URL

And that non-production environments are unaffected.
"""

import os

import pytest
from pydantic import ValidationError

from packages.shared.config import Settings


class TestProductionConfigValidation:
    """Validate that Settings() rejects production-incompatible config."""

    def _make_production_settings(self, **overrides) -> Settings:
        """Helper to construct Settings with production environment."""
        defaults = {
            "environment": "production",
            "database_url": "postgresql+asyncpg://prod:secret@db.example.com:25060/whatismytip",
            "admin_api_key": "a" * 48,  # sufficiently long key
        }
        defaults.update(overrides)
        return Settings(**defaults)

    def test_production_with_valid_config_succeeds(self):
        """A properly configured production Settings() should construct OK."""
        s = self._make_production_settings()
        assert s.environment == "production"

    def test_production_with_localhost_database_url_fails(self):
        """The localhost default must be rejected in production."""
        with pytest.raises(ValidationError) as exc_info:
            self._make_production_settings(
                database_url="postgresql+asyncpg://localhost/whatismytip"
            )
        assert "DATABASE_URL" in str(exc_info.value)
        assert "localhost" in str(exc_info.value).lower()

    def test_production_with_empty_database_url_fails(self):
        """An empty DATABASE_URL must be rejected in production."""
        with pytest.raises(ValidationError) as exc_info:
            self._make_production_settings(database_url="")
        assert "DATABASE_URL" in str(exc_info.value)

    def test_production_without_asyncpg_driver_fails(self):
        """DATABASE_URL without +asyncpg must be rejected in production."""
        with pytest.raises(ValidationError) as exc_info:
            self._make_production_settings(
                database_url="postgresql://prod:secret@db.example.com:25060/whatismytip"
            )
        assert "asyncpg" in str(exc_info.value)

    def test_production_with_empty_admin_key_fails(self):
        """Missing ADMIN_API_KEY must be rejected in production."""
        with pytest.raises(ValidationError) as exc_info:
            self._make_production_settings(admin_api_key="")
        assert "ADMIN_API_KEY" in str(exc_info.value)

    def test_production_with_short_admin_key_fails(self):
        """ADMIN_API_KEY shorter than 32 chars must be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            self._make_production_settings(admin_api_key="short")
        assert "ADMIN_API_KEY" in str(exc_info.value)
        assert "32" in str(exc_info.value)

    def test_development_with_localhost_defaults_is_allowed(self):
        """Development environment must NOT reject localhost defaults."""
        s = Settings(
            environment="development",
            database_url="postgresql+asyncpg://localhost/whatismytip",
            admin_api_key="",
        )
        assert s.environment == "development"

    def test_test_environment_with_defaults_is_allowed(self):
        """Test/staging environments must NOT reject defaults."""
        s = Settings(
            environment="test",
            database_url="postgresql+asyncpg://localhost/whatismytip",
            admin_api_key="",
        )
        assert s.environment == "test"

    def test_multiple_errors_reported_at_once(self):
        """When multiple things are wrong, all errors are reported."""
        with pytest.raises(ValidationError) as exc_info:
            self._make_production_settings(
                database_url="postgresql+asyncpg://localhost/whatismytip",
                admin_api_key="x",
            )
        error_str = str(exc_info.value)
        assert "DATABASE_URL" in error_str
        assert "ADMIN_API_KEY" in error_str


class TestLifespanProductionValidation:
    """Verify lifespan._validate_production_security catches misconfig."""

    def test_refuses_start_with_localhost_db_in_production(self, monkeypatch):
        """The lifespan guard must refuse to start with localhost DB."""
        from app.core.lifespan import _validate_production_security
        from packages.shared import config as _config

        monkeypatch.setattr(_config.settings, "environment", "production")
        monkeypatch.setattr(
            _config.settings,
            "database_url",
            "postgresql+asyncpg://localhost/whatismytip",
        )
        monkeypatch.setattr(_config.settings, "admin_api_key", "a" * 48)

        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            _validate_production_security()

    def test_refuses_start_with_missing_admin_key_in_production(self, monkeypatch):
        """The lifespan guard must refuse to start without admin key."""
        from app.core.lifespan import _validate_production_security
        from packages.shared import config as _config

        monkeypatch.setattr(_config.settings, "environment", "production")
        monkeypatch.setattr(
            _config.settings,
            "database_url",
            "postgresql+asyncpg://prod:secret@db.example.com:25060/db",
        )
        monkeypatch.setattr(_config.settings, "admin_api_key", "")

        with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
            _validate_production_security()

    def test_allows_start_in_development_with_defaults(self, monkeypatch):
        """Non-production must pass even with localhost + empty key."""
        from app.core.lifespan import _validate_production_security
        from packages.shared import config as _config

        monkeypatch.setattr(_config.settings, "environment", "development")
        monkeypatch.setattr(
            _config.settings,
            "database_url",
            "postgresql+asyncpg://localhost/whatismytip",
        )
        monkeypatch.setattr(_config.settings, "admin_api_key", "")

        # Should not raise
        _validate_production_security()

    def test_allows_start_with_valid_production_config(self, monkeypatch):
        """Valid production config must pass the lifespan guard."""
        from app.core.lifespan import _validate_production_security
        from packages.shared import config as _config

        monkeypatch.setattr(_config.settings, "environment", "production")
        monkeypatch.setattr(
            _config.settings,
            "database_url",
            "postgresql+asyncpg://prod:secret@db.example.com:25060/db",
        )
        monkeypatch.setattr(_config.settings, "admin_api_key", "a" * 48)

        # Should not raise
        _validate_production_security()
