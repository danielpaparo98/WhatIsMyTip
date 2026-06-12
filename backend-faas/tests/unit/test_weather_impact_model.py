"""Unit tests for WeatherImpactModel.

Tests cover weather tier classification, historical performance lookups,
cold-start behaviour, confidence/margin clamping, and backtest safety.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.models import Game, MatchWeather
from packages.shared.models_ml.weather_impact import WeatherImpactModel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def model():
    return WeatherImpactModel()


@pytest.fixture
def game():
    return Game(
        id=1,
        slug="test-game",
        home_team="Brisbane",
        away_team="Collingwood",
        venue="Gabba",
        date=datetime(2025, 6, 15, 18, 0, tzinfo=timezone.utc),
        completed=False,
    )


def _make_weather(
    game_id=1,
    temperature=20.0,
    precipitation=0.0,
    wind_speed=5.0,
    wind_gusts=10.0,
    humidity=50,
):
    """Factory helper to create a MatchWeather object."""
    return MatchWeather(
        game_id=game_id,
        venue="Gabba",
        match_date=datetime(2025, 6, 15).date(),
        temperature=temperature,
        precipitation=precipitation,
        wind_speed=wind_speed,
        wind_gusts=wind_gusts,
        humidity=humidity,
        data_type="forecast",
    )


def _mock_scalars_first(return_value):
    """Helper: mock result of db.execute().scalars().first()."""
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = return_value
    return result_mock


def _mock_result_all(return_value):
    """Helper: mock result of db.execute().all()."""
    result_mock = MagicMock()
    result_mock.all.return_value = return_value
    return result_mock


# ---------------------------------------------------------------------------
# Constructor / get_name
# ---------------------------------------------------------------------------


class TestWeatherImpactModelBasics:
    def test_model_instantiation(self, model):
        assert isinstance(model, WeatherImpactModel)

    def test_get_name(self, model):
        assert model.get_name() == "weather_impact"


# ---------------------------------------------------------------------------
# _classify_weather
# ---------------------------------------------------------------------------


class TestClassifyWeather:
    def test_good_weather(self, model):
        """Dry, calm, mild conditions → 'good'."""
        weather = _make_weather(temperature=22, precipitation=0, wind_gusts=8)
        assert model._classify_weather(weather) == "good"

    def test_moderate_light_rain(self, model):
        """Light precipitation but no wind → 'moderate'."""
        weather = _make_weather(temperature=20, precipitation=2.0, wind_gusts=8)
        assert model._classify_weather(weather) == "moderate"

    def test_challenging_heavy_rain(self, model):
        """Heavy precipitation (>5mm) gives score += 2 → 'challenging'."""
        weather = _make_weather(temperature=20, precipitation=6.0, wind_gusts=8)
        assert model._classify_weather(weather) == "challenging"

    def test_challenging_strong_gusts(self, model):
        """Strong gusts (>35) give score += 1 + moderate precip → 'challenging'."""
        weather = _make_weather(temperature=20, precipitation=2.0, wind_gusts=40.0)
        assert model._classify_weather(weather) == "challenging"

    def test_poor_heavy_rain_and_wind(self, model):
        """Heavy rain + strong gusts + extreme temp → 'poor'."""
        weather = _make_weather(
            temperature=5.0,  # < 10 → +1
            precipitation=8.0,  # > 5 → +2
            wind_gusts=55.0,  # > 50 → +2
        )
        # total score = 1 + 2 + 2 = 5 → "poor"
        assert model._classify_weather(weather) == "poor"

    def test_poor_hot_and_windy(self, model):
        """Hot temp (>35) + strong gusts (>50) → 'poor'."""
        weather = _make_weather(
            temperature=38.0,  # > 35 → +1
            precipitation=0.0,
            wind_gusts=55.0,  # > 50 → +2
        )
        # total score = 1 + 2 = 3 → "poor"
        assert model._classify_weather(weather) == "poor"

    def test_null_fields_default_to_good(self, model):
        """All None fields → score stays 0 → 'good'."""
        weather = MatchWeather(
            game_id=1,
            venue="Gabba",
            temperature=None,
            precipitation=None,
            wind_speed=None,
            wind_gusts=None,
            humidity=None,
        )
        assert model._classify_weather(weather) == "good"

    def test_cold_temperature_extreme(self, model):
        """Temperature < 10 contributes +1."""
        weather = _make_weather(temperature=5.0, precipitation=0.0, wind_gusts=8.0)
        # score = 1 → "moderate"
        assert model._classify_weather(weather) == "moderate"

    def test_hot_temperature_extreme(self, model):
        """Temperature > 35 contributes +1."""
        weather = _make_weather(temperature=37.0, precipitation=0.0, wind_gusts=8.0)
        # score = 1 → "moderate"
        assert model._classify_weather(weather) == "moderate"


# ---------------------------------------------------------------------------
# _get_match_weather
# ---------------------------------------------------------------------------


class TestGetMatchWeather:
    @pytest.mark.asyncio
    async def test_returns_weather_when_found(self, model, game):
        db = AsyncMock()
        weather = _make_weather()
        db.execute.return_value = _mock_scalars_first(weather)

        result = await model._get_match_weather(game, db)
        assert result is weather
        assert result.game_id == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, model, game):
        db = AsyncMock()
        db.execute.return_value = _mock_scalars_first(None)

        result = await model._get_match_weather(game, db)
        assert result is None


# ---------------------------------------------------------------------------
# _get_historical_performance
# ---------------------------------------------------------------------------


class TestGetHistoricalPerformance:
    @pytest.mark.asyncio
    async def test_returns_win_rate_for_similar_conditions(self, model, game):
        """Given historical games in the same weather tier, return win rate."""
        db = AsyncMock()

        # Create historical games with weather in "good" tier
        hist_game = MagicMock()
        hist_game.home_team = "Brisbane"
        hist_game.away_team = "Sydney"
        hist_game.home_score = 100
        hist_game.away_score = 80
        hist_game.completed = True
        hist_game.date = datetime(2025, 5, 1)

        hist_weather = _make_weather(temperature=22, precipitation=0, wind_gusts=8)

        # db.execute returns list of (Game, MatchWeather) tuples
        db.execute.return_value = _mock_result_all(
            [
                (hist_game, hist_weather),
            ]
        )

        wr = await model._get_historical_performance(
            "Gabba",
            "good",
            "Brisbane",
            db,
            before_date=game.date,
        )
        assert wr == 1.0  # Brisbane won 1/1

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_similar_games(self, model, game):
        """No historical games in this weather tier → win rate 0."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([])

        wr = await model._get_historical_performance(
            "Gabba",
            "poor",
            "Brisbane",
            db,
            before_date=game.date,
        )
        assert wr == 0.0


