from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple
from app.models import Game


class BaseModel(ABC):
    """Base class for all prediction models."""
    
    @abstractmethod
    async def predict(self, game: Game) -> Tuple[str, float, int]:
        """Predict winner, confidence, and margin for a game.
        
        Args:
            game: Game to predict
            
        Returns:
            Tuple of (winner_team, confidence, predicted_margin)
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Get the model name."""
        pass
