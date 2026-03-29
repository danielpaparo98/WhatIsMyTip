from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import List, Optional
from app.models import BacktestResult, Tip, Game


class BacktestCRUD:
    """CRUD operations for backtest results."""
    
    @staticmethod
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
        return result
