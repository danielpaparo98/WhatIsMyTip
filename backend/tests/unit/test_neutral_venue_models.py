"""GF-NEUTRAL: neutral-venue handling in the models.

The grand final's ``home_team`` is a fixture designation â€” the Elo model
must not gift it +50 rating points, and the home-advantage model must
abstain entirely (its venue win-rates describe the venue's tenants, not
these two teams).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.models import Game
from packages.shared.models_ml.elo import EloModel
from packages.shared.models_ml.home_advantage import HomeAdvantageModel


def _hist_game(season: int, round_id: int, home_score: int, away_score: int):
    """A completed HISTORICAL game (for the HA learning sample)."""
    g = MagicMock(spec=Game)
    g.season = season
    g.round_id = round_id
    g.home_score = home_score
    g.away_score = away_score
    g.completed = True
    return g


class TestLearnHomeAdvantage:
    def test_measures_mean_margin_in_elo_points(self):
        """2026 sample (60 games): home wins every game by 8 points ->
        HA = 80 Elo (the model's own convention: 1 pt = 10 Elo).  The
        max round (60) is the GF and is excluded from the sample."""
        games = [_hist_game(2026, r, 90, 82) for r in range(1, 61)]
        learned, neutral = EloModel.learn_home_advantage(games)
        assert learned == 80.0
        assert neutral == {(2026, 60)}  # the max round IS the GF

    def test_grand_final_excluded_from_sample(self):
        """The GF is neutral: its result must not feed the HA estimate.
        Season: rounds 1-55 home wins by 8, round 56 (GF) home LOSES by
        30.  With the GF included the mean would drop by ~2.4 points."""
        games = [_hist_game(2026, r, 90, 82) for r in range(1, 56)]
        games.append(_hist_game(2026, 56, 50, 80))  # the neutral GF
        learned, neutral = EloModel.learn_home_advantage(games)
        assert (2026, 56) in neutral
        # HA is measured from the 55 non-GF games only: +8 avg -> 80 Elo.
        assert learned == 80.0

    def test_too_small_sample_returns_none(self):
        games = [_hist_game(2026, 1, 90, 80) for _ in range(10)]  # 10 < 50
        learned, _neutral = EloModel.learn_home_advantage(games)
        assert learned is None

    def test_empty_sample_returns_none(self):
        learned, neutral = EloModel.learn_home_advantage([])
        assert learned is None
        assert neutral == set()


def _make_game(*, round_id: int = 29, season: int = 2026) -> Game:
    """Nominally-home GF fixture: Fremantle 'hosts' Brisbane at the MCG."""
    game = MagicMock(spec=Game)
    game.id = 101
    game.round_id = round_id
    game.season = season
    game.home_team = "Fremantle"
    game.away_team = "Brisbane"
    game.venue = "M.C.G."
    game.date = __import__("datetime").datetime(2026, 9, 26, 14, 30)
    game.completed = False
    return game


def _patch_neutral(flag: bool):
    return patch(
        "packages.shared.models_ml.neutral.is_grand_final",
        new=AsyncMock(return_value=flag),
    )


class TestHomeAdvantageNeutralAbstention:
    @pytest.mark.asyncio
    async def test_abstains_at_grand_final(self):
        """The model must ABSTAIN (raise) at a neutral venue so the
        orchestrator excludes it from consensus â€” its premise (venue
        tenants win at home) is meaningless there."""
        model = HomeAdvantageModel()
        game = _make_game()

        with _patch_neutral(True):
            with pytest.raises(ValueError, match="Neutral venue"):
                await model.predict(game, AsyncMock(spec=AsyncSession))

    @pytest.mark.asyncio
    async def test_regular_round_still_predicts(self):
        """A regular (non-GF) game keeps the normal home-advantage vote."""
        model = HomeAdvantageModel()
        game = _make_game(round_id=20)
        db = AsyncMock(spec=AsyncSession)

        with _patch_neutral(False), patch.object(
            model, "_calculate_home_advantage", new=AsyncMock()
        ), patch.object(
            model,
            "home_win_rate",
            {},
        ), patch.object(
            model, "overall_home_advantage", new=0.0
        ):
            # Empty venue stats -> overall fallback 0.0 -> away win path.
            winner, confidence, margin = await model.predict(game, db)

        assert winner == game.away_team


class TestEloNeutralVenue:
    def _elo_with_ratings(self, ratings):
        model = EloModel()
        game = _make_game()
        db = AsyncMock(spec=AsyncSession)
        return model, game, db, ratings

    def _isolated(self, model, ratings):
        """Bypass Redis/DB rating plumbing so predict's HA logic is
        tested in isolation: force the point-in-time fallback and feed
        it a fixed ratings table.  Returns ONE ExitStack context."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch.object(
                model.__class__,
                "_load_ratings_from_redis",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch.object(
                model.__class__,
                "load_from_cache",
                new=AsyncMock(return_value=False),
            )
        )
        stack.enter_context(
            patch.object(
                model.__class__,
                "_initialize_cache",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch.object(
                model,
                "_compute_point_in_time_ratings",
                new=AsyncMock(return_value=ratings),
            )
        )
        return stack

    @pytest.mark.asyncio
    async def test_no_home_bonus_at_grand_final(self):
        """Equal ratings + neutral venue -> NO phantom home boost: the
        prediction must not tip the nominal home side on HA alone."""
        model, game, db, _ = self._elo_with_ratings(None)
        patches = self._isolated(model, {"Fremantle": 1600.0, "Brisbane": 1600.0})

        with _patch_neutral(True), patches:
            winner, _confidence, _margin = await model.predict(game, db)

        # Equal ratings, zero HA -> even odds; the tie-break falls to the
        # away side in the formula â€” the point is Fremantle gets no +50.
        assert winner == game.away_team

    @pytest.mark.asyncio
    async def test_home_bonus_still_applies_at_regular_rounds(self):
        """Non-GF games keep the +50 home advantage (fixture home side)."""
        model, game, db, _ = self._elo_with_ratings(None)
        patches = self._isolated(model, {"Fremantle": 1600.0, "Brisbane": 1600.0})

        with _patch_neutral(False), patches:
            winner, _confidence, _margin = await model.predict(game, db)

        # +50 HA tips the even matchup to the nominal home side.
        assert winner == game.home_team


    @pytest.mark.asyncio
    async def test_neutral_ratings_still_decide_when_unequal(self):
        """Neutral venue does not mean 'no prediction' — genuine rating
        gaps still decide the game, just without the HA boost."""
        model, game, db, _ = self._elo_with_ratings(None)
        patches = self._isolated(model, {"Fremantle": 1500.0, "Brisbane": 1700.0})

        with _patch_neutral(True), patches:
            winner, _confidence, _margin = await model.predict(game, db)

        assert winner == game.away_team  # Brisbane is 200 points stronger

    @pytest.mark.asyncio
    async def test_learned_ha_stored_on_class_and_used_by_predict(self):
        """A measured HA (80) overrides the hard-coded 50 at predict
        time: equal ratings + HA 80 -> margin = 80/10 = 8 pts."""
        from contextlib import ExitStack

        model, game, db, _ = self._elo_with_ratings(None)
        patches = self._isolated(model, {"Fremantle": 1600.0, "Brisbane": 1600.0})

        with _patch_neutral(False), patches, ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    type(model), "_LEARNED_HOME_ADVANTAGE", new=80.0
                )
            )
            winner, _confidence, margin = await model.predict(game, db)

        assert winner == game.home_team
        assert margin == 8


class TestGrandFinalHeuristicSingleSource:
    """P0-8: the ``max(round_id)`` heuristic must live in exactly one
    implementation (``models_ml/neutral.is_grand_final``).  The match
    report service delegates to it; the round locator derives the same
    flag from its own query because it also needs ``max_round_id``.
    """

    def test_match_report_service_delegates_to_neutral(self):
        import inspect

        from packages.shared.services import match_report

        src = inspect.getsource(match_report.MatchReportService.is_grand_final)
        assert "_is_grand_final" in src, (
            "MatchReportService.is_grand_final must delegate to "
            "models_ml.neutral.is_grand_final, not re-implement the heuristic"
        )
        assert "func.max" not in src, (
            "MatchReportService.is_grand_final must not duplicate the SQL"
        )
