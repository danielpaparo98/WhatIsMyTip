"""Digital Ocean Function: Admin API.

Handles admin/cron-trigger HTTP requests routed through DO Functions
(Apache OpenWhisk) entry point.

All endpoints require X-API-Key header matching ADMIN_API_KEY env var.

Routes:
    POST /daily-sync/trigger           Trigger game sync
    POST /match-completion/trigger     Trigger match completion detection
    POST /tip-generation/trigger       Trigger tip generation
    POST /historic-refresh/trigger     Trigger historic data refresh
    GET  /historic-refresh/progress    Get refresh progress
    GET  /metrics                      Get job execution metrics
"""

import os
import platform
import sys
import traceback
from datetime import datetime, timezone

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from packages.shared.api_helpers import (
    check_rate_limit,
    check_request_size,
    parse_request,
    response,
    segments,
    validate_request,
    verify_api_key,
)
from packages.shared.cache import close_redis_pool
from packages.shared.config import settings
from packages.shared.crud.jobs import JobExecutionCRUD
from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.logger import get_logger
from packages.shared.models_ml.elo import EloModel
from packages.shared.schemas.admin import (
    DailySyncTriggerRequest,
    HistoricRefreshTriggerRequest,
    MatchCompletionTriggerRequest,
    TipGenerationTriggerRequest,
)
from packages.shared.services.game_sync import GameSyncService
from packages.shared.services.historic_data_refresh import HistoricDataRefreshService
from packages.shared.services.match_completion import MatchCompletionDetectorService
from packages.shared.services.tip_generation import TipGenerationService
from packages.shared.squiggle import SquiggleClient

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def _handle_daily_sync(session, body: dict) -> dict:
    """POST /daily-sync/trigger — trigger daily game sync."""
    validated, err = validate_request(body, DailySyncTriggerRequest)
    if err:
        return err

    season = validated.season or settings.current_season

    logger.info(f"Manual daily sync triggered for season {season}")

    try:
        squiggle_client = SquiggleClient()

        try:
            sync_service = GameSyncService(
                squiggle_client=squiggle_client,
                db_session=session,
                season=season,
            )

            sync_stats = await sync_service.sync_games()

            # Update Elo ratings cache
            await EloModel.update_cache(session)

            resp = {
                "success": True,
                "message": (
                    f"Successfully synced {sync_stats['total_games']} "
                    f"games for season {season}"
                ),
                "season": season,
                "games_created": sync_stats.get("games_created", 0),
                "games_updated": sync_stats.get("games_updated", 0),
                "games_skipped": sync_stats.get("games_skipped", 0),
                "games_failed": len(sync_stats.get("errors", [])),
                "duration_seconds": sync_stats.get("duration_seconds", 0.0),
            }

            logger.info(f"Manual daily sync completed: {resp['message']}")
            return response(200, data=resp)

        finally:
            await squiggle_client.close()

    except Exception as e:
        logger.error(f"Manual daily sync failed: {str(e)}", exc_info=True)
        return response(500, error="Internal server error. Please try again later.")


async def _handle_match_completion(session, body: dict) -> dict:
    """POST /match-completion/trigger — trigger match completion detection."""
    validated, err = validate_request(body, MatchCompletionTriggerRequest)
    if err:
        return err

    buffer_minutes = validated.buffer_minutes or settings.match_completion_buffer_minutes

    logger.info(f"Manual match completion detection triggered with {buffer_minutes} minute buffer")

    try:
        squiggle_client = SquiggleClient()

        try:
            detector_service = MatchCompletionDetectorService(
                squiggle_client=squiggle_client,
                db_session=session,
                buffer_minutes=buffer_minutes,
            )

            completion_stats = await detector_service.detect_and_process_completed_matches()

            # Update Elo ratings cache if games were completed
            elo_cache_updated = False
            if completion_stats["games_completed"] > 0:
                try:
                    await EloModel.update_cache(session)
                    elo_cache_updated = True
                except Exception as elo_error:
                    logger.error(f"Failed to update Elo cache: {str(elo_error)}", exc_info=True)

            resp = {
                "success": True,
                "message": (
                    f"Checked {completion_stats['games_checked']} games, "
                    f"marked {completion_stats['games_completed']} as complete"
                ),
                "games_checked": completion_stats.get("games_checked", 0),
                "games_completed": completion_stats.get("games_completed", 0),
                "games_already_completed": completion_stats.get("games_already_completed", 0),
                "games_not_ready": completion_stats.get("games_not_ready", 0),
                "games_failed": len(completion_stats.get("errors", [])),
                "duration_seconds": completion_stats.get("duration_seconds", 0.0),
                "elo_cache_updated": elo_cache_updated,
            }

            logger.info(f"Manual match completion detection completed: {resp['message']}")
            return response(200, data=resp)

        finally:
            await squiggle_client.close()

    except Exception as e:
        logger.error(f"Manual match completion detection failed: {str(e)}", exc_info=True)
        return response(500, error="Internal server error. Please try again later.")


