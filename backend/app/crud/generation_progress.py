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
        job_execution_id: Optional[int] = None,
    ) -> GenerationProgress:
        """Create a new progress record.
        
        Args:
            db: Database session
            operation_type: Type of operation (e.g., "historic_refresh", "tip_generation")
            total_items: Total number of items to process
            season: Optional season year
            job_execution_id: Optional job execution ID for tracking
            
        Returns:
            Created GenerationProgress record
        """
        progress = GenerationProgress(
            operation_type=operation_type,
            total_items=total_items,
            season=season,
            status="pending",
            started_at=datetime.utcnow(),
            job_execution_id=job_execution_id,
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
        """Update progress and status.
        
        Args:
            db: Database session
            progress_id: Progress record ID
            completed_items: Number of items completed
            status: Optional new status
            error_message: Optional error message
            
        Returns:
            Updated GenerationProgress record or None if not found
        """
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
        """Get progress by operation type and season.
        
        Args:
            db: Database session
            operation_type: Type of operation
            season: Optional season year
            
        Returns:
            Most recent GenerationProgress record or None
        """
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
        """Get all in-progress operations.
        
        Args:
            db: Database session
            operation_type: Optional operation type filter
            
        Returns:
            List of in-progress GenerationProgress records
        """
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
        """Get progress by ID.
        
        Args:
            db: Database session
            progress_id: Progress record ID
            
        Returns:
            GenerationProgress record or None
        """
        result = await db.execute(
            select(GenerationProgress).where(GenerationProgress.id == progress_id)
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def get_in_progress_operations(
        db: AsyncSession,
        operation_type: Optional[str] = None,
    ) -> List[GenerationProgress]:
        """Find operations that are in progress.
        
        Args:
            db: Database session
            operation_type: Optional operation type filter
            
        Returns:
            List of in-progress GenerationProgress records for resumption
        """
        query = select(GenerationProgress).where(
            GenerationProgress.status == "in_progress"
        )
        
        if operation_type:
            query = query.where(GenerationProgress.operation_type == operation_type)
        
        query = query.order_by(GenerationProgress.started_at.desc())
        
        result = await db.execute(query)
        return list(result.scalars().all())
    
    @staticmethod
    async def mark_completed(
        db: AsyncSession,
        progress_id: int,
        completed_items: Optional[int] = None,
    ) -> Optional[GenerationProgress]:
        """Mark operation as completed.
        
        Args:
            db: Database session
            progress_id: Progress record ID
            completed_items: Optional final completed items count
            
        Returns:
            Updated GenerationProgress record or None if not found
        """
        result = await db.execute(
            select(GenerationProgress).where(GenerationProgress.id == progress_id)
        )
        progress = result.scalar_one_or_none()
        
        if not progress:
            return None
        
        progress.status = "completed"
        progress.completed_at = datetime.utcnow()
        
        if completed_items is not None:
            progress.completed_items = completed_items
        
        progress.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(progress)
        return progress
    
    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        progress_id: int,
        error_message: str,
        completed_items: Optional[int] = None,
    ) -> Optional[GenerationProgress]:
        """Mark operation as failed.
        
        Args:
            db: Database session
            progress_id: Progress record ID
            error_message: Error message describing the failure
            completed_items: Optional final completed items count
            
        Returns:
            Updated GenerationProgress record or None if not found
        """
        result = await db.execute(
            select(GenerationProgress).where(GenerationProgress.id == progress_id)
        )
        progress = result.scalar_one_or_none()
        
        if not progress:
            return None
        
        progress.status = "failed"
        progress.completed_at = datetime.utcnow()
        progress.error_message = error_message
        
        if completed_items is not None:
            progress.completed_items = completed_items
        
        progress.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(progress)
        return progress
