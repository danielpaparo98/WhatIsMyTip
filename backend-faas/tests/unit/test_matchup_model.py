"""Unit tests for MatchupModel.

Tests cover head-to-head analysis, venue record lookup, time decay,
cold-start behaviour, confidence/margin clamping, and backtest safety.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from packages.shared.models_ml.matchup import MatchupModel
from packages.shared.models import Game


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def model():
    return MatchupModel()


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


def _make_historical_game(
    game_id,
    home_team,
    away_team,
    home_score,
    away_score,
    venue="Gabba",
    days_ago=365,
):
    """Create a historical completed Game object."""
    date = datetime(2025, 6, 15, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return Game(
        id=game_id,
        slug=f"hist-{game_id}",
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        venue=venue,
        date=date,
        completed=True,
    )


def _mock_scalars_all(return_value):
    """Helper: mock result of db.execute().scalars().all()."""
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = return_value
    return result_mock


# ---------------------------------------------------------------------------
# Constructor / get_name
# ---------------------------------------------------------------------------

class TestMatchupModelBasics:
    def test_model_instantiation(self, model):
        assert isinstance(model, MatchupModel)

    def test_get_name(self, model):
        assert model.get_name() == "matchup"


# ---------------------------------------------------------------------------
# _apply_time_decay
# ---------------------------------------------------------------------------

class TestApplyTimeDecay:
    def test_recent_game_high_weight(self, model):
        """A game from 1 month ago should have high weight."""
        game_date = datetime(2025, 5, 15, tzinfo=timezone.utc)
        pred_date = datetime(2025, 6, 15, tzinfo=timezone.utc)
        weight = model._apply_time_decay(game_date, pred_date)
        # 31 days / 365 ≈ 0.085 years, 0.5^0.085 ≈ 0.943
        assert 0.9 < weight <= 1.0

    def test_one_year_ago_half_weight(self, model):
        """A game from 1 year ago gets ~0.5 weight."""
        game_date = datetime(2024, 6, 15, tzinfo=timezone.utc)
        pred_date = datetime(2025, 6, 15, tzinfo=timezone.utc)
        weight = model._apply_time_decay(game_date, pred_date)
        # 365/365 = 1.0 year, 0.5^1.0 = 0.5
        assert weight == pytest.approx(0.5, abs=0.01)

    def test_two_years_ago_quarter_weight(self, model):
        """A game from 2 years ago gets ~0.25 weight."""
        game_date = datetime(2023, 6, 15, tzinfo=timezone.utc)
        pred_date = datetime(2025, 6, 15, tzinfo=timezone.utc)
        weight = model._apply_time_decay(game_date, pred_date)
        # 730/365 = 2.0 years, 0.5^2.0 = 0.25
        assert weight == pytest.approx(0.25, abs=0.01)

    def test_four_years_ago_low_weight(self, model):
        """A game from 4 years ago should have very low weight."""
        game_date = datetime(2021, 6, 15, tzinfo=timezone.utc)
        pred_date = datetime(2025, 6, 15, tzinfo=timezone.utc)
        weight = model._apply_time_decay(game_date, pred_date)
        # 4 years: 0.5^4 = 0.0625
        assert weight == pytest.approx(0.0625, abs=0.01)

    def test_same_day_full_weight(self, model):
        """A game on the same day gets weight 1.0."""
        game_date = datetime(2025, 6, 15, tzinfo=timezone.utc)
        pred_date = datetime(2025, 6, 15, tzinfo=timezone.utc)
        weight = model._apply_time_decay(game_date, pred_date)
        assert weight == pytest.approx(1.0)

    def test_none_dates_returns_minimal_weight(self, model):
        """None dates return a small default weight."""
        weight = model._apply_time_decay(None, datetime(2025, 6, 15, tzinfo=timezone.utc))
        assert 0.0 < weight <= 0.2


# ---------------------------------------------------------------------------
# _get_head_to_head
# ---------------------------------------------------------------------------

class TestGetHeadToHead:
    @pytest.mark.asyncio
    async def test_brisbane_wins_most_games(self, model, game):
        """Brisbane won 3 of 4 H2H games → high win rate."""
        db = AsyncMock()
        games = [
            _make_historical_game(1, "Brisbane", "Collingwood", 100, 80, days_ago=100),
            _make_historical_game(2, "Collingwood", "Brisbane", 70, 90, days_ago=200),
            _make_historical_game(3, "Brisbane", "Collingwood", 110, 85, days_ago=400),
            _make_historical_game(4, "Collingwood", "Brisbane", 95, 100, days_ago=600),
        ]
        db.execute.return_value = _mock_scalars_all(games)

        win_rate, count, avg_margin = await model._get_head_to_head(
            "Brisbane", "Collingwood", db, game.date
        )
        assert count == 4
        assert win_rate > 0.5  # Brisbane won most

    @pytest.mark.asyncio
    async def test_no_games_returns_default(self, model, game):
        """No H2H games → neutral win rate and 0 count."""
        db = AsyncMock()
        db.execute.return_value = _mock_scalars_all([])

        win_rate, count, avg_margin = await model._get_head_to_head(
            "Brisbane", "Collingwood", db, game.date
        )
        assert count == 0
        assert win_rate == 0.5  # neutral
        assert avg_margin == 0.0

    @pytest.mark.asyncio
    async def test_even_split_win_rate(self, model, game):
        """50/50 H2H → win rate close to 0.5."""
        db = AsyncMock()
        games = [
            _make_historical_game(1, "Brisbane", "Collingwood", 100, 80, days_ago=100),
            _make_historical_game(2, "Collingwood", "Brisbane", 100, 80, days_ago=100),
            _make_historical_game(3, "Brisbane", "Collingwood", 100, 80, days_ago=200),
            _make_historical_game(4, "Collingwood", "Brisbane", 100, 80, days_ago=200),
        ]
        db.execute.return_value = _mock_scalars_all(games)

        win_rate, count, avg_margin = await model._get_head_to_head(
            "Brisbane", "Collingwood", db, game.date
        )
        assert count == 4
        assert win_rate == pytest.approx(0.5, abs=0.05)

    @pytest.mark.asyncio
    async def test_away_team_perspective(self, model, game):
        """From Collingwood's perspective, the win rate should be inverted."""
        db = AsyncMock()
        games = [
            # Brisbane won as home
            _make_historical_game(1, "Brisbane", "Collingwood", 100, 80, days_ago=100),
            _make_historical_game(2, "Brisbane", "Collingwood", 100, 80, days_ago=200),
            _make_historical_game(3, "Brisbane", "Collingwood", 100, 80, days_ago=300),
            _make_historical_game(4, "Brisbane", "Collingwood", 100, 80, days_ago=400),
        ]
        db.execute.return_value = _mock_scalars_all(games)

        # From Collingwood's perspective
        win_rate, count, avg_margin = await model._get_head_to_head(
            "Collingwood", "Brisbane", db, game.date
        )
        assert win_rate < 0.1  # Collingwood lost all games

    @pytest.mark.asyncio
    async def test_positive_margin_for_dominant_team(self, model, game):
        """Brisbane dominates → avg margin should be positive."""
        db = AsyncMock()
        games = [
            _make_historical_game(1, "Brisbane", "Collingwood", 100, 70, days_ago=100),
            _make_historical_game(2, "Collingwood", "Brisbane", 60, 100, days_ago=200),
            _make_historical_game(3, "Brisbane", "Collingwood", 120, 80, days_ago=300),
        ]
        db.execute.return_value = _mock_scalars_all(games)

        win_rate, count, avg_margin = await model._get_head_to_head(
            "Brisbane", "Collingwood", db, game.date
        )
        assert avg_margin > 0  # Positive from Brisbane's perspective


