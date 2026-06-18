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


# ---------------------------------------------------------------------------
# EloModel cleanup (HI-001)
# ---------------------------------------------------------------------------


class TestEloModelCleanup:
    """Regression tests for the HI-001 EloModel cleanup.

    The fix removes:

    1. The dead ``_update_ratings`` and ``_get_team_ratings``
       instance methods (never called anywhere).
    2. The ``__import__("datetime").timedelta(...)`` runtime-import
       trick inside ``predict``.
    3. The ``.replace(tzinfo=None)`` call on a tz-aware datetime
       (which was a no-op pretending to be defensive code).

    These tests pin the post-fix behaviour: ``predict`` no longer
    relies on the runtime import, the point-in-time backtest path
    still works, and the dead methods are gone.
    """

    def test_no_runtime_datetime_import_in_predict_source(self):
        """``predict`` must NOT use ``__import__('datetime')``."""
        import inspect

        from packages.shared.models_ml.elo import EloModel

        src = inspect.getsource(EloModel.predict)
        assert "__import__" not in src, (
            "EloModel.predict still uses __import__(\"datetime\"); "
            "import timedelta at module top instead."
        )

    def test_no_replace_tzinfo_none_on_now_in_predict_source(self):
        """``predict`` must NOT strip tzinfo from ``datetime.now()``.

        The pre-fix code did ``now.replace(tzinfo=None)`` on a
        tz-aware datetime produced by ``datetime.now(timezone.utc)`` -
        a no-op pretending to be defensive code.  The fix still uses
        ``.replace(tzinfo=None)`` on the game date (which is
        genuinely tz-aware in some rows), but never on the freshly-
        built ``now`` sentinel.
        """
        import inspect
        import re

        from packages.shared.models_ml.elo import EloModel

        src = inspect.getsource(EloModel.predict)
        # Look for the pattern ``now.replace(tzinfo=None)``.
        assert not re.search(
            r"\bnow[a-zA-Z_]*\.replace\(tzinfo=None\)", src
        ), (
            "EloModel.predict still calls .replace(tzinfo=None) on "
            "a freshly-built 'now' datetime; normalise the comparison "
            "value to naive UTC once at the top instead."
        )

    def test_dead_methods_removed(self):
        """The dead ``_update_ratings`` / ``_get_team_ratings`` methods
        must be gone.
        """
        from packages.shared.models_ml.elo import EloModel

        assert not hasattr(EloModel, "_update_ratings"), (
            "_update_ratings is dead code and should be removed."
        )
        # _get_team_ratings only existed on instances; check via
        # ``vars()`` to avoid materialising an instance-level name
        # collision (in case a future refactor re-adds it as a
        # class-level attribute).
        assert "_get_team_ratings" not in vars(EloModel), (
            "_get_team_ratings is dead code and should be removed."
        )

    def test_compute_ratings_from_games_pure_function(self):
        """The shared computation helper remains available for the
        point-in-time backtest path.
        """
        from packages.shared.models_ml.elo import EloModel

        # No I/O — feeds in two ``Game``-shaped objects and a starting
        # ratings dict; expects an updated ratings dict back.
        class _FakeGame:
            def __init__(self, home, away, h_score, a_score):
                self.home_team = home
                self.away_team = away
                self.home_score = h_score
                self.away_score = a_score

        ratings: dict = {}
        result = EloModel._compute_ratings_from_games(
            [_FakeGame("Brisbane", "Collingwood", 80, 60)],
            ratings,
        )
        assert "Brisbane" in result and "Collingwood" in result
        # Brisbane won, so their rating should go up, Collingwood's down.
        assert result["Brisbane"] > 1500.0
        assert result["Collingwood"] < 1500.0

    def test_module_uses_top_level_timedelta_import(self):
        """The module must import ``timedelta`` at the top, not via
        ``__import__`` at runtime.
        """
        import ast
        import inspect

        from packages.shared.models_ml import elo as elo_module

        source = inspect.getsource(elo_module)
        tree = ast.parse(source)
        # Find module-level imports.
        top_imports: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for n in node.names:
                    top_imports.append(n.name)
            elif isinstance(node, ast.Import):
                for n in node.names:
                    top_imports.append(n.name)

        # The module path doesn't matter — we just want at least one
        # ``from datetime import ...`` that mentions ``timedelta``.
        assert any(name == "timedelta" for name in top_imports), (
            "elo.py should import `timedelta` from datetime at module top."
        )
