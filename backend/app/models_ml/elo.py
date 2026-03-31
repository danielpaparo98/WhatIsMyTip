import asyncio
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Tuple
from app.models_ml.base import BaseModel
from app.models import Game
from app.db import AsyncSessionLocal


class EloModel(BaseModel):
    """Elo rating model for predicting game outcomes.
    
    Uses a simplified Elo rating system adapted for AFL.
    """
    
    def __init__(self, k_factor: float = 32.0, home_advantage: float = 50.0):
        self.k_factor = k_factor
        self.home_advantage = home_advantage
        self.ratings: Dict[str, float] = {}
        self._lock = asyncio.Lock()
    
    def get_name(self) -> str:
        return "Elo"
    
    async def _get_team_ratings(self, db: AsyncSession) -> Dict[str, float]:
        """Get or initialize team Elo ratings."""
        async with self._lock:
            if not self.ratings:
                # Initialize all teams with 1500 rating
                from app.models import Game
                result = await db.execute(
                    select(Game.home_team).distinct()
                )
                home_teams = set(r[0] for r in result.all())
                result = await db.execute(
                    select(Game.away_team).distinct()
                )
                away_teams = set(r[0] for r in result.all())
                all_teams = home_teams.union(away_teams)
                
                for team in all_teams:
                    self.ratings[team] = 1500.0
            
            return self.ratings.copy()
    
    async def _update_ratings(self, db: AsyncSession):
        """Update Elo ratings based on historical games."""
        from app.models import Game
        from sqlalchemy import and_
        
        async with self._lock:
            result = await db.execute(
                select(Game)
                .where(Game.completed == True)
                .order_by(Game.date)
            )
            games = result.scalars().all()
            
            for game in games:
                home_rating = self.ratings.get(game.home_team, 1500.0)
                away_rating = self.ratings.get(game.away_team, 1500.0)
                
                # Expected scores
                expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - home_rating - self.home_advantage) / 400.0))
                expected_away = 1.0 - expected_home
                
                # Actual scores
                if game.home_score is not None and game.away_score is not None:
                    actual_home = 1.0 if game.home_score > game.away_score else 0.0
                    actual_away = 1.0 - actual_home
                    
                    # Update ratings
                    self.ratings[game.home_team] = home_rating + self.k_factor * (actual_home - expected_home)
                    self.ratings[game.away_team] = away_rating + self.k_factor * (actual_away - expected_away)
    
    async def predict(self, game: Game) -> Tuple[str, float, int]:
        """Predict winner using Elo ratings."""
        async with self._lock:
            async with AsyncSessionLocal() as db:
                ratings = await self._get_team_ratings(db)
                await self._update_ratings(db)
                
                home_rating = ratings.get(game.home_team, 1500.0)
                away_rating = ratings.get(game.away_team, 1500.0)
                
                # Apply home advantage
                effective_home = home_rating + self.home_advantage
                
                # Calculate expected probability
                expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - effective_home) / 400.0))
                expected_away = 1.0 - expected_home
                
                # Predict winner and margin
                if expected_home > expected_away:
                    winner = game.home_team
                    confidence = expected_home
                    margin = int((effective_home - away_rating) / 10)
                else:
                    winner = game.away_team
                    confidence = expected_away
                    margin = int((away_rating - effective_home) / 10)
                
                # Clamp margin to reasonable range
                margin = max(1, min(100, margin))
                
                return winner, confidence, margin
