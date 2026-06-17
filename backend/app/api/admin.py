"""FastAPI router for the admin endpoints.

A thin HTTP adapter over :mod:`packages.api.admin` that preserves URL
paths and response field names 1:1.

All admin endpoints require a valid ``X-API-Key`` header — the
``require_admin_key`` dependency is applied at the router level so it
cannot be bypassed by adding new routes.

Routes (mounted at ``/api/admin``):

* ``POST /{job_name}/trigger``         — for ``daily-sync``,
                                        ``match-completion``,
                                        ``tip-generation``,
                                        ``historic-refresh`` (422 on
                                        unknown name)
* ``GET  /historic-refresh/progress``  — current historic-refresh progress
* ``GET  /metrics``                    — per-job execution metrics
"""

from __future__ import annotations

import platform
from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from app.core.exceptions import http_error
from app.core.security import require_admin_key
from packages.shared.config import settings
from packages.shared.crud.jobs import JobExecutionCRUD
from packages.shared.models_ml.elo import EloModel
from packages.shared.schemas.admin import (
    DailySyncTriggerRequest,
    HistoricRefreshTriggerRequest,
    MatchCompletionTriggerRequest,
    TipGenerationTriggerRequest,
)
from packages.shared.services.game_sync import GameSyncService
from packages.shared.services.historic_data_refresh import (
    HistoricDataRefreshService,
)
from packages.shared.services.match_completion import (
    MatchCompletionDetectorService,
)
from packages.shared.services.tip_generation import TipGenerationService
from packages.shared.squiggle import SquiggleClient

# Allow-list of valid job names for the ``POST /{job_name}/trigger``
# endpoint.  Anything else returns 422.
ALLOWED_JOB_NAMES = {
    "daily-sync",
    "match-completion",
    "tip-generation",
    "historic-refresh",
}


# Apply require_admin_key at the router level so every endpoint
# (including any future ones) is automatically protected.
router = APIRouter(dependencies=[require_admin_key])


# ---------------------------------------------------------------------------
# POST /{job_name}/trigger
# ---------------------------------------------------------------------------


