"""Unit tests for WeatherImpactModel.

Tests cover weather tier classification, historical performance lookups
(venue filter, sample-size limit, None-vs-0.0 distinction), abstention
on missing weather data or one-sided no-data, removal of the home
weather bonus, one-team-None substitution, confidence/margin clamping,
and backtest safety.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.models import Game, MatchWeather
from packages.shared.models_ml.prediction import ABSTAINED, Prediction, is_abstained
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


def _make_historical_game(
    game_id,
    home_team,
    away_team,
    home_score,
    away_score,
    venue="Gabba",
):
    """Create a completed historical Game (defaults put it at the Gabba)."""
    return Game(
        id=game_id,
        slug=f"hist-{game_id}",
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        venue=venue,
        date=datetime(2025, 5, 1),
        completed=True,
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
        """Given >= _MIN_SAMPLE_SIZE historical games in the same weather
        tier, return the win rate (2 wins from 3 → 2/3)."""
        db = AsyncMock()

        hist_weather = _make_weather(temperature=22, precipitation=0, wind_gusts=8)
        games = [
            (_make_historical_game(101, "Brisbane", "Sydney", 100, 80), hist_weather),
            (_make_historical_game(102, "Sydney", "Brisbane", 90, 70), hist_weather),
            (_make_historical_game(103, "Brisbane", "Sydney", 95, 70), hist_weather),
        ]

        # db.execute returns list of (Game, MatchWeather) tuples
        db.execute.return_value = _mock_result_all(games)

        wr = await model._get_historical_performance(
            "Gabba",
            "good",
            "Brisbane",
            db,
            before_date=game.date,
        )
        assert wr == pytest.approx(2 / 3)  # Brisbane won 2/3

    @pytest.mark.asyncio
    async def test_thin_sample_below_min_returns_none(self, model, game):
        """REGRESSION: the old code returned a full-strength 0.0/1.0 win
        rate from a 1–2 game sample (driving 0.70-confidence picks). Now
        a thin sample is no usable information → None."""
        db = AsyncMock()

        hist_weather = _make_weather(temperature=22, precipitation=0, wind_gusts=8)
        games = [
            (_make_historical_game(101, "Brisbane", "Sydney", 100, 80), hist_weather),
            (_make_historical_game(102, "Sydney", "Brisbane", 90, 70), hist_weather),
        ]
        db.execute.return_value = _mock_result_all(games)

        wr = await model._get_historical_performance(
            "Gabba",
            "good",
            "Brisbane",
            db,
            before_date=game.date,
        )
        assert wr is None

    @pytest.mark.asyncio
    async def test_zero_win_rate_when_team_lost_all_similar_games(self, model, game):
        """A team that PLAYED and LOST its similar-condition games (at
        least _MIN_SAMPLE_SIZE of them) returns 0.0 — real data,
        deliberately distinct from None (no usable sample)."""
        db = AsyncMock()

        hist_weather = _make_weather(temperature=22, precipitation=0, wind_gusts=8)
        games = [
            (_make_historical_game(102, "Brisbane", "Sydney", 60, 120), hist_weather),
            (_make_historical_game(103, "Sydney", "Brisbane", 110, 50), hist_weather),
            (_make_historical_game(104, "Brisbane", "Sydney", 55, 90), hist_weather),
        ]
        db.execute.return_value = _mock_result_all(games)

        wr = await model._get_historical_performance(
            "Gabba",
            "good",
            "Brisbane",
            db,
            before_date=game.date,
        )
        assert wr == 0.0

    @pytest.mark.asyncio
    async def test_returns_none_when_no_similar_games(self, model, game):
        """No historical games in this weather tier → None (no data)."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([])

        wr = await model._get_historical_performance(
            "Gabba",
            "poor",
            "Brisbane",
            db,
            before_date=game.date,
        )
        assert wr is None

    @pytest.mark.asyncio
    async def test_query_filters_by_venue_and_limits_to_120(self, model, game):
        """The docstring promises games 'at the same venue' — the SQL must
        actually filter on Game.venue == venue.  The recency limit is 120
        (not 30): after venue AND weather-tier filtering, 30 recent games
        left unusable per-tier samples."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([])

        await model._get_historical_performance(
            "Gabba",
            "good",
            "Brisbane",
            db,
            before_date=game.date,
        )

        stmt = db.execute.call_args[0][0]

        # and_() → BooleanClauseList; inspect each clause structurally
        # (avoids compiling the datetime bind under literal_binds).
        def _is_venue_clause(clause):
            left = getattr(clause, "left", None)
            right = getattr(clause, "right", None)
            return (
                getattr(left, "name", None) == "venue"
                and getattr(right, "value", None) == "Gabba"
            )

        assert any(_is_venue_clause(c) for c in stmt.whereclause.clauses), (
            f"expected Game.venue == 'Gabba' filter in query, got: {stmt.whereclause}"
        )

        # LIMIT is compiled as a bound parameter by this SQLAlchemy
        # version — assert on the limit clause's value instead of the
        # rendered SQL string.
        limit_clause = stmt._limit_clause
        assert limit_clause is not None
        assert limit_clause.value == 120


# ---------------------------------------------------------------------------
# predict() — abstention on missing data (no fabricated cold-start picks)
# ---------------------------------------------------------------------------


class TestPredictAbstention:
    @pytest.mark.asyncio
    async def test_no_weather_row_abstains(self, model, game):
        """No MatchWeather row → ABSTAINED, never a default home pick."""
        with patch.object(model, "_get_match_weather", return_value=None):
            result = await model.predict(game, AsyncMock())

        assert result is ABSTAINED
        assert is_abstained(result)

    @pytest.mark.asyncio
    async def test_both_teams_no_similar_games_abstains(self, model, game):
        """Weather exists but NEITHER team has similar-condition history
        → ABSTAINED (the old (home, 0.55, 12) cold-start is gone)."""
        weather = _make_weather()

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(
                model, "_get_historical_performance", return_value=None
            ),
        ):
            result = await model.predict(game, AsyncMock())

        assert result is ABSTAINED
        assert is_abstained(result)

    @pytest.mark.asyncio
    async def test_no_weather_abstains_regardless_of_venue(self, model):
        """Unknown venue + no weather row → ABSTAINED (was a home default)."""
        game = Game(
            id=2,
            slug="test-2",
            home_team="Sydney",
            away_team="Melbourne",
            venue="Unknown Stadium",
            date=datetime(2025, 7, 1),
            completed=False,
        )
        with patch.object(model, "_get_match_weather", return_value=None):
            result = await model.predict(game, AsyncMock())

        assert result is ABSTAINED


# ---------------------------------------------------------------------------
# predict() — 0.0-with-data vs None (no-data) conflation regression
# ---------------------------------------------------------------------------


class TestNoDataVsAllLosses:
    @pytest.mark.asyncio
    async def test_home_lost_all_with_data_away_none_picks_away(self, model, game):
        """REGRESSION: home team LOST all its similar-condition games
        (win rate 0.0 WITH data) while the away team has no data.  The old
        code conflated 0.0 with no-data and returned the (home, 0.55, 12)
        cold start.  Now: away side gets 0.5 → diff = -0.5 → away wins at
        0.70 — a data-driven pick, not a fabricated default."""
        weather = _make_weather()  # good tier, severity 0.5

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                0.0 if team == "Brisbane" else None
            )
            result = await model.predict(game, AsyncMock())

        assert not is_abstained(result)
        assert isinstance(result, Prediction)
        assert result.pick == "Collingwood"
        assert result.probability == pytest.approx(0.70)
        assert result.score_projection == pytest.approx(25, abs=1)

    @pytest.mark.asyncio
    async def test_away_lost_all_with_data_home_none_picks_home(self, model, game):
        """Mirror case: away team 0.0 WITH data, home team None → home
        side substituted with 0.5 → diff = +0.5 → home wins at 0.70."""
        weather = _make_weather()

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                None if team == "Brisbane" else 0.0
            )
            result = await model.predict(game, AsyncMock())

        assert not is_abstained(result)
        assert isinstance(result, Prediction)
        assert result.pick == "Brisbane"
        assert result.probability == pytest.approx(0.70)

    @pytest.mark.asyncio
    async def test_both_teams_lost_all_is_not_abstained(self, model, game):
        """Both teams 0.0 WITH data → real signal (dead heat), NOT the old
        no-data cold-start: away-of-tie pick at coin-flip confidence."""
        weather = _make_weather()

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(
                model, "_get_historical_performance", return_value=0.0
            ),
        ):
            result = await model.predict(game, AsyncMock())

        assert isinstance(result, Prediction)
        assert result.pick == "Collingwood"  # diff == 0 → else branch
        assert result.probability == pytest.approx(0.50)
        assert result.score_projection == 1


# ---------------------------------------------------------------------------
# predict() — one-sided no data: None substitutes as 0.5
# ---------------------------------------------------------------------------


class TestOneSidedNoData:
    @pytest.mark.asyncio
    async def test_home_none_away_has_data_away_wins(self, model, game):
        """Home None → 0.5; away 0.6 → diff = -0.1 → away wins ~0.54."""
        weather = _make_weather()  # good tier, severity 0.5

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                None if team == "Brisbane" else 0.6
            )
            result = await model.predict(game, AsyncMock())

        assert isinstance(result, Prediction)
        assert result.pick == "Collingwood"
        assert result.probability == pytest.approx(0.54)
        assert result.score_projection == pytest.approx(5, abs=1)

    @pytest.mark.asyncio
    async def test_away_none_home_has_data_home_wins(self, model, game):
        """Away None → 0.5; home 0.6 → diff = +0.1 → home wins ~0.54."""
        weather = _make_weather()

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(model, "_get_historical_performance") as mock_perf,
        ):
            mock_perf.side_effect = lambda venue, tier, team, db, before_date: (
                0.6 if team == "Brisbane" else None
            )
            result = await model.predict(game, AsyncMock())

        assert isinstance(result, Prediction)
        assert result.pick == "Brisbane"
        assert result.probability == pytest.approx(0.54)


# ---------------------------------------------------------------------------
# predict() — no home weather bonus
# ---------------------------------------------------------------------------


class TestNoHomeWeatherBonus:
    @pytest.mark.asyncio
    async def test_poor_weather_equal_win_rates_no_home_bonus(self, model, game):
        """In the 'poor' tier with EQUAL win rates the pick must NOT be
        auto-home: the old +0.03 home bonus is gone, so diff == 0 → the
        else branch → away, coin-flip confidence, minimum margin."""
        weather = _make_weather(
            temperature=5.0,  # +1
            precipitation=8.0,  # +2
            wind_gusts=55.0,  # +2
        )  # → "poor"

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(
                model, "_get_historical_performance", return_value=0.5
            ),
        ):
            result = await model.predict(game, AsyncMock())

        assert isinstance(result, Prediction)
        assert result.pick == "Collingwood"  # NOT auto-home
        assert result.probability == pytest.approx(0.50)  # no bonus inflation
        assert result.score_projection == 1

    @pytest.mark.asyncio
    async def test_good_weather_equal_win_rates_away_of_tie(self, model, game):
        """Good weather tier with equal records → away-of-tie (unchanged)."""
        weather = _make_weather(temperature=22, precipitation=0, wind_gusts=8)

        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(
                model, "_get_historical_performance", return_value=0.50
            ),
        ):
            result = await model.predict(game, AsyncMock())

        assert isinstance(result, Prediction)
        assert result.pick == "Collingwood"  # diff == 0 → else branch


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
            result = await model.predict(game, AsyncMock())

        assert result.pick == "Brisbane"
        assert result.probability > 0.55
        assert result.score_projection > 1

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
            result = await model.predict(game, AsyncMock())

        assert result.pick == "Collingwood"
        assert result.probability > 0.50


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
            patch.object(
                model, "_get_historical_performance", return_value=0.5
            ),
        ):
            result = await model.predict(game, AsyncMock())

        assert result.probability >= 0.50

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
            result = await model.predict(game, AsyncMock())

        assert result.probability <= 0.95

    @pytest.mark.asyncio
    async def test_margin_lower_bound(self, model, game):
        """Margin must be at least 1."""
        weather = _make_weather()
        with (
            patch.object(model, "_get_match_weather", return_value=weather),
            patch.object(
                model, "_get_historical_performance", return_value=0.5
            ),
        ):
            result = await model.predict(game, AsyncMock())

        assert result.score_projection >= 1

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
            result = await model.predict(game, AsyncMock())

        assert result.score_projection <= 100


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_error_propagates_for_abstention(self, model, game):
        """Internal errors propagate — the orchestrator abstains (P0-3)."""
        db = AsyncMock()
        db.execute.side_effect = Exception("DB exploded")

        with patch.object(model, "_get_match_weather", side_effect=Exception("boom")):
            with pytest.raises(Exception):
                await model.predict(game, db)
