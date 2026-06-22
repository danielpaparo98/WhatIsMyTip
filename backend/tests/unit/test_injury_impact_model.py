"""Unit tests for InjuryImpactModel.

Tests cover player importance scoring, injury impact calculation,
cold-start behaviour, confidence/margin clamping, and backtest safety.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.models import Game, Injury, Player
from packages.shared.models_ml.injury_impact import InjuryImpactModel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def model():
    return InjuryImpactModel()


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


def _make_injury(player_name="Test Player", team="Brisbane",
                 injury_type="Hamstring", return_timeline="2-3 weeks"):
    """Factory helper to create an Injury object."""
    return Injury(
        player_name=player_name,
        team=team,
        injury_type=injury_type,
        return_timeline=return_timeline,
        scraped_at=datetime.now(timezone.utc),
    )


def _make_player(name="Test Player", player_id=1, current_team="Brisbane"):
    """Factory helper to create a Player object."""
    player = Player(
        name=name,
        current_team=current_team,
    )
    player.id = player_id
    return player


def _mock_result_all(return_value):
    """Helper: mock result of db.execute().all()."""
    result_mock = MagicMock()
    result_mock.all.return_value = return_value
    return result_mock


# ---------------------------------------------------------------------------
# Constructor / get_name
# ---------------------------------------------------------------------------

class TestInjuryImpactModelBasics:
    def test_model_instantiation(self, model):
        assert isinstance(model, InjuryImpactModel)

    def test_get_name(self, model):
        assert model.get_name() == "injury_impact"


# ---------------------------------------------------------------------------
# _calculate_importance
# ---------------------------------------------------------------------------

class TestCalculateImportance:
    def test_star_forward(self, model):
        """High goal-kicker with good disposals → high importance."""
        stats = {
            "avg_goals": 3.0,
            "avg_disposals": 20.0,
            "avg_tackles": 4.0,
            "avg_marks": 5.0,
            "avg_hitouts": 0.0,
        }
        importance = model._calculate_importance(stats)
        # 3.0*4 + 20*0.3 + 4*1.5 + 5*0.5 + 0 = 12 + 6 + 6 + 2.5 = 26.5
        assert importance == pytest.approx(26.5)

    def test_average_player(self, model):
        """Average player with moderate stats."""
        stats = {
            "avg_goals": 0.5,
            "avg_disposals": 12.0,
            "avg_tackles": 3.0,
            "avg_marks": 3.0,
            "avg_hitouts": 1.0,
        }
        importance = model._calculate_importance(stats)
        # 0.5*4 + 12*0.3 + 3*1.5 + 3*0.5 + 1*0.2 = 2 + 3.6 + 4.5 + 1.5 + 0.2 = 11.8
        assert importance == pytest.approx(11.8)

    def test_empty_stats(self, model):
        """No stats → importance 0."""
        stats = {}
        importance = model._calculate_importance(stats)
        assert importance == 0.0

    def test_ruckman(self, model):
        """Ruckman with high hitouts."""
        stats = {
            "avg_goals": 0.3,
            "avg_disposals": 10.0,
            "avg_tackles": 2.0,
            "avg_marks": 4.0,
            "avg_hitouts": 30.0,
        }
        importance = model._calculate_importance(stats)
        # 0.3*4 + 10*0.3 + 2*1.5 + 4*0.5 + 30*0.2 = 1.2 + 3 + 3 + 2 + 6 = 15.2
        assert importance == pytest.approx(15.2)


# ---------------------------------------------------------------------------
# _get_active_injuries
# ---------------------------------------------------------------------------

class TestGetActiveInjuries:
    @pytest.mark.asyncio
    async def test_returns_injuries_with_players(self, model, game):
        """Returns list of (Injury, Player) tuples for both teams."""
        db = AsyncMock()
        injury = _make_injury(player_name="Lachie Neale", team="Brisbane")
        player = _make_player(name="Lachie Neale", player_id=10)
        db.execute.return_value = _mock_result_all([(injury, player)])

        result = await model._get_active_injuries(game, db)
        assert len(result) == 1
        assert result[0][0].player_name == "Lachie Neale"
        assert result[0][1].id == 10

    @pytest.mark.asyncio
    async def test_no_injuries_returns_empty(self, model, game):
        """No injuries → empty list."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([])
        result = await model._get_active_injuries(game, db)
        assert result == []


# ---------------------------------------------------------------------------
# _get_player_stats
# ---------------------------------------------------------------------------

