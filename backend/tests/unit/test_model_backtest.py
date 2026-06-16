"""Unit tests for model-level backtest methods in BacktestService.

Tests the new model backtest methods by mocking DB queries and verifying
accuracy, profit, and aggregation calculations.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – lightweight stand-ins for ORM rows
# ---------------------------------------------------------------------------

class _FakePrediction:
    """Mimics a ModelPrediction ORM object."""

    def __init__(self, game_id: int, model_name: str, winner: str,
                 confidence: float, margin: int | None):
        self.id = 1
        self.game_id = game_id
        self.model_name = model_name
        self.winner = winner
        self.confidence = confidence
        self.margin = margin


class _FakeGame:
    """Mimics a Game ORM object."""

    def __init__(self, game_id: int, round_id: int, season: int,
                 home_team: str, away_team: str,
                 home_score: int | None, away_score: int | None,
                 completed: bool = True):
        self.id = game_id
        self.round_id = round_id
        self.season = season
        self.home_team = home_team
        self.away_team = away_team
        self.home_score = home_score
        self.away_score = away_score
        self.completed = completed


# ---------------------------------------------------------------------------
# TestCalculateFromModelPredictions
# ---------------------------------------------------------------------------

class TestCalculateFromModelPredictions:
    """Tests for BacktestService.calculate_backtest_from_model_predictions."""

    @pytest.fixture
    def service(self):
        from packages.shared.services.backtest import BacktestService
        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()
        svc.orchestrator = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_no_predictions_returns_zeros(self, service):
        """When there are no predictions, all metrics should be zero."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(
            mock_db, 2025, "elo"
        )

        assert result["model_name"] == "elo"
        assert result["season"] == 2025
        assert result["total_tips"] == 0
        assert result["total_correct"] == 0
        assert result["overall_accuracy"] == 0.0
        assert result["total_profit"] == 0.0
        assert result["avg_margin"] == 0.0

    @pytest.mark.asyncio
    async def test_all_correct_predictions(self, service):
        """All correct predictions should yield 100% accuracy and positive profit."""
        mock_db = AsyncMock()
        predictions = [
            (
                _FakePrediction(1, "elo", "Brisbane", 0.8, 15),
                _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 100, 80),
            ),
            (
                _FakePrediction(2, "elo", "Melbourne", 0.7, 10),
                _FakeGame(2, 1, 2025, "Melbourne", "Richmond", 90, 70),
            ),
            (
                _FakePrediction(3, "elo", "Geelong", 0.9, 20),
                _FakeGame(3, 2, 2025, "Geelong", "Hawthorn", 110, 85),
            ),
        ]
        mock_result = MagicMock()
        mock_result.all.return_value = predictions
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(
            mock_db, 2025, "elo"
        )

        assert result["total_tips"] == 3
        assert result["total_correct"] == 3
        assert result["overall_accuracy"] == 1.0
        assert result["total_profit"] == 30.0  # 3 × $10
        assert result["avg_margin"] == 15.0  # (15+10+20)/3

    @pytest.mark.asyncio
    async def test_all_wrong_predictions(self, service):
        """All wrong predictions should yield 0% accuracy and negative profit."""
        mock_db = AsyncMock()
        predictions = [
            (
                # Predicted Brisbane but Collingwood won (away_score > home_score)
                _FakePrediction(1, "form", "Brisbane", 0.6, 5),
                _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 70, 80),
            ),
            (
                _FakePrediction(2, "form", "Melbourne", 0.5, 3),
                _FakeGame(2, 1, 2025, "Melbourne", "Richmond", 60, 90),
            ),
        ]
        mock_result = MagicMock()
        mock_result.all.return_value = predictions
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(
            mock_db, 2025, "form"
        )

        assert result["total_tips"] == 2
        assert result["total_correct"] == 0
        assert result["overall_accuracy"] == 0.0
        assert result["total_profit"] == -20.0  # -2 × $10

    @pytest.mark.asyncio
    async def test_mixed_predictions(self, service):
        """Mix of correct and wrong predictions."""
        mock_db = AsyncMock()
        predictions = [
            (
                # Correct: Brisbane won at home
                _FakePrediction(1, "value", "Brisbane", 0.8, 15),
                _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 100, 80),
            ),
            (
                # Wrong: predicted Melbourne but Richmond won (away_score > home_score)
                _FakePrediction(2, "value", "Melbourne", 0.6, 5),
                _FakeGame(2, 2, 2025, "Melbourne", "Richmond", 60, 70),
            ),
            (
                # Correct: Geelong won at home
                _FakePrediction(3, "value", "Geelong", 0.7, 12),
                _FakeGame(3, 2, 2025, "Geelong", "Hawthorn", 95, 80),
            ),
            (
                # Wrong: predicted Carlton but Essendon won (away_score > home_score)
                _FakePrediction(4, "value", "Carlton", 0.55, 3),
                _FakeGame(4, 3, 2025, "Carlton", "Essendon", 65, 70),
            ),
        ]
        mock_result = MagicMock()
        mock_result.all.return_value = predictions
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(
            mock_db, 2025, "value"
        )

        assert result["total_tips"] == 4
        assert result["total_correct"] == 2
        assert result["overall_accuracy"] == 0.5
        assert result["total_profit"] == 0.0  # 2×10 - 2×10 = 0

    @pytest.mark.asyncio
    async def test_avg_margin_calculation(self, service):
        """Average margin is calculated correctly."""
        mock_db = AsyncMock()
        predictions = [
            (
                _FakePrediction(1, "elo", "Brisbane", 0.8, 15),
                _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 100, 80),
            ),
            (
                _FakePrediction(2, "elo", "Brisbane", 0.7, 5),
                _FakeGame(2, 2, 2025, "Brisbane", "Carlton", 90, 85),
            ),
        ]
        mock_result = MagicMock()
        mock_result.all.return_value = predictions
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(
            mock_db, 2025, "elo"
        )

        assert result["avg_margin"] == 10.0  # (15+5)/2

    @pytest.mark.asyncio
    async def test_none_margin_treated_as_zero(self, service):
        """Predictions with None margin should be treated as 0 in avg_margin."""
        mock_db = AsyncMock()
        pred = _FakePrediction(1, "elo", "Brisbane", 0.8, None)
        game = _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 100, 80)
        mock_result = MagicMock()
        mock_result.all.return_value = [(pred, game)]
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(
            mock_db, 2025, "elo"
        )

        assert result["avg_margin"] == 0.0


