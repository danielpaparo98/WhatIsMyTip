"""Unit tests for ``packages.shared.services.boosted_walkforward`` (BT-1).

Walk-forward backfill of ``boosted_tip`` tips: for each completed round,
train the boosted model ONLY on completed games strictly before that
round (production's weekly-cutoff semantics) and tip the round from the
stored 8-model predictions.  Below ``MIN_TRAINING_ROWS`` the round falls
back to majority vote — the same cold-start rule the runtime heuristic
uses.

The pure :func:`compute_walkforward_plans` core is tested with no
database: games are synthetic records, and the tiny real XGBRegressor
fits keep the trained-mode path honest (importorskip guards).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest

from packages.shared.heuristics.weighted_tip import weighted_tip_fallback
from packages.shared.models_ml.prediction import Prediction
from packages.shared.services.boosted_walkforward import (
    GameRecord,
    compute_walkforward_plans,
)

_HOME = "Brisbane"
_AWAY = "Collingwood"


def _preds(home_votes: int, away_votes: int, margin: int = 10) -> dict[str, Prediction]:
    """Deterministic prediction dict: `home_votes` models pick home, rest away."""
    preds: dict[str, Prediction] = {}
    for i, name in enumerate(
        [
            "elo",
            "form",
            "home_advantage",
            "value",
            "weather_impact",
            "injury_impact",
            "matchup",
            "player_form",
        ]
    ):
        if i < home_votes:
            preds[name] = Prediction(_HOME, 0.7, margin)
        else:
            preds[name] = Prediction(_AWAY, 0.6, margin)
    return preds


def _record(
    game_id: int,
    season: int,
    round_id: int,
    *,
    home_score: int = 60,
    away_score: int = 50,
    preds: dict[str, Prediction] | None = None,
) -> GameRecord:
    return GameRecord(
        game_id=game_id,
        season=season,
        round_id=round_id,
        home_team=_HOME,
        away_team=_AWAY,
        home_score=home_score,
        away_score=away_score,
        preds=preds if preds is not None else _preds(5, 3),
    )


def _many_games(
    start_id: int,
    season: int,
    round_id: int,
    count: int,
) -> List[GameRecord]:
    return [
        _record(start_id + i, season, round_id,
                home_score=60 + (i % 5), away_score=50)
        for i in range(count)
    ]


class TestWalkforwardFallback:
    def test_first_round_falls_back_to_majority_vote(self):
        """Zero training rows -> majority-vote tips, mode 'fallback'."""
        games = [_record(1, 2026, 1), _record(2, 2026, 1, preds=_preds(2, 6))]

        plans = compute_walkforward_plans(games)

        assert len(plans) == 1
        plan = plans[0]
        assert plan.mode == "fallback"
        assert plan.training_rows == 0
        assert len(plan.tips) == 2
        # 5-3 home majority -> home; 2-6 away majority -> away.
        assert plan.tips[0].prediction.pick == _HOME
        assert plan.tips[1].prediction.pick == _AWAY
        # Tips must equal the shared fallback exactly (reuse, never copy).
        for tip, game in zip(plan.tips, games):
            expected = weighted_tip_fallback(game.preds, _HOME, _AWAY)
            assert tip.prediction == expected

    def test_trained_mode_once_threshold_reached(self):
        """>= 100 prior rows -> XGBoost fit; the round's own games excluded."""
        games = (
            _many_games(1, 2026, 1, 100)
            + [_record(200 + i, 2026, 2) for i in range(5)]
        )

        xgboost = pytest.importorskip("xgboost")
        plans = compute_walkforward_plans(games)
        assert xgboost.__name__  # silence unused-import lint; importorskip is the guard

        assert [p.mode for p in plans] == ["fallback", "trained"]
        trained = plans[1]
        assert trained.training_rows == 100  # round 2's own 5 games NOT in the pool
        assert len(trained.tips) == 5
        for tip in trained.tips:
            assert 1 <= (tip.prediction.score_projection or 0)

    def test_cutoff_counts_only_strictly_earlier_rounds(self):
        """Pool == 100 prior games even though round 2 adds 60 more."""
        games = _many_games(1, 2026, 1, 100) + _many_games(500, 2026, 2, 60)

        plans = compute_walkforward_plans(games)

        assert plans[0].training_rows == 0
        assert plans[0].mode == "fallback"
        assert plans[1].training_rows == 100
        assert plans[1].mode == "trained"

    def test_games_without_predictions_are_skipped_and_never_train(self):
        games = (
            _many_games(1, 2026, 1, 100)
            + [_record(200, 2026, 2, preds={})]
            + [_record(201, 2026, 2)]
        )

        plans = compute_walkforward_plans(games)

        trained = plans[1]
        # The empty-preds game gets NO tip...
        assert [t.game_id for t in trained.tips] == [201]
        # ...and its features never entered the pool.
        assert trained.training_rows == 100
        assert trained.n_skipped_no_preds == 1

    def test_draws_are_never_correct(self):
        games = [
            _record(1, 2026, 1, home_score=50, away_score=50, preds=_preds(5, 3)),
            _record(2, 2026, 1, home_score=70, away_score=50, preds=_preds(5, 3)),
        ]

        plans = compute_walkforward_plans(games)

        plan = plans[0]
        assert plan.n_tipped == 2
        assert plan.n_correct == 1  # draw is not a correct tip; home blowout is

    def test_rounds_processed_in_ascending_order_across_seasons(self):
        games = (
            [_record(1, 2025, 2), _record(2, 2025, 1)]
            + _many_games(10, 2026, 1, 100)
            + [_record(300, 2026, 2)]
        )

        plans = compute_walkforward_plans(games)

        keys = [(p.season, p.round_id) for p in plans]
        assert keys == [(2025, 1), (2025, 2), (2026, 1), (2026, 2)]
        # 2025 games joined the pool (both rounds precede 2026).
        assert plans[2].training_rows == 2
        assert plans[3].training_rows == 102

    def test_pool_respects_lookback_window(self):
        """Seasons older than the 3-season window never enter the pool."""
        games = (
            _many_games(1, 2022, 1, 100)
            + _many_games(500, 2026, 1, 5)
        )

        plans = compute_walkforward_plans(games)

        # Window for season 2026 is 2024..2026: the 100 games from 2022 are
        # invisible, so round 1 of 2026 still falls back.
        assert plans[0].season == 2022
        assert plans[0].mode == "fallback"
        assert plans[1].training_rows == 0
        assert plans[1].mode == "fallback"


