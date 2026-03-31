from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import List, Optional
from app.models import GenerationProgress
from datetime import datetime


class GenerationProgressCRUD:
    """CRUD operations for generation progress tracking."""
    
    @staticmethod
    async def create(
        db: AsyncSession,
        operation_type: str,
        total_items: int = 0,
        season: Optional[int] = None,
    ) -> GenerationProgress:
        """Create a new progress record."""
        progress = GenerationProgress(
            operation_type=operation_type,
            total_items=total_items,
            season=season,
            status="pending",
            started_at=datetime.utcnow(),
        )
        db.add(progress)
        await db.commit()
        await db.refresh(progress)
        return progress
    
    @staticmethod
    async def update_progress(
        db: AsyncSession,
        progress_id: int,
        completed_items: int,
        status: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[GenerationProgress]:
        """Update progress and status."""
        result = await db.execute(
            select(GenerationProgress).where(GenerationProgress.id == progress_id)
        )
        progress = result.scalar_one_or_none()
        
        if not progress:
            return None
        
        progress.completed_items = completed_items
        progress.updated_at = datetime.utcnow()
        
        if status:
            progress.status = status
            if status in ["completed", "failed"]:
                progress.completed_at = datetime.utcnow()
        
        if error_message:
            progress.error_message = error_message
        
        await db.commit()
        await db.refresh(progress)
        return progress
    
    @staticmethod
    async def get_by_operation(
        db: AsyncSession,
        operation_type: str,
        season: Optional[int] = None,
    ) -> Optional[GenerationProgress]:
        """Get progress by operation type and season."""
        query = select(GenerationProgress).where(GenerationProgress.operation_type == operation_type)
        
        if season is not None:
            query = query.where(GenerationProgress.season == season)
        else:
            query = query.where(GenerationProgress.season.is_(None))
        
        query = query.order_by(GenerationProgress.started_at.desc())
        
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    @staticmethod
    async def get_active_operations(
        db: AsyncSession,
        operation_type: Optional[str] = None,
    ) -> List[GenerationProgress]:
        """Get all in-progress operations."""
        query = select(GenerationProgress).where(
            GenerationProgress.status.in_(["pending", "in_progress"])
        )
        
        if operation_type:
            query = query.where(GenerationProgress.operation_type == operation_type)
        
        query = query.order_by(GenerationProgress.started_at.desc())
        
        result = await db.execute(query)
        return list(result.scalars().all())
    
    @staticmethod
    async def get_by_id(
        db: AsyncSession,
        progress_id: int,
    ) -> Optional[GenerationProgress]:
        """Get progress by ID."""
        result = await db.execute(
            select(GenerationProgress).where(GenerationProgress.id == progress_id)
        )
        return result.scalar_one_or_none()
