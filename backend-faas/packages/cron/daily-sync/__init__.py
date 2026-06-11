"""Digital Ocean Scheduled Function: Daily Game Sync.

Triggered every 15 minutes by the DO scheduler. Syncs current-season games
from the Squiggle API and refreshes the Elo ratings cache.

During the AFL off-season (October–February) the job only executes inside a
2 AM – 4 AM window to reduce unnecessary API calls.
"""

import os
import sys
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.cache import close_redis_pool
from packages.shared.config import settings
from packages.shared.logger import get_logger, generate_execution_id
from packages.shared.crud.jobs import JobExecutionCRUD, JobLockCRUD
from packages.shared.squiggle import SquiggleClient
from packages.shared.services.game_sync import GameSyncService
from packages.shared.models_ml.elo import EloModel
from packages.shared.alerting import AlertingService
from packages.shared.exceptions import classify_error, TransientJobError

logger = get_logger(__name__)

JOB_NAME = "daily-sync"
LOCKED_BY = "faas-daily-sync"


async def main(args: dict) -> dict:
    """Scheduled function entry point.

    Args:
        args: Environment variables and trigger metadata from DO scheduler.

    Returns:
        dict with statusCode and body.
    """
    execution_id = generate_execution_id()
    log_extra = {"job_name": JOB_NAME, "execution_id": execution_id}

    logger.info(f"{JOB_NAME}: Starting execution", extra=log_extra)

    factory = _get_session_factory()
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
                expires_seconds=settings.daily_sync_timeout_seconds,
            )
            if not lock:
                logger.info(f"{JOB_NAME}: Could not acquire lock, skipping")
                return {"statusCode": 200, "body": {"message": "Job already running"}}

            locked = True

            # 2. Create execution record
            execution = await execution_crud.create_execution(JOB_NAME, status="running")
            await session.commit()

            # 3. Off-season reduced-frequency check
            tz = ZoneInfo(settings.cron_timezone)
            now_local = datetime.now(tz)
            current_month = now_local.month
            current_hour = now_local.hour

            if current_month in (10, 11, 12, 1, 2):
                if current_hour < 2 or current_hour >= 4:
                    msg = (
                        f"Skipping daily sync – off-season reduced frequency "
                        f"(month={current_month}, hour={current_hour})"
                    )
                    logger.info(msg)
                    await execution_crud.update_execution(
                        execution.id,
                        status="completed",
                        result_summary=msg,
                    )
                    await session.commit()
                    return {"statusCode": 200, "body": {"message": msg}}
                logger.info("Off-season: running once-daily sync in 2–4 AM window")

            # 4. Execute job logic
            start_time = time.time()
            season = settings.current_season

            squiggle_client = SquiggleClient()
            try:
                sync_service = GameSyncService(
                    squiggle_client=squiggle_client,
                    db_session=session,
                    season=season,
                )

                logger.info(f"Syncing games from Squiggle API for season {season}")
                sync_stats = await sync_service.sync_games()

                games_created = sync_stats["games_created"]
                games_updated = sync_stats["games_updated"]
                games_skipped = sync_stats["games_skipped"]
                total_games = sync_stats["total_games"]
                error_count = len(sync_stats.get("errors", []))

                if sync_stats.get("errors"):
                    logger.warning(
                        f"Game sync completed with {error_count} errors"
                    )

                # Update Elo ratings cache after successful sync
                logger.info("Updating Elo ratings cache")
                await EloModel.update_cache(session)

                duration = time.time() - start_time

                summary_parts = [
                    f"Synced {total_games} games for season {season}",
                    f"Created: {games_created}, Updated: {games_updated}, Skipped: {games_skipped}",
                    "Elo cache updated",
                ]
                if error_count > 0:
                    summary_parts.append(f"Failed: {error_count}")

                summary = "; ".join(summary_parts)
                logger.info(f"{JOB_NAME} completed: {summary}", extra=log_extra)

                # 5. Mark success
                await execution_crud.update_execution(
                    execution.id,
                    status="completed",
                    duration_seconds=int(duration),
                    items_processed=total_games,
                    items_failed=error_count,
                    result_summary=summary,
                )
                await session.commit()

                return {"statusCode": 200, "body": {"message": summary}}

            finally:
                await squiggle_client.close()

        except Exception as e:
            had_error = True
            classified = classify_error(e)
            if isinstance(classified, TransientJobError):
                logger.warning(
                    f"Transient error in {JOB_NAME}: {classified.message}",
                    extra={**log_extra, "error_type": "transient", "details": classified.details},
                )
            else:
                logger.error(
                    f"Permanent error in {JOB_NAME}: {classified.message}",
                    extra={**log_extra, "error_type": "permanent", "details": classified.details},
                )
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
                    duration_seconds=time.time() - start_time,
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
