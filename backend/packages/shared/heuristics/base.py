from abc import ABC, abstractmethod
from typing import Dict, List

from ..models import Game
from ..models_ml import BaseModel
from ..models_ml.prediction import Prediction


class BaseHeuristic(ABC):
    """Base class for heuristic strategies that wrap ML models."""

    def __init__(self, models: List[BaseModel]):
        self.models = models

    @abstractmethod
    async def apply(
        self, game: Game, model_predictions: Dict[str, Prediction]
    ) -> Prediction:
        """Apply heuristic to model predictions.

        Args:
            game: Game to predict
            model_predictions: Dict of model_name -> Prediction

        Returns:
            Prediction (pick, probability, score_projection)
        """
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        """Get the heuristic name."""
        pass
