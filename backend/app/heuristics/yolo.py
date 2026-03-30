from typing import Dict, Tuple
from app.heuristics.base import BaseHeuristic
from app.models import Game
from app.models_ml import BaseModel


class YOLOHeuristic(BaseHeuristic):
    """Aggressive heuristic that goes with the highest confidence prediction.
    
    This heuristic:
    1. Finds the model with the highest confidence prediction
    2. Uses that prediction directly
    3. Boosts the confidence slightly
    4. Uses the full margin prediction
    """
    
    def get_name(self) -> str:
        return "yolo"
    
    async def apply(
        self, game: Game, model_predictions: Dict[str, Tuple[str, float, int]]
    ) -> Tuple[str, float, int]:
        """Apply YOLO heuristic."""
        if not model_predictions:
            # Fallback to home team if no predictions
            return game.home_team, 0.6, 20
        
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
        
        return winner, boosted_confidence, adjusted_margin
