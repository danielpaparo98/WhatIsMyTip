"""Abstention contract tests for the ML models (P0-3 + P2-1 policy).

Two contract halves:

1. A model that fails internally must RAISE, so
   ``ModelOrchestrator._predict_one`` can record an abstention. Four
   models historically caught every exception and returned a confident-
   looking home-team vote — meaning a systemic bug (DB outage, schema
   drift) biased every tip toward the home side while *looking* like a
   normal prediction (ORCH-M7 follow-up).

2. A model with no usable data (cold start) must return the
   ``ABSTAINED`` singleton — never a fabricated home-team pick.
   Missing data carries no winner information, so voting "home at
   0.55" injected fixture bias into every early-season or
   data-sparse matchup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.models import MatchWeather
from packages.shared.models_ml.injury_impact import InjuryImpactModel
from packages.shared.models_ml.matchup import MatchupModel
from packages.shared.models_ml.player_form import PlayerFormModel
from packages.shared.models_ml.prediction import ABSTAINED, is_abstained
from packages.shared.models_ml.weather_impact import WeatherImpactModel

ALL_MODELS = [
    WeatherImpactModel(),
    InjuryImpactModel(),
    MatchupModel(),
    PlayerFormModel(),
]


def _make_game():
    return SimpleNamespace(
        id=1,
        slug="abs-00001",
        season=2026,
        round_id=3,
        home_team="Brisbane",
        away_team="Collingwood",
        venue="Gabba",
        date=datetime(2026, 4, 4, 10, 0),
        completed=False,
    )


# ---------------------------------------------------------------------------
# DB result helpers (mock style: db.execute → result accessors, as the
# models consume them)
# ---------------------------------------------------------------------------

def _empty_result():
    """A db.execute() result that reports 'no rows' for every accessor."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalars.return_value.first.return_value = None
    result.all.return_value = []
    result.first.return_value = None
    result.scalar_one_or_none.return_value = None
    result.one_or_none.return_value = None
    return result


def _weather_row_result(weather):
    """A db.execute() result whose scalars().first() yields *weather*."""
    result = MagicMock()
    result.scalars.return_value.first.return_value = weather
    return result


def _rows_result(rows):
    """A db.execute() result whose .all() yields *rows*."""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _good_weather(game_id: int) -> MatchWeather:
    """Mild conditions → classified as the 'good' weather tier."""
    return MatchWeather(
        game_id=game_id,
        temperature=20.0,
        precipitation=0.0,
        wind_gusts=10.0,
    )


# ---------------------------------------------------------------------------
# Contract 1: internal errors propagate (never become a home-team vote)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.get_name())
async def test_internal_error_propagates_for_abstention(model):
    """An internal error must propagate — never become a home-team vote."""
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(side_effect=RuntimeError("simulated db outage"))

    with pytest.raises(Exception, match="simulated db outage"):
        await model.predict(_make_game(), db)


# ---------------------------------------------------------------------------
# Contract 2: no usable data → ABSTAINED (never a fabricated home pick)
# ---------------------------------------------------------------------------

class TestNoDataAbstains:
    """Every model's no-data / cold-start path returns ``ABSTAINED``."""

    @pytest.mark.asyncio
    async def test_player_form_no_recent_games_abstains(self):
        """No recent games for either team → ABSTAINED."""
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(return_value=_empty_result())

        result = await PlayerFormModel().predict(_make_game(), db)

        assert result is ABSTAINED
        assert is_abstained(result)

    @pytest.mark.asyncio
    async def test_matchup_no_h2h_history_abstains(self):
        """Fewer than 3 H2H games → ABSTAINED."""
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(return_value=_empty_result())

        result = await MatchupModel().predict(_make_game(), db)

        assert result is ABSTAINED
        assert is_abstained(result)

    @pytest.mark.asyncio
    async def test_injury_impact_no_active_injuries_abstains(self):
        """No active injuries for either team → no winner information →
        ABSTAINED (this is a normal mid-season state, not a cold start)."""
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(return_value=_empty_result())

        result = await InjuryImpactModel().predict(_make_game(), db)

        assert result is ABSTAINED
        assert is_abstained(result)

    @pytest.mark.asyncio
    async def test_weather_no_match_weather_row_abstains(self):
        """No MatchWeather row for the game → ABSTAINED."""
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(return_value=_empty_result())

        result = await WeatherImpactModel().predict(_make_game(), db)

        assert result is ABSTAINED
        assert is_abstained(result)

    @pytest.mark.asyncio
    async def test_weather_no_similar_condition_games_abstains(self):
        """Weather row exists but both teams have zero similar-condition
        games → ABSTAINED."""
        db = AsyncMock(spec=AsyncSession)
        # Query order: match weather → home history → away history
        db.execute = AsyncMock(
            side_effect=[
                _weather_row_result(_good_weather(game_id=1)),
                _empty_result(),
                _empty_result(),
            ]
        )

        result = await WeatherImpactModel().predict(_make_game(), db)

        assert result is ABSTAINED
        assert is_abstained(result)

    @pytest.mark.asyncio
    async def test_weather_one_team_no_data_is_not_a_cold_start(self):
        """One team's similar-condition rate is missing → treated as 0.5
        in the diff (NOT a cold start): the model still votes from the
        other team's real data."""

        def _away_won_row(i: int):
            # Collingwood (away) won a recent 'good'-weather game at the Gabba
            game = SimpleNamespace(
                id=100 + i,
                home_team="Carlton",
                away_team="Collingwood",
                home_score=70,
                away_score=95,
                venue="Gabba",
                date=datetime(2026, 4, 4, tzinfo=timezone.utc)
                - timedelta(days=30 * (i + 1)),
            )
            return game, _good_weather(game_id=game.id)

        db = AsyncMock(spec=AsyncSession)
        # Query order: match weather → home history (empty) → away history (3 wins)
        db.execute = AsyncMock(
            side_effect=[
                _weather_row_result(_good_weather(game_id=1)),
                _empty_result(),
                _rows_result([_away_won_row(i) for i in range(3)]),
            ]
        )

        result = await WeatherImpactModel().predict(_make_game(), db)

        assert not is_abstained(result)
        # 0.5 (home, no data) - 1.0 (away, won all three) → away wins
        assert result.pick == "Collingwood"
