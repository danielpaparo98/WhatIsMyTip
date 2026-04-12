"""Base job class for cron job implementations."""

import time
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Dict, Any, Type
from sqlalchemy.ext.asyncio import AsyncSession

from app.logger import get_logger


logger = get_logger(__name__)


class JobError(Exception):
    """Base exception for job errors."""
    pass


class TransientJobError(JobError):
    """Exception for transient errors that can be retried."""
    pass


class PermanentJobError(JobError):
    """Exception for permanent errors that should not be retried."""
    pass


class BaseJob(ABC):
    """Base class for all cron jobs.
    
    Provides common functionality for job execution including:
    - Job locking to prevent concurrent execution
    - Execution tracking in database
    - Error classification and retry logic
    - Logging and metrics collection
    """
    
    def __init__(
        self,
        job_name: str,
        db_session: AsyncSession,
        settings: Any,
        instance_id: Optional[str] = None
    ):
        """Initialize the base job.
        
        Args:
            job_name: Name of the job
            db_session: Database session for tracking
            settings: Application settings
            instance_id: Optional instance identifier for distributed locking
        """
        self.job_name = job_name
        self.db_session = db_session
        self.settings = settings
        self.instance_id = instance_id or "default"
        self.logger = logger
        
    @abstractmethod
    async def execute(self) -> Dict[str, Any]:
        """Execute the job logic.
        
        Returns:
            Dictionary with execution results including:
            - items_processed: Number of items processed
            - items_failed: Number of items that failed
            - summary: Text summary of execution
        """
        pass
    
    async def run(self) -> Dict[str, Any]:
        """Run the job with full lifecycle management.
        
        This method handles:
        1. Job locking
        2. Execution tracking
        3. Error handling
        4. Result recording
        
        Returns:
            Dictionary with execution results
        """
        from app.crud.jobs import JobExecutionCRUD, JobLockCRUD
        
        execution_crud = JobExecutionCRUD(self.db_session)
        lock_crud = JobLockCRUD(self.db_session)
        
        # Try to acquire lock
        lock = await lock_crud.acquire_lock(
            job_name=self.job_name,
            locked_by=self.instance_id,
            expires_seconds=self.settings.job_lock_expire_seconds
        )
        
        if not lock:
            self.logger.warning(
                f"Job {self.job_name} is already locked, skipping execution",
                extra={"job_name": self.job_name}
            )
            return {
                "status": "skipped",
                "reason": "job_locked",
                "items_processed": 0,
                "items_failed": 0
            }
        
        # Create execution record
        execution = await execution_crud.create_execution(
            job_name=self.job_name,
            status="running"
        )
        
        self.logger.info(
            f"Starting job {self.job_name}",
            extra={
                "job_name": self.job_name,
                "job_execution_id": execution.id
            }
        )
        
        start_time = time.time()
        result: Dict[str, Any] = {
            "items_processed": 0,
            "items_failed": 0,
            "summary": None
        }
        
        try:
            # Execute the job
            result = await self.execute()
            
            # Calculate duration
            duration = time.time() - start_time
            
            # Update execution as completed
            await execution_crud.update_execution(
                execution_id=execution.id,
                status="completed",
                completed_at=datetime.utcnow(),
                duration_seconds=int(duration),
                items_processed=result.get("items_processed", 0),
                items_failed=result.get("items_failed", 0),
                result_summary=result.get("summary")
            )
            
            self.logger.info(
                f"Job {self.job_name} completed successfully",
                extra={
                    "job_name": self.job_name,
                    "job_execution_id": execution.id,
                    "duration_seconds": duration,
                    "items_processed": result.get("items_processed", 0),
                    "items_failed": result.get("items_failed", 0)
                }
            )
            
        except TransientJobError as e:
            # Transient error - should be retried
            duration = time.time() - start_time
            await execution_crud.update_execution(
                execution_id=execution.id,
                status="failed",
                completed_at=datetime.utcnow(),
                duration_seconds=int(duration),
                error_message=str(e),
                result_summary={"error_type": "transient"}
            )
            
            self.logger.error(
                f"Job {self.job_name} failed with transient error: {e}",
                extra={
                    "job_name": self.job_name,
                    "job_execution_id": execution.id,
                    "error": str(e),
                    "error_type": "transient"
                }
            )
            
            # Release lock for retry
            await lock_crud.release_lock(self.job_name, self.instance_id)
            raise
            
        except PermanentJobError as e:
            # Permanent error - should not be retried
            duration = time.time() - start_time
            await execution_crud.update_execution(
                execution_id=execution.id,
                status="failed",
                completed_at=datetime.utcnow(),
                duration_seconds=int(duration),
                error_message=str(e),
                result_summary={"error_type": "permanent"}
            )
            
            self.logger.error(
                f"Job {self.job_name} failed with permanent error: {e}",
                extra={
                    "job_name": self.job_name,
                    "job_execution_id": execution.id,
                    "error": str(e),
                    "error_type": "permanent"
                }
            )
            
            # Release lock
            await lock_crud.release_lock(self.job_name, self.instance_id)
            raise
            
        except Exception as e:
            # Unexpected error
            duration = time.time() - start_time
            await execution_crud.update_execution(
                execution_id=execution.id,
                status="failed",
                completed_at=datetime.utcnow(),
                duration_seconds=int(duration),
                error_message=str(e),
                result_summary={"error_type": "unexpected"}
            )
            
            self.logger.exception(
                f"Job {self.job_name} failed with unexpected error: {e}",
                extra={
                    "job_name": self.job_name,
                    "job_execution_id": execution.id,
                    "error": str(e),
                    "error_type": "unexpected"
                }
            )
            
            # Release lock
            await lock_crud.release_lock(self.job_name, self.instance_id)
            raise
            
        finally:
            # Always release lock
            await lock_crud.release_lock(self.job_name, self.instance_id)
        
        return result
    
    async def retry_with_backoff(
        self,
        func,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_multiplier: float = 2.0,
        max_delay: float = 300.0,
        jitter: float = 0.1
    ) -> Any:
        """Retry a function with exponential backoff.
        
        Args:
            func: Async function to retry
            max_retries: Maximum number of retries
            initial_delay: Initial delay in seconds
            backoff_multiplier: Multiplier for exponential backoff
            max_delay: Maximum delay between retries
            jitter: Random jitter factor (0-1)
            
        Returns:
            Result of the function
            
        Raises:
            Exception: If all retries fail
        """
        import random
        
        last_exception = None
        delay = initial_delay
        
        for attempt in range(max_retries + 1):
            try:
                return await func()
            except Exception as e:
                last_exception = e
                
                if attempt == max_retries:
                    raise
                
                # Calculate delay with jitter
                actual_delay = min(delay * (1 + random.uniform(-jitter, jitter)), max_delay)
                
                self.logger.warning(
                    f"Attempt {attempt + 1}/{max_retries} failed, retrying in {actual_delay:.2f}s",
                    extra={
                        "job_name": self.job_name,
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "error": str(e)
                    }
                )
                
                await asyncio.sleep(actual_delay)
                delay *= backoff_multiplier
        
        raise last_exception  # type: ignore
