from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GenerationProgress


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
            started_at=datetime.now(timezone.utc),
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
        progress.updated_at = datetime.now(timezone.utc)

        if status:
            progress.status = status
            if status in ["completed", "failed"]:
                progress.completed_at = datetime.now(timezone.utc)

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
        """Get the progress record for the given operation (R4 contract).

        Returns the most relevant :class:`GenerationProgress` row for an
        operation_type / season pair, in this priority order:

        1. **In-flight** — the most-recent row with
           ``status == 'in_progress'``.
        2. **Finished** — the most-recent row with
           ``status IN ('completed', 'failed')``.
        3. ``None`` — when no row matches either bucket.

        Args:
            db: Database session.
            operation_type: Type of operation (e.g. ``"historic_refresh"``).
            season: Optional season year.  When ``None``, only rows with
                ``season IS NULL`` match.

        Returns:
            The single most-relevant :class:`GenerationProgress` row or
            ``None`` when no row matches.

        Note:
            Multiple rows can match the ``(operation_type, season)``
            pair (e.g. a re-triggered ``historic-refresh``).  We use
            ``scalars().first()`` to return the most-recent row
            rather than ``scalar_one_or_none()``, which would raise
            :class:`sqlalchemy.exc.MultipleResultsFound`.
        """
        base_filters = [GenerationProgress.operation_type == operation_type]
        if season is not None:
            base_filters.append(GenerationProgress.season == season)
        else:
            base_filters.append(GenerationProgress.season.is_(None))

        # 1. In-flight wins.  A row is considered in-flight when its
        #    status is exactly ``in_progress`` (the same definition used
        #    by ``get_in_progress_operations``).
        in_progress_result = await db.execute(
            select(GenerationProgress)
            .where(*base_filters, GenerationProgress.status == "in_progress")
            .order_by(GenerationProgress.started_at.desc())
        )
        in_progress = in_progress_result.scalars().first()
        if in_progress is not None:
            return in_progress

        # 2. Fall back to the most-recently-finished row.  We treat
        #    ``completed`` and ``failed`` as finished; ``pending`` rows
        #    are ignored (they have not started).
        finished_result = await db.execute(
            select(GenerationProgress)
            .where(
                *base_filters,
                GenerationProgress.status.in_(["completed", "failed"]),
            )
            .order_by(GenerationProgress.started_at.desc())
        )
        return finished_result.scalars().first()

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
        query = select(GenerationProgress).where(GenerationProgress.status == "in_progress")

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
        progress.completed_at = datetime.now(timezone.utc)

        if completed_items is not None:
            progress.completed_items = completed_items

        progress.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(progress)
        return progress

    @staticmethod
    async def upsert_active(
        db: AsyncSession,
        operation_type: str,
        total_items: int = 0,
        completed_items: int = 0,
    ) -> GenerationProgress:
        """Create or update an in-progress record for a given operation.

        Finds the latest ``in_progress`` record for *operation_type* (if any),
        updates its counters, or creates a fresh one.  Returns the record.

        Args:
            db: Database session.
            operation_type: Operation type (e.g. ``"historic_refresh"``).
            total_items: Total items to process.
            completed_items: Items completed so far.
        """
        now = datetime.now(timezone.utc)

        # Try to find an existing in-progress record
        result = await db.execute(
            select(GenerationProgress)
            .where(GenerationProgress.operation_type == operation_type)
            .where(GenerationProgress.status == "in_progress")
            .order_by(GenerationProgress.started_at.desc())
            .limit(1)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.completed_items = completed_items
            existing.total_items = total_items
            existing.updated_at = now
        else:
            existing = GenerationProgress(
                operation_type=operation_type,
                total_items=total_items,
                completed_items=completed_items,
                status="in_progress",
                started_at=now,
                updated_at=now,
            )
            db.add(existing)

        await db.commit()
        await db.refresh(existing)
        return existing

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
        progress.completed_at = datetime.now(timezone.utc)
        progress.error_message = error_message

        if completed_items is not None:
            progress.completed_items = completed_items

        progress.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(progress)
        return progress