@router.post("/{job_name}/trigger")
async def trigger_job(
    job_name: Annotated[
        str,
        Path(description="Job name (one of the ALLOWED_JOB_NAMES)"),
    ],
    body: Annotated[Optional[dict], Body()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger one of the four background jobs.

    The body is optional and dispatched to a per-job Pydantic model
    based on the ``job_name`` path parameter.  FastAPI returns 422
    when the body fails the matching schema's validation.
    """
    if job_name not in ALLOWED_JOB_NAMES:
        raise http_error(
            422,
            "invalid_job_name",
            f"Invalid job_name. Must be one of: {', '.join(sorted(ALLOWED_JOB_NAMES))}",
        )

    body = body or {}

    if job_name == "daily-sync":
        parsed = DailySyncTriggerRequest.model_validate(body)
        return await _run_daily_sync(db, parsed)
    elif job_name == "match-completion":
        parsed = MatchCompletionTriggerRequest.model_validate(body)
        return await _run_match_completion(db, parsed)
    elif job_name == "tip-generation":
        parsed = TipGenerationTriggerRequest.model_validate(body)
        return await _run_tip_generation(db, parsed)
    elif job_name == "historic-refresh":
        parsed = HistoricRefreshTriggerRequest.model_validate(body)
        return await _run_historic_refresh(db, parsed)
    # Unreachable — job_name is validated above
    raise http_error(500, "internal_error", "unreachable")


# ---------------------------------------------------------------------------
# POST helpers (one per job)
# ---------------------------------------------------------------------------


async def _run_daily_sync(
    db: AsyncSession, body: DailySyncTriggerRequest
) -> dict:
    """Trigger the daily game-sync job."""
    season = body.season or settings.current_season

    squiggle_client = SquiggleClient()
    try:
        sync_service = GameSyncService(
            squiggle_client=squiggle_client,
            db_session=db,
            season=season,
        )
        sync_stats = await sync_service.sync_games()
        await EloModel.update_cache(db)

        return {
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
    finally:
        await squiggle_client.close()


async def _run_match_completion(
    db: AsyncSession, body: MatchCompletionTriggerRequest
) -> dict:
    """Trigger the match-completion detection job."""
    buffer_minutes = body.buffer_minutes or settings.match_completion_buffer_minutes

    squiggle_client = SquiggleClient()
    try:
        detector_service = MatchCompletionDetectorService(
            squiggle_client=squiggle_client,
            db_session=db,
            buffer_minutes=buffer_minutes,
        )
        completion_stats = (
            await detector_service.detect_and_process_completed_matches()
        )

        elo_cache_updated = False
        if completion_stats["games_completed"] > 0:
            try:
                await EloModel.update_cache(db)
                elo_cache_updated = True
            except Exception:
                elo_cache_updated = False

        return {
            "success": True,
            "message": (
                f"Checked {completion_stats['games_checked']} games, "
                f"marked {completion_stats['games_completed']} as complete"
            ),
            "games_checked": completion_stats.get("games_checked", 0),
            "games_completed": completion_stats.get("games_completed", 0),
            "games_already_completed": completion_stats.get(
                "games_already_completed", 0
            ),
            "games_not_ready": completion_stats.get("games_not_ready", 0),
            "games_failed": len(completion_stats.get("errors", [])),
            "duration_seconds": completion_stats.get("duration_seconds", 0.0),
            "elo_cache_updated": elo_cache_updated,
        }
    finally:
        await squiggle_client.close()


async def _run_tip_generation(
    db: AsyncSession, body: TipGenerationTriggerRequest
) -> dict:
    """Trigger the tip-generation job."""
    season = body.season
    round_id = body.round_id
    regenerate = body.regenerate

    generation_service = TipGenerationService(
        db_session=db,
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
        generation_stats = (
            await generation_service.generate_for_next_upcoming_round(
                regenerate=regenerate,
            )
        )

    return {
        "success": True,
        "message": generation_stats.get("message", "Tip generation completed"),
        "season": generation_stats.get("season"),
        "round_id": generation_stats.get("round_id"),
        "games_processed": generation_stats.get("games_processed", 0),
        "tips_created": generation_stats.get("tips_created", 0),
        "tips_skipped": generation_stats.get("tips_skipped", 0),
        "tips_updated": generation_stats.get("tips_updated", 0),
        "model_predictions_created": generation_stats.get(
            "model_predictions_created", 0
        ),
        "model_predictions_updated": generation_stats.get(
            "model_predictions_updated", 0
        ),
        "errors": generation_stats.get("errors", []),
        "duration_seconds": generation_stats.get("duration_seconds", 0.0),
    }


async def _run_historic_refresh(
    db: AsyncSession, body: HistoricRefreshTriggerRequest
) -> dict:
    """Trigger the historic-data-refresh job."""
    seasons_str = body.seasons or settings.historic_refresh_seasons
    round_id = body.round_id
    regenerate_tips = body.regenerate_tips

    refresh_service = HistoricDataRefreshService(
        db_session=db,
        seasons=None,
        round_id=round_id,
        regenerate_tips=regenerate_tips,
    )

    refresh_stats = await refresh_service.refresh_from_string(
        seasons_str=seasons_str,
        round_id=round_id,
        regenerate_tips=regenerate_tips,
    )

    return {
        "success": True,
        "message": (
            f"Successfully refreshed {refresh_stats['seasons_processed']} seasons"
        ),
        "seasons_processed": refresh_stats.get("seasons_processed", 0),
        "games_synced": refresh_stats.get("games_synced", 0),
        "tips_generated": refresh_stats.get("tips_generated", 0),
        "errors": refresh_stats.get("errors", []),
        "duration_seconds": refresh_stats.get("duration_seconds", 0.0),
        "season_stats": refresh_stats.get("season_stats", {}),
    }


# ---------------------------------------------------------------------------
# GET /historic-refresh/progress
# ---------------------------------------------------------------------------


@router.get("/historic-refresh/progress")
async def historic_refresh_progress(
    db: AsyncSession = Depends(get_db),
):
    """Return the current historic-refresh progress (R4 contract).

    Returns:

    * **200** with the in-flight row if a historic-refresh operation is
      currently running (``status == 'in_progress'``).
    * **200** with the most-recently-finished row (``status`` in
      ``completed`` / ``failed``) when no job is in flight.
    * **404** ``not_found`` when no historic-refresh row exists.

    The "in-flight wins" rule keeps clients polling a long-running job
    from being confused by stale completed rows in the table.
    """
    refresh_service = HistoricDataRefreshService(
        db_session=db,
        seasons=[],
        round_id=None,
        regenerate_tips=False,
    )
    progress = await refresh_service.get_progress()

    if not progress:
        raise http_error(
            404,
            "not_found",
            "No historic refresh operation found for this endpoint",
        )

    return {
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


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def metrics(
    db: AsyncSession = Depends(get_db),
):
    """Return per-job execution metrics + system info + alerting flag."""
    job_names = sorted(ALLOWED_JOB_NAMES)
    execution_crud = JobExecutionCRUD(db)

    metrics_payload: dict = {}
    for job_name in job_names:
        metrics_payload[job_name] = await execution_crud.get_job_metrics(
            job_name
        )

    system_info = {
        "python_version": platform.python_version(),
        "platform": platform.system(),
    }

    return {
        "metrics": metrics_payload,
        "system": system_info,
        "alerting_enabled": settings.alert_enabled,
    }
