"""Unit tests for PlayerFormModel.

Tests cover form score calculation, recent game lookups, advanced stat
aggregation, cold-start behaviour, confidence/margin clamping, and
backtest safety.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from packages.shared.models_ml.player_form import PlayerFormModel
from packages.shared.models import Game


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def model():
    return PlayerFormModel()


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


def _mock_result_all(return_value):
    """Helper: mock result of db.execute().all()."""
    result_mock = MagicMock()
    result_mock.all.return_value = return_value
    return result_mock


def _mock_one_or_none(return_value):
    """Helper: mock result of db.execute().one_or_none()."""
    result_mock = MagicMock()
    result_mock.one_or_none.return_value = return_value
    return result_mock


# ---------------------------------------------------------------------------
# Constructor / get_name
# ---------------------------------------------------------------------------

class TestPlayerFormModelBasics:
    def test_model_instantiation(self, model):
        assert isinstance(model, PlayerFormModel)

    def test_get_name(self, model):
        assert model.get_name() == "player_form"


# ---------------------------------------------------------------------------
# _calculate_form_score
# ---------------------------------------------------------------------------

class TestCalculateFormScore:
    def test_high_form_score(self, model):
        """Strong advanced stats → high form score."""
        stats = {
            "avg_score_involvements": 6.0,
            "avg_contested_possessions": 10.0,
            "avg_metres_gained": 300.0,
            "avg_pressure_acts": 15.0,
            "avg_tog_pct": 85.0,
        }
        score = model._calculate_form_score(stats)
        # 6*3 + 10*2 + 300*0.1 + 15*1.5 + 85*0.5
        # = 18 + 20 + 30 + 22.5 + 42.5 = 133.0
        assert score == pytest.approx(133.0)

    def test_low_form_score(self, model):
        """Weak advanced stats → low form score."""
        stats = {
            "avg_score_involvements": 2.0,
            "avg_contested_possessions": 4.0,
            "avg_metres_gained": 100.0,
            "avg_pressure_acts": 5.0,
            "avg_tog_pct": 60.0,
        }
        score = model._calculate_form_score(stats)
        # 2*3 + 4*2 + 100*0.1 + 5*1.5 + 60*0.5
        # = 6 + 8 + 10 + 7.5 + 30 = 61.5
        assert score == pytest.approx(61.5)

    def test_empty_stats(self, model):
        """No stats → score 0."""
        score = model._calculate_form_score({})
        assert score == 0.0

    def test_partial_stats(self, model):
        """Only some stats present → others default to 0."""
        stats = {
            "avg_score_involvements": 5.0,
            "avg_contested_possessions": 8.0,
        }
        score = model._calculate_form_score(stats)
        # 5*3 + 8*2 + 0 + 0 + 0 = 15 + 16 = 31.0
        assert score == pytest.approx(31.0)

    def test_zero_stats(self, model):
        """All zero values → score 0."""
        stats = {
            "avg_score_involvements": 0.0,
            "avg_contested_possessions": 0.0,
            "avg_metres_gained": 0.0,
            "avg_pressure_acts": 0.0,
            "avg_tog_pct": 0.0,
        }
        score = model._calculate_form_score(stats)
        assert score == 0.0


# ---------------------------------------------------------------------------
# _get_recent_games
# ---------------------------------------------------------------------------

class TestGetRecentGames:
    @pytest.mark.asyncio
    async def test_returns_game_ids(self, model, game):
        """Should return list of game IDs."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([(10,), (20,), (30,), (40,), (50,)])

        ids = await model._get_recent_games("Brisbane", db, game.date)
        assert ids == [10, 20, 30, 40, 50]

    @pytest.mark.asyncio
    async def test_no_completed_games(self, model, game):
        """No completed games → empty list."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([])

        ids = await model._get_recent_games("Brisbane", db, game.date)
        assert ids == []

    @pytest.mark.asyncio
    async def test_respects_limit(self, model, game):
        """Should only return up to `limit` games."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([(1,), (2,), (3,)])

        ids = await model._get_recent_games("Brisbane", db, game.date, limit=3)
        assert len(ids) == 3

    @pytest.mark.asyncio
    async def test_default_limit_is_five(self, model, game):
        """Default limit should be 5."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([(1,), (2,), (3,), (4,), (5,)])

        ids = await model._get_recent_games("Brisbane", db, game.date)
        assert len(ids) == 5


# ---------------------------------------------------------------------------
# _get_team_advanced_stats
# ---------------------------------------------------------------------------

class TestGetTeamAdvancedStats:
    @pytest.mark.asyncio
    async def test_returns_avg_stats(self, model, game):
        """Returns aggregated stats dict for given game IDs and team."""
        db = AsyncMock()

        row = MagicMock()
        row.avg_tog_pct = 78.5
        row.avg_metres_gained = 250.0
        row.avg_score_involvements = 4.5
        row.avg_contested_possessions = 8.2
        row.avg_pressure_acts = 12.3
        db.execute.return_value = _mock_one_or_none(row)

        stats = await model._get_team_advanced_stats([10, 20, 30], "Brisbane", db)
        assert stats["avg_tog_pct"] == 78.5
        assert stats["avg_metres_gained"] == 250.0
        assert stats["avg_score_involvements"] == 4.5
        assert stats["avg_contested_possessions"] == 8.2
        assert stats["avg_pressure_acts"] == 12.3

    @pytest.mark.asyncio
    async def test_empty_game_ids_returns_empty(self, model, game):
        """No game IDs → empty dict without DB query."""
        db = AsyncMock()
        stats = await model._get_team_advanced_stats([], "Brisbane", db)
        assert stats == {}
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_stats_found(self, model, game):
        """Query returns None → empty dict."""
        db = AsyncMock()
        db.execute.return_value = _mock_one_or_none(None)

        stats = await model._get_team_advanced_stats([10, 20], "Brisbane", db)
        assert stats == {}

    @pytest.mark.asyncio
    async def test_all_null_stats_returns_empty(self, model, game):
        """All null stat values → treated as no data, returns empty dict."""
        db = AsyncMock()
        row = MagicMock()
        row.avg_tog_pct = None
        row.avg_metres_gained = None
        row.avg_score_involvements = None
        row.avg_contested_possessions = None
        row.avg_pressure_acts = None
        db.execute.return_value = _mock_one_or_none(row)

        stats = await model._get_team_advanced_stats([10], "Brisbane", db)
        # All nulls → treated as no data per architecture doc
        assert stats == {}

    @pytest.mark.asyncio
    async def test_partial_null_stats_returns_zeroes(self, model, game):
        """Some null stat values → nulls default to 0.0 in the returned dict."""
        db = AsyncMock()
        row = MagicMock()
        row.avg_tog_pct = 78.0
        row.avg_metres_gained = None
        row.avg_score_involvements = 5.0
        row.avg_contested_possessions = None
        row.avg_pressure_acts = 10.0
        db.execute.return_value = _mock_one_or_none(row)

        stats = await model._get_team_advanced_stats([10], "Brisbane", db)
        assert stats["avg_tog_pct"] == 78.0
        assert stats["avg_metres_gained"] == 0.0
        assert stats["avg_score_involvements"] == 5.0
        assert stats["avg_contested_possessions"] == 0.0
        assert stats["avg_pressure_acts"] == 10.0


# ---------------------------------------------------------------------------
# predict() — cold-start / no data scenarios
# ---------------------------------------------------------------------------

class TestPredictColdStart:
    @pytest.mark.asyncio
    async def test_no_games_for_either_team(self, model, game):
        """No recent games for either team → cold start."""
        db = AsyncMock()
        with patch.object(model, "_get_recent_games", return_value=[]):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 6

    @pytest.mark.asyncio
    async def test_no_advanced_stats_cold_start(self, model, game):
        """Games exist but no advanced stats → cold start."""
        db = AsyncMock()
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3]), \
             patch.object(model, "_get_team_advanced_stats", return_value={}):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 6

    @pytest.mark.asyncio
    async def test_home_no_games_away_has_data(self, model, game):
        """Home has no games, away has data → away should be favoured."""
        db = AsyncMock()
        away_stats = {
            "avg_score_involvements": 5.0,
            "avg_contested_possessions": 8.0,
            "avg_metres_gained": 250.0,
            "avg_pressure_acts": 12.0,
            "avg_tog_pct": 78.0,
        }
        with patch.object(model, "_get_recent_games") as mock_games, \
             patch.object(model, "_get_team_advanced_stats") as mock_stats:
            mock_games.side_effect = lambda team, db, before_date, limit=5: (
                [] if team == "Brisbane" else [1, 2, 3]
            )
            mock_stats.side_effect = lambda game_ids, team, db: (
                {} if team == "Brisbane" else away_stats
            )
            winner, confidence, margin = await model.predict(game, db)

        # Away has data, home doesn't → away should win
        assert winner == "Collingwood"
        assert confidence > 0.50


# ---------------------------------------------------------------------------
# predict() — full scenario tests
# ---------------------------------------------------------------------------

class TestPredictScenarios:
    @pytest.mark.asyncio
    async def test_team_a_better_form_wins(self, model, game):
        """Brisbane has much better advanced stats → Brisbane wins."""
        db = AsyncMock()
        home_stats = {
            "avg_score_involvements": 6.0,
            "avg_contested_possessions": 10.0,
            "avg_metres_gained": 300.0,
            "avg_pressure_acts": 15.0,
            "avg_tog_pct": 85.0,
        }
        away_stats = {
            "avg_score_involvements": 3.0,
            "avg_contested_possessions": 5.0,
            "avg_metres_gained": 150.0,
            "avg_pressure_acts": 8.0,
            "avg_tog_pct": 65.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3, 4, 5]), \
             patch.object(model, "_get_team_advanced_stats") as mock_stats:
            mock_stats.side_effect = lambda game_ids, team, db: (
                home_stats if team == "Brisbane" else away_stats
            )
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence > 0.55

    @pytest.mark.asyncio
    async def test_similar_form_low_confidence(self, model, game):
        """Similar form → low confidence, home advantage tips it."""
        db = AsyncMock()
        stats = {
            "avg_score_involvements": 5.0,
            "avg_contested_possessions": 8.0,
            "avg_metres_gained": 250.0,
            "avg_pressure_acts": 12.0,
            "avg_tog_pct": 78.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3, 4, 5]), \
             patch.object(model, "_get_team_advanced_stats", return_value=stats):
            winner, confidence, margin = await model.predict(game, db)

        # Same stats + home advantage → home wins
        assert winner == "Brisbane"
        assert confidence < 0.60  # Low confidence for similar form

    @pytest.mark.asyncio
    async def test_one_team_no_stats(self, model, game):
        """One team has stats, other doesn't → team with stats wins."""
        db = AsyncMock()
        home_stats = {
            "avg_score_involvements": 5.0,
            "avg_contested_possessions": 8.0,
            "avg_metres_gained": 250.0,
            "avg_pressure_acts": 12.0,
            "avg_tog_pct": 78.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3, 4, 5]), \
             patch.object(model, "_get_team_advanced_stats") as mock_stats:
            mock_stats.side_effect = lambda game_ids, team, db: (
                home_stats if team == "Brisbane" else {}
            )
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence > 0.55

    @pytest.mark.asyncio
    async def test_away_team_better_form(self, model, game):
        """Collingwood has much better form → Collingwood wins."""
        db = AsyncMock()
        home_stats = {
            "avg_score_involvements": 2.0,
            "avg_contested_possessions": 4.0,
            "avg_metres_gained": 100.0,
            "avg_pressure_acts": 5.0,
            "avg_tog_pct": 60.0,
        }
        away_stats = {
            "avg_score_involvements": 7.0,
            "avg_contested_possessions": 12.0,
            "avg_metres_gained": 350.0,
            "avg_pressure_acts": 18.0,
            "avg_tog_pct": 88.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3, 4, 5]), \
             patch.object(model, "_get_team_advanced_stats") as mock_stats:
            mock_stats.side_effect = lambda game_ids, team, db: (
                home_stats if team == "Brisbane" else away_stats
            )
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Collingwood"
        assert confidence > 0.50

    @pytest.mark.asyncio
    async def test_only_bad_recent_games(self, model, game):
        """One team has only bad recent games → other team wins."""
        db = AsyncMock()
        home_stats = {
            "avg_score_involvements": 1.0,
            "avg_contested_possessions": 2.0,
            "avg_metres_gained": 50.0,
            "avg_pressure_acts": 3.0,
            "avg_tog_pct": 50.0,
        }
        away_stats = {
            "avg_score_involvements": 6.0,
            "avg_contested_possessions": 10.0,
            "avg_metres_gained": 300.0,
            "avg_pressure_acts": 15.0,
            "avg_tog_pct": 85.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3, 4, 5]), \
             patch.object(model, "_get_team_advanced_stats") as mock_stats:
            mock_stats.side_effect = lambda game_ids, team, db: (
                home_stats if team == "Brisbane" else away_stats
            )
            winner, confidence, margin = await model.predict(game, db)

        # Even with home advantage (+2), bad home form should lose
        # home_score = (1*3 + 2*2 + 50*0.1 + 3*1.5 + 50*0.5) + 2 = (3+4+5+4.5+25) + 2 = 43.5
        # away_score = 6*3 + 10*2 + 300*0.1 + 15*1.5 + 85*0.5 = 18+20+30+22.5+42.5 = 133.0
        assert winner == "Collingwood"


