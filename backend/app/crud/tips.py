from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, insert
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
    async def create_batch(db: AsyncSession, tips_data: List[dict]) -> List[Tip]:
        """Create multiple tips in a single bulk insert operation.
        
        Args:
            db: Database session
            tips_data: List of dictionaries containing tip data
            
        Returns:
            List of created Tip objects
        """
        from app.cache import invalidate_cache_pattern
        
        try:
            stmt = insert(Tip).values(tips_data).returning(Tip)
            result = await db.execute(stmt)
            await db.commit()
            
            # Invalidate cache for tip-related queries
            invalidate_cache_pattern(short_cache, "tips_by_heuristic:")
            invalidate_cache_pattern(short_cache, "tips_by_round:")
            
            return list(result.scalars().all())
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
        force: bool = False,
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
        from app.crud import GameCRUD, ModelPredictionCRUD
        from app.orchestrator import ModelOrchestrator
        
        # Get games for round
        games = await GameCRUD.get_by_round(db, season, round_id)
        
        if not games:
            return {
                "success": False,
                "message": f"No games found for round {round_id}, season {season}",
                "tips_count": 0,
                "tips_created": 0,
                "tips_updated": 0,
                "tips_skipped": 0,
                "heuristics_used": [],
                "season": season,
                "round_id": round_id,
            }
        
        # Initialize orchestrator
        orchestrator = ModelOrchestrator()
        
        # Determine which heuristics to use
        if heuristics:
            heuristics_to_use = [h for h in heuristics if h in orchestrator.get_available_heuristics()]
        else:
            heuristics_to_use = orchestrator.get_available_heuristics()
        
        # Track statistics
        tips_created = 0
        tips_updated = 0
        tips_skipped = 0
        
        # Generate tips (idempotent - only create if not exist, unless force=True)
        for game in games:
            # Check if tips already exist for this game
            existing_tips = await TipCRUD.get_by_game(db, game.id)
            existing_heuristics = {tip.heuristic for tip in existing_tips}
            
            for heuristic in heuristics_to_use:
                # Check if tip already exists
                if heuristic in existing_heuristics:
                    if force:
                        # Delete existing tips for this heuristic
                        await TipCRUD.delete_for_game(db, game.id)
                        existing_heuristics.discard(heuristic)
                        # Re-fetch to get remaining tips
                        remaining_tips = await TipCRUD.get_by_game(db, game.id)
                        existing_heuristics = {tip.heuristic for tip in remaining_tips}
                        # Now create new tip (will be counted as created)
                    else:
                        tips_skipped += 1
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
                    tips_created += 1
                except Exception as e:
                    # Handle unique constraint violation (race condition)
                    # If another request created tip, just skip it
                    if "uq_game_heuristic" in str(e) or "duplicate" in str(e).lower():
                        tips_skipped += 1
                        continue
                    raise
            
            # Generate and store model predictions for this game
            for model in orchestrator.models:
                try:
                    winner, confidence, margin = await model.predict(game, db)
                    await ModelPredictionCRUD.create_or_update(
                        db=db,
                        game_id=game.id,
                        model_name=model.get_name(),
                        winner=winner,
                        confidence=confidence,
                        margin=margin,
                    )
                except Exception as e:
                    # Log error but continue with other models
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Error generating prediction for model {model.get_name()}: {e}", exc_info=True)
        
        return {
            "success": True,
            "message": f"Generated {tips_created} tips for round {round_id}, season {season}",
            "tips_count": tips_created,
            "tips_created": tips_created,
            "tips_updated": tips_updated,
            "tips_skipped": tips_skipped,
            "heuristics_used": heuristics_to_use,
            "season": season,
            "round_id": round_id,
        }
    
    @staticmethod
    async def generate_tips_for_game(
        db: AsyncSession,
        game_id: int,
        heuristics: Optional[List[str]] = None,
        force: bool = False
    ) -> dict:
        """Generate tips for a single game.
        
        Args:
            db: Database session
            game_id: Database ID of game
            heuristics: Optional list of heuristics to generate (default: all)
            force: Whether to force regeneration of existing tips (default: False)
            
        Returns:
            Dict with generation results:
            - tips_created: Number of tips created
            - tips_updated: Number of tips updated
            - tips_skipped: Number of tips skipped
            - heuristics_used: List of heuristics used
        """
        from app.crud import GameCRUD, ModelPredictionCRUD
        from app.orchestrator import ModelOrchestrator
        
        # Get game
        game = await GameCRUD.get_by_id(db, game_id)
        
        if not game:
            return {
                "success": False,
                "message": f"Game {game_id} not found",
                "tips_created": 0,
                "tips_updated": 0,
                "tips_skipped": 0,
                "heuristics_used": [],
                "game_id": game_id,
            }
        
        # Initialize orchestrator
        orchestrator = ModelOrchestrator()
        
        # Determine which heuristics to use
        if heuristics:
            heuristics_to_use = [h for h in heuristics if h in orchestrator.get_available_heuristics()]
        else:
            heuristics_to_use = orchestrator.get_available_heuristics()
        
        # Track statistics
        tips_created = 0
        tips_updated = 0
        tips_skipped = 0
        
        # Check if tips already exist for this game
        existing_tips = await TipCRUD.get_by_game(db, game_id)
        existing_heuristics = {tip.heuristic for tip in existing_tips}
        
        for heuristic in heuristics_to_use:
            # Check if tip already exists
            if heuristic in existing_heuristics:
                if force:
                    # Delete existing tips for this heuristic
                    await TipCRUD.delete_for_game(db, game_id)
                    existing_heuristics.discard(heuristic)
                    # Re-fetch to get remaining tips
                    remaining_tips = await TipCRUD.get_by_game(db, game_id)
                    existing_heuristics = {tip.heuristic for tip in remaining_tips}
                    # Now create new tip (will be counted as created)
                else:
                    tips_skipped += 1
                    continue
            
            # Generate prediction using heuristic
            winner, confidence, margin = await orchestrator.predict(game, heuristic)
            
            try:
                tip = await TipCRUD.create(
                    db=db,
                    game_id=game_id,
                    heuristic=heuristic,
                    selected_team=winner,
                    margin=margin,
                    confidence=confidence,
                    explanation="",  # Explanations can be generated separately
                )
                tips_created += 1
            except Exception as e:
                # Handle unique constraint violation (race condition)
                # If another request created tip, just skip it
                if "uq_game_heuristic" in str(e) or "duplicate" in str(e).lower():
                    tips_skipped += 1
                    continue
                raise
        
        # Generate and store model predictions for this game
        for model in orchestrator.models:
            try:
                winner, confidence, margin = await model.predict(game, db)
                await ModelPredictionCRUD.create_or_update(
                    db=db,
                    game_id=game_id,
                    model_name=model.get_name(),
                    winner=winner,
                    confidence=confidence,
                    margin=margin,
                )
            except Exception as e:
                # Log error but continue with other models
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error generating prediction for model {model.get_name()}: {e}", exc_info=True)
        
        return {
            "success": True,
            "message": f"Generated {tips_created} tips for game {game_id}",
            "tips_created": tips_created,
            "tips_updated": tips_updated,
            "tips_skipped": tips_skipped,
            "heuristics_used": heuristics_to_use,
            "game_id": game_id,
        }
