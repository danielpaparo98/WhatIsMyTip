"""Unit tests for the Games API DO Function.

Tests the ``main()`` entry point by mocking the database session factory,
CRUD operations, and Redis pool. No external dependencies required.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

        # Mock weather query returning no weather data
        mock_weather_result = MagicMock()
        mock_weather_result.scalar_one_or_none.return_value = None

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
            mock_session.execute = AsyncMock(return_value=mock_weather_result)

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
        assert "Access-Control-Allow-Origin" in headers
        assert "GET" in headers["Access-Control-Allow-Methods"]


class TestWeatherInGameDetail:
    """Test weather data integration in game detail endpoint."""

    @pytest.mark.asyncio
    async def test_game_detail_includes_weather_when_available(self):
        """GET /{slug}/detail includes weather data when a MatchWeather record exists."""
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

        # Mock weather row
        mock_weather = MagicMock()
        mock_weather.temperature = 22.5
        mock_weather.precipitation = 0.0
        mock_weather.wind_speed = 15.3
        mock_weather.wind_gusts = 25.0
        mock_weather.wind_direction = 180
        mock_weather.humidity = 55
        mock_weather.weather_code = 1
        mock_weather.data_type = "historical"

        mock_weather_result = MagicMock()
        mock_weather_result.scalar_one_or_none.return_value = mock_weather

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
            mock_session.execute = AsyncMock(return_value=mock_weather_result)

            result = await main({"__ow_method": "GET", "__ow_path": "/abc123def4/detail"})

        assert result["statusCode"] == 200
        body = result["body"]
        assert "weather" in body
        weather = body["weather"]
        assert weather is not None
        assert weather["temperature"] == 22.5
        assert weather["precipitation"] == 0.0
        assert weather["wind_speed"] == 15.3
        assert weather["wind_gusts"] == 25.0
        assert weather["wind_direction"] == 180
        assert weather["humidity"] == 55
        assert weather["weather_code"] == 1
        assert weather["data_type"] == "historical"

    @pytest.mark.asyncio
    async def test_game_detail_weather_null_when_not_available(self):
        """GET /{slug}/detail has null weather when no MatchWeather record exists."""
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

        # No weather record found
        mock_weather_result = MagicMock()
        mock_weather_result.scalar_one_or_none.return_value = None

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
            mock_analysis_crud.get_by_game_id = AsyncMock(return_value=None)
            mock_session.execute = AsyncMock(return_value=mock_weather_result)

            result = await main({"__ow_method": "GET", "__ow_path": "/abc123def4/detail"})

        assert result["statusCode"] == 200
        body = result["body"]
        assert "weather" in body
        assert body["weather"] is None


class TestWeatherResponseSchema:
    """Test WeatherResponse Pydantic schema serialization."""

    def test_weather_response_from_attributes(self):
        """WeatherResponse serializes correctly from a model-like object."""
        from packages.shared.schemas.games import WeatherResponse

        mock_weather = MagicMock()
        mock_weather.temperature = 18.2
        mock_weather.precipitation = 1.5
        mock_weather.wind_speed = 10.0
        mock_weather.wind_gusts = 20.0
        mock_weather.wind_direction = 270
        mock_weather.humidity = 72
        mock_weather.weather_code = 3
        mock_weather.data_type = "forecast"

        resp = WeatherResponse.model_validate(mock_weather)
        assert resp.temperature == 18.2
        assert resp.precipitation == 1.5
        assert resp.wind_speed == 10.0
        assert resp.wind_gusts == 20.0
        assert resp.wind_direction == 270
        assert resp.humidity == 72
        assert resp.weather_code == 3
        assert resp.data_type == "forecast"

    def test_weather_response_defaults(self):
        """WeatherResponse defaults data_type to 'historical' and all fields are optional."""
        from packages.shared.schemas.games import WeatherResponse

        resp = WeatherResponse()
        assert resp.temperature is None
        assert resp.precipitation is None
        assert resp.wind_speed is None
        assert resp.wind_gusts is None
        assert resp.wind_direction is None
        assert resp.humidity is None
        assert resp.weather_code is None
        assert resp.data_type == "historical"

    def test_weather_response_serializes_to_dict(self):
        """WeatherResponse correctly serializes to a dictionary."""
        from packages.shared.schemas.games import WeatherResponse

        resp = WeatherResponse(
            temperature=25.0,
            precipitation=0.0,
            wind_speed=12.5,
            humidity=60,
            weather_code=0,
        )
        data = resp.model_dump()
        assert data["temperature"] == 25.0
        assert data["precipitation"] == 0.0
        assert data["wind_speed"] == 12.5
        assert data["wind_gusts"] is None
        assert data["wind_direction"] is None
        assert data["humidity"] == 60
        assert data["weather_code"] == 0
        assert data["data_type"] == "historical"
