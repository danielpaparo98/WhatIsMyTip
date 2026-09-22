"""Abstention contract tests for the ML models (P0-3).

The orchestrator's contract is: a model that fails internally must
RAISE, so ``ModelOrchestrator._predict_one`` can record an abstention.
Four models instead caught every exception and returned a confident-
looking home-team vote — meaning a systemic bug (DB outage, schema
drift) biased every tip toward the home side while *looking* like a
normal prediction (ORCH-M7 follow-up).

Cold-start behavior (insufficient data → deterministic default) is
deliberate and stays; only the exception-swallowing is fixed.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.models_ml.injury_impact import InjuryImpactModel
from packages.shared.models_ml.matchup import MatchupModel
from packages.shared.models_ml.player_form import PlayerFormModel
from packages.shared.models_ml.weather_impact import WeatherImpactModel

ALL_MODELS = [
    WeatherImpactModel(),
    InjuryImpactModel(),
    MatchupModel(),
    PlayerFormModel(),
]


def _make_game():
    from types import SimpleNamespace

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


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.get_name())
async def test_internal_error_propagates_for_abstention(model):
    """An internal error must propagate — never become a home-team vote."""
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(side_effect=RuntimeError("simulated db outage"))

    with pytest.raises(Exception, match="simulated db outage"):
        await model.predict(_make_game(), db)
