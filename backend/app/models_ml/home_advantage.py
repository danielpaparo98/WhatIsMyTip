import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Dict, Tuple
from app.models_ml.base import BaseModel
from app.models import Game
from app.db import AsyncSessionLocal


class HomeAdvantageModel(BaseModel):
    """Simple home advantage model based on historical data."""
    
    def __init__(self):
        self.home_win_rate: Dict[str, float] = {}
        self.overall_home_advantage: float = 0.0
    
    def get_name(self) -> str:
        return "HomeAdvantage"
    
    async def _calculate_home_advantage(self, db: AsyncSession):
        """Calculate home advantage statistics."""
        result = await db.execute(
            select(
                Game.venue,
                func.count().label("total_games"),
                func.sum(
                    func.case(
                        (Game.home_score > Game.away_score, 1),
                        else_=0,
                    )
                ).label("home_wins"),
            )
            .where(Game.completed == True)
            .group_by(Game.venue)
        )
        
        venue_stats = result.all()
        
        for venue, total, home_wins in venue_stats:
            if total > 0:
                self.home_win_rate[venue] = home_wins / total
        
        # Calculate overall home advantage
        result = await db.execute(
            select(
                func.count().label("total_games"),
                func.sum(
                    func.case(
                        (Game.home_score > Game.away_score, 1),
                        else_=0,
                    )
                ).label("home_wins"),
            )
            .where(Game.completed == True)
        )
        
        total, home_wins = result.one()
        self.overall_home_advantage = (home_wins / total) if total > 0 else 0.5
    
    async def predict(self, game: Game) -> Tuple[str, float, int]:
        """Predict winner based on home advantage."""
        async with AsyncSessionLocal() as db:
            await self._calculate_home_advantage(db)
            
            # Get venue-specific home advantage
            venue_advantage = self.home_win_rate.get(
                game.venue, self.overall_home_advantage
            )
            
            # Adjust for venue
            if venue_advantage > 0.5:
                winner = game.home_team
                confidence = venue_advantage
                margin = int((venue_advantage - 0.5) * 200)
            else:
                winner = game.away_team
                confidence = 1.0 - venue_advantage
                margin = int((0.5 - venue_advantage) * 200)
            
            # Clamp values
            confidence = max(0.5, min(0.85, confidence))
            margin = max(1, min(60, margin))
            
            return winner, confidence, margin