# ---------------------------------------------------------------------------
# TestCompareModels
# ---------------------------------------------------------------------------

class TestCompareModels:
    """Tests for BacktestService.compare_models."""

    @pytest.fixture
    def service(self):
        from packages.shared.services.backtest import BacktestService
        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()
        svc.orchestrator = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_compare_multiple_models(self, service):
        """compare_models returns results for all models sorted by accuracy."""
        mock_db = AsyncMock()

        # First call: get distinct model names
        names_result = MagicMock()
        names_result.all.return_value = [("elo",), ("form",), ("value",)]

        # Second call onwards: calculate_backtest_from_model_predictions
        # We'll mock calculate_backtest_from_model_predictions directly
        async def fake_calc(db, season, model_name):
            data = {
                "elo": {"model_name": "elo", "season": season, "total_tips": 10,
                        "total_correct": 7, "overall_accuracy": 0.7,
                        "total_profit": 40.0, "avg_margin": 12.0},
                "form": {"model_name": "form", "season": season, "total_tips": 10,
                         "total_correct": 5, "overall_accuracy": 0.5,
                         "total_profit": 0.0, "avg_margin": 8.0},
                "value": {"model_name": "value", "season": season, "total_tips": 10,
                          "total_correct": 8, "overall_accuracy": 0.8,
                          "total_profit": 60.0, "avg_margin": 15.0},
            }
            return data[model_name]

        mock_db.execute.return_value = names_result
        service.calculate_backtest_from_model_predictions = AsyncMock(
            side_effect=fake_calc
        )

        result = await service.compare_models(mock_db, 2025)

        assert len(result) == 3
        # Should be sorted by accuracy descending
        assert result[0]["model_name"] == "value"
        assert result[0]["overall_accuracy"] == 0.8
        assert result[1]["model_name"] == "elo"
        assert result[1]["overall_accuracy"] == 0.7
        assert result[2]["model_name"] == "form"
        assert result[2]["overall_accuracy"] == 0.5

    @pytest.mark.asyncio
    async def test_compare_empty_no_models(self, service):
        """compare_models returns empty list when no models exist."""
        mock_db = AsyncMock()
        names_result = MagicMock()
        names_result.all.return_value = []
        mock_db.execute.return_value = names_result

        result = await service.compare_models(mock_db, 2025)

        assert result == []

    @pytest.mark.asyncio
    async def test_compare_single_model(self, service):
        """compare_models works with a single model."""
        mock_db = AsyncMock()
        names_result = MagicMock()
        names_result.all.return_value = [("elo",)]
        mock_db.execute.return_value = names_result

        service.calculate_backtest_from_model_predictions = AsyncMock(
            return_value={
                "model_name": "elo", "season": 2025, "total_tips": 5,
                "total_correct": 3, "overall_accuracy": 0.6,
                "total_profit": 10.0, "avg_margin": 10.0,
            }
        )

        result = await service.compare_models(mock_db, 2025)

        assert len(result) == 1
        assert result[0]["model_name"] == "elo"


