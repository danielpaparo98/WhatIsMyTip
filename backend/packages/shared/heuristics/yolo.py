from typing import Dict, Tuple

from ..models import Game
from ..models_ml.prediction import Prediction
from .base import BaseHeuristic


class YOLOHeuristic(BaseHeuristic):
    """Aggressive heuristic that goes with the highest confidence prediction.

    This heuristic:
    1. Finds the model with the highest confidence prediction
    2. Uses that prediction directly
    3. Boosts the confidence slightly
    4. Uses the full margin prediction

    Zero-information default: with no model votes the heuristic returns
    the alphabetically first team at 0.50 confidence with the 10-point
    margin floor — a deterministic, home/away-neutral cold start with no
    fabricated confidence.
    """

    def get_name(self) -> str:
        return "yolo"

    async def apply(
        self, game: Game, model_predictions: Dict[str, Tuple[str, float, int]]
    ) -> Tuple[str, float, int]:
        """Apply YOLO heuristic."""
        if not model_predictions:
            # Zero information must look like zero information.  The
            # alphabetically-first team is a home/away-neutral deterministic
            # default; 0.50 confidence is an honest coin flip; 10 keeps
            # YOLO's existing max(10, margin) floor idiom.
            return Prediction(min(game.home_team, game.away_team), 0.50, 10)

        # Find the prediction with highest confidence
        best_model = max(
            model_predictions.items(),
            key=lambda x: x[1][1],  # x[1] is (winner, confidence, margin)
        )

        model_name, (winner, confidence, margin) = best_model

        # Boost confidence slightly for YOLO
        boosted_confidence = min(0.95, confidence * 1.1)

        # Use full margin
        adjusted_margin = max(10, margin)

        return Prediction(winner, boosted_confidence, adjusted_margin)
