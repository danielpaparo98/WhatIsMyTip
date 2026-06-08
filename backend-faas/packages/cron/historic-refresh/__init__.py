"""Digital Ocean Scheduled Function: Historic Data Refresh.

Triggered Sunday at 4 AM by the DO scheduler. Refreshes historical game data
and tips for seasons 2010–2025 from the Squiggle API.

**Batch processing**: DO Functions have a 60-minute max timeout for scheduled
functions, but a full refresh takes ~2 hours. Seasons are therefore processed
in batches of 4 (e.g. 2010–2013, 2014–2017, 2018–2021, 2022–2025). Each batch
should complete within 30 minutes.

The ``start_season`` parameter (from ``args`` or the ``START_SEASON`` env var)
determines which batch to process. When omitted the function defaults to the
first batch (2010).
"""

import os
import sys
import time
import traceback
from datetime import datetime

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.cache import close_redis_pool
from packages.shared.config import settings
from packages.shared.logger import get_logger
from packages.shared.crud.jobs import JobExecutionCRUD, JobLockCRUD
from packages.shared.services.historic_data_refresh import HistoricDataRefreshService

logger = get_logger(__name__)

JOB_NAME = "historic-refresh"
LOCKED_BY = "faas-historic-refresh"

# All seasons covered by the historic refresh
ALL_SEASONS = list(range(2010, 2026))

# Number of seasons per batch (keeps each invocation under 30 min)
BATCH_SIZE = 4


def _build_batches() -> list[list[int]]:
    """Split ALL_SEASONS into batches of BATCH_SIZE."""
    return [
        ALL_SEASONS[i : i + BATCH_SIZE]
        for i in range(0, len(ALL_SEASONS), BATCH_SIZE)
    ]


def _resolve_batch(args: dict) -> list[int]:
    """Determine which batch to process.

    Priority:
        1. ``start_season`` key in *args* (e.g. from a self-triggered call)
        2. ``START_SEASON`` environment variable
        3. Default: first batch (2010–2013)

    Returns:
        List of season years for the selected batch.
    """
    start_season = args.get("start_season") or os.environ.get("START_SEASON")

    if start_season:
        try:
            start_year = int(start_season)
            # Find the batch that contains this year
            for batch in _build_batches():
                if start_year in batch:
                    logger.info(f"Selected batch containing start_season={start_year}: {batch}")
                    return batch
            # If start_year is outside the range, fall through to default
            logger.warning(
                f"start_season={start_year} not found in any batch, using default"
            )
        except (ValueError, TypeError):
            logger.warning(f"Invalid start_season value: {start_season}, using default")

    # Default to first batch
    batches = _build_batches()
    logger.info(f"Using default (first) batch: {batches[0]}")
    return batches[0]


# Maximum runtime before yielding to avoid exceeding DO Functions timeout.
# 50 minutes leaves a 10-minute buffer under the 60-minute scheduled limit.
MAX_RUNTIME_SECONDS = 3000


async def main(args: dict) -> dict:
    """Scheduled function entry point.

    Processes batches of seasons in a loop with a time check to stay
    under the DO Functions 60-minute timeout. If there are more batches
    to process after the current one, continues processing as long as
    there is sufficient time remaining.

    Args:
        args: Environment variables and trigger metadata from DO scheduler.
              May contain ``start_season`` to select a specific batch.

    Returns:
        dict with statusCode and body.
    """
    factory = _get_session_factory()
    async with factory() as session:
        execution = None
        lock_crud = JobLockCRUD(session)
        execution_crud = JobExecutionCRUD(session)
        locked = False

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

            # 3. Determine starting batch and build full batch list
            first_batch = _resolve_batch(args)
            batches = _build_batches()

            # Find the index of the first batch to process
            start_idx = 0
            for i, batch in enumerate(batches):
                if batch == first_batch:
                    start_idx = i
                    break

            # 4. Process batches with time check
            overall_start = time.time()
            total_seasons_processed = 0
            total_games_synced = 0
            total_tips_generated = 0
            total_errors = 0
            batches_processed = 0

            for batch_idx in range(start_idx, len(batches)):
                # Check if we have time for another batch
                elapsed = time.time() - overall_start
                if elapsed > MAX_RUNTIME_SECONDS:
                    logger.warning(
                        f"Approaching timeout after {elapsed:.0f}s, "
                        f"processed {batches_processed} batch(es). "
                        f"Remaining batches will need another invocation."
                    )
                    break

                seasons = batches[batch_idx]
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
            return {"statusCode": 500, "body": {"error": str(e)}}

        finally:
            if locked:
                try:
                    await lock_crud.release_lock(JOB_NAME, LOCKED_BY)
                    await session.commit()
                except Exception:
                    logger.error(f"Failed to release lock: {traceback.format_exc()}")
            await close_redis_pool()
            await dispose_engine()
