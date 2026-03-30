import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, or_
from typing import Dict, Tuple
from app.models_ml.base import BaseModel
from app.models import Game
from app.db import AsyncSessionLocal


class ValueModel(BaseModel):
    """Value-based model identifying undervalued teams."""
    
    def __init__(self):
        self.team_win_rates: Dict[str, float] = {}
    
    def get_name(self) -> str:
        return "Value"
    
    async def _calculate_win_rates(self, db: AsyncSession):
        """Calculate historical win rates for each team."""
        teams_result = await db.execute(
            select(Game.home_team).distinct().union(select(Game.away_team).distinct())
        )
        teams = [r[0] for r in teams_result.all()]
        
        for team in teams:
            result = await db.execute(
                select(
                    func.count().label("total"),
                    func.sum(
                        case((Game.home_score > Game.away_score, 1), else_=0)
                    ).label("wins"),
                )
                .where(
                    or_(Game.home_team == team, Game.away_team == team),
                    Game.completed == True,
                )
            )
            
            total, wins = result.one()
            self.team_win_rates[team] = (wins / total) if total > 0 else 0.5
    
    async def predict(self, game: Game) -> Tuple[str, float, int]:
        """Predict winner based on value (undervalued teams)."""
        async with AsyncSessionLocal() as db:
            await self._calculate_win_rates(db)
            
            home_rate = self.team_win_rates.get(game.home_team, 0.5)
            away_rate = self.team_win_rates.get(game.away_team, 0.5)
            
            # Apply home advantage adjustment
            adjusted_home = min(home_rate + 0.05, 0.9)
            
            # Predict team with better historical performance
            if adjusted_home > away_rate:
                winner = game.home_team
                confidence = (adjusted_home - away_rate) + 0.5
                margin = int((adjusted_home - away_rate) * 150)
            else:
                winner = game.away_team
                confidence = (away_rate - adjusted_home) + 0.5
                margin = int((away_rate - adjusted_home) * 150)
            
            # Clamp values
            confidence = max(0.5, min(0.9, confidence))
            margin = max(1, min(80, margin))
            
            return winner, confidence, margin
