"""Digital Ocean Scheduled Function: Tip Generation.

Triggered daily at 3 AM by the DO scheduler. Generates tips (with AI
explanations) for the next upcoming round that needs tips.
"""

import os
import sys
import time
import traceback

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from packages.shared.alerting import AlertingService
from packages.shared.cache import close_redis_pool
from packages.shared.config import settings
from packages.shared.crud.games import GameCRUD
from packages.shared.crud.jobs import JobExecutionCRUD, JobLockCRUD
from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.exceptions import TransientJobError, classify_error
from packages.shared.logger import generate_execution_id, get_logger
from packages.shared.services.explanation import ExplanationService
from packages.shared.services.tip_generation import TipGenerationService

logger = get_logger(__name__)

JOB_NAME = "tip-generation"
LOCKED_BY = "faas-tip-generation"


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
                expires_seconds=settings.tip_generation_timeout_seconds,
            )
            if not lock:
                logger.info(f"{JOB_NAME}: Could not acquire lock, skipping")
                return {"statusCode": 200, "body": {"message": "Job already running"}}

            locked = True

            # 2. Create execution record
            execution = await execution_crud.create_execution(JOB_NAME, status="running")
            await session.commit()

            # 3. Find next upcoming round that needs tips
            next_round = await GameCRUD.get_next_upcoming_round(session)

            if not next_round:
                msg = "No upcoming rounds found that need tips"
                logger.info(msg)
                await execution_crud.update_execution(
                    execution.id,
                    status="completed",
                    result_summary=msg,
                )
                await session.commit()
                return {"statusCode": 200, "body": {"message": msg}}

            season, round_id = next_round
            logger.info(f"Found next upcoming round: season {season}, round {round_id}")

            # 4. Execute job logic
            start_time = time.time()
            regenerate = settings.tip_generation_regenerate_existing

            generation_service = TipGenerationService(
                db_session=session,
                season=season,
                round_id=round_id,
            )

            logger.info(
                f"Generating tips for season {season}, round {round_id}, "
                f"regenerate={regenerate}"
            )

            generation_stats = await generation_service.generate_for_round(
                season=season,
                round_id=round_id,
                regenerate=regenerate,
            )

            games_processed = generation_stats["games_processed"]
            tips_created = generation_stats["tips_created"]
            tips_skipped = generation_stats["tips_skipped"]
            tips_updated = generation_stats.get("tips_updated", 0)
            model_predictions_created = generation_stats["model_predictions_created"]
            model_predictions_updated = generation_stats.get("model_predictions_updated", 0)
            error_count = len(generation_stats.get("errors", []))

            # Build summary
            summary_parts = [
                f"Generated tips for season {season}, round {round_id}",
                f"Processed {games_processed} games",
                f"Created {tips_created} tips",
                f"Skipped {tips_skipped} existing tips",
            ]

            if tips_updated > 0:
                summary_parts.append(f"Updated {tips_updated} tips")

            summary_parts.append(
                f"Created {model_predictions_created} model predictions"
            )

            if model_predictions_updated > 0:
                summary_parts.append(
                    f"Updated {model_predictions_updated} model predictions"
                )

            if error_count > 0:
                summary_parts.append(f"Failed: {error_count}")

            # Generate AI explanations for the round's tips
            try:
                explanation_service = ExplanationService()
                explanation_count = await explanation_service.generate_for_round(
                    session, season, round_id
                )
                if explanation_count > 0:
                    summary_parts.append(
                        f"Generated {explanation_count} AI explanations"
                    )
                    logger.info(
                        f"Generated {explanation_count} AI explanations for "
                        f"season {season}, round {round_id}"
                    )
                await explanation_service.close()
            except Exception as e:
                # Explanation failure should not fail the job
                logger.warning(
                    f"Explanation generation failed for season {season}, "
                    f"round {round_id}: {e}",
                    exc_info=True,
                )
                summary_parts.append("Explanation generation failed (tips still saved)")

            duration = time.time() - start_time
            summary = "; ".join(summary_parts)
            logger.info(f"{JOB_NAME} completed: {summary}", extra=log_extra)

            # 5. Mark success
            await execution_crud.update_execution(
                execution.id,
                status="completed",
                duration_seconds=int(duration),
                items_processed=games_processed,
                items_failed=error_count,
                result_summary=summary,
            )
            await session.commit()

            return {"statusCode": 200, "body": {"message": summary}}

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
