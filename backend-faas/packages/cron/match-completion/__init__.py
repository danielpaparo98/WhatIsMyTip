"""Digital Ocean Scheduled Function: Match Completion Detection.

Triggered every 15 minutes (offset by 5) by the DO scheduler. Detects
recently completed matches, updates final scores, and refreshes the Elo
ratings cache when games are newly completed.
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
from packages.shared.squiggle import SquiggleClient
from packages.shared.services.match_completion import MatchCompletionDetectorService
from packages.shared.models_ml.elo import EloModel

logger = get_logger(__name__)

JOB_NAME = "match-completion"
LOCKED_BY = "faas-match-completion"


async def main(args: dict) -> dict:
    """Scheduled function entry point.

    Args:
        args: Environment variables and trigger metadata from DO scheduler.

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
                expires_seconds=settings.completion_check_timeout_seconds,
            )
            if not lock:
                logger.info(f"{JOB_NAME}: Could not acquire lock, skipping")
                return {"statusCode": 200, "body": {"message": "Job already running"}}

            locked = True

            # 2. Create execution record
            execution = await execution_crud.create_execution(JOB_NAME, status="running")
            await session.commit()

            # 3. Execute job logic
            start_time = time.time()
            buffer_minutes = settings.match_completion_buffer_minutes

            squiggle_client = SquiggleClient()
            try:
                detector_service = MatchCompletionDetectorService(
                    squiggle_client=squiggle_client,
                    db_session=session,
                    buffer_minutes=buffer_minutes,
                )

                logger.info(
                    f"Detecting completed matches with {buffer_minutes} minute buffer"
                )
                completion_stats = await detector_service.detect_and_process_completed_matches()

                games_checked = completion_stats["games_checked"]
                games_completed = completion_stats["games_completed"]
                games_already_completed = completion_stats["games_already_completed"]
                games_not_ready = completion_stats["games_not_ready"]
                error_count = len(completion_stats.get("errors", []))

                elo_cache_updated = False

                # Update Elo ratings cache if games were completed
                if games_completed > 0:
                    logger.info(
                        f"Updating Elo ratings cache after {games_completed} completed games"
                    )
                    try:
                        await EloModel.update_cache(session)
                        elo_cache_updated = True
                        logger.info("Elo ratings cache updated successfully")
                    except Exception as elo_error:
                        logger.error(
                            f"Failed to update Elo cache: {elo_error}",
                            exc_info=True,
                        )
                        # Don't fail the job if Elo cache update fails

                duration = time.time() - start_time

                # Build summary
                summary_parts = [
                    f"Checked {games_checked} games for completion",
                    f"Marked {games_completed} games as complete",
                    f"{games_not_ready} games not ready",
                    f"{games_already_completed} already complete",
                ]

                if elo_cache_updated:
                    summary_parts.append("Elo cache updated")

                if error_count > 0:
                    summary_parts.append(f"Failed: {error_count}")

                summary = "; ".join(summary_parts)
                logger.info(f"{JOB_NAME} completed: {summary}")

                # 4. Mark success
                await execution_crud.update_execution(
                    execution.id,
                    status="completed",
                    duration_seconds=int(duration),
                    items_processed=games_checked,
                    items_failed=error_count,
                    result_summary=summary,
                )
                await session.commit()

                return {"statusCode": 200, "body": {"message": summary}}

            finally:
                await squiggle_client.close()

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
