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
    async def test_empty_predictions_default_is_alphabetically_first(self):
        """Zero information must look like zero information.

        No votes → the alphabetically first team (home/away-neutral
        deterministic default), coin-flip confidence 0.50, and the
        heuristic's minimum margin 5.
        """
        game = _make_game()  # home=Richmond, away=Carlton → min is Carlton
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Carlton"
        assert confidence == 0.50
        assert margin == 5

    @pytest.mark.asyncio
    async def test_empty_predictions_neutral_when_home_is_alphabetically_last(self):
        """Proves neutrality: home team alphabetically AFTER away → away picked."""
        game = _make_game(home_team="Sydney", away_team="Brisbane")
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Brisbane"  # min("Sydney", "Brisbane") — the away team
        assert confidence == 0.50
        assert margin == 5

    @pytest.mark.asyncio
    async def test_vote_tie_resolves_to_alphabetically_first_team(self):
        """A 1–1 vote split must not depend on dict insertion (model
        completion) order — the alphabetically first team wins."""
        game = _make_game()  # home=Richmond, away=Carlton → min is Carlton

        # Away vote first in insertion order…
        predictions_away_first = {
            "elo": ("Carlton", 0.6, 8),
            "form": ("Richmond", 0.7, 12),
        }
        # …and home vote first in insertion order.
        predictions_home_first = {
            "elo": ("Richmond", 0.7, 12),
            "form": ("Carlton", 0.6, 8),
        }

        winner_a, _, _ = await self.heuristic.apply(game, predictions_away_first)
        winner_b, _, _ = await self.heuristic.apply(game, predictions_home_first)

        assert winner_a == "Carlton"
        assert winner_b == "Carlton"

    @pytest.mark.asyncio
    async def test_vote_tie_neutral_when_home_is_alphabetically_last(self):
        """Proves vote-tie neutrality: home team alphabetically AFTER away."""
        game = _make_game(home_team="Sydney", away_team="Brisbane")
        predictions = {
            "elo": ("Sydney", 0.7, 12),
            "form": ("Brisbane", 0.6, 8),
        }
        winner, _, _ = await self.heuristic.apply(game, predictions)
        assert winner == "Brisbane"

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
    async def test_empty_predictions_default_is_alphabetically_first(self):
        """Zero information must look like zero information.

        No votes → alphabetically first team (home/away-neutral),
        coin-flip confidence 0.50, and YOLO's 10-point margin floor.
        """
        game = _make_game()  # home=Richmond, away=Carlton → min is Carlton
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Carlton"
        assert confidence == 0.50
        assert margin == 10

    @pytest.mark.asyncio
    async def test_empty_predictions_neutral_when_home_is_alphabetically_last(self):
        """Proves neutrality: home team alphabetically AFTER away → away picked."""
        game = _make_game(home_team="Sydney", away_team="Adelaide")
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Adelaide"  # min("Sydney", "Adelaide") — the away team
        assert confidence == 0.50
        assert margin == 10

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
    async def test_empty_predictions_default_is_alphabetically_first(self):
        """With no predictions, cold-start picks the alphabetically first team.

        Neutral rule: min("Richmond", "Carlton") = "Carlton" (here the away
        team), fixed 0.55 confidence and margin 6.
        """
        game = _make_game()
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Carlton"
        assert confidence == 0.55
        assert margin == 6

    @pytest.mark.asyncio
    async def test_empty_predictions_neutral_when_home_is_alphabetically_first(self):
        """Proves neutrality: home alphabetically FIRST → home picked (not away)."""
        game = _make_game(home_team="Adelaide", away_team="Brisbane")
        winner, confidence, margin = await self.heuristic.apply(game, {})
        assert winner == "Adelaide"
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
