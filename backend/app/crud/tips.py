from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Optional
from app.models import Tip


class TipCRUD:
    """CRUD operations for tips."""
    
    @staticmethod
    async def get_by_id(db: AsyncSession, tip_id: int) -> Optional[Tip]:
        """Get a tip by ID."""
        result = await db.execute(select(Tip).where(Tip.id == tip_id))
        return result.scalar_one_or_none()
    
    @staticmethod
    async def get_by_game(db: AsyncSession, game_id: int) -> List[Tip]:
        """Get all tips for a game."""
        result = await db.execute(
            select(Tip).where(Tip.game_id == game_id).order_by(Tip.heuristic)
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def get_by_heuristic(
        db: AsyncSession, heuristic: str, limit: int = 100
    ) -> List[Tip]:
        """Get tips by heuristic type."""
        result = await db.execute(
            select(Tip)
            .where(Tip.heuristic == heuristic)
            .order_by(Tip.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def get_by_round(
        db: AsyncSession, season: int, round_id: int
    ) -> List[Tip]:
        """Get all tips for a round."""
        from app.models import Game
        
        result = await db.execute(
            select(Tip)
            .join(Game)
            .where(
                and_(Game.season == season, Game.round_id == round_id)
            )
            .order_by(Tip.heuristic, Game.date)
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def create(
        db: AsyncSession,
        game_id: int,
        heuristic: str,
        selected_team: str,
        margin: int,
        confidence: float,
        explanation: str,
    ) -> Tip:
        """Create a new tip."""
        tip = Tip(
            game_id=game_id,
            heuristic=heuristic,
            selected_team=selected_team,
            margin=margin,
            confidence=confidence,
            explanation=explanation,
        )
        db.add(tip)
        await db.commit()
        await db.refresh(tip)
        return tip
    
    @staticmethod
    async def delete_for_game(db: AsyncSession, game_id: int) -> int:
        """Delete all tips for a game."""
        result = await db.execute(select(Tip).where(Tip.game_id == game_id))
        tips = result.scalars().all()
        count = len(tips)
        for tip in tips:
            await db.delete(tip)
        await db.commit()
        return count