# ---------------------------------------------------------------------------
# TestGetModelRoundByRound
# ---------------------------------------------------------------------------

class TestGetModelRoundByRound:
    """Tests for BacktestService.get_model_round_by_round."""

    @pytest.fixture
    def service(self):
        from packages.shared.services.backtest import BacktestService
        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()
        svc.orchestrator = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_round_by_round_aggregation(self, service):
        """Per-round accuracy and profit are calculated correctly."""
        mock_db = AsyncMock()
        # Simulate aggregated rows: (round_id, tips_made, tips_correct, profit)
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (1, 4, 3, 20.0),   # Round 1: 3/4 correct, +$20
            (2, 4, 2, 0.0),    # Round 2: 2/4 correct, $0
            (3, 3, 1, -10.0),  # Round 3: 1/3 correct, -$10
        ]
        mock_db.execute.return_value = mock_result

        result = await service.get_model_round_by_round(mock_db, 2025, "elo")

        assert len(result) == 3

        assert result[0]["round_id"] == 1
        assert result[0]["tips_made"] == 4
        assert result[0]["tips_correct"] == 3
        assert result[0]["accuracy"] == 0.75
        assert result[0]["profit"] == 20.0

        assert result[1]["round_id"] == 2
        assert result[1]["accuracy"] == 0.5

        assert result[2]["round_id"] == 3
        assert result[2]["accuracy"] == pytest.approx(1 / 3)

    @pytest.mark.asyncio
    async def test_round_by_round_no_data(self, service):
        """Empty result when no predictions exist."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        result = await service.get_model_round_by_round(mock_db, 2025, "elo")

        assert result == []

    @pytest.mark.asyncio
    async def test_round_by_round_zero_tips(self, service):
        """Round with zero tips should have 0.0 accuracy (guard division)."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (1, 0, 0, 0.0),
        ]
        mock_db.execute.return_value = mock_result

        result = await service.get_model_round_by_round(mock_db, 2025, "elo")

        assert len(result) == 1
        assert result[0]["accuracy"] == 0.0


# ---------------------------------------------------------------------------
# TestRunModelBacktest
# ---------------------------------------------------------------------------

