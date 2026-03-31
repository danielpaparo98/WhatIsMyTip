from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import List, Optional
from app.models import BacktestResult, Tip, Game
from app.cache import cached, short_cache, medium_cache, long_cache


class BacktestCRUD:
    """CRUD operations for backtest results."""
    
    @staticmethod
    @cached(cache=medium_cache, key_prefix="backtest_by_heuristic:")
    async def get_by_heuristic(
        db: AsyncSession, heuristic: str, limit: int = 50
    ) -> List[BacktestResult]:
        """Get backtest results by heuristic."""
        result = await db.execute(
            select(BacktestResult)
            .where(BacktestResult.heuristic == heuristic)
            .order_by(BacktestResult.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
    
    @staticmethod
    @cached(cache=short_cache, key_prefix="backtest_latest:")
    async def get_latest(
        db: AsyncSession, heuristic: Optional[str] = None
    ) -> List[BacktestResult]:
        """Get the latest backtest results."""
        query = select(BacktestResult).order_by(
            BacktestResult.season.desc(),
            BacktestResult.round_id.desc(),
        )
        if heuristic:
            query = query.where(BacktestResult.heuristic == heuristic)
        
        result = await db.execute(query)
        return list(result.scalars().all())
    
    @staticmethod
    async def create(
        db: AsyncSession,
        heuristic: str,
        season: int,
        round_id: int,
        tips_made: int,
        tips_correct: int,
        accuracy: float,
        profit: float,
    ) -> BacktestResult:
        """Create a backtest result."""
        from app.cache import invalidate_cache_pattern
        
        result = BacktestResult(
            heuristic=heuristic,
            season=season,
            round_id=round_id,
            tips_made=tips_made,
            tips_correct=tips_correct,
            accuracy=accuracy,
            profit=profit,
        )
        db.add(result)
        await db.commit()
        await db.refresh(result)
        
        # Invalidate cache for backtest-related queries
        invalidate_cache_pattern(medium_cache, "backtest_by_heuristic:")
        invalidate_cache_pattern(short_cache, "backtest_latest:")
        invalidate_cache_pattern(long_cache, "backtest_seasons:")
        invalidate_cache_pattern(medium_cache, "backtest_table:")
        
        return result
    
    @staticmethod
    @cached(cache=long_cache, key_prefix="backtest_seasons:")
    async def get_available_seasons(db: AsyncSession) -> List[int]:
        """Get list of distinct seasons with backtest results."""
        from sqlalchemy import select
        result = await db.execute(
            select(BacktestResult.season).distinct().order_by(BacktestResult.season.desc())
        )
        return [row[0] for row in result]
    
    @staticmethod
    @cached(cache=medium_cache, key_prefix="backtest_table:")
    async def get_table_data(db: AsyncSession, season: int) -> List[BacktestResult]:
        """Get detailed backtest results for a season."""
        result = await db.execute(
            select(BacktestResult)
            .where(BacktestResult.season == season)
            .order_by(BacktestResult.heuristic, BacktestResult.round_id)
        )
        return list(result.scalars().all())