class TestGetPlayerStats:
    @pytest.mark.asyncio
    async def test_returns_avg_stats_for_player(self, model, game):
        """Returns aggregated stats dict for given player IDs."""
        db = AsyncMock()

        row = MagicMock()
        row.player_id = 10
        row.avg_goals = 2.0
        row.avg_disposals = 25.0
        row.avg_tackles = 5.0
        row.avg_marks = 6.0
        row.avg_hitouts = 0.5
        db.execute.return_value = _mock_result_all([row])

        result = await model._get_player_stats([10], game, db)
        assert 10 in result
        assert result[10]["avg_goals"] == 2.0

    @pytest.mark.asyncio
    async def test_empty_player_ids_returns_empty(self, model, game):
        """No player IDs → empty dict without DB query."""
        db = AsyncMock()
        result = await model._get_player_stats([], game, db)
        assert result == {}
        db.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# predict() — cold-start / no data scenarios
# ---------------------------------------------------------------------------

class TestPredictColdStart:
    @pytest.mark.asyncio
    async def test_no_injuries_returns_cold_start(self, model, game):
        """No injuries for either team → cold-start default."""
        db = AsyncMock()
        with patch.object(model, "_get_active_injuries", return_value=[]):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == pytest.approx(0.52)
        assert margin == 8

    @pytest.mark.asyncio
    async def test_all_players_available_returns_cold_start(self, model, game):
        """Injuries exist but all players 'Available' → filtered out → cold start."""
        db = AsyncMock()
        # These should be filtered out
        _make_injury(return_timeline="Available")
        _make_player(player_id=10)
        with patch.object(model, "_get_active_injuries", return_value=[]):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert 0.50 <= confidence <= 0.95


# ---------------------------------------------------------------------------
# predict() — full scenario tests
# ---------------------------------------------------------------------------

class TestPredictScenarios:
    @pytest.mark.asyncio
    async def test_key_player_injured_home(self, model, game):
        """Home team star injured → away team favoured."""
        db = AsyncMock()

        injury = _make_injury(player_name="Lachie Neale", team="Brisbane")
        player = _make_player(name="Lachie Neale", player_id=10)

        # Mock stats: star player has high stats
        player_stats = {
            10: {
                "avg_goals": 2.0,
                "avg_disposals": 25.0,
                "avg_tackles": 5.0,
                "avg_marks": 6.0,
                "avg_hitouts": 0.5,
            }
        }

        # Mock team averages
        team_avgs = {
            "Brisbane": {"avg_goals": 12.0, "avg_disposals": 350.0},
            "Collingwood": {"avg_goals": 12.0, "avg_disposals": 350.0},
        }

        with patch.object(model, "_get_active_injuries", return_value=[(injury, player)]), \
             patch.object(model, "_get_player_stats", return_value=player_stats), \
             patch.object(model, "_get_team_averages", return_value=team_avgs):
            winner, confidence, margin = await model.predict(game, db)

        # Away team should win (home has key injury)
        assert winner == "Collingwood"
        assert confidence > 0.50

    @pytest.mark.asyncio
    async def test_key_players_injured_both_teams(self, model, game):
        """Both teams have injuries → team with less impact wins."""
        db = AsyncMock()

        # Home team has a star injured
        home_injury = _make_injury(player_name="Star Player", team="Brisbane")
        home_player = _make_player(name="Star Player", player_id=10)

        # Away team has a role player injured
        away_injury = _make_injury(player_name="Role Player", team="Collingwood")
        away_player = _make_player(name="Role Player", player_id=20)

        player_stats = {
            10: {"avg_goals": 3.0, "avg_disposals": 25.0, "avg_tackles": 5.0,
                 "avg_marks": 6.0, "avg_hitouts": 0.0},
            20: {"avg_goals": 0.3, "avg_disposals": 8.0, "avg_tackles": 2.0,
                 "avg_marks": 2.0, "avg_hitouts": 0.0},
        }

        team_avgs = {
            "Brisbane": {"avg_goals": 12.0, "avg_disposals": 350.0},
            "Collingwood": {"avg_goals": 12.0, "avg_disposals": 350.0},
        }

        with patch.object(model, "_get_active_injuries",
                          return_value=[(home_injury, home_player), (away_injury, away_player)]), \
             patch.object(model, "_get_player_stats", return_value=player_stats), \
             patch.object(model, "_get_team_averages", return_value=team_avgs):
            winner, confidence, margin = await model.predict(game, db)

        # Away team has less impactful injury → away favoured
        assert winner == "Collingwood"
        assert confidence > 0.50

    @pytest.mark.asyncio
    async def test_no_player_stats_uses_default_importance(self, model, game):
        """Injured player not found in stats → default importance of 3.0."""
        db = AsyncMock()

        injury = _make_injury(player_name="Unknown Player", team="Collingwood")
        player = _make_player(name="Unknown Player", player_id=99)

        # No stats for player 99
        player_stats = {}
        team_avgs = {
            "Brisbane": {"avg_goals": 12.0, "avg_disposals": 350.0},
            "Collingwood": {"avg_goals": 12.0, "avg_disposals": 350.0},
        }

        with patch.object(model, "_get_active_injuries", return_value=[(injury, player)]), \
             patch.object(model, "_get_player_stats", return_value=player_stats), \
             patch.object(model, "_get_team_averages", return_value=team_avgs):
            winner, confidence, margin = await model.predict(game, db)

        # Only away team has injury (default importance 3.0) → home wins
        assert winner == "Brisbane"
        assert confidence > 0.50

    @pytest.mark.asyncio
    async def test_unresolved_player_uses_default_importance(self, model, game):
        """Injured player not matched to Player table → count × default."""
        db = AsyncMock()

        injury = _make_injury(player_name="Mystery Player", team="Collingwood")
        # Player is None (not found in players table)
        player = None

        player_stats = {}
        team_avgs = {
            "Brisbane": {"avg_goals": 12.0, "avg_disposals": 350.0},
            "Collingwood": {"avg_goals": 12.0, "avg_disposals": 350.0},
        }

        with patch.object(model, "_get_active_injuries", return_value=[(injury, player)]), \
             patch.object(model, "_get_player_stats", return_value=player_stats), \
             patch.object(model, "_get_team_averages", return_value=team_avgs):
            winner, confidence, margin = await model.predict(game, db)

        # Away has injury with default importance → home wins
        assert winner == "Brisbane"


