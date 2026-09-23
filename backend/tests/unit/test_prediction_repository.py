"""Prediction repository seam (P2-4).

Models query game history with a point-in-time (no-leakage) rule that
was previously re-implemented — with slight variations — inside every
model.  The repository is the ONE place that rule lives:

* every returned game is ``completed``;
* every returned game is strictly BEFORE the reference date.

``FormModel`` is the first model migrated to the seam (pattern-setter);
models still accept the legacy ``db`` argument for backward
compatibility until the orchestrator hands out repositories.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.shared.models_ml.form import FormModel
from packages.shared.models_ml.prediction import Prediction
from packages.shared.models_ml.repository import (
    SqlGameHistoryRepository,
)


def _completed_game(home, away, hs, as_, days_before=10):
    return SimpleNamespace(
        home_team=home,
        away_team=away,
        home_score=hs,
        away_score=as_,
        date=datetime(2026, 5, 1).replace(day=max(1, days_before)),
        completed=True,
    )


class TestSqlGameHistoryRepository:
    @pytest.mark.asyncio
    async def test_query_enforces_point_in_time_rule(self):
        """The emitted SQL must carry completed + date < before + team
        match — the no-leakage rule lives here, not in each model."""
        from unittest.mock import MagicMock

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        ))
        before = datetime(2026, 5, 10)

        repo = SqlGameHistoryRepository(db)
        await repo.recent_games_for_participant("Home", before=before, limit=5)

        statement = db.execute.await_args_list[0].args[0]
        compiled = str(statement.compile())
        assert "games.completed" in compiled or "completed = true" in compiled.lower()
        assert "games.date <" in compiled
        assert "(games.home_team = " in compiled or "games.away_team = " in compiled
        assert "LIMIT" in compiled.upper()

    @pytest.mark.asyncio
    async def test_returns_scalars(self):
        from unittest.mock import MagicMock

        game = _completed_game("Home", "Away", 50, 40)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[game])))
        ))

        repo = SqlGameHistoryRepository(db)
        games = await repo.recent_games_for_participant("Home", before=datetime(2026, 5, 10))

        assert games == [game]


class TestFormModelUsesRepository:
    @pytest.mark.asyncio
    async def test_predict_flows_through_injected_repository(self):
        """The model must use the injected repository — no raw session
        queries — and pass the game's date as the point-in-time bound."""
        captured: list = []

        class FakeRepository:
            async def recent_games_for_participant(self, participant, *, before, limit):
                captured.append(
                    {"participant": participant, "before": before, "limit": limit}
                )
                if participant == "Home":
                    return [
                        _completed_game("Home", "Away", 60, 40),   # win
                        _completed_game("Away", "Home", 30, 30),   # draw (away-listed)
                    ]
                return []

        game = SimpleNamespace(
            id=1,
            home_team="Home",
            away_team="Away",
            date=datetime(2026, 5, 10),
        )

        model = FormModel(games_to_consider=5, repository=FakeRepository())
        result = await model.predict(game, db=AsyncMock())

        home_calls = [c for c in captured if c["participant"] == "Home"]
        assert home_calls, "Home team must be queried through the repository"
        assert home_calls[0]["before"] == game.date
        assert home_calls[0]["limit"] == 5
        assert isinstance(result, Prediction)
        assert result.pick == "Home"  # Home has form, Away has none

    @pytest.mark.asyncio
    async def test_repository_receives_participant_scoped_calls(self):
        """Both participants are queried through the same seam."""
        seen: list = []

        class RecordingRepository:
            async def recent_games_for_participant(self, participant, *, before, limit):
                seen.append(participant)
                return []

        game = SimpleNamespace(
            id=2, home_team="Alpha", away_team="Beta", date=datetime(2026, 6, 1)
        )
        model = FormModel(repository=RecordingRepository())
        await model.predict(game, db=AsyncMock())

        assert seen == ["Alpha", "Beta"]