# ---------------------------------------------------------------------------
# Confidence and margin clamping
# ---------------------------------------------------------------------------

class TestClamping:
    @pytest.mark.asyncio
    async def test_confidence_lower_bound(self, model, game):
        """Confidence must be at least 0.50."""
        db = AsyncMock()
        stats = {
            "avg_score_involvements": 5.0,
            "avg_contested_possessions": 8.0,
            "avg_metres_gained": 250.0,
            "avg_pressure_acts": 12.0,
            "avg_tog_pct": 78.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3]), \
             patch.object(model, "_get_team_advanced_stats", return_value=stats):
            winner, confidence, margin = await model.predict(game, db)

        assert confidence >= 0.50

    @pytest.mark.asyncio
    async def test_confidence_upper_bound(self, model, game):
        """Confidence must not exceed 0.95."""
        db = AsyncMock()
        home_stats = {
            "avg_score_involvements": 10.0,
            "avg_contested_possessions": 15.0,
            "avg_metres_gained": 500.0,
            "avg_pressure_acts": 25.0,
            "avg_tog_pct": 95.0,
        }
        away_stats = {
            "avg_score_involvements": 1.0,
            "avg_contested_possessions": 2.0,
            "avg_metres_gained": 50.0,
            "avg_pressure_acts": 3.0,
            "avg_tog_pct": 40.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3, 4, 5]), \
             patch.object(model, "_get_team_advanced_stats") as mock_stats:
            mock_stats.side_effect = lambda game_ids, team, db: (
                home_stats if team == "Brisbane" else away_stats
            )
            winner, confidence, margin = await model.predict(game, db)

        assert confidence <= 0.95

    @pytest.mark.asyncio
    async def test_margin_lower_bound(self, model, game):
        """Margin must be at least 1."""
        db = AsyncMock()
        stats = {
            "avg_score_involvements": 5.0,
            "avg_contested_possessions": 8.0,
            "avg_metres_gained": 250.0,
            "avg_pressure_acts": 12.0,
            "avg_tog_pct": 78.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3]), \
             patch.object(model, "_get_team_advanced_stats", return_value=stats):
            winner, confidence, margin = await model.predict(game, db)

        assert margin >= 1

    @pytest.mark.asyncio
    async def test_margin_upper_bound(self, model, game):
        """Margin must not exceed 100."""
        db = AsyncMock()
        home_stats = {
            "avg_score_involvements": 10.0,
            "avg_contested_possessions": 15.0,
            "avg_metres_gained": 500.0,
            "avg_pressure_acts": 25.0,
            "avg_tog_pct": 95.0,
        }
        away_stats = {
            "avg_score_involvements": 1.0,
            "avg_contested_possessions": 2.0,
            "avg_metres_gained": 50.0,
            "avg_pressure_acts": 3.0,
            "avg_tog_pct": 40.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2, 3, 4, 5]), \
             patch.object(model, "_get_team_advanced_stats") as mock_stats:
            mock_stats.side_effect = lambda game_ids, team, db: (
                home_stats if team == "Brisbane" else away_stats
            )
            winner, confidence, margin = await model.predict(game, db)

        assert margin <= 100


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_games_played(self, model, game):
        """No games played yet → cold start."""
        db = AsyncMock()
        with patch.object(model, "_get_recent_games", return_value=[]):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 6

    @pytest.mark.asyncio
    async def test_only_1_2_games_with_stats(self, model, game):
        """Only 1-2 games of data → still predict if stats available."""
        db = AsyncMock()
        stats = {
            "avg_score_involvements": 5.0,
            "avg_contested_possessions": 8.0,
            "avg_metres_gained": 250.0,
            "avg_pressure_acts": 12.0,
            "avg_tog_pct": 78.0,
        }
        with patch.object(model, "_get_recent_games", return_value=[1, 2]), \
             patch.object(model, "_get_team_advanced_stats", return_value=stats):
            winner, confidence, margin = await model.predict(game, db)

        # Both teams have same stats + home advantage → home wins
        assert winner == "Brisbane"
        assert 0.50 <= confidence <= 0.95
        assert 1 <= margin <= 100

    @pytest.mark.asyncio
    async def test_error_returns_safe_default(self, model, game):
        """Any exception inside predict returns a safe default."""
        db = AsyncMock()
        with patch.object(model, "_get_recent_games", side_effect=Exception("DB error")):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 6
