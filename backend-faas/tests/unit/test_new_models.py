"""Unit tests for new SQLAlchemy model definitions.

Tests model instantiation, default values, and table names for the
Player, MatchWeather, PlayerMatchStats, PlayerAdvancedStats, and Injury
models without requiring a database.
"""

import pytest
from datetime import date, datetime, timezone

from packages.shared.models import (
    Game,
    Injury,
    MatchWeather,
    Player,
    PlayerAdvancedStats,
    PlayerMatchStats,
)


class TestPlayerModel:
    def test_player_instantiation(self):
        player = Player(
            name="Lachie Neale",
            afltables_id="01A/Lachie_Neale",
            footywire_id=12345,
            current_team="Brisbane",
            position="Midfield",
            height="185cm",
            weight="83kg",
            date_of_birth=date(1994, 5, 24),
            draft_info="2012 National Draft - Pick #58",
        )
        assert player.name == "Lachie Neale"
        assert player.afltables_id == "01A/Lachie_Neale"
        assert player.footywire_id == 12345
        assert player.current_team == "Brisbane"
        assert player.position == "Midfield"
        assert player.height == "185cm"
        assert player.weight == "83kg"
        assert player.date_of_birth == date(1994, 5, 24)
        assert player.draft_info == "2012 National Draft - Pick #58"

    def test_player_minimal(self):
        player = Player(name="Patrick Cripps")
        assert player.name == "Patrick Cripps"
        assert player.afltables_id is None
        assert player.footywire_id is None
        assert player.current_team is None
        assert player.position is None

    def test_player_tablename(self):
        assert Player.__tablename__ == "players"

    def test_player_name_is_unique(self):
        """Verify the name column has a unique constraint."""
        assert Player.__table__.c.name.unique is True

    def test_player_afltables_id_is_unique(self):
        """Verify the afltables_id column has a unique constraint."""
        assert Player.__table__.c.afltables_id.unique is True


class TestMatchWeatherModel:
    def test_match_weather_instantiation(self):
        weather = MatchWeather(
            game_id=1,
            venue="MCG",
            match_date=date(2025, 6, 1),
            temperature=15.3,
            precipitation=0.0,
            wind_speed=12.5,
            wind_direction=180,
            wind_gusts=22.1,
            humidity=65,
            weather_code=3,
            data_type="historical",
            raw_hourly={
                "time": ["2025-06-01T04:00", "2025-06-01T05:00"],
                "temperature_2m": [14.5, 15.3],
            },
        )
        assert weather.game_id == 1
        assert weather.venue == "MCG"
        assert weather.temperature == 15.3
        assert weather.humidity == 65
        assert weather.weather_code == 3
        assert weather.data_type == "historical"
        assert weather.raw_hourly["temperature_2m"] == [14.5, 15.3]

    def test_match_weather_minimal(self):
        weather = MatchWeather(game_id=5)
        assert weather.game_id == 5
        assert weather.venue is None
        assert weather.temperature is None
        assert weather.raw_hourly is None

    def test_match_weather_tablename(self):
        assert MatchWeather.__tablename__ == "match_weather"

    def test_match_weather_game_id_is_unique(self):
        """One-to-one with games — game_id has unique constraint."""
        assert MatchWeather.__table__.c.game_id.unique is True

    def test_match_weather_has_game_relationship(self):
        """Verify the relationship to Game model."""
        assert hasattr(MatchWeather, "game")


