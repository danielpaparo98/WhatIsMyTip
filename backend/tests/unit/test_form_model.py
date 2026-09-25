"""FormModel bias regressions (model-bias-fixes 2026-09-25).

Two defects are pinned here:

* ``_get_recent_form`` averaged ``abs(score_diff)`` — a team losing five
  games by 40 got the same form boost as a team winning five by 40.
  The average must be the SIGNED mean margin from the team's
  perspective, so heavy losses hurt form.
* ``predict`` unconditionally added ``+1.0`` to the home score.  Home
  advantage is not this model's job (it lives in ``elo.py`` and
  ``home_advantage.py``); with the bump removed, identical form means
  an exact tie and the strict ``home > away`` rule hands the tip to
  the away team — never a manufactured home pick.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.models_ml.form import FormModel


def _game(
    *,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    days_before: int = 1,
):
    """A completed historical game; ``days_before`` keeps ordering deterministic."""
    return SimpleNamespace(
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        date=datetime(2026, 4, 28 - days_before, 10, 0),
    )


class _FakeGameHistoryRepository:
    """In-memory ``GameHistoryRepository`` returning canned games per team."""

    def __init__(self, games_by_team):
        self._games_by_team = games_by_team

    async def recent_games_for_participant(self, participant, *, before, limit=5):
        return list(self._games_by_team.get(participant, []))[:limit]


def _form_model(games_by_team) -> FormModel:
    return FormModel(repository=_FakeGameHistoryRepository(games_by_team))


def _history_for(team: str, *, diffs) -> list:
    """Five recent games for ``team`` with the given signed result margins."""
    other = f"{team}Opponent"
    games = []
    for i, diff in enumerate(diffs):
        # Split venues so the home/away sign logic is exercised: even
        # indexes put the team at home, odd indexes away.
        if i % 2 == 0:
            games.append(
                _game(
                    home_team=team,
                    away_team=other,
                    home_score=60,
                    away_score=60 - diff,
                    days_before=i + 1,
                )
            )
        else:
            games.append(
                _game(
                    home_team=other,
                    away_team=team,
                    home_score=60 - diff,
                    away_score=60,
                    days_before=i + 1,
                )
            )
    return games


class TestSignedScoreDiff:
    @pytest.mark.asyncio
    async def test_big_losses_score_worse_than_narrow_losses(self):
        """Regression for the ``abs()`` bug: a team losing five games by
        40 must produce a WORSE form score than a team losing five by 1 —
        heavy defeats must not masquerade as good form."""
        big_loss_history = _history_for("BigLoss", diffs=[-40, -40, -40, -40, -40])
        narrow_loss_history = _history_for("NarrowLoss", diffs=[-1, -1, -1, -1, -1])
        model = _form_model(
            {"BigLoss": big_loss_history, "NarrowLoss": narrow_loss_history}
        )
        db = MagicMock(spec=AsyncSession)

        # Unit level: the average margin itself is signed.
        big_form = await model._get_recent_form(db, "BigLoss", datetime(2026, 5, 1))
        narrow_form = await model._get_recent_form(
            db, "NarrowLoss", datetime(2026, 5, 1)
        )
        assert big_form["avg_score_diff"] == -40
        assert narrow_form["avg_score_diff"] == -1
        assert big_form["avg_score_diff"] < narrow_form["avg_score_diff"]

        # Composite level: the big-loss team (at home) must lose the
        # tip to the narrow-loss away team.  Under the abs() bug the
        # home side scored -5 + 40/10 = -1 versus the away side's
        # -5 + 1/10 = -4.9 and stole the tip.
        game = SimpleNamespace(
            home_team="BigLoss",
            away_team="NarrowLoss",
            date=datetime(2026, 5, 1),
        )
        winner, confidence, margin = await model.predict(game, db)

        assert winner == "NarrowLoss"


class TestNoHomeBias:
    @pytest.mark.asyncio
    async def test_identical_form_does_not_default_to_home_pick(self):
        """Identical recent form → an exact tie, and the strict
        ``home > away`` rule must hand the tip to the AWAY team.  The
        removed ``+1.0`` home bump used to flip this to a home pick."""
        home_history = _history_for("Home", diffs=[1, 2, 3, -3, -3])
        away_history = _history_for("Away", diffs=[1, 2, 3, -3, -3])
        model = _form_model({"Home": home_history, "Away": away_history})
        db = MagicMock(spec=AsyncSession)

        # Both teams: 3W 2L, mean margin 0 → form score 4.0 each.
        game = SimpleNamespace(
            home_team="Home",
            away_team="Away",
            date=datetime(2026, 5, 1),
        )
        winner, confidence, margin = await model.predict(game, db)

        assert winner == "Away"
        # Coin-flip confidence floor and minimum margin on an exact tie.
        assert confidence == 0.5
        assert margin == 1

    @pytest.mark.asyncio
    async def test_better_away_form_beats_worse_home_team(self):
        """A strictly better-form AWAY team must be picked over a
        worse-form HOME team even when the gap is smaller than the old
        +1.0 bump: home form 4.0 (3W 2L, mean 0) vs away form 4.1
        (3W 2L, mean +1)."""
        home_history = _history_for("Home", diffs=[1, 2, 3, -3, -3])
        away_history = _history_for("Away", diffs=[1, 2, 3, 4, -5])
        model = _form_model({"Home": home_history, "Away": away_history})
        db = MagicMock(spec=AsyncSession)

        game = SimpleNamespace(
            home_team="Home",
            away_team="Away",
            date=datetime(2026, 5, 1),
        )
        winner, confidence, margin = await model.predict(game, db)

        assert winner == "Away"
