"""Unit tests for the Tips API DO Function.

Tests the ``main()`` entry point by mocking the database session factory,
CRUD operations, and Redis pool. No external dependencies required.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_tip_mock(**overrides):
    """Create a mock Tip-like object."""
    defaults = {
        "id": 1,
        "game_id": 1,
        "heuristic": "best_bet",
        "selected_team": "Brisbane",
        "margin": 12,
        "confidence": 0.75,
        "explanation": "Strong at home",
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    tip = MagicMock()
    for k, v in defaults.items():
        setattr(tip, k, v)
    return tip


def _make_game_mock(**overrides):
    """Create a mock Game-like object."""
    defaults = {
        "id": 1,
        "slug": "abc123def4",
        "squiggle_id": 12345,
        "round_id": 1,
        "season": 2025,
        "home_team": "Brisbane",
        "away_team": "Collingwood",
        "home_score": 85,
        "away_score": 72,
        "venue": "Gabba",
        "date": datetime(2025, 3, 15, 18, 0, tzinfo=timezone.utc),
        "completed": True,
    }
    defaults.update(overrides)
    game = MagicMock()
    for k, v in defaults.items():
        setattr(game, k, v)
    return game


class TestTipsFunctionRouting:
    """Test the main() routing logic for the tips function."""

    @pytest.mark.asyncio
    async def test_options_returns_cors_preflight(self):
        """OPTIONS request returns 204 with CORS headers."""
        from packages.api.tips import main

        result = await main({"__ow_method": "OPTIONS", "__ow_path": "/"})
        assert result["statusCode"] == 204
        assert "Access-Control-Allow-Origin" in result["headers"]

    @pytest.mark.asyncio
    async def test_get_root_lists_tips(self):
        """GET / returns a list of tips (default best_bet)."""
        from packages.api.tips import main

        mock_session = AsyncMock()
        mock_tips = [_make_tip_mock()]

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.TipCRUD") as mock_crud, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_crud.get_by_heuristic = AsyncMock(return_value=mock_tips)

            result = await main({"__ow_method": "GET", "__ow_path": "/"})

        assert result["statusCode"] == 200
        assert "body" in result

    @pytest.mark.asyncio
    async def test_get_tips_by_valid_heuristic(self):
        """GET /best_bet returns tips for that heuristic."""
        from packages.api.tips import main

        mock_session = AsyncMock()
        mock_tips = [_make_tip_mock(heuristic="best_bet")]

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.TipCRUD") as mock_crud, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_crud.get_by_heuristic = AsyncMock(return_value=mock_tips)

            result = await main({"__ow_method": "GET", "__ow_path": "/best_bet"})

        assert result["statusCode"] == 200

    @pytest.mark.asyncio
    async def test_get_tips_by_invalid_heuristic_returns_404(self):
        """GET /invalid_heuristic falls through to 404 (not a valid route)."""
        from packages.api.tips import main

        mock_session = AsyncMock()

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({"__ow_method": "GET", "__ow_path": "/invalid_heuristic"})

        # Invalid heuristic is not in VALID_HEURISTICS so it doesn't match
        # the /{heuristic} route and falls through to 404
        assert result["statusCode"] == 404

    @pytest.mark.asyncio
    async def test_get_games_with_tips_missing_params(self):
        """GET /games-with-tips without season/round returns 400."""
        from packages.api.tips import main

        mock_session = AsyncMock()

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({
                "__ow_method": "GET",
                "__ow_path": "/games-with-tips",
                "__ow_query": "",
            })

        assert result["statusCode"] == 400

    @pytest.mark.asyncio
    async def test_post_generate_missing_api_key_returns_401(self):
        """POST /generate without API key returns 401."""
        from packages.api.tips import main

        mock_session = AsyncMock()

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({
                "__ow_method": "POST",
                "__ow_path": "/generate",
                "__ow_body": json.dumps({"season": 2025, "round": 1}),
                "__ow_query": "",
                "__ow_headers": {},
            })

        assert result["statusCode"] == 401

    @pytest.mark.asyncio
    async def test_post_generate_invalid_api_key_returns_401(self):
        """POST /generate with wrong API key returns 401."""
        from packages.api.tips import main

        mock_session = AsyncMock()

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({
                "__ow_method": "POST",
                "__ow_path": "/generate",
                "__ow_body": json.dumps({"season": 2025, "round": 1}),
                "__ow_query": "",
                "__ow_headers": {"x-api-key": "wrong-key"},
            })

        assert result["statusCode"] == 401

    @pytest.mark.asyncio
    async def test_post_generate_missing_params(self):
        """POST /generate without season/round returns 422 (validation error)."""
        from packages.api.tips import main

        mock_session = AsyncMock()

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({
                "__ow_method": "POST",
                "__ow_path": "/generate",
                "__ow_body": "{}",
                "__ow_query": "",
                "__ow_headers": {"x-api-key": "test-api-key"},
            })

        assert result["statusCode"] == 422

    @pytest.mark.asyncio
    async def test_post_generate_no_games_found(self):
        """POST /generate with valid params but no games returns 404."""
        from packages.api.tips import main

        mock_session = AsyncMock()

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.GameCRUD") as mock_game_crud, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_game_crud.get_by_round = AsyncMock(return_value=[])

            result = await main({
                "__ow_method": "POST",
                "__ow_path": "/generate",
                "__ow_body": json.dumps({"season": 2025, "round": 1}),
                "__ow_query": "",
                "__ow_headers": {"x-api-key": "test-api-key"},
            })

        assert result["statusCode"] == 404

    @pytest.mark.asyncio
    async def test_post_generate_success(self):
        """POST /generate with valid params and API key returns 200 with stats."""
        from packages.api.tips import main

        mock_session = AsyncMock()
        mock_games = [_make_game_mock()]

        mock_stats = {
            "games_processed": 1,
            "tips_created": 3,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 4,
            "model_predictions_updated": 0,
            "errors": [],
        }

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.GameCRUD") as mock_game_crud, \
             patch("packages.api.tips.TipGenerationService") as mock_service, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_game_crud.get_by_round = AsyncMock(return_value=mock_games)
            mock_service.return_value.generate_for_round = AsyncMock(return_value=mock_stats)

            result = await main({
                "__ow_method": "POST",
                "__ow_path": "/generate",
                "__ow_body": json.dumps({"season": 2025, "round": 1}),
                "__ow_query": "",
                "__ow_headers": {"x-api-key": "test-api-key"},
            })

        assert result["statusCode"] == 200
        assert result["body"]["tips_created"] == 3

    @pytest.mark.asyncio
    async def test_unknown_route_returns_404(self):
        """Unknown routes return 404."""
        from packages.api.tips import main

        mock_session = AsyncMock()

        with patch("packages.api.tips._get_session_factory") as mock_factory, \
             patch("packages.api.tips.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({"__ow_method": "GET", "__ow_path": "/unknown/path"})

        assert result["statusCode"] == 404