async def _handle_tip_generation(session, body: dict) -> dict:
    """POST /tip-generation/trigger — trigger tip generation."""
    validated, err = validate_request(body, TipGenerationTriggerRequest)
    if err:
        return err

    season = validated.season
    round_id = validated.round_id
    regenerate = validated.regenerate

    logger.info(
        f"Manual tip generation triggered for "
        f"season={season}, round_id={round_id}, regenerate={regenerate}"
    )

    try:
        generation_service = TipGenerationService(
            db_session=session,
            season=season,
            round_id=round_id,
        )

        if season and round_id:
            generation_stats = await generation_service.generate_for_round(
                season=season,
                round_id=round_id,
                regenerate=regenerate,
            )
        else:
            generation_stats = await generation_service.generate_for_next_upcoming_round(
                regenerate=regenerate,
            )

        resp = {
            "success": True,
            "message": generation_stats.get("message", "Tip generation completed"),
            "season": generation_stats.get("season"),
            "round_id": generation_stats.get("round_id"),
            "games_processed": generation_stats.get("games_processed", 0),
            "tips_created": generation_stats.get("tips_created", 0),
            "tips_skipped": generation_stats.get("tips_skipped", 0),
            "tips_updated": generation_stats.get("tips_updated", 0),
            "model_predictions_created": generation_stats.get("model_predictions_created", 0),
            "model_predictions_updated": generation_stats.get("model_predictions_updated", 0),
            "errors": generation_stats.get("errors", []),
            "duration_seconds": generation_stats.get("duration_seconds", 0.0),
        }

        logger.info(f"Manual tip generation completed: {resp['message']}")
        return response(200, data=resp)

    except Exception as e:
        logger.error(f"Manual tip generation failed: {str(e)}", exc_info=True)
        return response(500, error="Internal server error. Please try again later.")


async def _handle_historic_refresh(session, body: dict) -> dict:
    """POST /historic-refresh/trigger — trigger historic data refresh."""
    validated, err = validate_request(body, HistoricRefreshTriggerRequest)
    if err:
        return err

    seasons_str = validated.seasons or settings.historic_refresh_seasons
    round_id = validated.round_id
    regenerate_tips = validated.regenerate_tips

    logger.info(
        f"Manual historic refresh triggered for "
        f"seasons={seasons_str}, round_id={round_id}, regenerate_tips={regenerate_tips}"
    )

    try:
        refresh_service = HistoricDataRefreshService(
            db_session=session,
            seasons=None,  # Will be parsed from string
            round_id=round_id,
            regenerate_tips=regenerate_tips,
        )

        refresh_stats = await refresh_service.refresh_from_string(
            seasons_str=seasons_str,
            round_id=round_id,
            regenerate_tips=regenerate_tips,
        )

        resp = {
            "success": True,
            "message": f"Successfully refreshed {refresh_stats['seasons_processed']} seasons",
            "seasons_processed": refresh_stats.get("seasons_processed", 0),
            "games_synced": refresh_stats.get("games_synced", 0),
            "tips_generated": refresh_stats.get("tips_generated", 0),
            "errors": refresh_stats.get("errors", []),
            "duration_seconds": refresh_stats.get("duration_seconds", 0.0),
            "season_stats": refresh_stats.get("season_stats", {}),
        }

        logger.info(f"Manual historic refresh completed: {resp['message']}")
        return response(200, data=resp)

    except Exception as e:
        logger.error(f"Manual historic refresh failed: {str(e)}", exc_info=True)
        return response(500, error="Internal server error. Please try again later.")


