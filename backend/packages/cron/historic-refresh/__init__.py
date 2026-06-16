"""Digital Ocean Scheduled Function: Historic Data Refresh.

Triggered Sunday at 4 AM by the DO scheduler. Refreshes historical game data
and tips for seasons 2010–2025 from the Squiggle API.

**Batch processing with continuation**: DO Functions have a 15-minute max timeout
for scheduled functions. Seasons are processed in batches of 2. When the
function runs out of time before completing all seasons, it stores the remaining
seasons in Redis so the next invocation can resume from where it left off.
"""

import os
import sys
import time
import traceback

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from packages.shared.alerting import AlertingService
from packages.shared.cache import RedisCache, close_redis_pool, invalidate_cache_pattern, medium_cache
from packages.shared.config import settings
from packages.shared.crud.generation_progress import GenerationProgressCRUD
from packages.shared.crud.jobs import JobExecutionCRUD, JobLockCRUD
from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.exceptions import TransientJobError, classify_error
from packages.shared.logger import generate_execution_id, get_logger
from packages.shared.services.historic_data_refresh import HistoricDataRefreshService

logger = get_logger(__name__)

JOB_NAME = "historic-refresh"
LOCKED_BY = "faas-historic-refresh"

# All seasons covered by the historic refresh
ALL_SEASONS = list(range(2010, 2026))

# Number of seasons per batch — finer granularity keeps each batch short
# within the 15-minute limit.
BATCH_SIZE = 2

# Redis key for continuation state (prefix "wimt:" is added by RedisCache).
CONTINUATION_KEY = "historic-refresh:remaining"

# TTL for the continuation marker — 1 week is plenty for a weekly job.
CONTINUATION_TTL = 7 * 24 * 3600


def _build_batches(seasons: list[int] | None = None) -> list[list[int]]:
    """Split *seasons* into batches of ``BATCH_SIZE``.

    Args:
        seasons: List of season years.  Defaults to ``ALL_SEASONS``.
    """
    source = seasons if seasons is not None else ALL_SEASONS
    return [
        source[i : i + BATCH_SIZE]
        for i in range(0, len(source), BATCH_SIZE)
    ]


# Maximum runtime before yielding to avoid exceeding DO Functions timeout.
# 13 minutes leaves a 2-minute safety buffer under the 15-minute hard limit.
MAX_RUNTIME_SECONDS = 780


async def main(args: dict) -> dict:
    """Scheduled function entry point.

    Processes batches of seasons in a loop with a time check to stay under
    the DO Functions 15-minute timeout.  If time runs out, the remaining
    seasons are stored in Redis so the next invocation can resume.

    Args:
        args: Environment variables and trigger metadata from DO scheduler.

    Returns:
        dict with statusCode and body.
    """
    execution_id = generate_execution_id()
    log_extra = {"job_name": JOB_NAME, "execution_id": execution_id}

    logger.info("%s: Starting execution", JOB_NAME, extra=log_extra)

    factory = _get_session_factory()
    cache = RedisCache()

    async with factory() as session:
        execution = None
        lock_crud = JobLockCRUD(session)
        execution_crud = JobExecutionCRUD(session)
        locked = False
        had_error = False

        try:
            # 1. Acquire lock
            lock = await lock_crud.acquire_lock(
                job_name=JOB_NAME,
                locked_by=LOCKED_BY,
                expires_seconds=settings.historical_refresh_timeout_seconds,
            )
            if not lock:
                logger.info("%s: Could not acquire lock, skipping", JOB_NAME)
                return {"statusCode": 200, "body": {"message": "Job already running"}}

            locked = True

            # 2. Create execution record
            execution = await execution_crud.create_execution(JOB_NAME, status="running")
            await session.commit()

            # 3. Execute job logic (delegated to the reusable service function
            #    shared with the new in-process BaseJob wrapper).
            start_time = time.time()

            from packages.shared.services.historic_refresh import (
                run_historic_refresh as _service,
            )

            result = await _service(session, cache=cache)
            summary = result.get("message", "")
            total_seasons_processed = result.get("total_seasons_processed", 0)
            total_errors = result.get("total_errors", 0)
            duration = time.time() - start_time

            logger.info("%s completed: %s", JOB_NAME, summary, extra=log_extra)

            # 5. Mark success
            await execution_crud.update_execution(
                execution.id,
                status="completed",
                duration_seconds=int(duration),
                items_processed=total_seasons_processed,
                items_failed=total_errors,
                result_summary=summary,
            )
            await session.commit()

            return {"statusCode": 200, "body": {"message": summary}}

        except Exception as e:
            had_error = True
            classified = classify_error(e)
            if isinstance(classified, TransientJobError):
                logger.warning(
                    "Transient error in %s: %s",
                    JOB_NAME,
                    classified.message,
                    extra={**log_extra, "error_type": "transient", "details": classified.details},
                )
            else:
                logger.error(
                    "Permanent error in %s: %s",
                    JOB_NAME,
                    classified.message,
                    extra={**log_extra, "error_type": "permanent", "details": classified.details},
                )
            logger.error("%s error: %s\n%s", JOB_NAME, e, traceback.format_exc())
            if execution:
                try:
                    await execution_crud.update_execution(
                        execution.id,
                        status="failed",
                        error_message=str(e),
                    )
                    await session.commit()
                except Exception:
                    logger.error("Failed to update execution record: %s", traceback.format_exc())
            try:
                alerting = AlertingService()
                await alerting.send_failure_alert(
                    job_name=JOB_NAME,
                    error=str(e),
                    execution_id=str(execution.id) if execution else None,
                )
            except Exception:
                logger.error("Failed to send alert: %s", traceback.format_exc())
            return {"statusCode": 500, "body": {"error": str(e)}}

        finally:
            if locked:
                try:
                    await lock_crud.release_lock(JOB_NAME, LOCKED_BY)
                    await session.commit()
                except Exception:
                    logger.error("Failed to release lock: %s", traceback.format_exc())
            await close_redis_pool(force=had_error)
            await dispose_engine(force=had_error)
