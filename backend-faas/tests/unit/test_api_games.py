"""Unit tests for the Games API DO Function.

Tests the ``main()`` entry point by mocking the database session factory,
CRUD operations, and Redis pool. No external dependencies required.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# The games function uses sys.path manipulation so we import the module
# after patching the session factory.


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
        "home_score": None,
        "away_score": None,
        "venue": "Gabba",
        "date": datetime(2025, 3, 15, 18, 0, tzinfo=timezone.utc),
        "completed": False,
        "predictions_generated": False,
        "tips_generated": False,
    }
    defaults.update(overrides)
    game = MagicMock()
    for k, v in defaults.items():
        setattr(game, k, v)
    return game


class TestGamesFunctionRouting:
    """Test the main() routing logic for the games function."""

    @pytest.mark.asyncio
    async def test_options_returns_cors_preflight(self):
        """OPTIONS request returns 204 with CORS headers."""
        from packages.api.games import main

        result = await main({"__ow_method": "OPTIONS", "__ow_path": "/"})
        assert result["statusCode"] == 204
        assert "Access-Control-Allow-Origin" in result["headers"]

    @pytest.mark.asyncio
    async def test_get_root_lists_games(self):
        """GET / returns a list of upcoming games."""
        from packages.api.games import main

        mock_session = AsyncMock()
        mock_games = [_make_game_mock()]

        with patch("packages.api.games._get_session_factory") as mock_factory, \
             patch("packages.api.games.GameCRUD") as mock_crud, \
             patch("packages.api.games.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_crud.get_upcoming = AsyncMock(return_value=mock_games)

            result = await main({"__ow_method": "GET", "__ow_path": "/"})

        assert result["statusCode"] == 200
        assert "body" in result

    @pytest.mark.asyncio
    async def test_get_by_slug_found(self):
        """GET /{slug} returns the game when found."""
        from packages.api.games import main

        mock_session = AsyncMock()
        mock_game = _make_game_mock()

        with patch("packages.api.games._get_session_factory") as mock_factory, \
             patch("packages.api.games.GameCRUD") as mock_crud, \
             patch("packages.api.games.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_crud.get_by_slug = AsyncMock(return_value=mock_game)

            result = await main({"__ow_method": "GET", "__ow_path": "/abc123def4"})

        assert result["statusCode"] == 200

    @pytest.mark.asyncio
    async def test_get_by_slug_not_found(self):
        """GET /{slug} returns 404 when game not found."""
        from packages.api.games import main

        mock_session = AsyncMock()

        with patch("packages.api.games._get_session_factory") as mock_factory, \
             patch("packages.api.games.GameCRUD") as mock_crud, \
             patch("packages.api.games.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_crud.get_by_slug = AsyncMock(return_value=None)

            result = await main({"__ow_method": "GET", "__ow_path": "/nonexistent"})

        assert result["statusCode"] == 404
        assert "not found" in result["body"]["error"].lower()

    @pytest.mark.asyncio
    async def test_get_game_detail(self):
        """GET /{slug}/detail returns full game detail."""
        from packages.api.games import main

        mock_session = AsyncMock()
        mock_game = _make_game_mock()
        mock_tip = MagicMock(
            id=1, heuristic="best_bet", selected_team="Brisbane",
            margin=12, confidence=0.75, explanation="Strong at home",
            game_id=1, created_at=datetime.now(timezone.utc),
        )
        mock_prediction = MagicMock(
            model_name="elo", winner="Brisbane", confidence=0.7, margin=12,
        )
        mock_analysis = MagicMock(
            id=1, game_id=1, analysis_text="Some analysis",
            created_at=datetime.now(timezone.utc),
        )

        with patch("packages.api.games._get_session_factory") as mock_factory, \
             patch("packages.api.games.GameCRUD") as mock_game_crud, \
             patch("packages.api.games.TipCRUD") as mock_tip_crud, \
             patch("packages.api.games.ModelPredictionCRUD") as mock_pred_crud, \
             patch("packages.api.games.MatchAnalysisCRUD") as mock_analysis_crud, \
             patch("packages.api.games.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_game_crud.get_by_slug = AsyncMock(return_value=mock_game)
            mock_tip_crud.get_by_game = AsyncMock(return_value=[mock_tip])
            mock_pred_crud.get_by_game = AsyncMock(return_value=[mock_prediction])
            mock_analysis_crud.get_by_game_id = AsyncMock(return_value=mock_analysis)

            result = await main({"__ow_method": "GET", "__ow_path": "/abc123def4/detail"})

        assert result["statusCode"] == 200
        body = result["body"]
        assert "game" in body
        assert "tips" in body
        assert "model_predictions" in body

    @pytest.mark.asyncio
    async def test_get_game_detail_not_found(self):
        """GET /{slug}/detail returns 404 when game not found."""
        from packages.api.games import main

        mock_session = AsyncMock()

        with patch("packages.api.games._get_session_factory") as mock_factory, \
             patch("packages.api.games.GameCRUD") as mock_crud, \
             patch("packages.api.games.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_crud.get_by_slug = AsyncMock(return_value=None)

            result = await main({"__ow_method": "GET", "__ow_path": "/nonexistent/detail"})

        assert result["statusCode"] == 404

    @pytest.mark.asyncio
    async def test_unknown_route_returns_404(self):
        """GET /{slug}/invalid returns 404."""
        from packages.api.games import main

        mock_session = AsyncMock()

        with patch("packages.api.games._get_session_factory") as mock_factory, \
             patch("packages.api.games.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({"__ow_method": "GET", "__ow_path": "/abc/invalid"})

        assert result["statusCode"] == 404

    @pytest.mark.asyncio
    async def test_cors_headers_present(self):
        """All responses include CORS headers."""
        from packages.api.games import main

        with patch("packages.api.games._get_session_factory") as mock_factory, \
             patch("packages.api.games.close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await main({"__ow_method": "OPTIONS", "__ow_path": "/"})

        headers = result["headers"]
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert "GET" in headers["Access-Control-Allow-Methods"]
