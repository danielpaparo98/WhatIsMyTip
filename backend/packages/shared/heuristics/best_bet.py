from collections import Counter
from typing import Dict, Tuple

from ..models import Game
from ..models_ml.prediction import Prediction
from .base import BaseHeuristic


class BestBetHeuristic(BaseHeuristic):
    """Conservative heuristic that picks the most confident consensus pick.

    This heuristic:
    1. Aggregates predictions from all models
    2. Selects the winner with the most model agreement (vote ties
       resolve to the alphabetically first team — home/away-neutral)
    3. Uses a weighted average of confidences
    4. Applies a conservative margin adjustment

    Zero-information default: with no model votes the heuristic returns
    the alphabetically first team at 0.50 confidence with the minimum
    5-point margin — a deterministic, home/away-neutral cold start with
    no fabricated confidence.
    """

    def get_name(self) -> str:
        return "best_bet"

    async def apply(
        self, game: Game, model_predictions: Dict[str, Tuple[str, float, int]]
    ) -> Tuple[str, float, int]:
        """Apply best bet heuristic."""
        if not model_predictions:
            # Zero information must look like zero information.  The
            # alphabetically-first team is a home/away-neutral deterministic
            # default (a home-team pick here would fabricate fixture bias);
            # 0.50 confidence is an honest coin flip; 5 is this heuristic's
            # minimum margin (same floor as the vote path below).
            return Prediction(min(game.home_team, game.away_team), 0.50, 5)

        # Count votes for each team
        votes = Counter()
        confidences = {}
        margins = {}

        for model_name, (winner, confidence, margin) in model_predictions.items():
            votes[winner] += 1
            if winner not in confidences:
                confidences[winner] = []
                margins[winner] = []
            confidences[winner].append(confidence)
            margins[winner].append(margin)

        # Get the winner with most votes.  Vote TIES (e.g. a 4–4 split
        # across eight models) resolve to the alphabetically first team —
        # the same home/away-neutral rule as weighted_tip_fallback.
        # Counter.most_common alone would fall back to dict insertion
        # order (i.e. model completion order under asyncio.gather), which
        # is nondeterministic and could quietly favour one fixture side.
        max_votes = max(votes.values())
        winner = min(team for team, n in votes.items() if n == max_votes)

        # Calculate weighted confidence
        avg_confidence = sum(confidences[winner]) / len(confidences[winner])

        # Apply conservative adjustment (reduce confidence slightly)
        adjusted_confidence = min(0.9, avg_confidence * 0.95)

        # Calculate margin (average of models that picked this winner)
        avg_margin = sum(margins[winner]) / len(margins[winner])

        # Conservative margin adjustment
        adjusted_margin = max(5, int(avg_margin * 0.8))

        return Prediction(winner, adjusted_confidence, adjusted_margin)
