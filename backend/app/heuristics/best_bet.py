from typing import Dict, Tuple, List
from collections import Counter
from app.heuristics.base import BaseHeuristic
from app.models import Game
from app.models_ml import BaseModel


class BestBetHeuristic(BaseHeuristic):
    """Conservative heuristic that picks the most confident consensus pick.
    
    This heuristic:
    1. Aggregates predictions from all models
    2. Selects the winner with the most model agreement
    3. Uses a weighted average of confidences
    4. Applies a conservative margin adjustment
    """
    
    def get_name(self) -> str:
        return "best_bet"
    
    async def apply(
        self, game: Game, model_predictions: Dict[str, Tuple[str, float, int]]
    ) -> Tuple[str, float, int]:
        """Apply best bet heuristic."""
        if not model_predictions:
            # Fallback to home team if no predictions
            return game.home_team, 0.55, 15
        
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
        
        # Get the winner with most votes
        winner = votes.most_common(1)[0][0]
        
        # Calculate weighted confidence
        avg_confidence = sum(confidences[winner]) / len(confidences[winner])
        
        # Apply conservative adjustment (reduce confidence slightly)
        adjusted_confidence = min(0.9, avg_confidence * 0.95)
        
        # Calculate margin (average of models that picked this winner)
        avg_margin = sum(margins[winner]) / len(margins[winner])
        
        # Conservative margin adjustment
        adjusted_margin = max(5, int(avg_margin * 0.8))
        
        return winner, adjusted_confidence, adjusted_margin
