from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Optional
from app.models import Tip
from app.cache import cached, short_cache, medium_cache


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
    @cached(cache=short_cache, key_prefix="tips_by_heuristic:")
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
    @cached(cache=short_cache, key_prefix="tips_by_round:")
    async def get_by_round(
        db: AsyncSession, season: int, round_id: int
    ) -> List[Tip]:
        """Get all tips for a round."""
        from app.models import Game
        
        result = await db.execute(
            select(Tip)
            .join(Game, Tip.game_id == Game.id)
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
        """Create a new tip with proper transaction management."""
        from app.cache import invalidate_cache_pattern
        
        try:
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
            
            # Invalidate cache for tip-related queries
            invalidate_cache_pattern(short_cache, "tips_by_heuristic:")
            invalidate_cache_pattern(short_cache, "tips_by_round:")
            
            return tip
        except Exception as e:
            await db.rollback()
            raise
    
    @staticmethod
    async def delete_for_game(db: AsyncSession, game_id: int) -> int:
        """Delete all tips for a game with proper transaction management."""
        from app.cache import invalidate_cache_pattern
        
        try:
            result = await db.execute(select(Tip).where(Tip.game_id == game_id))
            tips = result.scalars().all()
            count = len(tips)
            for tip in tips:
                db.delete(tip)
            await db.commit()
            
            # Invalidate cache for tip-related queries
            invalidate_cache_pattern(short_cache, "tips_by_heuristic:")
            invalidate_cache_pattern(short_cache, "tips_by_round:")
            
            return count
        except Exception as e:
            await db.rollback()
            raise
    
    @staticmethod
    async def regenerate_tips_for_round(
        db: AsyncSession,
        season: int,
        round_id: int,
        heuristics: Optional[List[str]] = None,
    ) -> dict:
        """Generate tips for a specific round using the ModelOrchestrator.
        
        Args:
            db: Database session
            season: Season year
            round_id: Round number
            heuristics: Optional list of heuristics to generate (default: all)
            
        Returns:
            Dict with generation results including tips count and heuristics used
        """
        from app.crud import GameCRUD
        from app.orchestrator import ModelOrchestrator
        
        # Get games for round
        games = await GameCRUD.get_by_round(db, season, round_id)
        
        if not games:
            return {
                "success": False,
                "message": f"No games found for round {round_id}, season {season}",
                "tips_count": 0,
                "heuristics_used": [],
            }
        
        # Initialize orchestrator
        orchestrator = ModelOrchestrator()
        
        # Determine which heuristics to use
        if heuristics:
            heuristics_to_use = [h for h in heuristics if h in orchestrator.get_available_heuristics()]
        else:
            heuristics_to_use = orchestrator.get_available_heuristics()
        
        # Generate tips (idempotent - only create if not exist)
        tips_created = []
        for game in games:
            # Check if tips already exist for this game
            existing_tips = await TipCRUD.get_by_game(db, game.id)
            existing_heuristics = {tip.heuristic for tip in existing_tips}
            
            for heuristic in heuristics_to_use:
                # Skip if tip already exists for this game and heuristic
                if heuristic in existing_heuristics:
                    continue
                
                winner, confidence, margin = await orchestrator.predict(game, heuristic)
                
                try:
                    tip = await TipCRUD.create(
                        db=db,
                        game_id=game.id,
                        heuristic=heuristic,
                        selected_team=winner,
                        margin=margin,
                        confidence=confidence,
                        explanation="",  # Explanations can be generated separately
                    )
                    tips_created.append(tip)
                except Exception as e:
                    # Handle unique constraint violation (race condition)
                    # If another request created tip, just skip it
                    if "uq_game_heuristic" in str(e) or "duplicate" in str(e).lower():
                        continue
                    raise
        
        return {
            "success": True,
            "message": f"Generated {len(tips_created)} tips for round {round_id}, season {season}",
            "tips_count": len(tips_created),
            "heuristics_used": heuristics_to_use,
            "season": season,
            "round_id": round_id,
        }