# ---------------------------------------------------------------------------
# predict() — cold-start / no data scenarios
# ---------------------------------------------------------------------------


class TestPredictColdStart:
    @pytest.mark.asyncio
    async def test_no_weather_data_returns_cold_start(self, model, game):
        """When no weather forecast exists, return cold-start defaults."""
        db = AsyncMock()
        # First call: get_match_weather → None
        db.execute.return_value = _mock_scalars_first(None)

        with patch.object(model, "_get_match_weather", return_value=None):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"  # home team default
        assert 0.50 <= confidence <= 0.95
        assert 1 <= margin <= 100

    @pytest.mark.asyncio
    async def test_no_historical_weather_returns_cold_start(self, model, game):
        """Weather exists but no historical data → cold start."""
        db = AsyncMock()
        weather = _make_weather()

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance", return_value=0.0),
        ):
            winner, confidence, margin = await model.predict(game, db)

        # Should return cold-start default
        assert winner == "Brisbane"
        assert 0.50 <= confidence <= 0.95
        assert 1 <= margin <= 100


# ---------------------------------------------------------------------------
# predict() — full scenario tests
# ---------------------------------------------------------------------------


class TestPredictScenarios:
    @pytest.mark.asyncio
    async def test_rain_forecast_home_advantage(self, model, game):
        """Rain forecast with home team historically better in wet → home wins."""
        weather = _make_weather(precipitation=8.0, wind_gusts=40.0)

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            # Home team great in wet, away team poor
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                0.75 if team == "Brisbane" else 0.35
            )
            winner, confidence, margin = await model.predict(game, AsyncMock())

        assert winner == "Brisbane"
        assert confidence > 0.55
        assert margin > 1

    @pytest.mark.asyncio
    async def test_wind_forecast_away_better(self, model, game):
        """Windy conditions where away team has better record → away wins."""
        weather = _make_weather(precipitation=0, wind_gusts=45.0)

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            # Away team better in wind
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                0.30 if team == "Brisbane" else 0.70
            )
            winner, confidence, margin = await model.predict(game, AsyncMock())

        assert winner == "Collingwood"
        assert confidence > 0.50

    @pytest.mark.asyncio
    async def test_normal_weather_home_slight_edge(self, model, game):
        """Normal conditions with similar records → close prediction."""
        weather = _make_weather(temperature=22, precipitation=0, wind_gusts=8)

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                0.55 if team == "Brisbane" else 0.50
            )
            winner, confidence, margin = await model.predict(game, AsyncMock())

        assert winner in ("Brisbane", "Collingwood")
        assert 0.50 <= confidence <= 0.95

    @pytest.mark.asyncio
    async def test_good_weather_no_bonus(self, model, game):
        """Good weather tier gives no home weather bonus."""
        weather = _make_weather(temperature=22, precipitation=0, wind_gusts=8)

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            # Exactly equal records
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: 0.50
            winner, confidence, margin = await model.predict(game, AsyncMock())

        # With equal records and good weather (no bonus), adjusted_diff = 0 → away wins  # noqa: E501
        assert winner == "Collingwood"  # away team when diff <= 0


