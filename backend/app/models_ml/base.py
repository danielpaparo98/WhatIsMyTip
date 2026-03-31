from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Game


class BaseModel(ABC):
    """Base class for all prediction models."""
    
    @abstractmethod
    async def predict(self, game: Game, db: AsyncSession) -> Tuple[str, float, int]:
        """Predict winner, confidence, and margin for a game.
        
        Args:
            game: Game to predict
            db: Database session to use for queries
            
        Returns:
            Tuple of (winner_team, confidence, predicted_margin)
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Get model name."""
        pass
