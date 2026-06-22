"""Unit tests for new SQLAlchemy model definitions.

Tests model instantiation, default values, and table names for the
Player, MatchWeather, PlayerMatchStats, PlayerAdvancedStats, and Injury
models without requiring a database.
"""

from datetime import date, datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from packages.shared.models import (
    Game,
    Injury,
    MatchWeather,
    ModelCoefficient,
    ModelVersion,
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


class TestModelVersionModel:
    def test_model_version_instantiation(self):
        version = ModelVersion(
            model_name="weighted_tip",
            version=3,
            intercept=1.25,
            training_rows=1234,
            metrics={"r2": 0.71, "mae": 11.2},
            is_active=True,
        )
        assert version.model_name == "weighted_tip"
        assert version.version == 3
        assert version.intercept == 1.25
        assert version.training_rows == 1234
        assert version.metrics == {"r2": 0.71, "mae": 11.2}
        assert version.is_active is True

    def test_model_version_column_defaults(self):
        """insert-time defaults: intercept=0.0, training_rows=0, is_active=False.

        SQLAlchemy ``Column(default=...)`` applies at flush, not at
        instantiation, so we inspect the configured default on the column
        metadata rather than an un-flushed instance.
        """
        assert ModelVersion.__table__.c.intercept.default.arg == 0.0
        assert ModelVersion.__table__.c.training_rows.default.arg == 0
        assert ModelVersion.__table__.c.is_active.default.arg is False
        # metrics is nullable JSONB (no default).
        assert ModelVersion.__table__.c.metrics.default is None

    def test_model_version_tablename(self):
        assert ModelVersion.__tablename__ == "model_versions"

    def test_model_version_expected_columns(self):
        columns = set(ModelVersion.__table__.c.keys())
        for expected in [
            "id",
            "model_name",
            "version",
            "intercept",
            "trained_at",
            "training_rows",
            "metrics",
            "is_active",
            "created_at",
        ]:
            assert expected in columns, f"ModelVersion missing column {expected}"

    def test_model_version_metrics_is_jsonb(self):
        assert isinstance(ModelVersion.__table__.c.metrics.type, JSONB)

    def test_model_version_unique_constraint(self):
        """Verify unique constraint on (model_name, version)."""
        constraints = {
            c.name
            for c in ModelVersion.__table__.constraints
            if hasattr(c, "name") and c.name is not None
        }
        assert "uq_model_versions_name_version" in constraints

    def test_model_version_has_coefficients_relationship(self):
        """Verify the relationship to ModelCoefficient."""
        assert hasattr(ModelVersion, "coefficients")


class TestModelCoefficientModel:
    def test_model_coefficient_instantiation(self):
        coeff = ModelCoefficient(
            model_version_id=7,
            feature_name="elo",
            coefficient=0.42,
        )
        assert coeff.model_version_id == 7
        assert coeff.feature_name == "elo"
        assert coeff.coefficient == 0.42

    def test_model_coefficient_tablename(self):
        assert ModelCoefficient.__tablename__ == "model_coefficients"

    def test_model_coefficient_expected_columns(self):
        columns = set(ModelCoefficient.__table__.c.keys())
        for expected in [
            "id",
            "model_version_id",
            "feature_name",
            "coefficient",
            "created_at",
        ]:
            assert expected in columns, f"ModelCoefficient missing column {expected}"

    def test_model_coefficient_fk_references_model_versions_with_cascade(self):
        """model_version_id FK → model_versions.id with ON DELETE CASCADE."""
        fks = list(ModelCoefficient.__table__.c.model_version_id.foreign_keys)
        assert len(fks) == 1
        fk = fks[0]
        assert fk.column.table.name == "model_versions"
        assert fk.ondelete == "CASCADE"

    def test_model_coefficient_unique_constraint(self):
        """Verify unique constraint on (model_version_id, feature_name)."""
        constraints = {
            c.name
            for c in ModelCoefficient.__table__.constraints
            if hasattr(c, "name") and c.name is not None
        }
        assert "uq_model_coefficients_version_feature" in constraints

    def test_model_coefficient_has_model_version_relationship(self):
        """Verify the relationship to ModelVersion."""
        assert hasattr(ModelCoefficient, "model_version")