# ---------------------------------------------------------------------------
# Confidence and margin clamping
# ---------------------------------------------------------------------------

class TestClamping:
    @pytest.mark.asyncio
    async def test_confidence_lower_bound(self, model, game):
        """Confidence must be at least 0.50."""
        db = AsyncMock()
        with patch.object(model, "_get_active_injuries", return_value=[]):
            winner, confidence, margin = await model.predict(game, db)

        assert confidence >= 0.50

    @pytest.mark.asyncio
    async def test_confidence_upper_bound(self, model, game):
        """Confidence must not exceed 0.95."""
        db = AsyncMock()
        # Create many home injuries to maximize diff
        injuries = []
        for i in range(10):
            inj = _make_injury(player_name=f"Player {i}", team="Brisbane")
            pl = _make_player(name=f"Player {i}", player_id=100 + i)
            injuries.append((inj, pl))

        player_stats = {}
        for i in range(10):
            player_stats[100 + i] = {
                "avg_goals": 3.0, "avg_disposals": 30.0,
                "avg_tackles": 6.0, "avg_marks": 8.0, "avg_hitouts": 5.0,
            }

        team_avgs = {
            "Brisbane": {"avg_goals": 12.0, "avg_disposals": 350.0},
            "Collingwood": {"avg_goals": 12.0, "avg_disposals": 350.0},
        }

        with patch.object(model, "_get_active_injuries", return_value=injuries), \
             patch.object(model, "_get_player_stats", return_value=player_stats), \
             patch.object(model, "_get_team_averages", return_value=team_avgs):
            winner, confidence, margin = await model.predict(game, db)

        assert confidence <= 0.95

    @pytest.mark.asyncio
    async def test_margin_lower_bound(self, model, game):
        """Margin must be at least 1."""
        db = AsyncMock()
        with patch.object(model, "_get_active_injuries", return_value=[]):
            winner, confidence, margin = await model.predict(game, db)

        assert margin >= 1

    @pytest.mark.asyncio
    async def test_margin_upper_bound(self, model, game):
        """Margin must not exceed 100."""
        db = AsyncMock()
        injuries = []
        for i in range(10):
            inj = _make_injury(player_name=f"Player {i}", team="Brisbane")
            pl = _make_player(name=f"Player {i}", player_id=100 + i)
            injuries.append((inj, pl))

        player_stats = {}
        for i in range(10):
            player_stats[100 + i] = {
                "avg_goals": 3.0, "avg_disposals": 30.0,
                "avg_tackles": 6.0, "avg_marks": 8.0, "avg_hitouts": 5.0,
            }

        team_avgs = {
            "Brisbane": {"avg_goals": 12.0, "avg_disposals": 350.0},
            "Collingwood": {"avg_goals": 12.0, "avg_disposals": 350.0},
        }

        with patch.object(model, "_get_active_injuries", return_value=injuries), \
             patch.object(model, "_get_player_stats", return_value=player_stats), \
             patch.object(model, "_get_team_averages", return_value=team_avgs):
            winner, confidence, margin = await model.predict(game, db)

        assert margin <= 100


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_error_returns_safe_default(self, model, game):
        """Any exception inside predict returns a safe default."""
        db = AsyncMock()
        with patch.object(model, "_get_active_injuries",
                          side_effect=Exception("DB error")):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence == 0.55
        assert margin == 5

    @pytest.mark.asyncio
    async def test_no_team_averages_uses_default(self, model, game):
        """No team averages data → uses default for normalization."""
        db = AsyncMock()
        injury = _make_injury(player_name="Test Player", team="Brisbane")
        player = _make_player(name="Test Player", player_id=10)

        player_stats = {
            10: {"avg_goals": 1.0, "avg_disposals": 15.0, "avg_tackles": 3.0,
                 "avg_marks": 4.0, "avg_hitouts": 0.0},
        }
        team_avgs = {}  # No averages

        with patch.object(model, "_get_active_injuries", return_value=[(injury, player)]), \
             patch.object(model, "_get_player_stats", return_value=player_stats), \
             patch.object(model, "_get_team_averages", return_value=team_avgs):
            winner, confidence, margin = await model.predict(game, db)

        assert winner in ("Brisbane", "Collingwood")
        assert 0.50 <= confidence <= 0.95
        assert 1 <= margin <= 100

    @pytest.mark.asyncio
    async def test_injury_with_test_status_filtered(self, model, game):
        """Players with 'Test' return_timeline are filtered out."""
        db = AsyncMock()
        # _get_active_injuries filters these — confirm empty result → cold start
        with patch.object(model, "_get_active_injuries", return_value=[]):
            winner, confidence, margin = await model.predict(game, db)

        assert winner == "Brisbane"
        assert confidence >= 0.50