class TestRunModelBacktest:
    """Tests for BacktestService.run_model_backtest."""

    @pytest.fixture
    def service(self):
        from packages.shared.services.backtest import BacktestService
        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()
        # Mock orchestrator with models
        mock_model = MagicMock()
        mock_model.get_name.return_value = "elo"
        mock_model.predict = AsyncMock(return_value=("Brisbane", 0.8, 15))
        svc.orchestrator = MagicMock()
        svc.orchestrator.models = [mock_model]
        return svc

    @pytest.mark.asyncio
    async def test_generate_predictions_and_compare(self, service):
        """run_model_backtest generates predictions for games without them."""
        mock_db = AsyncMock()

        # First execute: completed games
        games_result = MagicMock()
        game1 = _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 100, 80)
        games_result.scalars.return_value.all.return_value = [game1]

        # Second execute: existing predictions (empty — no (game_id, model_name) pairs)
        existing_result = MagicMock()
        existing_result.all.return_value = []

        # Track execute calls
        call_count = 0

        def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return games_result
            elif call_count == 2:
                return existing_result
            return MagicMock()

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        # Mock ModelPredictionCRUD.create
        with patch("packages.shared.services.backtest.ModelPredictionCRUD") as mock_crud:
            mock_crud.create = AsyncMock(
                return_value=_FakePrediction(1, "elo", "Brisbane", 0.8, 15)
            )

            # Mock compare_models
            expected_comparison = [
                {"model_name": "elo", "season": 2025, "total_tips": 1,
                 "total_correct": 1, "overall_accuracy": 1.0,
                 "total_profit": 10.0, "avg_margin": 15.0},
            ]
            service.compare_models = AsyncMock(return_value=expected_comparison)

            result = await service.run_model_backtest(mock_db, 2025)

        assert len(result) == 1
        assert result[0]["model_name"] == "elo"
        assert result[0]["overall_accuracy"] == 1.0

    @pytest.mark.asyncio
    async def test_skip_when_all_models_already_have_predictions(self, service):
        """run_model_backtest skips prediction when game+model already exists."""
        mock_db = AsyncMock()

        games_result = MagicMock()
        game1 = _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 100, 80)
        games_result.scalars.return_value.all.return_value = [game1]

        # Existing predictions now return (game_id, model_name) tuples
        existing_result = MagicMock()
        existing_result.all.return_value = [(1, "elo")]  # Game 1 already has "elo" prediction

        call_count = 0

        def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return games_result
            return existing_result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        expected_comparison = [
            {"model_name": "elo", "season": 2025, "total_tips": 1,
             "total_correct": 1, "overall_accuracy": 1.0,
             "total_profit": 10.0, "avg_margin": 15.0},
        ]
        service.compare_models = AsyncMock(return_value=expected_comparison)

        with patch("packages.shared.services.backtest.ModelPredictionCRUD") as mock_crud:
            result = await service.run_model_backtest(mock_db, 2025)

        # ModelPredictionCRUD.create should NOT have been called
        mock_crud.create.assert_not_called()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_generates_only_missing_models_for_partial_predictions(self, service):
        """run_model_backtest generates predictions only for models missing from a game."""
        mock_db = AsyncMock()

        games_result = MagicMock()
        game1 = _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 100, 80)
        games_result.scalars.return_value.all.return_value = [game1]

        # Game 1 already has "weather_impact" prediction but NOT "elo"
        existing_result = MagicMock()
        existing_result.all.return_value = [(1, "weather_impact")]

        call_count = 0

        def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return games_result
            return existing_result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        expected_comparison = [
            {"model_name": "elo", "season": 2025, "total_tips": 1,
             "total_correct": 1, "overall_accuracy": 1.0,
             "total_profit": 10.0, "avg_margin": 15.0},
        ]
        service.compare_models = AsyncMock(return_value=expected_comparison)

        with patch("packages.shared.services.backtest.ModelPredictionCRUD") as mock_crud:
            mock_crud.create = AsyncMock(
                return_value=_FakePrediction(1, "elo", "Brisbane", 0.8, 15)
            )
            result = await service.run_model_backtest(mock_db, 2025)

        # elo prediction should have been generated (it was missing)
        mock_crud.create.assert_called_once()
        assert result[0]["model_name"] == "elo"

    @pytest.mark.asyncio
    async def test_model_predict_error_does_not_abort(self, service):
        """A failing model.predict() should be skipped, not abort the backtest."""
        mock_db = AsyncMock()

        games_result = MagicMock()
        game1 = _FakeGame(1, 1, 2025, "Brisbane", "Collingwood", 100, 80)
        games_result.scalars.return_value.all.return_value = [game1]

        existing_result = MagicMock()
        existing_result.all.return_value = []

        call_count = 0

        def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return games_result
            return existing_result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        # Make model.predict raise an error
        service.orchestrator.models[0].predict = AsyncMock(
            side_effect=Exception("DB connection failed")
        )

        expected_comparison = []
        service.compare_models = AsyncMock(return_value=expected_comparison)

        with patch("packages.shared.services.backtest.ModelPredictionCRUD"):
            result = await service.run_model_backtest(mock_db, 2025)

        # Should complete without error (predictions list will be empty)
        assert result == []


# (FaaS-era TestModelCompareAPIEndpoint removed in Phase 5 � replaced by
# test_app_api_backtest.py, which exercises the same coverage through
# the FastAPI TestClient.)