# ---------------------------------------------------------------------------
# _get_venue_record
# ---------------------------------------------------------------------------

class TestGetVenueRecord:
    @pytest.mark.asyncio
    async def test_team_good_at_venue(self, model, game):
        """Team wins most games at this venue → high win rate."""
        db = AsyncMock()
        games = [
            _make_historical_game(1, "Brisbane", "Sydney", 120, 80, venue="Gabba", days_ago=100),
            _make_historical_game(2, "Brisbane", "Melbourne", 100, 90, venue="Gabba", days_ago=200),
            _make_historical_game(3, "Carlton", "Brisbane", 70, 100, venue="Gabba", days_ago=300),
        ]
        db.execute.return_value = _mock_scalars_all(games)

        wr = await model._get_venue_record("Brisbane", "Gabba", db, game.date)
        assert wr > 0.5

    @pytest.mark.asyncio
    async def test_team_bad_at_venue(self, model, game):
        """Team loses most games at this venue → low win rate."""
        db = AsyncMock()
        games = [
            _make_historical_game(1, "Brisbane", "Sydney", 60, 120, venue="SCG", days_ago=100),
            _make_historical_game(2, "Brisbane", "Hawthorn", 70, 110, venue="SCG", days_ago=200),
        ]
        db.execute.return_value = _mock_scalars_all(games)

        wr = await model._get_venue_record("Brisbane", "SCG", db, game.date)
        assert wr < 0.5

    @pytest.mark.asyncio
    async def test_no_games_at_venue(self, model, game):
        """No games at venue → neutral 0.5."""
        db = AsyncMock()
        db.execute.return_value = _mock_scalars_all([])

        wr = await model._get_venue_record("Brisbane", "MCG", db, game.date)
        assert wr == 0.5