# ---------------------------------------------------------------------------
# I/O wrapper (fake session — no database)
# ---------------------------------------------------------------------------


class _ScalarsView:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _FakeIOResult:
    def __init__(self, rows, scalar_values=None):
        self._rows = rows
        self._scalar_values = scalar_values

    def all(self):
        return self._rows

    def scalars(self):
        return _ScalarsView(self._scalar_values or [])


class _QueueSession:
    """AsyncSession stand-in popping one canned result per execute call."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = 0
        self.rolled_back = 0

    async def execute(self, stmt):
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1


def _game_row(game_id, season, round_id):
    """(Game, ModelPrediction) row pair as the joined query returns them."""

    def _pred(name):
        return SimpleNamespace(
            model_name=name, winner=_HOME, confidence=0.7, margin=10
        )

    game = SimpleNamespace(
        id=game_id,
        season=season,
        round_id=round_id,
        home_team=_HOME,
        away_team=_AWAY,
        home_score=60,
        away_score=50,
        completed=True,
    )
    # Four stored model predictions: clears MIN_MODELS_PER_GAME for training.
    return (
        (game, _pred("elo")),
        (game, _pred("form")),
        (game, _pred("value")),
        (game, _pred("matchup")),
    )


def _flat_rows(game_rounds):
    rows = []
    for game_id, season, round_id in game_rounds:
        rows.extend(_game_row(game_id, season, round_id))
    return rows


class TestRunBoostedWalkforwardBackfill:
    async def test_dry_run_writes_nothing(self, monkeypatch):
        from packages.shared.services.boosted_walkforward import (
            run_boosted_walkforward_backfill,
        )

        session = _QueueSession([
            _FakeIOResult(_flat_rows([(1, 2026, 1), (2, 2026, 1)])),
            _FakeIOResult([], scalar_values=[]),
        ])

        summary = await run_boosted_walkforward_backfill(
            session, [2026], dry_run=True
        )

        assert summary["status"] == "dry_run"
        assert summary["tips_created"] == 0
        assert session.added == []
        assert session.committed == 0
        assert summary["rounds_processed"] == 1
        assert summary["per_round"][0]["mode"] == "fallback"

    async def test_real_run_persists_and_skips_existing(self, monkeypatch):
        from packages.shared.services.boosted_walkforward import (
            BOOSTED_TIP_MODEL_NAME,
            run_boosted_walkforward_backfill,
        )

        # Game 1 already has a boosted tip; games 2-3 do not.
        session = _QueueSession([
            _FakeIOResult(_flat_rows([(1, 2026, 1), (2, 2026, 1), (3, 2026, 1)])),
            _FakeIOResult([], scalar_values=[1]),
        ])

        summary = await run_boosted_walkforward_backfill(session, [2026])

        assert summary["status"] == "completed"
        assert summary["tips_created"] == 2
        assert summary["tips_skipped_existing"] == 1
        assert session.committed == 1
        assert len(session.added) == 2
        for tip in session.added:
            assert tip.heuristic == BOOSTED_TIP_MODEL_NAME
            assert tip.selected_team == _HOME
            assert tip.margin is not None and tip.margin >= 1
            assert "BT-1 walk-forward backfill" in tip.explanation
        assert {t.game_id for t in session.added} == {2, 3}

    async def test_empty_seasons_rejected(self):
        from packages.shared.services.boosted_walkforward import (
            run_boosted_walkforward_backfill,
        )

        with pytest.raises(ValueError):
            await run_boosted_walkforward_backfill(_QueueSession([]), [])
