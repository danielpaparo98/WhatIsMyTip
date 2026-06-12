"""Integration tests for API function entry points.

Tests the actual function entry points (main()) with real database and Redis
connections. Requires running infrastructure (start with scripts/dev.sh).

These tests verify:
- Functions don't crash on invocation
- Response shape matches the expected contract
- CORS headers are present
- Health endpoint works without auth
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ALERT_ENABLED", "false")
os.environ.setdefault("ADMIN_API_KEY", "test-api-key")

import pytest

from packages.shared.cache import close_redis_pool
from packages.shared.db import dispose_engine

# Skip entire module if no DB available
pytestmark = pytest.mark.skipif(
    "not config.getoption('--run-integration', default=False)",
    reason="Integration tests require --run-integration flag and running PostgreSQL",
)


@pytest.fixture(autouse=True)
async def _cleanup():
    """Force-cleanup connections after every test."""
    yield
    await close_redis_pool(force=True)
    await dispose_engine(force=True)


class TestGamesAPIIntegration:
    """Integration tests for the games API function."""

    @pytest.mark.asyncio
    async def test_list_games_returns_ok(self):
        """GET /games should return 200 with a list body."""
        from packages.api.games import main

        args = {
            "__ow_method": "GET",
            "__ow_path": "/",
            "__ow_query": {},
            "__ow_headers": {"origin": "http://localhost:3000"},
        }

        result = await main(args)
        assert result["statusCode"] == 200
        # response() returns body as a dict, not JSON string
        body = result["body"]
        assert isinstance(body, (list, dict))

    @pytest.mark.asyncio
    async def test_list_games_has_cors_headers(self):
        """Games API responses should include CORS headers."""
        from packages.api.games import main

        args = {
            "__ow_method": "GET",
            "__ow_path": "/",
            "__ow_query": {},
            "__ow_headers": {"origin": "http://localhost:3000"},
        }

        result = await main(args)
        headers = result.get("headers", {})
        assert "Access-Control-Allow-Origin" in headers
        assert headers.get("Content-Type") == "application/json"

    @pytest.mark.asyncio
    async def test_options_returns_cors_preflight(self):
        """OPTIONS request should return 204 with CORS headers."""
        from packages.api.games import main

        args = {
            "__ow_method": "OPTIONS",
            "__ow_path": "/",
            "__ow_query": {},
            "__ow_headers": {"origin": "http://localhost:3000"},
        }

        result = await main(args)
        assert result["statusCode"] == 204
        assert "Access-Control-Allow-Origin" in result.get("headers", {})

    @pytest.mark.asyncio
    async def test_unknown_path_returns_404(self):
        """GET /games/nonexistent-slug should return 404."""
        from packages.api.games import main

        args = {
            "__ow_method": "GET",
            "__ow_path": "/nonexistent-game-slug",
            "__ow_query": {},
            "__ow_headers": {},
        }

        result = await main(args)
        assert result["statusCode"] == 404


class TestAdminAPIIntegration:
    """Integration tests for the admin API function."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """GET /health should return healthy status without auth."""
        from packages.api.admin import main

        args = {
            "__ow_method": "GET",
            "__ow_path": "/health",
            "__ow_query": {},
            "__ow_headers": {},
        }

        result = await main(args)
        assert result["statusCode"] == 200

        body = result["body"]
        assert body["status"] == "healthy"
        assert "timestamp" in body

    @pytest.mark.asyncio
    async def test_health_has_cors_headers(self):
        """Health endpoint should include CORS headers."""
        from packages.api.admin import main

        args = {
            "__ow_method": "GET",
            "__ow_path": "/health",
            "__ow_query": {},
            "__ow_headers": {"origin": "http://localhost:3000"},
        }

        result = await main(args)
        headers = result.get("headers", {})
        assert "Access-Control-Allow-Origin" in headers

    @pytest.mark.asyncio
    async def test_admin_endpoints_require_auth(self):
        """Admin endpoints (non-health) should require API key."""
        from packages.api.admin import main

        args = {
            "__ow_method": "GET",
            "__ow_path": "/metrics",
            "__ow_query": {},
            "__ow_headers": {},
        }

        result = await main(args)
        assert result["statusCode"] == 401

    @pytest.mark.asyncio
    async def test_admin_endpoints_with_valid_auth(self):
        """Admin endpoints with valid API key should not return 401."""
        from packages.api.admin import main

        args = {
            "__ow_method": "GET",
            "__ow_path": "/metrics",
            "__ow_query": {},
            "__ow_headers": {"x-api-key": "test-api-key"},
        }

        result = await main(args)
        # Should not be 401 (could be 200 or 500 depending on DB state)
        assert result["statusCode"] != 401

    @pytest.mark.asyncio
    async def test_unknown_admin_path_returns_404(self):
        """Unknown admin paths should return 404."""
        from packages.api.admin import main

        args = {
            "__ow_method": "GET",
            "__ow_path": "/nonexistent",
            "__ow_query": {},
            "__ow_headers": {"x-api-key": "test-api-key"},
        }

        result = await main(args)
        assert result["statusCode"] == 404