class TestPlayerMatchStatsModel:
    def test_player_match_stats_instantiation(self):
        stats = PlayerMatchStats(
            game_id=1,
            player_id=10,
            team="Brisbane",
            kicks=15,
            handballs=12,
            disposals=27,
            marks=8,
            goals=2,
            behinds=1,
            tackles=5,
            hitouts=0,
            frees_for=2,
            frees_against=1,
        )
        assert stats.game_id == 1
        assert stats.player_id == 10
        assert stats.team == "Brisbane"
        assert stats.kicks == 15
        assert stats.disposals == 27
        assert stats.goals == 2

    def test_player_match_stats_tablename(self):
        assert PlayerMatchStats.__tablename__ == "player_match_stats"

    def test_player_match_stats_unique_constraint(self):
        """Verify composite unique constraint on (game_id, player_id)."""
        constraints = {
            c.name for c in PlayerMatchStats.__table__.constraints
            if hasattr(c, "name") and c.name is not None
        }
        assert "uq_pms_game_player" in constraints

    def test_player_match_stats_has_relationships(self):
        """Verify relationships to Game and Player models."""
        assert hasattr(PlayerMatchStats, "game")
        assert hasattr(PlayerMatchStats, "player")


class TestPlayerAdvancedStatsModel:
    def test_player_advanced_stats_instantiation(self):
        stats = PlayerAdvancedStats(
            game_id=1,
            player_id=10,
            round_label="R5",
            opponent="Coll",
            tog_pct=82.5,
            metres_gained=450,
            score_involvements=8,
            contested_possessions=12,
            pressure_acts=18,
        )
        assert stats.game_id == 1
        assert stats.player_id == 10
        assert stats.round_label == "R5"
        assert stats.tog_pct == 82.5
        assert stats.metres_gained == 450
        assert stats.score_involvements == 8
        assert stats.contested_possessions == 12
        assert stats.pressure_acts == 18

    def test_player_advanced_stats_tablename(self):
        assert PlayerAdvancedStats.__tablename__ == "player_advanced_stats"

    def test_player_advanced_stats_unique_constraint(self):
        """Verify composite unique constraint on (game_id, player_id)."""
        constraints = {
            c.name for c in PlayerAdvancedStats.__table__.constraints
            if hasattr(c, "name") and c.name is not None
        }
        assert "uq_pas_game_player" in constraints

    def test_player_advanced_stats_has_relationships(self):
        """Verify relationships to Game and Player models."""
        assert hasattr(PlayerAdvancedStats, "game")
        assert hasattr(PlayerAdvancedStats, "player")


class TestInjuryModel:
    def test_injury_instantiation(self):
        injury = Injury(
            player_id=10,
            player_name="Lachie Neale",
            team="Brisbane",
            injury_type="Hamstring",
            return_timeline="2-3 weeks",
            source="footywire",
            scraped_at=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert injury.player_id == 10
        assert injury.player_name == "Lachie Neale"
        assert injury.team == "Brisbane"
        assert injury.injury_type == "Hamstring"
        assert injury.return_timeline == "2-3 weeks"
        assert injury.source == "footywire"

    def test_injury_without_player_id(self):
        """Injuries can exist without a matched player_id."""
        injury = Injury(
            player_name="Unknown Player",
            injury_type="Knee",
            scraped_at=datetime.now(timezone.utc),
        )
        assert injury.player_id is None
        assert injury.player_name == "Unknown Player"

    def test_injury_tablename(self):
        assert Injury.__tablename__ == "injuries"

    def test_injury_unique_constraint(self):
        """Verify unique constraint on (player_name, injury_type)."""
        constraints = {
            c.name for c in Injury.__table__.constraints
            if hasattr(c, "name") and c.name is not None
        }
        assert "uq_injuries_player_injury" in constraints

    def test_injury_has_player_relationship(self):
        """Verify the relationship to Player model."""
        assert hasattr(Injury, "player")


class TestGameModelAfltablesMatchId:
    def test_game_has_afltables_match_id(self):
        """Verify the new afltables_match_id column exists on Game."""
        game = Game(
            slug="test",
            afltables_match_id="2025060101",
        )
        assert game.afltables_match_id == "2025060101"

    def test_game_afltables_match_id_nullable(self):
        """The column should be nullable for existing games."""
        game = Game(slug="test")
        assert game.afltables_match_id is None
