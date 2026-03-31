from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from app.models import ModelPrediction
from app.cache import cached, short_cache


class ModelPredictionCRUD:
    """CRUD operations for model predictions."""
    
    @staticmethod
    async def get_by_game(db: AsyncSession, game_id: int) -> List[ModelPrediction]:
        """Get all model predictions for a game."""
        result = await db.execute(
            select(ModelPrediction)
            .where(ModelPrediction.game_id == game_id)
            .order_by(ModelPrediction.model_name)
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def create(
        db: AsyncSession,
        game_id: int,
        model_name: str,
        winner: str,
        confidence: float,
        margin: int,
    ) -> ModelPrediction:
        """Create a new model prediction with proper transaction management."""
        from app.cache import invalidate_cache_pattern
        
        try:
            prediction = ModelPrediction(
                game_id=game_id,
                model_name=model_name,
                winner=winner,
                confidence=confidence,
                margin=margin,
            )
            db.add(prediction)
            await db.commit()
            await db.refresh(prediction)
            
            # Invalidate cache for model prediction queries
            invalidate_cache_pattern(short_cache, "model_predictions:")
            
            return prediction
        except Exception as e:
            await db.rollback()
            raise
    
    @staticmethod
    async def create_or_update(
        db: AsyncSession,
        game_id: int,
        model_name: str,
        winner: str,
        confidence: float,
        margin: int,
    ) -> ModelPrediction:
        """Create or update a model prediction."""
        # Check if prediction already exists
        result = await db.execute(
            select(ModelPrediction).where(
                ModelPrediction.game_id == game_id,
                ModelPrediction.model_name == model_name
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            # Update existing prediction
            existing.winner = winner
            existing.confidence = confidence
            existing.margin = margin
            await db.commit()
            await db.refresh(existing)
            return existing
        else:
            # Create new prediction
            return await ModelPredictionCRUD.create(
                db=db,
                game_id=game_id,
                model_name=model_name,
                winner=winner,
                confidence=confidence,
                margin=margin,
            )
    
    @staticmethod
    async def delete_for_game(db: AsyncSession, game_id: int) -> int:
        """Delete all model predictions for a game."""
        from app.cache import invalidate_cache_pattern
        
        try:
            result = await db.execute(
                select(ModelPrediction).where(ModelPrediction.game_id == game_id)
            )
            predictions = result.scalars().all()
            count = len(predictions)
            for prediction in predictions:
                db.delete(prediction)
            await db.commit()
            
            # Invalidate cache
            invalidate_cache_pattern(short_cache, "model_predictions:")
            
            return count
        except Exception as e:
            await db.rollback()
            raise
