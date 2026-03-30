from typing import Dict, Tuple, List
from collections import Counter
from app.heuristics.base import BaseHeuristic
from app.models import Game
from app.models_ml import BaseModel


class HighRiskHighRewardHeuristic(BaseHeuristic):
    """High variance heuristic that targets upset opportunities.
    
    This heuristic:
    1. Identifies games where models disagree significantly
    2. Picks the underdog when there's disagreement
    3. Uses moderate confidence but higher margin
    4. Targets games with higher variance
    """
    
    def get_name(self) -> str:
        return "high_risk_high_reward"
    
    async def apply(
        self, game: Game, model_predictions: Dict[str, Tuple[str, float, int]]
    ) -> Tuple[str, float, int]:
        """Apply high risk high reward heuristic."""
        if not model_predictions:
            # Fallback to away team (more risky)
            return game.away_team, 0.55, 25
        
        # Count votes
        votes = {}
        confidences = {}
        margins = {}
        
        for model_name, (winner, confidence, margin) in model_predictions.items():
            if winner not in votes:
                votes[winner] = 0
                confidences[winner] = []
                margins[winner] = []
            votes[winner] += 1
            confidences[winner].append(confidence)
            margins[winner].append(margin)
        
        # Check if there's disagreement (no clear consensus)
        vote_counts = sorted(votes.values(), reverse=True)
        
        if len(vote_counts) > 1 and vote_counts[0] == vote_counts[1]:
            # Models are split - pick the underdog (away team usually)
            winner = game.away_team
            avg_confidence = sum(confidences.get(winner, [0.5])) / max(len(confidences.get(winner, [1])), 1)
            avg_margin = sum(margins.get(winner, [20])) / max(len(margins.get(winner, [1])), 1)
        else:
            # Pick the team with fewer votes (underdog)
            underdog = min(votes.items(), key=lambda x: x[1])[0]
            winner = underdog
            avg_confidence = sum(confidences.get(winner, [0.5])) / max(len(confidences.get(winner, [1])), 1)
            avg_margin = sum(margins.get(winner, [20])) / max(len(margins.get(winner, [1])), 1)
        
        # Moderate confidence but higher margin
        adjusted_confidence = max(0.5, min(0.75, avg_confidence))
        adjusted_margin = max(15, int(avg_margin * 1.3))
        
        return winner, adjusted_confidence, adjusted_margin
