from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert
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
    @cached(cache=short_cache, key_prefix="model_predictions_by_games:")
    async def get_by_games(db: AsyncSession, game_ids: List[int]) -> dict:
        """Get all model predictions for multiple games in a single batch query.
        
        Args:
            db: Database session
            game_ids: List of game IDs to fetch predictions for
            
        Returns:
            Dictionary mapping game_id to list of ModelPrediction objects
        """
        result = await db.execute(
            select(ModelPrediction)
            .where(ModelPrediction.game_id.in_(game_ids))
            .order_by(ModelPrediction.game_id, ModelPrediction.model_name)
        )
        predictions = list(result.scalars().all())
        
        # Group predictions by game_id
        predictions_by_game = {}
        for prediction in predictions:
            if prediction.game_id not in predictions_by_game:
                predictions_by_game[prediction.game_id] = []
            predictions_by_game[prediction.game_id].append(prediction)
        
        return predictions_by_game
    
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
            invalidate_cache_pattern(short_cache, "model_predictions_by_games:")
            
            return prediction
        except Exception as e:
            await db.rollback()
            raise
    
    @staticmethod
    async def create_batch(db: AsyncSession, predictions_data: List[dict]) -> List[ModelPrediction]:
        """Create multiple model predictions in a single bulk insert operation.
        
        Args:
            db: Database session
            predictions_data: List of dictionaries containing prediction data
            
        Returns:
            List of created ModelPrediction objects
        """
        from app.cache import invalidate_cache_pattern
        
        try:
            stmt = insert(ModelPrediction).values(predictions_data).returning(ModelPrediction)
            result = await db.execute(stmt)
            await db.commit()
            
            # Invalidate cache for model prediction queries
            invalidate_cache_pattern(short_cache, "model_predictions:")
            invalidate_cache_pattern(short_cache, "model_predictions_by_games:")
            
            return list(result.scalars().all())
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
            invalidate_cache_pattern(short_cache, "model_predictions_by_games:")
            
            return count
        except Exception as e:
            await db.rollback()
            raise
