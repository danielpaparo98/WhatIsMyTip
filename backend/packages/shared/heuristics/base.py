from abc import ABC, abstractmethod
from typing import Dict, List, Tuple

from ..models import Game
from ..models_ml import BaseModel


class BaseHeuristic(ABC):
    """Base class for heuristic strategies that wrap ML models."""

    def __init__(self, models: List[BaseModel]):
        self.models = models

    @abstractmethod
    async def apply(
        self, game: Game, model_predictions: Dict[str, Tuple[str, float, int]]
    ) -> Tuple[str, float, int]:
        """Apply heuristic to model predictions.

        Args:
            game: Game to predict
            model_predictions: Dict of model_name -> (winner, confidence, margin)

        Returns:
            Tuple of (winner, confidence, margin)
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Get the heuristic name."""
        pass