# ---------------------------------------------------------------------------
# Confidence and margin clamping
# ---------------------------------------------------------------------------


class TestClamping:
    @pytest.mark.asyncio
    async def test_confidence_lower_bound(self, model, game):
        """Confidence must be at least 0.50."""
        weather = _make_weather()
        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance", return_value=0.5),
        ):
            winner, confidence, margin = await model.predict(game, AsyncMock())

        assert confidence >= 0.50

    @pytest.mark.asyncio
    async def test_confidence_upper_bound(self, model, game):
        """Confidence must not exceed 0.95."""
        weather = _make_weather(precipitation=10.0, wind_gusts=60.0)

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            # Extreme difference
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                1.0 if team == "Brisbane" else 0.0
            )
            winner, confidence, margin = await model.predict(game, AsyncMock())

        assert confidence <= 0.95

    @pytest.mark.asyncio
    async def test_margin_lower_bound(self, model, game):
        """Margin must be at least 1."""
        weather = _make_weather()
        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance", return_value=0.5),
        ):
            winner, confidence, margin = await model.predict(game, AsyncMock())

        assert margin >= 1

    @pytest.mark.asyncio
    async def test_margin_upper_bound(self, model, game):
        """Margin must not exceed 100."""
        weather = _make_weather(precipitation=10.0, wind_gusts=60.0)

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                1.0 if team == "Brisbane" else 0.0
            )
            winner, confidence, margin = await model.predict(game, AsyncMock())

        assert margin <= 100


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_venue_not_found(self, model):
        """Game with unknown venue still produces a valid prediction."""
        game = Game(
            id=2,
            slug="test-2",
            home_team="Sydney",
            away_team="Melbourne",
            venue="Unknown Stadium",
            date=datetime(2025, 7, 1),
            completed=False,
        )
        db = AsyncMock()
        with patch.object(model, "_get_match_weather", return_value=None):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Sydney"
        assert 0.50 <= confidence <= 0.95
        assert 1 <= margin <= 100

    @pytest.mark.asyncio
    async def test_error_returns_safe_default(self, model, game):
        """Any exception inside predict returns a safe default."""
        db = AsyncMock()
        db.execute.side_effect = Exception("DB exploded")

        with patch.object(model, "_get_match_weather", side_effect=Exception("boom")):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 12

    @pytest.mark.asyncio
    async def test_insufficient_similar_games_uses_fallback(self, model, game):
        """Fewer than 3 similar-condition games → cold-start fallback."""
        weather = _make_weather(precipitation=10.0)

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance", return_value=0.0),
        ):
            winner, confidence, margin = await model.predict(game, AsyncMock())

        # Should fall back to cold-start
        assert winner == "Brisbane"
        assert 0.50 <= confidence <= 0.95
        assert 1 <= margin <= 100
