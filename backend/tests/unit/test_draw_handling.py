"""Draw-semantics regression tests (P0-5).

AFL has draws.  Three places treated a drawn game as a decisive
result:

* ``FormModel`` counted a draw as a loss;
* the backtest scorer credited the AWAY team as the winner of a draw
  (``home > away else away``), so an away tip on a drawn game scored
  as CORRECT;
* the Elo rating walk scored a draw as a full home loss (0.0) instead
  of the standard half score (0.5).

A tip on a drawn game must never be correct — neither side won.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.models_ml.elo import EloModel
from packages.shared.models_ml.form import FormModel
from packages.shared.services.backtest import STAKE_PER_GAME, BacktestService


def _drawn_game(*, team_at_home: bool = True):
    return SimpleNamespace(
        home_team="Home" if team_at_home else "Away",
        away_team="Away" if team_at_home else "Home",
        home_score=50,
        away_score=50,
        date=datetime(2026, 4, 1, 10, 0),
    )


def _result(rows, scalar_rows=None) -> MagicMock:
    r = MagicMock()
    r.all.return_value = rows
    if scalar_rows is not None:
        r.scalars.return_value.all.return_value = scalar_rows
    return r


class TestFormModelDraws:
    @pytest.mark.asyncio
    async def test_draw_counted_as_draw_not_loss(self):
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            return_value=_result([], scalar_rows=[_drawn_game(team_at_home=True)])
        )

        form = await FormModel()._get_recent_form(db, "Home", datetime(2026, 5, 1))

        assert form["draws"] == 1
        assert form["wins"] == 0
        assert form["losses"] == 0

    @pytest.mark.asyncio
    async def test_away_draw_counted_as_draw_not_loss(self):
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            return_value=_result([], scalar_rows=[_drawn_game(team_at_home=False)])
        )

        form = await FormModel()._get_recent_form(db, "Home", datetime(2026, 5, 1))

        assert form["draws"] == 1
        assert form["losses"] == 0


class TestBacktestDrawScoring:
    @pytest.mark.asyncio
    async def test_tip_on_drawn_game_is_never_correct(self):
        """A tip on the away side of a drawn game must be incorrect —
        the away team did not win anything."""
        db = AsyncMock(spec=AsyncSession)
        tip = SimpleNamespace(selected_team="Away")
        game = _drawn_game()
        db.execute = AsyncMock(
            side_effect=[
                _result([(tip, game)]),   # tip rows
                _result([(1, 1, 1)]),     # round accuracies (1 round, 1/1)
            ]
        )

        metrics = await BacktestService().calculate_backtest_from_tips(
            db, season=2026, heuristic="best_bet"
        )

        assert metrics["total_correct"] == 0
        assert metrics["total_profit"] == -STAKE_PER_GAME


class TestEloDrawScoring:
    def test_draw_is_half_score_for_both_sides(self):
        """Equal-strength teams drawing must both keep a 1500 rating —
        a draw is 0.5 for each side, not a home loss."""
        games = [_drawn_game()]
        ratings = EloModel._compute_ratings_from_games(
            games,
            {"Home": 1500.0, "Away": 1500.0},
            k_factor=20.0,
            home_advantage=0.0,
        )

        assert ratings["Home"] == pytest.approx(1500.0)
        assert ratings["Away"] == pytest.approx(1500.0)
