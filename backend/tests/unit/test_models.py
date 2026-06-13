"""Unit tests for SQLAlchemy model definitions.

Tests model instantiation and default values without requiring a database.
"""

from datetime import datetime, timezone

from packages.shared.models import (
    BacktestResult,
    EloCache,
    Game,
    GenerationProgress,
    JobExecution,
    JobLock,
    MatchAnalysis,
    ModelPrediction,
    Tip,
)


class TestGameModel:
    def test_game_instantiation(self):
        game = Game(
            slug="abc123def4",
            squiggle_id=12345,
            round_id=1,
            season=2025,
            home_team="Brisbane",
            away_team="Collingwood",
            venue="Gabba",
        )
        assert game.slug == "abc123def4"
        assert game.home_team == "Brisbane"
        assert game.away_team == "Collingwood"
        assert game.venue == "Gabba"
        assert game.season == 2025
        assert game.round_id == 1

    def test_game_default_values(self):
        game = Game(slug="test-slug")
        # SQLAlchemy Column defaults are database-level; Python objects
        # get None until flushed. We test that the attributes are accessible.
        assert game.home_score is None
        assert game.away_score is None
        assert game.home_team is None
        assert game.away_team is None

    def test_game_tablename(self):
        assert Game.__tablename__ == "games"


class TestTipModel:
    def test_tip_instantiation(self):
        tip = Tip(
            game_id=1,
            heuristic="best_bet",
            selected_team="Brisbane",
            margin=12,
            confidence=0.75,
            explanation="Strong at home",
        )
        assert tip.game_id == 1
        assert tip.heuristic == "best_bet"
        assert tip.selected_team == "Brisbane"
        assert tip.margin == 12
        assert tip.confidence == 0.75

    def test_tip_tablename(self):
        assert Tip.__tablename__ == "tips"


class TestModelPredictionModel:
    def test_prediction_instantiation(self):
        pred = ModelPrediction(
            game_id=1,
            model_name="elo",
            winner="Brisbane",
            confidence=0.7,
            margin=12,
        )
        assert pred.game_id == 1
        assert pred.model_name == "elo"
        assert pred.winner == "Brisbane"

    def test_prediction_tablename(self):
        assert ModelPrediction.__tablename__ == "model_predictions"


class TestBacktestResultModel:
    def test_backtest_instantiation(self):
        bt = BacktestResult(
            heuristic="best_bet",
            season=2025,
            round_id=5,
            tips_made=9,
            tips_correct=6,
            accuracy=0.667,
            profit=3.5,
        )
        assert bt.heuristic == "best_bet"
        assert bt.season == 2025
        assert bt.accuracy == 0.667

    def test_backtest_tablename(self):
        assert BacktestResult.__tablename__ == "backtest_results"


class TestGenerationProgressModel:
    def test_generation_progress_instantiation(self):
        gp = GenerationProgress(
            operation_type="historical_generation",
            season=2025,
            total_items=100,
            completed_items=50,
            status="in_progress",
        )
        assert gp.operation_type == "historical_generation"
        assert gp.status == "in_progress"
        assert gp.total_items == 100

    def test_generation_progress_explicit_values(self):
        """Test with explicit values since SQLAlchemy defaults are DB-level."""
        gp = GenerationProgress(
            operation_type="test",
            total_items=0,
            completed_items=0,
            status="pending",
        )
        assert gp.total_items == 0
        assert gp.completed_items == 0
        assert gp.status == "pending"

    def test_generation_progress_tablename(self):
        assert GenerationProgress.__tablename__ == "generation_progress"


class TestJobExecutionModel:
    def test_job_execution_instantiation(self):
        je = JobExecution(
            job_name="daily-sync",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        assert je.job_name == "daily-sync"
        assert je.status == "running"

    def test_job_execution_tablename(self):
        assert JobExecution.__tablename__ == "job_executions"


class TestJobLockModel:
    def test_job_lock_instantiation(self):
        jl = JobLock(
            job_name="daily-sync",
            locked_at=datetime.now(timezone.utc),
            locked_by="test-instance",
            expires_at=datetime.now(timezone.utc),
        )
        assert jl.job_name == "daily-sync"
        assert jl.locked_by == "test-instance"

    def test_job_lock_tablename(self):
        assert JobLock.__tablename__ == "job_locks"


class TestEloCacheModel:
    def test_elo_cache_instantiation(self):
        ec = EloCache(
            team_name="Brisbane",
            rating=1550.0,
            games_played=10,
            last_updated=datetime.now(timezone.utc),
            season=2025,
        )
        assert ec.team_name == "Brisbane"
        assert ec.rating == 1550.0

    def test_elo_cache_tablename(self):
        assert EloCache.__tablename__ == "elo_cache"


class TestMatchAnalysisModel:
    def test_match_analysis_instantiation(self):
        ma = MatchAnalysis(
            game_id=1,
            analysis_text="Brisbane look strong at the Gabba.",
        )
        assert ma.game_id == 1
        assert "Gabba" in ma.analysis_text

    def test_match_analysis_tablename(self):
        assert MatchAnalysis.__tablename__ == "match_analyses"
