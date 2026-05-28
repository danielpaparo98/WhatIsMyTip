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

from packages.shared.db import _get_session_factory
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


async def main(args: dict) -> dict:
    """Scheduled function entry point.

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

            # 3. Resolve which batch to process
            seasons = _resolve_batch(args)
            seasons_str = ",".join(str(s) for s in seasons)

            logger.info(
                f"Starting historic data refresh for batch: {seasons_str}"
            )

            # 4. Execute job logic
            start_time = time.time()

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

            seasons_processed = refresh_stats["seasons_processed"]
            games_synced = refresh_stats["games_synced"]
            tips_generated = refresh_stats["tips_generated"]
            error_count = len(refresh_stats.get("errors", []))

            duration = time.time() - start_time

            # Build summary
            summary_parts = [
                f"Processed {seasons_processed}/{len(seasons)} seasons (batch: {seasons_str})",
                f"Synced {games_synced} games",
                f"Generated {tips_generated} tips",
            ]

            if error_count > 0:
                summary_parts.append(f"Failed: {error_count} season(s)")

            summary = "; ".join(summary_parts)
            logger.info(f"{JOB_NAME} completed: {summary}")

            # 5. Mark success
            await execution_crud.update_execution(
                execution.id,
                status="completed",
                duration_seconds=int(duration),
                items_processed=seasons_processed,
                items_failed=error_count,
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
