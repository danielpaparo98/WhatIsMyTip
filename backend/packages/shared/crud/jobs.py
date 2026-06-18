"""CRUD operations for job executions and locks."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import JobExecution, JobLock


class JobExecutionCRUD:
    """CRUD operations for job executions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_execution(
        self,
        job_name: str,
        status: str = "pending"
    ) -> JobExecution:
        """Create a new job execution record."""
        execution = JobExecution(
            job_name=job_name,
            status=status,
            started_at=datetime.now(timezone.utc),
            items_processed=0,
            items_failed=0
        )
        self.db.add(execution)
        await self.db.commit()
        await self.db.refresh(execution)
        return execution

    async def get_execution(self, execution_id: int) -> Optional[JobExecution]:
        """Get a job execution by ID."""
        result = await self.db.execute(
            select(JobExecution).where(JobExecution.id == execution_id)
        )
        return result.scalar_one_or_none()

    async def get_executions_by_job(
        self,
        job_name: str,
        limit: int = 100,
        status: Optional[str] = None
    ) -> List[JobExecution]:
        """Get job executions for a specific job."""
        query = select(JobExecution).where(JobExecution.job_name == job_name)

        if status:
            query = query.where(JobExecution.status == status)

        query = query.order_by(JobExecution.started_at.desc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_recent_executions(
        self,
        limit: int = 50,
        job_name: Optional[str] = None
    ) -> List[JobExecution]:
        """Get recent job executions."""
        query = select(JobExecution)

        if job_name:
            query = query.where(JobExecution.job_name == job_name)

        query = query.order_by(JobExecution.started_at.desc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_execution(
        self,
        execution_id: int,
        status: Optional[str] = None,
        completed_at: Optional[datetime] = None,
        duration_seconds: Optional[int] = None,
        items_processed: Optional[int] = None,
        items_failed: Optional[int] = None,
        error_message: Optional[str] = None,
        result_summary: Optional[str] = None
    ) -> Optional[JobExecution]:
        """Update a job execution record."""
        execution = await self.get_execution(execution_id)

        if not execution:
            return None

        if status is not None:
            execution.status = status
        if completed_at is not None:
            execution.completed_at = completed_at
        if duration_seconds is not None:
            execution.duration_seconds = duration_seconds
        if items_processed is not None:
            execution.items_processed = items_processed
        if items_failed is not None:
            execution.items_failed = items_failed
        if error_message is not None:
            execution.error_message = error_message
        if result_summary is not None:
            execution.result_summary = result_summary

        await self.db.commit()
        await self.db.refresh(execution)
        return execution

    async def get_job_metrics(self, job_name: str) -> dict:
        """Get metrics for a specific job."""
        # Get total runs
        total_result = await self.db.execute(
            select(func.count(JobExecution.id)).where(JobExecution.job_name == job_name)
        )
        total_runs = total_result.scalar() or 0

        # Get successful runs
        success_result = await self.db.execute(
            select(func.count(JobExecution.id)).where(
                and_(
                    JobExecution.job_name == job_name,
                    JobExecution.status == "completed"
                )
            )
        )
        successful_runs = success_result.scalar() or 0

        # Get failed runs
        failed_result = await self.db.execute(
            select(func.count(JobExecution.id)).where(
                and_(
                    JobExecution.job_name == job_name,
                    JobExecution.status == "failed"
                )
            )
        )
        failed_runs = failed_result.scalar() or 0

        # Get average duration
        duration_result = await self.db.execute(
            select(func.avg(JobExecution.duration_seconds)).where(
                and_(
                    JobExecution.job_name == job_name,
                    JobExecution.duration_seconds.isnot(None)
                )
            )
        )
        avg_duration = duration_result.scalar() or 0.0

        # Get last run
        last_run_result = await self.db.execute(
            select(JobExecution.started_at).where(JobExecution.job_name == job_name)
            .order_by(JobExecution.started_at.desc()).limit(1)
        )
        last_run = last_run_result.scalar_one_or_none()

        # Get last success
        last_success_result = await self.db.execute(
            select(JobExecution.completed_at).where(
                and_(
                    JobExecution.job_name == job_name,
                    JobExecution.status == "completed"
                )
            ).order_by(JobExecution.completed_at.desc()).limit(1)
        )
        last_success = last_success_result.scalar_one_or_none()

        # Get last failure
        last_failure_result = await self.db.execute(
            select(JobExecution.completed_at).where(
                and_(
                    JobExecution.job_name == job_name,
                    JobExecution.status == "failed"
                )
            ).order_by(JobExecution.completed_at.desc()).limit(1)
        )
        last_failure = last_failure_result.scalar_one_or_none()

        # Calculate success rate
        success_rate = (successful_runs / total_runs) if total_runs > 0 else 0.0

        return {
            "job_name": job_name,
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "failed_runs": failed_runs,
            "average_duration_seconds": avg_duration,
            "last_run_at": last_run,
            "last_success_at": last_success,
            "last_failure_at": last_failure,
            "success_rate": success_rate
        }

    async def cleanup_old_executions(
        self,
        days_to_keep: int = 30
    ) -> int:
        """Delete old job execution records."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)

        result = await self.db.execute(
            select(JobExecution.id).where(JobExecution.started_at < cutoff_date)
        )
        old_ids = [row[0] for row in result.all()]

        if old_ids:
            await self.db.execute(
                select(JobExecution).where(JobExecution.id.in_(old_ids))
            )

            for execution_id in old_ids:
                execution = await self.get_execution(execution_id)
                if execution:
                    await self.db.delete(execution)

            await self.db.commit()

        return len(old_ids)


class JobLockCRUD:
    """CRUD operations for job locks."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def acquire_lock(
        self,
        job_name: str,
        locked_by: str,
        expires_seconds: int = 3600
    ) -> Optional[JobLock]:
        """Acquire a job lock atomically using INSERT ... ON CONFLICT.

        Uses a single SQL statement to atomically acquire the lock,
        preventing race conditions where two concurrent invocations
        could both acquire the same lock.

        Before the INSERT, the method opportunistically cleans up any
        lock rows that have been expired for more than 24 hours
        (ME-004).  This keeps the ``job_locks`` table from growing
        without bound when long-running jobs crash and never call
        ``release_lock``.  The cleanup call is wrapped in
        ``try/except`` so a failure here never blocks the lock
        acquisition path.

        Returns:
            JobLock if lock was acquired, None if already locked
        """
        # Opportunistic stale-lock cleanup (ME-004).  We do this
        # *before* the atomic INSERT so a newly-acquired lock is not
        # inadvertently removed by a subsequent cleanup.  Failures
        # are swallowed because the primary purpose of this call is
        # to acquire the lock.
        try:
            await self.cleanup_expired_locks(min_age_seconds=24 * 3600)
        except Exception:
            # The cleanup is best-effort; log via the application
            # logger if available but never block the acquire path.
            pass

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_seconds)

        # Single atomic statement:
        # - If no row exists → INSERT succeeds → lock acquired
        # - If row exists and expired → UPDATE applies → lock acquired
        # - If row exists and not expired → WHERE fails → no change
        stmt = text("""
            INSERT INTO job_locks (job_name, locked_at, locked_by, expires_at)
            VALUES (:job_name, :now, :locked_by, :expires_at)
            ON CONFLICT (job_name)
            DO UPDATE SET locked_at = :now, locked_by = :locked_by, expires_at = :expires_at
            WHERE job_locks.expires_at < :now
        """)
        result = await self.db.execute(
            stmt,
            {
                "job_name": job_name,
                "now": now,
                "locked_by": locked_by,
                "expires_at": expires_at,
            },
        )
        await self.db.commit()

        if result.rowcount > 0:
            # Lock was acquired — fetch the row to return as ORM object
            fetch_result = await self.db.execute(
                select(JobLock).where(JobLock.job_name == job_name)
            )
            return fetch_result.scalar_one_or_none()

        return None

    async def release_lock(
        self,
        job_name: str,
        locked_by: str
    ) -> bool:
        """Release a job lock.

        Returns:
            True if lock was released, False if lock was not found or owned by another instance
        """
        result = await self.db.execute(
            select(JobLock).where(
                and_(
                    JobLock.job_name == job_name,
                    JobLock.locked_by == locked_by
                )
            )
        )
        lock = result.scalar_one_or_none()

        if lock:
            await self.db.delete(lock)
            await self.db.commit()
            return True

        return False

    async def get_lock(self, job_name: str) -> Optional[JobLock]:
        """Get a job lock by job name."""
        result = await self.db.execute(
            select(JobLock).where(JobLock.job_name == job_name)
        )
        return result.scalar_one_or_none()

    async def is_locked(self, job_name: str) -> bool:
        """Check if a job is locked."""
        lock = await self.get_lock(job_name)

        if not lock:
            return False

        # Check if lock is expired
        if lock.expires_at < datetime.now(timezone.utc):
            # Lock is expired, delete it
            await self.db.delete(lock)
            await self.db.commit()
            return False

        return True

    async def cleanup_expired_locks(self, min_age_seconds: int = 0) -> int:
        """Delete expired job locks.

        ``min_age_seconds`` is the minimum time a lock must have been
        expired before it is considered stale.  A value of ``0`` (the
        default) deletes every expired lock.  When called from
        ``acquire_lock`` (ME-004) we pass ``24 * 3600`` so freshly-
        expired locks are not yanked out from under a job that is
        still running but exceeded its TTL by a few seconds.

        Returns:
            Number of locks removed.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=min_age_seconds)

        result = await self.db.execute(
            select(JobLock).where(JobLock.expires_at < cutoff)
        )
        expired_locks = list(result.scalars().all())

        for lock in expired_locks:
            await self.db.delete(lock)

        await self.db.commit()
        return len(expired_locks)

    async def get_all_locks(self) -> List[JobLock]:
        """Get all active job locks."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(JobLock).where(JobLock.expires_at > now)
        )
        return list(result.scalars().all())