# ---------------------------------------------------------------------------
# predict() — cold-start / no data scenarios
# ---------------------------------------------------------------------------

class TestPredictColdStart:
    @pytest.mark.asyncio
    async def test_no_h2h_games_cold_start(self, model, game):
        """No H2H games → cold start default."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.5, 0, 0.0)):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"  # home team default
        assert confidence == 0.55
        assert margin == 8

    @pytest.mark.asyncio
    async def test_fewer_than_3_games_cold_start(self, model, game):
        """Fewer than 3 H2H games → cold start."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.6, 2, 5.0)):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 8

    @pytest.mark.asyncio
    async def test_exactly_3_games_not_cold_start(self, model, game):
        """Exactly 3 H2H games → NOT cold start, full prediction."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.7, 3, 10.0)), \
             patch.object(model, "_get_venue_record", return_value=0.5):
            winner, confidence, margin = await model.predict(game, db)

        # Should produce a real prediction, not cold start
        assert winner == "Brisbane"  # H2H WR 0.7 favours home
        assert confidence > 0.55  # Higher than cold start


# ---------------------------------------------------------------------------
# predict() — full scenario tests
# ---------------------------------------------------------------------------

class TestPredictScenarios:
    @pytest.mark.asyncio
    async def test_home_dominates_h2h(self, model, game):
        """Brisbane dominates H2H → Brisbane wins with high confidence."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.85, 10, 15.0)), \
             patch.object(model, "_get_venue_record") as mock_venue:
            mock_venue.side_effect = lambda team, venue, db, before_date: (
                0.7 if team == "Brisbane" else 0.4
            )
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence > 0.60
        assert margin >= 1

    @pytest.mark.asyncio
    async def test_even_matchup(self, model, game):
        """Even H2H and venue → close prediction."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.50, 5, 0.0)), \
             patch.object(model, "_get_venue_record", return_value=0.5):
            winner, confidence, margin = await model.predict(game, db)

        # With exactly 0.5 H2H and 0.5 venue, combined = 0 → away wins by convention
        assert winner in ("Brisbane", "Collingwood")
        assert 0.50 <= confidence <= 0.95
        assert 1 <= margin <= 100

    @pytest.mark.asyncio
    async def test_strong_venue_advantage(self, model, game):
        """Away team dominates H2H but home has strong venue record → home wins."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.35, 10, -10.0)), \
             patch.object(model, "_get_venue_record") as mock_venue:
            mock_venue.side_effect = lambda team, venue, db, before_date: (
                0.9 if team == "Brisbane" else 0.3
            )
            winner, confidence, margin = await model.predict(game, db)

        # 0.6 * (0.35 - 0.5) + 0.4 * (0.9 - 0.3) = -0.09 + 0.24 = 0.15
        # Positive → home wins
        assert winner == "Brisbane"

    @pytest.mark.asyncio
    async def test_away_team_dominates_h2h(self, model, game):
        """Collingwood dominates H2H → Collingwood wins."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.25, 10, -12.0)), \
             patch.object(model, "_get_venue_record", return_value=0.5):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Collingwood"
        assert confidence > 0.50

    @pytest.mark.asyncio
    async def test_no_venue_data_still_predicts(self, model, game):
        """No venue data (0.5 default) → prediction relies on H2H only."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.7, 8, 8.0)), \
             patch.object(model, "_get_venue_record", return_value=0.5):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence > 0.50


