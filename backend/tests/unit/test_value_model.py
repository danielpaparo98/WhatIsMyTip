"""Unit tests for ValueModel.

Tests cover historical win-rate aggregation, the raw-rate comparison in
predict (no home adjustment — home advantage is owned by the elo and
home_advantage models), away-of-tie behaviour, cold-start defaults,
confidence/margin clamping, and the point-in-time (no-leakage) filter.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.models import Game
from packages.shared.models_ml.value import ValueModel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def model():
    return ValueModel()


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


def _row(team, total, wins):
    """Factory: one aggregated row as returned by _calculate_win_rates queries."""
    return SimpleNamespace(team=team, total=total, wins=wins)


def _mock_result_all(return_value):
    """Helper: mock result of db.execute().all()."""
    result_mock = MagicMock()
    result_mock.all.return_value = return_value
    return result_mock


def _db_with_stats(home_rows, away_rows):
    """AsyncSession mock whose two execute() calls return home then away stats.

    _calculate_win_rates issues exactly two queries: home games first,
    then away games.
    """
    db = AsyncMock()
    db.execute.side_effect = [_mock_result_all(home_rows), _mock_result_all(away_rows)]
    return db


# ---------------------------------------------------------------------------
# _calculate_win_rates
# ---------------------------------------------------------------------------


class TestCalculateWinRates:
    @pytest.mark.asyncio
    async def test_combines_home_and_away_games(self, model):
        """A team's win rate pools its home and away games."""
        db = _db_with_stats(
            home_rows=[_row("Brisbane", 10, 6)],
            away_rows=[_row("Brisbane", 10, 6), _row("Collingwood", 10, 4)],
        )

        await model._calculate_win_rates(db)

        assert model.team_win_rates["Brisbane"] == pytest.approx(0.6)  # 12/20
        assert model.team_win_rates["Collingwood"] == pytest.approx(0.4)  # 4/10

    @pytest.mark.asyncio
    async def test_team_with_no_games_defaults_to_half(self, model):
        """A team row with zero games (defensive) → win rate 0.5."""
        db = _db_with_stats(home_rows=[_row("Brisbane", 0, 0)], away_rows=[])

        await model._calculate_win_rates(db)

        assert model.team_win_rates["Brisbane"] == 0.5


# ---------------------------------------------------------------------------
# predict() — raw win-rate comparison (no home adjustment)
# ---------------------------------------------------------------------------


class TestPredictRawRateComparison:
    @pytest.mark.asyncio
    async def test_away_team_with_better_record_beats_home(self, model, game):
        """Away team 0.53 vs home team 0.50 → away wins.

        The core regression test: the removed +0.05 home bump would have
        flipped this to a home pick.
        """
        db = _db_with_stats(
            home_rows=[_row("Brisbane", 10, 5)],  # 0.50
            away_rows=[_row("Collingwood", 100, 53)],  # 0.53
        )

        winner, confidence, margin = await model.predict(game, db)

        assert winner == "Collingwood"
        assert confidence == pytest.approx(0.53)  # (0.53 - 0.50) + 0.5
        assert margin == 4  # int(0.03 * 150)

    @pytest.mark.asyncio
    async def test_equal_win_rates_tie_goes_to_away(self, model, game):
        """Equal win rates → strict '>' means away-of-tie, coin-flip values."""
        db = _db_with_stats(
            home_rows=[_row("Brisbane", 10, 5)],  # 0.50
            away_rows=[_row("Collingwood", 10, 5)],  # 0.50
        )

        winner, confidence, margin = await model.predict(game, db)

        assert winner == "Collingwood"
        assert confidence == 0.5  # 0.5 floor — no fake inflation
        assert margin == 1  # margin floor

    @pytest.mark.asyncio
    async def test_home_team_with_better_record_wins(self, model, game):
        """Home team 0.50 vs away team 0.25 → home still wins (no regression)."""
        db = _db_with_stats(
            home_rows=[_row("Brisbane", 16, 8)],  # 0.50
            away_rows=[_row("Collingwood", 16, 4)],  # 0.25
        )

        winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == pytest.approx(0.75)  # (0.50 - 0.25) + 0.5
        assert margin == 37  # int(0.25 * 150)


# ---------------------------------------------------------------------------
# predict() — unknown-team defaults
# ---------------------------------------------------------------------------


class TestPredictUnknownTeams:
    @pytest.mark.asyncio
    async def test_both_teams_unknown_defaults_to_half(self, model, game):
        """No historical games at all → both 0.5 → away-of-tie, coin flip."""
        db = _db_with_stats(home_rows=[], away_rows=[])

        winner, confidence, margin = await model.predict(game, db)

        assert winner == "Collingwood"
        assert confidence == 0.5
        assert margin == 1

    @pytest.mark.asyncio
    async def test_unknown_home_team_gets_half_not_zero(self, model, game):
        """Unknown home team defaults to 0.5 (not 0.0) → beats a 0.25 away team."""
        db = _db_with_stats(
            home_rows=[],  # Brisbane unknown
            away_rows=[_row("Collingwood", 16, 4)],  # 0.25
        )

        winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == pytest.approx(0.75)
        assert margin == 37


# ---------------------------------------------------------------------------
# predict() — clamping and point-in-time safety
# ---------------------------------------------------------------------------


class TestPredictClampingAndSafety:
    @pytest.mark.asyncio
    async def test_confidence_and_margin_clamped(self, model, game):
        """Extreme rates → confidence ≤ 0.9 and margin ≤ 80."""
        db = _db_with_stats(
            home_rows=[_row("Brisbane", 10, 10)],  # 1.00
            away_rows=[_row("Collingwood", 10, 0)],  # 0.00
        )

        winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.9
        assert margin == 80

    @pytest.mark.asyncio
    async def test_predict_only_uses_games_before_game_date(self, model, game):
        """predict passes the game's date as the point-in-time cutoff."""
        model.team_win_rates = {"Brisbane": 0.6, "Collingwood": 0.4}

        with patch.object(
            model, "_calculate_win_rates", new_callable=AsyncMock
        ) as mock_rates:
            await model.predict(game, AsyncMock())

        assert mock_rates.await_args.kwargs["before_date"] == game.date
