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
"""

import json
import os
import sys
import traceback
from urllib.parse import parse_qs

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.cache import close_redis_pool
from packages.shared.config import settings
from packages.shared.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_request(args: dict) -> tuple:
    """Parse DO Function args into (method, path, query, body, headers)."""
    method = args.get("__ow_method", "GET").upper()
    path = args.get("__ow_path", "/").strip("/")
    raw_query = args.get("__ow_query", "")
    if isinstance(raw_query, str) and raw_query:
        parsed = parse_qs(raw_query)
        query = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
    elif isinstance(raw_query, dict):
        query = raw_query
    else:
        query = {}
    body_raw = args.get("__ow_body", "")
    headers = args.get("__ow_headers", {}) or {}

    body: dict = {}
    if body_raw:
        if isinstance(body_raw, str):
            try:
                body = json.loads(body_raw)
            except json.JSONDecodeError:
                body = {}
        elif isinstance(body_raw, dict):
            body = body_raw

    return method, path, query, body, headers


def _response(status_code: int, data=None, error: str | None = None) -> dict:
    """Build a DO Function response dict."""
    body = {}
    if error:
        body = {"error": error}
    elif data is not None:
        body = data

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": settings.cors_origins[0] if settings.cors_origins else "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
        },
        "body": body,
    }


def _segments(path: str) -> list[str]:
    """Split path into non-empty segments."""
    return [s for s in path.split("/") if s]


def _verify_api_key(headers: dict) -> bool:
    """Check X-API-Key header against configured ADMIN_API_KEY."""
    api_key = headers.get("x-api-key") or headers.get("X-API-Key")
    if not api_key:
        return False
    if not settings.admin_api_key:
        return False
    return api_key == settings.admin_api_key


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _handle_daily_sync(session, body: dict) -> dict:
    """POST /daily-sync/trigger — trigger daily game sync."""
    from packages.shared.squiggle import SquiggleClient
    from packages.shared.services.game_sync import GameSyncService
    from packages.shared.models_ml.elo import EloModel

    season = body.get("season") or settings.current_season

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

            response = {
                "success": True,
                "message": f"Successfully synced {sync_stats['total_games']} games for season {season}",
                "season": season,
                "games_created": sync_stats.get("games_created", 0),
                "games_updated": sync_stats.get("games_updated", 0),
                "games_skipped": sync_stats.get("games_skipped", 0),
                "games_failed": len(sync_stats.get("errors", [])),
                "duration_seconds": sync_stats.get("duration_seconds", 0.0),
            }

            logger.info(f"Manual daily sync completed: {response['message']}")
            return _response(200, data=response)

        finally:
            await squiggle_client.close()

    except Exception as e:
        logger.error(f"Manual daily sync failed: {str(e)}", exc_info=True)
        return _response(500, error="Internal server error. Please try again later.")


async def _handle_match_completion(session, body: dict) -> dict:
    """POST /match-completion/trigger — trigger match completion detection."""
    from packages.shared.squiggle import SquiggleClient
    from packages.shared.services.match_completion import MatchCompletionDetectorService
    from packages.shared.models_ml.elo import EloModel

    buffer_minutes = body.get("buffer_minutes") or settings.match_completion_buffer_minutes

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

            response = {
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

            logger.info(f"Manual match completion detection completed: {response['message']}")
            return _response(200, data=response)

        finally:
            await squiggle_client.close()

    except Exception as e:
        logger.error(f"Manual match completion detection failed: {str(e)}", exc_info=True)
        return _response(500, error="Internal server error. Please try again later.")


async def _handle_tip_generation(session, body: dict) -> dict:
    """POST /tip-generation/trigger — trigger tip generation."""
    from packages.shared.services.tip_generation import TipGenerationService

    season = body.get("season")
    round_id = body.get("round_id")
    regenerate = body.get("regenerate", False)

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

        response = {
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

        logger.info(f"Manual tip generation completed: {response['message']}")
        return _response(200, data=response)

    except Exception as e:
        logger.error(f"Manual tip generation failed: {str(e)}", exc_info=True)
        return _response(500, error="Internal server error. Please try again later.")


async def _handle_historic_refresh(session, body: dict) -> dict:
    """POST /historic-refresh/trigger — trigger historic data refresh."""
    from packages.shared.services.historic_data_refresh import HistoricDataRefreshService

    seasons_str = body.get("seasons") or settings.historic_refresh_seasons
    round_id = body.get("round_id")
    regenerate_tips = body.get("regenerate_tips", False)

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

        response = {
            "success": True,
            "message": f"Successfully refreshed {refresh_stats['seasons_processed']} seasons",
            "seasons_processed": refresh_stats.get("seasons_processed", 0),
            "games_synced": refresh_stats.get("games_synced", 0),
            "tips_generated": refresh_stats.get("tips_generated", 0),
            "errors": refresh_stats.get("errors", []),
            "duration_seconds": refresh_stats.get("duration_seconds", 0.0),
            "season_stats": refresh_stats.get("season_stats", {}),
        }

        logger.info(f"Manual historic refresh completed: {response['message']}")
        return _response(200, data=response)

    except Exception as e:
        logger.error(f"Manual historic refresh failed: {str(e)}", exc_info=True)
        return _response(500, error="Internal server error. Please try again later.")


async def _handle_historic_refresh_progress(session) -> dict:
    """GET /historic-refresh/progress — get refresh progress."""
    from packages.shared.services.historic_data_refresh import HistoricDataRefreshService

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
            response = {
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
            response = {
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

        return _response(200, data=response)

    except Exception as e:
        logger.error(f"Failed to fetch historic refresh progress: {str(e)}", exc_info=True)
        return _response(500, error="Internal server error. Please try again later.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(args: dict) -> dict:
    """DO Function entry point."""
    method, path, query, body, headers = _parse_request(args)
    segs = _segments(path)

    # Handle CORS preflight
    if method == "OPTIONS":
        return _response(204)

    # Authenticate all admin endpoints
    if not _verify_api_key(headers):
        return _response(401, error="Invalid or missing API key")

    factory = _get_session_factory()
    async with factory() as session:
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

            return _response(404, error="Not found")

        except Exception as e:
            logger.error(f"Error in admin function: {e}\n{traceback.format_exc()}")
            return _response(500, error=str(e))
        finally:
            await close_redis_pool()
            await dispose_engine()
