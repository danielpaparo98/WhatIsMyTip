"""Unit tests for the Backtest API DO Function.

Tests the ``main()`` entry point by mocking the database session factory,
BacktestService, and Redis pool. No external dependencies required.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


class TestBacktestFunctionRouting:
    """Test the main() routing logic for the backtest function."""

    @pytest.mark.asyncio
    async def test_options_returns_cors_preflight(self):
        """OPTIONS request returns 204 with CORS headers."""
        from packages.api.backtest import main

        result = await main({"__ow_method": "OPTIONS", "__ow_path": "/"})
        assert result["statusCode"] == 204
        assert "Access-Control-Allow-Origin" in result["headers"]

    @pytest.mark.asyncio
    async def test_post_method_not_allowed(self):
        """POST requests return 405."""
        from packages.api.backtest import main

        mock_session = AsyncMock()

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({"__ow_method": "POST", "__ow_path": "/"})

        assert result["statusCode"] == 405

    @pytest.mark.asyncio
    async def test_get_root_deprecated(self):
        """GET / returns deprecated empty results."""
        from packages.api.backtest import main

        mock_session = AsyncMock()

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({"__ow_method": "GET", "__ow_path": "/"})

        assert result["statusCode"] == 200
        assert result["body"]["count"] == 0

    @pytest.mark.asyncio
    async def test_get_current_season(self):
        """GET /current-season returns performance data."""
        from packages.api.backtest import main

        mock_session = AsyncMock()
        mock_performance = {
            "season": 2025,
            "heuristics": {
                "best_bet": {"overall_accuracy": 0.7, "total_profit": 5.0},
            },
        }

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.BacktestService") as mock_service_cls, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_service_cls.return_value.get_current_season_performance = AsyncMock(
                return_value=mock_performance
            )

            result = await main({"__ow_method": "GET", "__ow_path": "/current-season"})

        assert result["statusCode"] == 200

    @pytest.mark.asyncio
    async def test_get_compare_missing_season(self):
        """GET /compare without season param returns 400."""
        from packages.api.backtest import main

        mock_session = AsyncMock()

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({
                "__ow_method": "GET",
                "__ow_path": "/compare",
                "__ow_query": {},
            })

        assert result["statusCode"] == 400
        assert "season" in result["body"]["error"].lower()

    @pytest.mark.asyncio
    async def test_get_compare_with_season(self):
        """GET /compare?season=2025 returns comparison data."""
        from packages.api.backtest import main

        mock_session = AsyncMock()
        mock_comparison = {
            "best_bet": {"overall_accuracy": 0.7, "total_profit": 5.0},
            "yolo": {"overall_accuracy": 0.6, "total_profit": -2.0},
        }

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.BacktestService") as mock_service_cls, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_service_cls.return_value.compare_heuristics = AsyncMock(
                return_value=mock_comparison
            )

            result = await main({
                "__ow_method": "GET",
                "__ow_path": "/compare",
                "__ow_query": {"season": "2025"},
            })

        assert result["statusCode"] == 200
        assert result["body"]["season"] == 2025
        assert "comparison" in result["body"]
        assert "best_overall" in result["body"]

    @pytest.mark.asyncio
    async def test_get_seasons(self):
        """GET /seasons returns available seasons."""
        from packages.api.backtest import main

        mock_session = AsyncMock()

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.BacktestService") as mock_service_cls, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_service_cls.return_value.get_available_seasons = AsyncMock(
                return_value=[2024, 2025]
            )

            result = await main({"__ow_method": "GET", "__ow_path": "/seasons"})

        assert result["statusCode"] == 200
        assert result["body"]["available_years"] == [2024, 2025]

    @pytest.mark.asyncio
    async def test_get_table_missing_season(self):
        """GET /table without season returns 400."""
        from packages.api.backtest import main

        mock_session = AsyncMock()

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({
                "__ow_method": "GET",
                "__ow_path": "/table",
                "__ow_query": {},
            })

        assert result["statusCode"] == 400

    @pytest.mark.asyncio
    async def test_get_by_heuristic_deprecated(self):
        """GET /best_bet (catch-all) returns deprecated empty results."""
        from packages.api.backtest import main

        mock_session = AsyncMock()

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({"__ow_method": "GET", "__ow_path": "/best_bet"})

        assert result["statusCode"] == 200
        assert result["body"]["count"] == 0

    @pytest.mark.asyncio
    async def test_unknown_route_returns_404(self):
        """Unknown routes return 404."""
        from packages.api.backtest import main

        mock_session = AsyncMock()

        with patch("packages.api.backtest._get_session_factory") as mock_factory, \
             patch("packages.api.backtest.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({"__ow_method": "GET", "__ow_path": "/unknown/path/here"})

        assert result["statusCode"] == 404
