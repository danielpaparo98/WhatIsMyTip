from typing import Dict, Tuple, List
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Game
from app.models_ml import BaseModel, EloModel, FormModel, HomeAdvantageModel, ValueModel
from app.heuristics import BaseHeuristic, BestBetHeuristic, YOLOHeuristic, HighRiskHighRewardHeuristic


class ModelOrchestrator:
    """Orchestrates ML models and heuristic layers for predictions."""
    
    def __init__(self):
        # Initialize ML models
        self.models: List[BaseModel] = [
            EloModel(),
            FormModel(),
            HomeAdvantageModel(),
            ValueModel(),
        ]
        
        # Initialize heuristics
        self.heuristics: Dict[str, BaseHeuristic] = {
            "best_bet": BestBetHeuristic(self.models),
            "yolo": YOLOHeuristic(self.models),
            "high_risk_high_reward": HighRiskHighRewardHeuristic(self.models),
        }
    
    async def predict(
        self, game: Game, heuristic: str = "best_bet"
    ) -> Tuple[str, float, int]:
        """Generate a prediction for a game using the specified heuristic.
        
        Args:
            game: Game to predict
            heuristic: Heuristic to apply (best_bet, yolo, high_risk_high_reward)
            
        Returns:
            Tuple of (winner, confidence, margin)
        """
        if heuristic not in self.heuristics:
            raise ValueError(f"Unknown heuristic: {heuristic}")
        
        # Get predictions from all models
        model_predictions: Dict[str, Tuple[str, float, int]] = {}
        for model in self.models:
            winner, confidence, margin = await model.predict(game)
            model_predictions[model.get_name()] = (winner, confidence, margin)
        
        # Apply heuristic
        heuristic_obj = self.heuristics[heuristic]
        return await heuristic_obj.apply(game, model_predictions)
    
    async def predict_all(
        self, game: Game
    ) -> Dict[str, Tuple[str, float, int]]:
        """Generate predictions for all heuristics.
        
        Args:
            game: Game to predict
            
        Returns:
            Dict of heuristic -> (winner, confidence, margin)
        """
        results = {}
        for heuristic_name in self.heuristics:
            results[heuristic_name] = await self.predict(game, heuristic_name)
        return results
    
    def get_available_heuristics(self) -> List[str]:
        """Get list of available heuristics."""
        return list(self.heuristics.keys())
