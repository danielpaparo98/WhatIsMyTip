"""Unit tests for the Admin API DO Function.

Tests the ``main()`` entry point with mocked dependencies.
All admin endpoints require a valid X-API-Key header.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestAdminAuth:
    """Test authentication requirements for admin endpoints."""

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_401(self):
        """Request without X-API-Key header returns 401."""
        from packages.api.admin import main

        result = await main(
            {
                "__ow_method": "POST",
                "__ow_path": "/daily-sync/trigger",
                "__ow_headers": {},
            }
        )
        assert result["statusCode"] == 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(self):
        """Request with wrong API key returns 401."""
        from packages.api.admin import main

        result = await main(
            {
                "__ow_method": "POST",
                "__ow_path": "/daily-sync/trigger",
                "__ow_headers": {"x-api-key": "wrong-key"},
            }
        )
        assert result["statusCode"] == 401

    @pytest.mark.asyncio
    async def test_options_bypasses_auth(self):
        """OPTIONS preflight request bypasses authentication."""
        from packages.api.admin import main

        result = await main(
            {
                "__ow_method": "OPTIONS",
                "__ow_path": "/daily-sync/trigger",
                "__ow_headers": {},
            }
        )
        assert result["statusCode"] == 204


class TestAdminDailySync:
    """Test POST /daily-sync/trigger."""

    @pytest.mark.asyncio
    async def test_daily_sync_trigger_success(self):
        """Valid trigger returns 200 with sync stats."""
        from packages.api.admin import main

        mock_session = AsyncMock()
        mock_stats = {
            "total_games": 9,
            "games_created": 0,
            "games_updated": 3,
            "games_skipped": 6,
            "errors": [],
            "duration_seconds": 1.5,
        }

        with (
            patch("packages.api.admin._get_session_factory") as mock_factory,
            patch("packages.api.admin.close_redis_pool", new_callable=AsyncMock),
            patch("packages.api.admin.verify_api_key", return_value=True),
            patch("packages.api.admin._handle_daily_sync", new_callable=AsyncMock) as mock_handler,
        ):
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_handler.return_value = {"statusCode": 200, "body": mock_stats}

            result = await main(
                {
                    "__ow_method": "POST",
                    "__ow_path": "/daily-sync/trigger",
                    "__ow_headers": {"x-api-key": "test-api-key"},
                    "__ow_body": "{}",
                }
            )

        assert result["statusCode"] == 200
        assert result["body"]["total_games"] == 9


class TestAdminTipGeneration:
    """Test POST /tip-generation/trigger."""

    @pytest.mark.asyncio
    async def test_tip_generation_trigger_success(self):
        """Valid trigger returns 200 with generation stats."""
        from packages.api.admin import main

        mock_session = AsyncMock()
        mock_stats = {
            "success": True,
            "message": "Generated tips for season 2025, round 1",
            "season": 2025,
            "round_id": 1,
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": [],
            "duration_seconds": 2.5,
        }

        with (
            patch("packages.api.admin._get_session_factory") as mock_factory,
            patch("packages.api.admin.close_redis_pool", new_callable=AsyncMock),
            patch("packages.api.admin.verify_api_key", return_value=True),
            patch(
                "packages.api.admin._handle_tip_generation", new_callable=AsyncMock
            ) as mock_handler,
        ):
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_handler.return_value = {"statusCode": 200, "body": mock_stats}

            result = await main(
                {
                    "__ow_method": "POST",
                    "__ow_path": "/tip-generation/trigger",
                    "__ow_headers": {"x-api-key": "test-api-key"},
                    "__ow_body": json.dumps({"season": 2025, "round_id": 1}),
                }
            )

        assert result["statusCode"] == 200
        assert result["body"]["tips_created"] == 27


class TestAdminHistoricRefreshProgress:
    """Test GET /historic-refresh/progress."""

    @pytest.mark.asyncio
    async def test_historic_refresh_progress(self):
        """Returns progress data for active refresh operation."""
        from packages.api.admin import main

        mock_session = AsyncMock()
        mock_progress = {
            "progress_id": 1,
            "operation_type": "historical_generation",
            "total_items": 100,
            "completed_items": 75,
            "status": "in_progress",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": None,
            "error_message": None,
            "progress_percentage": 75.0,
        }

        with (
            patch("packages.api.admin._get_session_factory") as mock_factory,
            patch("packages.api.admin.close_redis_pool", new_callable=AsyncMock),
            patch("packages.api.admin.verify_api_key", return_value=True),
            patch(
                "packages.api.admin._handle_historic_refresh_progress", new_callable=AsyncMock
            ) as mock_handler,
        ):
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_handler.return_value = {"statusCode": 200, "body": mock_progress}

            result = await main(
                {
                    "__ow_method": "GET",
                    "__ow_path": "/historic-refresh/progress",
                    "__ow_headers": {"x-api-key": "test-api-key"},
                }
            )

        assert result["statusCode"] == 200
        assert result["body"]["status"] == "in_progress"


class TestAdminRouting:
    """Test routing edge cases."""

    @pytest.mark.asyncio
    async def test_unknown_route_returns_404(self):
        """Unknown admin routes return 404."""
        from packages.api.admin import main

        mock_session = AsyncMock()

        with (
            patch("packages.api.admin._get_session_factory") as mock_factory,
            patch("packages.api.admin.close_redis_pool", new_callable=AsyncMock),
            patch("packages.api.admin.verify_api_key", return_value=True),
        ):
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main(
                {
                    "__ow_method": "GET",
                    "__ow_path": "/unknown-route",
                    "__ow_headers": {"x-api-key": "test-api-key"},
                }
            )

        assert result["statusCode"] == 404

    @pytest.mark.asyncio
    async def test_match_completion_trigger(self):
        """POST /match-completion/trigger is routed correctly."""
        from packages.api.admin import main

        mock_session = AsyncMock()

        with (
            patch("packages.api.admin._get_session_factory") as mock_factory,
            patch("packages.api.admin.close_redis_pool", new_callable=AsyncMock),
            patch("packages.api.admin.verify_api_key", return_value=True),
            patch(
                "packages.api.admin._handle_match_completion", new_callable=AsyncMock
            ) as mock_handler,
        ):
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_handler.return_value = {"statusCode": 200, "body": {"success": True}}

            result = await main(
                {
                    "__ow_method": "POST",
                    "__ow_path": "/match-completion/trigger",
                    "__ow_headers": {"x-api-key": "test-api-key"},
                    "__ow_body": "{}",
                }
            )

        assert result["statusCode"] == 200

    @pytest.mark.asyncio
    async def test_historic_refresh_trigger(self):
        """POST /historic-refresh/trigger is routed correctly."""
        from packages.api.admin import main

        mock_session = AsyncMock()

        with (
            patch("packages.api.admin._get_session_factory") as mock_factory,
            patch("packages.api.admin.close_redis_pool", new_callable=AsyncMock),
            patch("packages.api.admin.verify_api_key", return_value=True),
            patch(
                "packages.api.admin._handle_historic_refresh", new_callable=AsyncMock
            ) as mock_handler,
        ):
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_handler.return_value = {"statusCode": 200, "body": {"success": True}}

            result = await main(
                {
                    "__ow_method": "POST",
                    "__ow_path": "/historic-refresh/trigger",
                    "__ow_headers": {"x-api-key": "test-api-key"},
                    "__ow_body": "{}",
                }
            )

        assert result["statusCode"] == 200


class TestAdminMetrics:
    """Test GET /metrics endpoint."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        """GET /metrics returns job metrics with auth."""
        from packages.api.admin import main

        mock_session = AsyncMock()

        with (
            patch("packages.api.admin._get_session_factory") as mock_factory,
            patch("packages.api.admin.close_redis_pool", new_callable=AsyncMock),
            patch("packages.api.admin.verify_api_key", return_value=True),
            patch("packages.api.admin._handle_metrics", new_callable=AsyncMock) as mock_handler,
        ):
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_handler.return_value = {
                "statusCode": 200,
                "body": {
                    "metrics": {},
                    "system": {"python_version": "3.12.0", "platform": "Windows"},
                    "alerting_enabled": False,
                },
            }

            result = await main(
                {
                    "__ow_method": "GET",
                    "__ow_path": "/metrics",
                    "__ow_headers": {"x-api-key": "test-api-key"},
                }
            )

        assert result["statusCode"] == 200
        assert "metrics" in result["body"]
        assert "system" in result["body"]
        assert "alerting_enabled" in result["body"]
