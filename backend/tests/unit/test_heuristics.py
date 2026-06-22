"""Unit tests for heuristic implementations.

These are pure computation tests — no database or Redis mocking required.
The heuristic ``apply()`` methods receive a mock game object and a dict of
model predictions, then return (winner, confidence, margin).
"""

from unittest.mock import MagicMock

import pytest

from packages.shared.heuristics.best_bet import BestBetHeuristic
from packages.shared.heuristics.weighted_tip import WeightedTipHeuristic
from packages.shared.heuristics.yolo import YOLOHeuristic


def _make_game(home_team="Richmond", away_team="Carlton"):
    """Create a mock Game object for testing."""
    game = MagicMock()
    game.home_team = home_team
    game.away_team = away_team
    return game


# ---------------------------------------------------------------------------
# BestBetHeuristic
# ---------------------------------------------------------------------------

class TestBestBetHeuristic:
    def setup_method(self):
        self.heuristic = BestBetHeuristic(models=[])

    def test_get_name(self):
        assert self.heuristic.get_name() == "best_bet"

    @pytest.mark.asyncio
    async def test_consensus_prediction(self):
        """When most models agree, best bet should pick the consensus."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.7, 12),
            "form": ("Richmond", 0.65, 10),
            "home_advantage": ("Richmond", 0.6, 8),
            "value": ("Carlton", 0.55, 5),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert winner == "Richmond"
        assert confidence > 0
        assert margin > 0

    @pytest.mark.asyncio
    async def test_empty_predictions_fallback(self):
        """With no predictions, should fall back to home team."""
        game = _make_game()
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Richmond"
        assert confidence == 0.55
        assert margin == 15

    @pytest.mark.asyncio
    async def test_confidence_capped_at_09(self):
        """Confidence should never exceed 0.9."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.95, 20),
            "form": ("Richmond", 0.95, 18),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert confidence <= 0.9

    @pytest.mark.asyncio
    async def test_single_model_prediction(self):
        """With a single model, that model's winner is chosen."""
        game = _make_game()
        predictions = {
            "elo": ("Carlton", 0.6, 8),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert winner == "Carlton"
        assert confidence > 0

    @pytest.mark.asyncio
    async def test_margin_minimum(self):
        """Margin should be at least 5."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.6, 2),
            "form": ("Richmond", 0.6, 3),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert margin >= 5

    @pytest.mark.asyncio
    async def test_away_team_consensus(self):
        """When most models pick the away team, best bet should agree."""
        game = _make_game()
        predictions = {
            "elo": ("Carlton", 0.7, 12),
            "form": ("Carlton", 0.65, 10),
            "home_advantage": ("Richmond", 0.6, 8),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert winner == "Carlton"


# ---------------------------------------------------------------------------
# YOLOHeuristic
# ---------------------------------------------------------------------------

class TestYOLOHeuristic:
    def setup_method(self):
        self.heuristic = YOLOHeuristic(models=[])

    def test_get_name(self):
        assert self.heuristic.get_name() == "yolo"

    @pytest.mark.asyncio
    async def test_picks_highest_confidence(self):
        """YOLO should pick the model with highest confidence."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.7, 12),
            "form": ("Carlton", 0.9, 15),
            "home_advantage": ("Richmond", 0.6, 8),
            "value": ("Richmond", 0.55, 5),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert winner == "Carlton"  # form model had highest confidence

    @pytest.mark.asyncio
    async def test_empty_predictions_fallback(self):
        """With no predictions, should fall back to home team."""
        game = _make_game()
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Richmond"
        assert confidence == 0.6
        assert margin == 20

    @pytest.mark.asyncio
    async def test_confidence_boosted(self):
        """YOLO should boost confidence slightly."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.7, 12),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        # Boosted: min(0.95, 0.7 * 1.1) = min(0.95, 0.77) = 0.77
        assert confidence == pytest.approx(0.77, abs=0.01)

    @pytest.mark.asyncio
    async def test_confidence_capped_at_095(self):
        """Boosted confidence should never exceed 0.95."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.95, 30),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert confidence <= 0.95

    @pytest.mark.asyncio
    async def test_margin_minimum_10(self):
        """YOLO margin should be at least 10."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.7, 3),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert margin >= 10


# ---------------------------------------------------------------------------
# WeightedTipHeuristic
# ---------------------------------------------------------------------------

class TestWeightedTipHeuristic:
    def setup_method(self):
        self.heuristic = WeightedTipHeuristic(models=[])

    def test_get_name(self):
        assert self.heuristic.get_name() == "weighted_tip"

    @pytest.mark.asyncio
    async def test_empty_predictions_fallback(self):
        """With no predictions, cold-start returns the away team."""
        game = _make_game()
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Carlton"
        assert confidence == 0.55
        assert margin == 6

    @pytest.mark.asyncio
    async def test_without_coefficients_uses_majority_vote(self):
        """Before coefficients are injected, the majority-vote fallback wins."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.7, 12),
            "form": ("Richmond", 0.65, 10),
            "home_advantage": ("Richmond", 0.6, 8),
            "value": ("Carlton", 0.55, 5),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        # Majority vote → Richmond; fallback confidence is fixed at 0.55.
        assert winner == "Richmond"
        assert confidence == 0.55

    @pytest.mark.asyncio
    async def test_set_coefficients_switches_to_linear_path(self):
        """After set_coefficients, the learned linear combiner is used."""
        game = _make_game()
        predictions = {"elo": ("Richmond", 0.7, 20)}
        self.heuristic.set_coefficients(1.0, {"elo_margin_home": 1.0})
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        # y = intercept(1.0) + coef(1.0) * signed_margin(20) = 21 → home, margin 21
        assert winner == "Richmond"
        assert margin == 21