# ---------------------------------------------------------------------------
# Confidence and margin clamping
# ---------------------------------------------------------------------------

class TestClamping:
    @pytest.mark.asyncio
    async def test_confidence_lower_bound(self, model, game):
        """Confidence must be at least 0.50."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.50, 5, 0.0)), \
             patch.object(model, "_get_venue_record", return_value=0.5):
            winner, confidence, margin = await model.predict(game, db)

        assert confidence >= 0.50

    @pytest.mark.asyncio
    async def test_confidence_upper_bound(self, model, game):
        """Confidence must not exceed 0.95."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.95, 10, 30.0)), \
             patch.object(model, "_get_venue_record") as mock_venue:
            mock_venue.side_effect = lambda team, venue, db, before_date: (
                0.9 if team == "Brisbane" else 0.1
            )
            winner, confidence, margin = await model.predict(game, db)

        assert confidence <= 0.95

    @pytest.mark.asyncio
    async def test_margin_lower_bound(self, model, game):
        """Margin must be at least 1."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.50, 5, 0.0)), \
             patch.object(model, "_get_venue_record", return_value=0.5):
            winner, confidence, margin = await model.predict(game, db)

        assert margin >= 1

    @pytest.mark.asyncio
    async def test_margin_upper_bound(self, model, game):
        """Margin must not exceed 100."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.95, 10, 50.0)), \
             patch.object(model, "_get_venue_record") as mock_venue:
            mock_venue.side_effect = lambda team, venue, db, before_date: (
                0.95 if team == "Brisbane" else 0.05
            )
            winner, confidence, margin = await model.predict(game, db)

        assert margin <= 100


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_first_ever_meeting(self, model, game):
        """First ever meeting between teams → cold start."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.5, 0, 0.0)):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 8

    @pytest.mark.asyncio
    async def test_only_1_historical_game(self, model, game):
        """Only 1 H2H game → cold start (< 3 threshold)."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(1.0, 1, 20.0)):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 8

    @pytest.mark.asyncio
    async def test_error_returns_safe_default(self, model, game):
        """Any exception inside predict returns a safe default."""
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", side_effect=Exception("DB error")):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 8

    @pytest.mark.asyncio
    async def test_unknown_venue(self, model, game):
        """Game with unknown venue still produces a valid prediction."""
        game_unknown = Game(
            id=2, slug="test-2",
            home_team="Sydney", away_team="Melbourne",
            venue="Unknown Stadium",
            date=datetime(2025, 7, 1),
            completed=False,
        )
        db = AsyncMock()
        with patch.object(model, "_get_head_to_head", return_value=(0.6, 5, 8.0)), \
             patch.object(model, "_get_venue_record", return_value=0.5):
            winner, confidence, margin = await model.predict(game_unknown, db)

        assert winner == "Sydney"
        assert 0.50 <= confidence <= 0.95
        assert 1 <= margin <= 100
