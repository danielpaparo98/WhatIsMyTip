import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from typing import Dict, Tuple, Optional
from datetime import datetime, timedelta, date

from .base import BaseModel
from ..models import Game
from ..cache import _get_client
from ..logger import get_logger

logger = get_logger(__name__)

# Redis key prefix and TTL for home advantage cache
_HOME_ADV_PREFIX = "wimt:home_adv:"
_HOME_ADV_TTL = 3600  # 1 hour


class HomeAdvantageModel(BaseModel):
    """Simple home advantage model based on historical data.
    
    In the FaaS environment, venue advantage data is cached in Redis with a
    1-hour TTL instead of an in-memory class-level dict.
    """
    
    def __init__(self):
        self.home_win_rate: Dict[str, float] = {}
        self.overall_home_advantage: float = 0.0
    
    def get_name(self) -> str:
        return "home_advantage"
    
    async def _calculate_home_advantage(self, db: AsyncSession, game: Game):
        """Calculate home advantage statistics using only historical data.
        
        Only uses games that occurred BEFORE the prediction game's date
        to ensure no data leakage in backtesting. Results are cached in Redis
        with a 1-hour TTL.
        
        Args:
            db: Database session
            game: The game being predicted (used for temporal filtering)
        """
        # Build cache key from the game date
        cache_key = f"{_HOME_ADV_PREFIX}{game.date.isoformat() if game.date else 'all'}"
        
        # Check Redis cache first
        try:
            client = _get_client()
            raw = await client.get(cache_key)
            if raw is not None:
                cached = json.loads(raw)
                self.home_win_rate = cached["home_win_rate"]
                self.overall_home_advantage = cached["overall_home_advantage"]
                return
        except Exception as e:
            logger.warning(f"HomeAdvantageModel: Redis cache read error: {e}")
        
        # Cache miss — compute from DB
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
        
        # Store in Redis with TTL
        try:
            client = _get_client()
            await client.set(
                cache_key,
                json.dumps({
                    "home_win_rate": self.home_win_rate,
                    "overall_home_advantage": self.overall_home_advantage,
                }),
                ex=_HOME_ADV_TTL,
            )
        except Exception as e:
            logger.warning(f"HomeAdvantageModel: Redis cache write error: {e}")
    
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
