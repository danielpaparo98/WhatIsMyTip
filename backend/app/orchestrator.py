from typing import Dict, Tuple, List
import time
import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Game
from app.models_ml import BaseModel, EloModel, FormModel, HomeAdvantageModel, ValueModel
from app.heuristics import BaseHeuristic, BestBetHeuristic, YOLOHeuristic, HighRiskHighRewardHeuristic

logger = logging.getLogger(__name__)


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
        self, game: Game, heuristic: str = "best_bet", db: AsyncSession = None
    ) -> Tuple[str, float, int]:
        """Generate a prediction for a game using specified heuristic.
        
        Args:
            game: Game to predict
            heuristic: Heuristic to apply (best_bet, yolo, high_risk_high_reward)
            db: Database session to use for queries
            
        Returns:
            Tuple of (winner, confidence, margin)
        """
        start_time = time.time()
        logger.warning(f"ModelOrchestrator.predict: STARTING for game {game.id} with heuristic '{heuristic}'")
        
        if heuristic not in self.heuristics:
            raise ValueError(f"Unknown heuristic: {heuristic}")
        
        # Get predictions from all models in parallel
        model_predictions: Dict[str, Tuple[str, float, int]] = {}
        model_start = time.time()
        
        async def predict_with_logging(model: BaseModel) -> Tuple[str, Tuple[str, float, int]]:
            """Predict with error handling and timing."""
            model_predict_start = time.time()
            try:
                result = await model.predict(game, db)
                model_predict_time = time.time() - model_predict_start
                logger.warning(f"ModelOrchestrator.predict: {model.get_name()} model took {model_predict_time:.4f}s")
                return model.get_name(), result
            except Exception as e:
                model_predict_time = time.time() - model_predict_start
                logger.error(f"ModelOrchestrator.predict: {model.get_name()} model failed after {model_predict_time:.4f}s: {e}")
                # Return a default prediction on error
                return model.get_name(), (str(game.home_team), 0.5, 0)
        
        # Run all models in parallel
        tasks = [predict_with_logging(model) for model in self.models]
        results = await asyncio.gather(*tasks)
        
        # Build predictions dictionary
        for model_name, prediction in results:
            model_predictions[model_name] = prediction
        
        model_total_time = time.time() - model_start
        logger.warning(f"ModelOrchestrator.predict: ALL MODELS took {model_total_time:.4f}s")
        
        # Apply heuristic
        heuristic_obj = self.heuristics[heuristic]
        heuristic_start = time.time()
        result = await heuristic_obj.apply(game, model_predictions)
        heuristic_time = time.time() - heuristic_start
        
        total_time = time.time() - start_time
        logger.warning(f"ModelOrchestrator.predict: COMPLETED in {total_time:.4f}s (heuristic: {heuristic_time:.4f}s)")
        
        return result
    
    async def predict_all(
        self, game: Game, db: AsyncSession = None
    ) -> Dict[str, Tuple[str, float, int]]:
        """Generate predictions for all heuristics.
        
        Args:
            game: Game to predict
            db: Database session to use for queries
            
        Returns:
            Dict of heuristic -> (winner, confidence, margin)
        """
        results = {}
        for heuristic_name in self.heuristics:
            results[heuristic_name] = await self.predict(game, heuristic_name, db)
        return results
    
    def get_available_heuristics(self) -> List[str]:
        """Get list of available heuristics."""
        return list(self.heuristics.keys())
