"""Cron job management using fastapi-crons."""

import socket
import os
from typing import Optional, Dict, Any, List
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logger import get_logger
from app.crud.jobs import JobExecutionCRUD, JobLockCRUD
from app.schemas.cron import (
    JobStatusResponse,
    JobTriggerResponse,
    CronHealthResponse,
    JobMetrics
)

logger = get_logger(__name__)


class CronJobManager:
    """Manager for cron jobs using fastapi-crons.
    
    This class handles:
    - Registration of cron jobs with schedules
    - Job execution tracking
    - Job locking to prevent concurrent execution
    - Manual job triggering
    - Job status monitoring
    """
    
    def __init__(self, app: FastAPI):
        """Initialize the CronJobManager.
        
        Args:
            app: FastAPI application instance
        """
        self.app = app
        self.jobs: Dict[str, Any] = {}
        self.instance_id = f"{socket.gethostname()}-{os.getpid()}"
        self.enabled = settings.cron_enabled
        self.logger = logger
        
    async def register_job(
        self,
        name: str,
        schedule: str,
        job_class: type,
        enabled: bool = True
    ) -> None:
        """Register a cron job.
        
        Args:
            name: Unique name for the job
            schedule: Cron schedule expression (e.g., "0 2 * * *")
            job_class: Job class that inherits from BaseJob
            enabled: Whether the job is enabled
        """
        if not self.enabled:
            self.logger.info(f"Cron jobs disabled, skipping registration of {name}")
            return
        
        self.jobs[name] = {
            "name": name,
            "schedule": schedule,
            "job_class": job_class,
            "enabled": enabled
        }
        
        self.logger.info(
            f"Registered cron job: {name} with schedule: {schedule}",
            extra={"job_name": name, "schedule": schedule}
        )
    
    async def register_jobs(self) -> None:
        """Register all cron jobs.
        
        This method should be called during application startup.
        Jobs will be registered here in subsequent phases.
        """
        if not self.enabled:
            self.logger.info("Cron jobs are disabled, skipping registration")
            return
        
        self.logger.info("Registering cron jobs...")
        
        # Phase 2: Daily Game Sync Job
        from app.cron.jobs.daily_sync import DailyGameSyncJob
        
        await self.register_job(
            name="daily_game_sync",
            schedule=settings.cron_daily_sync,
            job_class=DailyGameSyncJob
        )
        
        # Phase 3: Match Completion Detection Job
        from app.cron.jobs.match_completion import MatchCompletionDetectionJob
        
        await self.register_job(
            name="match_completion_detector",
            schedule=settings.cron_match_completion_check,
            job_class=MatchCompletionDetectionJob,
            enabled=settings.match_completion_check_enabled
        )
        
        # Phase 4: Tip Generation Job
        from app.cron.jobs.tip_generation import TipGenerationJob
        
        await self.register_job(
            name="tip_generation",
            schedule=settings.cron_tip_generation,
            job_class=TipGenerationJob,
            enabled=settings.tip_generation_enabled
        )
        
        self.logger.info(f"Registered {len(self.jobs)} cron jobs")
    
    async def execute_job(
        self,
        job_name: str,
        db: AsyncSession,
        force: bool = False
    ) -> JobTriggerResponse:
        """Execute a job manually.
        
        Args:
            job_name: Name of the job to execute
            db: Database session
            force: Force execution even if job is locked
            
        Returns:
            JobTriggerResponse with execution result
        """
        if job_name not in self.jobs:
            return JobTriggerResponse(
                job_name=job_name,
                status="error",
                execution_id=None,
                message=f"Job {job_name} not found"
            )
        
        job_config = self.jobs[job_name]
        
        if not job_config["enabled"]:
            return JobTriggerResponse(
                job_name=job_name,
                status="skipped",
                execution_id=None,
                message=f"Job {job_name} is disabled"
            )
        
        # Check lock if not forcing
        if not force:
            lock_crud = JobLockCRUD(db)
            is_locked = await lock_crud.is_locked(job_name)
            
            if is_locked:
                return JobTriggerResponse(
                    job_name=job_name,
                    status="skipped",
                    execution_id=None,
                    message=f"Job {job_name} is already running"
                )
        
        # Create job instance and execute
        job_class = job_config["job_class"]
        job = job_class(
            job_name=job_name,
            db_session=db,
            settings=settings,
            instance_id=self.instance_id
        )
        
        try:
            result = await job.run()
            
            execution_crud = JobExecutionCRUD(db)
            # Get the most recent execution for this job
            executions = await execution_crud.get_executions_by_job(job_name, limit=1)
            execution_id = executions[0].id if executions else None
            
            return JobTriggerResponse(
                job_name=job_name,
                status="success",
                execution_id=execution_id,
                message=f"Job {job_name} completed successfully"
            )
            
        except Exception as e:
            self.logger.exception(
                f"Failed to execute job {job_name}: {e}",
                extra={"job_name": job_name, "error": str(e)}
            )
            
            return JobTriggerResponse(
                job_name=job_name,
                status="failed",
                execution_id=None,
                message=f"Job {job_name} failed: {str(e)}"
            )
    
    async def get_job_status(self, job_name: str, db: AsyncSession) -> Optional[JobStatusResponse]:
        """Get the status of a specific job.
        
        Args:
            job_name: Name of the job
            db: Database session
            
        Returns:
            JobStatusResponse or None if job not found
        """
        if job_name not in self.jobs:
            return None
        
        job_config = self.jobs[job_name]
        execution_crud = JobExecutionCRUD(db)
        lock_crud = JobLockCRUD(db)
        
        # Get job metrics
        metrics = await execution_crud.get_job_metrics(job_name)
        
        # Get lock status
        lock = await lock_crud.get_lock(job_name)
        
        return JobStatusResponse(
            job_name=job_name,
            status="enabled" if job_config["enabled"] else "disabled",
            last_run=metrics.get("last_run_at"),
            last_duration_seconds=metrics.get("average_duration_seconds"),
            last_success_at=metrics.get("last_success_at"),
            last_failure_at=metrics.get("last_failure_at"),
            total_runs=metrics.get("total_runs", 0),
            successful_runs=metrics.get("successful_runs", 0),
            failed_runs=metrics.get("failed_runs", 0),
            success_rate=metrics.get("success_rate", 0.0),
            is_locked=lock is not None,
            locked_at=lock.locked_at if lock else None,
            locked_by=lock.locked_by if lock else None,
            expires_at=lock.expires_at if lock else None
        )
    
    async def get_all_jobs_status(self, db: AsyncSession) -> List[JobStatusResponse]:
        """Get status of all registered jobs.
        
        Args:
            db: Database session
            
        Returns:
            List of JobStatusResponse
        """
        statuses = []
        
        for job_name in self.jobs:
            status = await self.get_job_status(job_name, db)
            if status:
                statuses.append(status)
        
        return statuses
    
    async def get_health(self, db: AsyncSession) -> CronHealthResponse:
        """Get health status of cron jobs.
        
        Args:
            db: Database session
            
        Returns:
            CronHealthResponse
        """
        from datetime import datetime
        
        # Check database connectivity
        try:
            from sqlalchemy import text
            await db.execute(text("SELECT 1"))
            db_status = "connected"
        except Exception as e:
            self.logger.error(f"Database health check failed: {e}")
            db_status = "disconnected"
        
        # Get all job statuses
        job_statuses = await self.get_all_jobs_status(db)
        
        # Determine overall status
        if db_status != "connected":
            overall_status = "unhealthy"
        elif any(job.is_locked for job in job_statuses):
            overall_status = "degraded"
        else:
            overall_status = "healthy"
        
        return CronHealthResponse(
            status=overall_status,
            timestamp=datetime.utcnow(),
            jobs=job_statuses,
            database=db_status,
            cron_enabled=self.enabled
        )
    
    async def enable_job(self, job_name: str) -> bool:
        """Enable a job.
        
        Args:
            job_name: Name of the job to enable
            
        Returns:
            True if successful, False if job not found
        """
        if job_name not in self.jobs:
            return False
        
        self.jobs[job_name]["enabled"] = True
        self.logger.info(f"Enabled job: {job_name}", extra={"job_name": job_name})
        return True
    
    async def disable_job(self, job_name: str) -> bool:
        """Disable a job.
        
        Args:
            job_name: Name of the job to disable
            
        Returns:
            True if successful, False if job not found
        """
        if job_name not in self.jobs:
            return False
        
        self.jobs[job_name]["enabled"] = False
        self.logger.info(f"Disabled job: {job_name}", extra={"job_name": job_name})
        return True
    
    async def cleanup_expired_locks(self, db: AsyncSession) -> int:
        """Clean up expired job locks.
        
        Args:
            db: Database session
            
        Returns:
            Number of locks cleaned up
        """
        lock_crud = JobLockCRUD(db)
        count = await lock_crud.cleanup_expired_locks()
        
        if count > 0:
            self.logger.info(f"Cleaned up {count} expired job locks")
        
        return count


# Global instance
cron_manager: Optional[CronJobManager] = None


def get_cron_manager() -> CronJobManager:
    """Get the global cron manager instance.
    
    Returns:
        CronJobManager instance
        
    Raises:
        RuntimeError: If cron manager is not initialized
    """
    if cron_manager is None:
        raise RuntimeError("CronJobManager not initialized. Call init_cron_manager() first.")
    return cron_manager


def init_cron_manager(app: FastAPI) -> CronJobManager:
    """Initialize the global cron manager.
    
    Args:
        app: FastAPI application instance
        
    Returns:
        CronJobManager instance
    """
    global cron_manager
    cron_manager = CronJobManager(app)
    return cron_manager