async def _handle_historic_refresh_progress(session) -> dict:
    """GET /historic-refresh/progress — get refresh progress."""
    logger.info("Fetching historic refresh progress")

    try:
        refresh_service = HistoricDataRefreshService(
            db_session=session,
            seasons=[],
            round_id=None,
            regenerate_tips=False,
        )

        progress = await refresh_service.get_progress()

        if progress:
            resp = {
                "progress_id": progress.get("progress_id"),
                "operation_type": progress.get("operation_type"),
                "total_items": progress.get("total_items"),
                "completed_items": progress.get("completed_items"),
                "status": progress.get("status"),
                "started_at": progress.get("started_at"),
                "completed_at": progress.get("completed_at"),
                "error_message": progress.get("error_message"),
                "progress_percentage": progress.get("progress_percentage"),
            }
            logger.info(f"Historic refresh progress: {progress.get('status')}")
        else:
            resp = {
                "progress_id": None,
                "operation_type": None,
                "total_items": None,
                "completed_items": None,
                "status": None,
                "started_at": None,
                "completed_at": None,
                "error_message": None,
                "progress_percentage": None,
                "message": "No active historic refresh operation found",
            }
            logger.info("No active historic refresh operation found")

        return response(200, data=resp)

    except Exception as e:
        logger.error(f"Failed to fetch historic refresh progress: {str(e)}", exc_info=True)
        return response(500, error="Internal server error. Please try again later.")


async def _handle_metrics(session) -> dict:
    """GET /metrics — return job execution metrics for observability."""
    job_names = [
        "daily-sync",
        "match-completion",
        "tip-generation",
        "historic-refresh",
    ]

    execution_crud = JobExecutionCRUD(session)
    metrics = {}
    for job_name in job_names:
        metrics[job_name] = await execution_crud.get_job_metrics(job_name)

    system_info = {
        "python_version": platform.python_version(),
        "platform": platform.system(),
    }

    return response(
        200,
        data={
            "metrics": metrics,
            "system": system_info,
            "alerting_enabled": settings.alert_enabled,
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_ADMIN_METHODS = ["GET", "POST", "OPTIONS"]


async def main(args: dict) -> dict:
    """DO Function entry point."""
    method, path, query, body, headers = parse_request(args)
    segs = segments(path)

    # Handle CORS preflight
    if method == "OPTIONS":
        return response(204, request_args=args, allowed_methods=_ADMIN_METHODS)

    # Security checks — request size then rate limit
    size_error = check_request_size(args)
    if size_error:
        return size_error

    rate_limit_response = await check_rate_limit(args)
    if rate_limit_response:
        return rate_limit_response

    # Health check — no auth required
    if method == "GET" and segs == ["health"]:
        return response(
            200,
            data={
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            request_args=args,
            allowed_methods=_ADMIN_METHODS,
        )

    # Authenticate all other admin endpoints
    if not verify_api_key(headers, query, body):
        return response(
            401,
            error="Invalid or missing API key",
            request_args=args,
            allowed_methods=_ADMIN_METHODS,
        )

    factory = _get_session_factory()
    async with factory() as session:
        had_error = False
        try:
            # ---- Routing ----

            # POST /daily-sync/trigger
            if method == "POST" and segs == ["daily-sync", "trigger"]:
                return await _handle_daily_sync(session, body)

            # POST /match-completion/trigger
            if method == "POST" and segs == ["match-completion", "trigger"]:
                return await _handle_match_completion(session, body)

            # POST /tip-generation/trigger
            if method == "POST" and segs == ["tip-generation", "trigger"]:
                return await _handle_tip_generation(session, body)

            # POST /historic-refresh/trigger
            if method == "POST" and segs == ["historic-refresh", "trigger"]:
                return await _handle_historic_refresh(session, body)

            # GET /historic-refresh/progress
            if method == "GET" and segs == ["historic-refresh", "progress"]:
                return await _handle_historic_refresh_progress(session)

            # GET /metrics
            if method == "GET" and segs == ["metrics"]:
                return await _handle_metrics(session)

            return response(
                404, error="Not found", request_args=args, allowed_methods=_ADMIN_METHODS
            )

        except Exception as e:
            had_error = True
            logger.error(f"Error in admin function: {e}\n{traceback.format_exc()}")
            return response(500, error=str(e), request_args=args, allowed_methods=_ADMIN_METHODS)
        finally:
            await close_redis_pool(force=had_error)
            await dispose_engine(force=had_error)
