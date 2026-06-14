"""Unit tests for the health check endpoint in the games API handler."""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make shared package importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))



# ---------------------------------------------------------------------------
# Helper: import the games handler with mocked heavy dependencies
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    """Ensure settings.environment is 'test' so log level is INFO."""
    with patch("packages.shared.config.settings") as mock_settings:
        mock_settings.environment = "test"
        mock_settings.cors_origins = ["*"]
        mock_settings.cors_origins_list = []
        yield mock_settings


@pytest.fixture
def games_module():
    """Import the games module with mocked DB/cache dependencies."""
    with patch("packages.shared.db._get_session_factory"), \
         patch("packages.shared.db.get_engine"), \
         patch("packages.shared.db.dispose_engine", new_callable=AsyncMock), \
         patch("packages.shared.cache.close_redis_pool", new_callable=AsyncMock), \
         patch("packages.shared.cache._get_client"), \
         patch("packages.shared.config.settings") as mock_settings:
        mock_settings.environment = "test"
        mock_settings.cors_origins = ["*"]
        mock_settings.cors_origins_list = []
        mock_settings.admin_api_key = "test-key"

        # Re-import to get a fresh module with the mocks applied
        import importlib

        import packages.api.games as games_mod
        importlib.reload(games_mod)
        yield games_mod


# ---------------------------------------------------------------------------
# Tests: _handle_health
# ---------------------------------------------------------------------------

class TestHandleHealth:
    """Tests for the _handle_health() function."""

    @pytest.mark.asyncio
    async def test_healthy_when_both_db_and_redis_ok(self, games_module):
        """Returns 200 with status healthy when both DB and Redis are reachable."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch.object(games_module, "get_engine", return_value=mock_engine), \
             patch.object(games_module, "_get_client", return_value=mock_redis):
            result = await games_module._handle_health()

        assert result["statusCode"] == 200
        body = result["body"]
        assert body["status"] == "healthy"
        assert body["checks"]["db"] == "ok"
        assert body["checks"]["redis"] == "ok"
        assert "timestamp" in body

    @pytest.mark.asyncio
    async def test_degraded_when_db_fails(self, games_module):
        """Returns 503 when PostgreSQL connectivity fails."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("Connection refused")

        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch.object(games_module, "get_engine", return_value=mock_engine), \
             patch.object(games_module, "_get_client", return_value=mock_redis):
            result = await games_module._handle_health()

        assert result["statusCode"] == 503
        body = result["body"]
        assert body["status"] == "degraded"
        assert "error" in body["checks"]["db"]
        assert body["checks"]["redis"] == "ok"

    @pytest.mark.asyncio
    async def test_degraded_when_redis_fails(self, games_module):
        """Returns 503 when Redis connectivity fails."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()
        mock_redis.ping.side_effect = Exception("Redis connection refused")

        with patch.object(games_module, "get_engine", return_value=mock_engine), \
             patch.object(games_module, "_get_client", return_value=mock_redis):
            result = await games_module._handle_health()

        assert result["statusCode"] == 503
        body = result["body"]
        assert body["status"] == "degraded"
        assert body["checks"]["db"] == "ok"
        assert "error" in body["checks"]["redis"]

    @pytest.mark.asyncio
    async def test_degraded_when_both_fail(self, games_module):
        """Returns 503 when both DB and Redis connectivity fail."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("DB down")

        mock_redis = AsyncMock()
        mock_redis.ping.side_effect = Exception("Redis down")

        with patch.object(games_module, "get_engine", return_value=mock_engine), \
             patch.object(games_module, "_get_client", return_value=mock_redis):
            result = await games_module._handle_health()

        assert result["statusCode"] == 503
        body = result["body"]
        assert body["status"] == "degraded"
        assert "error" in body["checks"]["db"]
        assert "error" in body["checks"]["redis"]

    @pytest.mark.asyncio
    async def test_response_includes_timestamp_and_checks(self, games_module):
        """Response body includes timestamp (ISO format) and checks dict."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch.object(games_module, "get_engine", return_value=mock_engine), \
             patch.object(games_module, "_get_client", return_value=mock_redis):
            result = await games_module._handle_health()

        body = result["body"]
        assert "timestamp" in body
        # Timestamp should be parseable as ISO format
        from datetime import datetime
        datetime.fromisoformat(body["timestamp"])
        assert isinstance(body["checks"], dict)
        assert "db" in body["checks"]
        assert "redis" in body["checks"]

    @pytest.mark.asyncio
    async def test_cors_methods_restricted_to_get_options(self, games_module):
        """Health check response includes CORS Allow-Methods: GET, OPTIONS."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch.object(games_module, "get_engine", return_value=mock_engine), \
             patch.object(games_module, "_get_client", return_value=mock_redis):
            result = await games_module._handle_health()

        assert result["headers"]["Access-Control-Allow-Methods"] == "GET, OPTIONS"


# ---------------------------------------------------------------------------
# Tests: routing integration
# ---------------------------------------------------------------------------

class TestHealthRouting:
    """Tests that the main() function routes to the health check correctly."""

    @pytest.mark.asyncio
    async def test_health_via_path(self, games_module):
        """GET /health returns health check response."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch.object(games_module, "get_engine", return_value=mock_engine), \
             patch.object(games_module, "_get_client", return_value=mock_redis):
            args = {"__ow_method": "GET", "__ow_path": "/health", "__ow_query": ""}
            result = await games_module.main(args)

        assert result["statusCode"] == 200
        assert result["body"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_via_body_action(self, games_module):
        """POST with body {"action": "health"} returns health check response."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch.object(games_module, "get_engine", return_value=mock_engine), \
             patch.object(games_module, "_get_client", return_value=mock_redis):
            args = {
                "__ow_method": "POST",
                "__ow_path": "/",
                "__ow_query": "",
                "__ow_body": json.dumps({"action": "health"}),
            }
            result = await games_module.main(args)

        assert result["statusCode"] == 200
        assert result["body"]["status"] == "healthy"
