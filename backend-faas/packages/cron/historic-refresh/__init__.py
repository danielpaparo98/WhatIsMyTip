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

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.cache import RedisCache, close_redis_pool
from packages.shared.config import settings
from packages.shared.logger import get_logger
from packages.shared.crud.jobs import JobExecutionCRUD, JobLockCRUD
from packages.shared.services.historic_data_refresh import HistoricDataRefreshService
from packages.shared.alerting import AlertingService

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
                logger.info(f"{JOB_NAME}: Could not acquire lock, skipping")
                return {"statusCode": 200, "body": {"message": "Job already running"}}

            locked = True

            # 2. Create execution record
            execution = await execution_crud.create_execution(JOB_NAME, status="running")
            await session.commit()

            # 3. Determine seasons to process — check Redis continuation marker first
            remaining = await cache.get(CONTINUATION_KEY)
            if remaining:
                seasons_to_process = remaining
                logger.info(
                    f"Resuming from continuation marker: "
                    f"{len(seasons_to_process)} seasons remaining"
                )
            else:
                seasons_to_process = list(ALL_SEASONS)
                logger.info(
                    f"Starting fresh: processing all {len(seasons_to_process)} seasons"
                )

            batches = _build_batches(seasons_to_process)

            # 4. Process batches with time check
            overall_start = time.time()
            total_seasons_processed = 0
            total_games_synced = 0
            total_tips_generated = 0
            total_errors = 0
            batches_processed = 0

            for batch_idx, seasons in enumerate(batches):
                # Check if we have time for another batch
                elapsed = time.time() - overall_start
                if elapsed > MAX_RUNTIME_SECONDS:
                    # Store remaining seasons in Redis for next invocation
                    remaining_seasons = []
                    for remaining_batch in batches[batch_idx:]:
                        remaining_seasons.extend(remaining_batch)
                    await cache.set(
                        CONTINUATION_KEY,
                        remaining_seasons,
                        ttl=CONTINUATION_TTL,
                    )
                    logger.warning(
                        f"Approaching timeout after {elapsed:.0f}s, "
                        f"processed {batches_processed} batch(es). "
                        f"Stored {len(remaining_seasons)} remaining seasons in Redis."
                    )
                    try:
                        alerting = AlertingService()
                        await alerting.send_timeout_alert(
                            job_name=JOB_NAME,
                            elapsed_seconds=elapsed,
                            remaining_work=f"{len(remaining_seasons)} seasons remaining",
                        )
                    except Exception:
                        logger.error(f"Failed to send timeout alert: {traceback.format_exc()}")
                    break

                seasons_str = ",".join(str(s) for s in seasons)

                logger.info(
                    f"Processing batch {batch_idx + 1}/{len(batches)}: {seasons_str}"
                )

                batch_start = time.time()

                refresh_service = HistoricDataRefreshService(
                    db_session=session,
                    seasons=seasons,
                    round_id=None,
                    regenerate_tips=settings.historic_refresh_regenerate_tips,
                )

                refresh_stats = await refresh_service.refresh_from_string(
                    seasons_str=seasons_str,
                    round_id=None,
                    regenerate_tips=settings.historic_refresh_regenerate_tips,
                )

                batch_duration = time.time() - batch_start
                total_seasons_processed += refresh_stats["seasons_processed"]
                total_games_synced += refresh_stats["games_synced"]
                total_tips_generated += refresh_stats["tips_generated"]
                total_errors += len(refresh_stats.get("errors", []))
                batches_processed += 1

                logger.info(
                    f"Batch {seasons_str} completed in {batch_duration:.1f}s: "
                    f"{refresh_stats['seasons_processed']} seasons, "
                    f"{refresh_stats['games_synced']} games, "
                    f"{refresh_stats['tips_generated']} tips"
                )

            # If all batches were processed, clear the continuation marker
            if batches_processed == len(batches):
                await cache.delete(CONTINUATION_KEY)
                logger.info("All seasons processed, continuation marker cleared")

            # Build summary
            overall_duration = time.time() - overall_start
            summary_parts = [
                f"Processed {total_seasons_processed} seasons across {batches_processed} batch(es)",
                f"Synced {total_games_synced} games",
                f"Generated {total_tips_generated} tips",
            ]

            if total_errors > 0:
                summary_parts.append(f"Failed: {total_errors} season(s)")

            summary = "; ".join(summary_parts)
            logger.info(f"{JOB_NAME} completed: {summary}")

            # 5. Mark success
            await execution_crud.update_execution(
                execution.id,
                status="completed",
                duration_seconds=int(overall_duration),
                items_processed=total_seasons_processed,
                items_failed=total_errors,
                result_summary=summary,
            )
            await session.commit()

            return {"statusCode": 200, "body": {"message": summary}}

        except Exception as e:
            had_error = True
            logger.error(f"{JOB_NAME} error: {e}\n{traceback.format_exc()}")
            if execution:
                try:
                    await execution_crud.update_execution(
                        execution.id,
                        status="failed",
                        error_message=str(e),
                    )
                    await session.commit()
                except Exception:
                    logger.error(f"Failed to update execution record: {traceback.format_exc()}")
            try:
                alerting = AlertingService()
                await alerting.send_failure_alert(
                    job_name=JOB_NAME,
                    error=str(e),
                    execution_id=str(execution.id) if execution else None,
                )
            except Exception:
                logger.error(f"Failed to send alert: {traceback.format_exc()}")
            return {"statusCode": 500, "body": {"error": str(e)}}

        finally:
            if locked:
                try:
                    await lock_crud.release_lock(JOB_NAME, LOCKED_BY)
                    await session.commit()
                except Exception:
                    logger.error(f"Failed to release lock: {traceback.format_exc()}")
            await close_redis_pool(force=had_error)
            await dispose_engine(force=had_error)
