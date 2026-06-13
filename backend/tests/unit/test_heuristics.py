"""Unit tests for heuristic implementations.

These are pure computation tests — no database or Redis mocking required.
The heuristic ``apply()`` methods receive a mock game object and a dict of
model predictions, then return (winner, confidence, margin).
"""

from unittest.mock import MagicMock

import pytest

from packages.shared.heuristics.best_bet import BestBetHeuristic
from packages.shared.heuristics.high_risk_high_reward import HighRiskHighRewardHeuristic
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
# HighRiskHighRewardHeuristic
# ---------------------------------------------------------------------------

class TestHighRiskHighRewardHeuristic:
    def setup_method(self):
        self.heuristic = HighRiskHighRewardHeuristic(models=[])

    def test_get_name(self):
        assert self.heuristic.get_name() == "high_risk_high_reward"

    @pytest.mark.asyncio
    async def test_empty_predictions_fallback(self):
        """With no predictions, should fall back to away team (risky)."""
        game = _make_game()
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Carlton"
        assert confidence == 0.55
        assert margin == 25

    @pytest.mark.asyncio
    async def test_picks_underdog(self):
        """Should pick the team with fewer model votes."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.7, 12),
            "form": ("Richmond", 0.65, 10),
            "home_advantage": ("Richmond", 0.6, 8),
            "value": ("Carlton", 0.55, 5),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        # Carlton has fewer votes (1 vs 3), so it's the underdog
        assert winner == "Carlton"

    @pytest.mark.asyncio
    async def test_confidence_bounded(self):
        """Confidence should be between 0.5 and 0.75."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.9, 30),
            "form": ("Carlton", 0.3, 5),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert 0.5 <= confidence <= 0.75

    @pytest.mark.asyncio
    async def test_split_models_picks_away(self):
        """When models are evenly split, the away team is picked."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.7, 12),
            "form": ("Carlton", 0.7, 12),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert winner == "Carlton"

    @pytest.mark.asyncio
    async def test_margin_minimum_15(self):
        """High risk margin should be at least 15."""
        game = _make_game()
        predictions = {
            "elo": ("Richmond", 0.6, 5),
            "form": ("Carlton", 0.6, 5),
        }
        winner, confidence, margin = await self.heuristic.apply(game, predictions)
        assert margin >= 15
