from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from typing import Dict, Tuple, Optional
from datetime import datetime, timedelta, date
from app.models_ml.base import BaseModel
from app.models import Game


class HomeAdvantageModel(BaseModel):
    """Simple home advantage model based on historical data."""
    
    # Class-level cache with TTL for venue advantage data
    _cache: Dict[str, dict] = {}
    _cache_expiry: Dict[str, datetime] = {}
    CACHE_TTL = timedelta(hours=1)
    
    def __init__(self):
        self.home_win_rate: Dict[str, float] = {}
        self.overall_home_advantage: float = 0.0
    
    def get_name(self) -> str:
        return "home_advantage"
    
    async def _calculate_home_advantage(self, db: AsyncSession, game: Game):
        """Calculate home advantage statistics using only historical data.
        
        Only uses games that occurred BEFORE the prediction game's date
        to ensure no data leakage in backtesting. Results are cached with TTL.
        
        Args:
            db: Database session
            game: The game being predicted (used for temporal filtering)
        """
        # Build cache key from the game date
        cache_key = f"home_adv_{game.date.isoformat() if game.date else 'all'}"
        
        # Check cache first
        if cache_key in self.__class__._cache and datetime.now() < self.__class__._cache_expiry.get(cache_key, datetime.min):
            cached = self.__class__._cache[cache_key]
            self.home_win_rate = cached["home_win_rate"].copy()
            self.overall_home_advantage = cached["overall_home_advantage"]
            return
        
        result = await db.execute(
            select(
                Game.venue,
                func.count().label("total_games"),
                func.sum(
                    case((Game.home_score > Game.away_score, 1), else_=0)
                ).label("home_wins"),
            )
            .where(
                Game.completed == True,
                Game.date < game.date
            )
            .group_by(Game.venue)
        )
        
        venue_stats = result.all()
        
        for venue, total, home_wins in venue_stats:
            if total > 0:
                self.home_win_rate[venue] = home_wins / total
        
        # Calculate overall home advantage using only historical games
        result = await db.execute(
            select(
                func.count().label("total_games"),
                func.sum(
                    case((Game.home_score > Game.away_score, 1), else_=0)
                ).label("home_wins"),
            )
            .where(
                Game.completed == True,
                Game.date < game.date
            )
        )
        
        total, home_wins = result.one()
        self.overall_home_advantage = (home_wins / total) if total > 0 else 0.5
        
        # Store in class-level cache with TTL
        self.__class__._cache[cache_key] = {
            "home_win_rate": self.home_win_rate.copy(),
            "overall_home_advantage": self.overall_home_advantage,
        }
        self.__class__._cache_expiry[cache_key] = datetime.now() + self.__class__.CACHE_TTL
    
    async def predict(self, game: Game, db: AsyncSession) -> Tuple[str, float, int]:
        """Predict winner based on home advantage.
        
        Uses only historical data before the prediction game's date.
        """
        await self._calculate_home_advantage(db, game)
        
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