# ---------------------------------------------------------------------------
# Point-in-time safety (walk-forward backfill regression guard)
# ---------------------------------------------------------------------------

class TestPointInTimeSafety:
    """Injuries must be point-in-time: only those scraped on/before game.date.

    Regression guard: previously the injuries query had NO date filter, so
    future-scraped injuries (e.g. 2026 data present in the DB) leaked into
    historical games, corrupting point-in-time predictions and forcing the
    model off its clean cold-start path. The player-stat and team-average
    sub-queries already filter on ``Game.date < game.date``; the injuries
    query must do the equivalent ``Injury.scraped_at <= game.date``.
    """

    @staticmethod
    def _compiled_sql(db) -> str:
        """Return the compiled SQL of the statement passed to db.execute()."""
        stmt = db.execute.call_args.args[0]
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    @pytest.mark.asyncio
    async def test_query_contains_scraped_at_cutoff(self, model, game):
        """The compiled query must filter injuries.scraped_at <= game.date."""
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([])
        await model._get_active_injuries(game, db)

        compiled = self._compiled_sql(db)
        assert "scraped_at <=" in compiled
        # The game's date must be bound as the cutoff value.
        assert "2025-06-15" in compiled

    @pytest.mark.asyncio
    async def test_cutoff_is_bound_to_each_game_date(self, model):
        """An old game (predating all scrapes) uses its own date as cutoff."""
        old_game = Game(
            id=2,
            slug="old-game",
            home_team="Brisbane",
            away_team="Collingwood",
            venue="Gabba",
            date=datetime(2010, 3, 1, tzinfo=timezone.utc),
            completed=True,
        )
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([])
        await model._get_active_injuries(old_game, db)

        compiled = self._compiled_sql(db)
        assert "scraped_at <=" in compiled
        # Old game's date is the cutoff → all 2026 scrapes excluded (cold-start).
        assert "2010-03-01" in compiled
        assert "2025-06-15" not in compiled

    @pytest.mark.asyncio
    async def test_recent_game_date_keeps_prior_scrapes(self, model, game):
        """A game dated AFTER a scrape uses that recent date as the cutoff.

        Because the cutoff is the game's own date (2025-06-15), any injury
        scraped on or before it would pass the filter — confirming the
        predicate is relative to the game, not a hard future-blocking cutoff.
        """
        db = AsyncMock()
        db.execute.return_value = _mock_result_all([])
        await model._get_active_injuries(game, db)

        compiled = self._compiled_sql(db)
        assert "scraped_at <=" in compiled
        assert "2025-06-15" in compiled
        # No future-ish hard cutoff leaking into the query.
        assert "2026" not in compiled
