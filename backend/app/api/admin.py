"""FastAPI router for the admin endpoints.

A thin HTTP adapter over :mod:`packages.api.admin` that preserves URL
paths and response field names 1:1.

All admin endpoints require a valid ``X-API-Key`` header â€” the
``require_admin_key`` dependency is applied at the router level so it
cannot be bypassed by adding new routes.

Routes (mounted at ``/api/admin``):

* ``POST /{job_name}/trigger``         â€” for ``daily-sync``,
                                        ``match-completion``,
                                        ``tip-generation``,
                                        ``historic-refresh`` (422 on
                                        unknown name)
* ``POST /match-report/regenerate``    â€” delete + regenerate the
                                        grand-final pre-match report
                                        for one game (``?slug=``)
* ``GET  /historic-refresh/progress``  â€” current historic-refresh progress
* ``GET  /metrics``                    â€” per-job execution metrics
"""

from __future__ import annotations

import asyncio
import logging
import platform
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from app.core.exceptions import http_error
from app.core.security import require_admin_key
from packages.shared.cache import short_cache
from packages.shared.config import settings
from packages.shared.crud import GameCRUD, MatchReportCRUD
from packages.shared.crud.jobs import JobExecutionCRUD
from packages.shared.models_ml.elo import EloModel
from packages.shared.schemas.admin import (
    DailySyncTriggerRequest,
    HistoricRefreshTriggerRequest,
    LeagueSyncTriggerRequest,
    MatchCompletionTriggerRequest,
    TipGenerationTriggerRequest,
)
from packages.shared.services.game_sync import GameSyncService
from packages.shared.services.historic_data_refresh import (
    HistoricDataRefreshService,
)
from packages.shared.services.local_competition_sync import (
    run_all_leagues_sync,
)
from packages.shared.services.match_completion import (
    MatchCompletionDetectorService,
)
from packages.shared.services.match_report import MatchReportService
from packages.shared.services.tip_generation import TipGenerationService
from packages.shared.squiggle import SquiggleClient

logger = logging.getLogger(__name__)

# Cache TTL for the per-job admin metrics.  30s is short enough that
# the dashboard feels fresh after a job run, long enough to absorb
# the dashboard's natural polling cadence.
_METRICS_CACHE_TTL_S = 30.0
_METRICS_CACHE_KEY_PREFIX = "admin_metrics:"

# Allow-list of valid job names for the ``POST /{job_name}/trigger``
# endpoint.  Anything else returns 422.
ALLOWED_JOB_NAMES = {
    "daily-sync",
    "match-completion",
    "tip-generation",
    "historic-refresh",
    "league-sync",
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
        # GF-OPS (2026-09-21): the refresh runs FAR longer than any
        # proxy permits (DO ingress killed a 10-minute run with a 524).
        # Fire it as a detached task on its OWN session (the
        # request-scoped session closes with this response) and let
        # operators poll GET /historic-refresh/progress â€” the endpoint
        # designed for exactly this.

        asyncio.create_task(_run_historic_refresh_detached(parsed))
        return {
            "success": True,
            "status": "triggered",
            "message": (
                "Historic refresh started in the background. Poll "
                "GET /api/admin/historic-refresh/progress until it "
                "reports completed/failed."
            ),
            "seasons": parsed.seasons or settings.historic_refresh_seasons,
            "round_id": parsed.round_id,
            "regenerate_tips": parsed.regenerate_tips,
        }
    elif job_name == "league-sync":
        parsed = LeagueSyncTriggerRequest.model_validate(body)
        return await _run_league_sync(db, parsed)
    # Unreachable â€” job_name is validated above
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


async def _run_league_sync(
    db: AsyncSession, body: LeagueSyncTriggerRequest
) -> dict:
    """Trigger the state-league sync (one season across live leagues)."""
    season = body.season or settings.current_season
    stats = await run_all_leagues_sync(db, season=season, leagues=body.leagues)
    return {
        "success": True,
        "message": (
            f"Synced {stats['fixtures_synced']} fixtures across "
            f"{len(stats['leagues_synced'])} leagues "
            f"({len(stats['leagues_failed'])} failed)"
        ),
        "season": stats["season"],
        "leagues_synced": stats["leagues_synced"],
        "leagues_failed": stats["leagues_failed"],
        "fixtures_synced": stats["fixtures_synced"],
        "errors": stats["errors"],
    }


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


# GF-OPS: single-flight guard â€” repeated triggers must NOT stack
# concurrent refresh jobs on the small instance (observed: three
# overlapping runs starved the worker until the API went dark).
_historic_refresh_lock = asyncio.Lock()


async def _run_historic_refresh_detached(
    body: HistoricRefreshTriggerRequest,
) -> None:
    """Run the historic-refresh job detached from any request.

    Opens its OWN DB session (the request's session is torn down when
    the trigger response returns) and survives the caller: the ingress
    may drop the HTTP connection, but the job keeps running and its
    progress stays pollable.
    """
    seasons_str = body.seasons or settings.historic_refresh_seasons
    round_id = body.round_id
    regenerate_tips = body.regenerate_tips

    if _historic_refresh_lock.locked():
        logging.getLogger(__name__).warning(
            "Historic refresh already running â€” skipping duplicate trigger"
        )
        return

    from packages.shared.db import get_session

    async with _historic_refresh_lock:
        try:
            async with get_session() as db:
                refresh_service = HistoricDataRefreshService(
                    db_session=db,
                    seasons=None,
                    round_id=round_id,
                    regenerate_tips=regenerate_tips,
                )
                stats = await refresh_service.refresh_from_string(
                    seasons_str=seasons_str,
                    round_id=round_id,
                    regenerate_tips=regenerate_tips,
                )
            logging.getLogger(__name__).info(
                "Detached historic refresh finished: %s seasons, %s games, %s errors",
                stats.get("seasons_processed", 0),
                stats.get("games_synced", 0),
                len(stats.get("errors", [])),
            )
        except Exception:  # noqa: BLE001 â€” detached: log, nothing to bubble to
            logging.getLogger(__name__).exception(
                "Detached historic refresh failed"
            )


async def _run_historic_refresh(
    db: AsyncSession, body: HistoricRefreshTriggerRequest
) -> dict:
    """Trigger the historic-data-refresh job (INLINE â€” retained for
    direct/service use; the admin endpoint now uses the detached runner
    so long runs survive proxy timeouts)."""
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
# POST /historic-refresh/reset-progress  (OPS: stale-progress unblock)
# ---------------------------------------------------------------------------


@router.post("/historic-refresh/reset-progress")
async def historic_refresh_reset_progress(
    db: AsyncSession = Depends(get_db),
):
    """Mark stale ``in_progress`` historic-refresh rows as failed.

    OPS unblock (2026-09-20): a client disconnect mid-run (or a crashed
    run) leaves a ``generation_progress`` row stuck at ``in_progress``,
    and the next trigger aborts with a UniqueViolation on the
    in-progress constraint â€” every subsequent run becomes impossible
    until the stale row is cleared.  This endpoint marks those rows
    ``failed`` (with an explanatory error message) so the next trigger
    starts clean.
    """
    from packages.shared.crud.generation_progress import GenerationProgressCRUD

    stale = await GenerationProgressCRUD.get_in_progress_operations(
        db, operation_type="historic_refresh"
    )
    reset_ids: list[int] = []
    for row in stale:
        await GenerationProgressCRUD.mark_failed(
            db,
            progress_id=row.id,
            error_message="Marked failed by admin reset-progress (stale in_progress row)",
        )
        reset_ids.append(row.id)

    if not reset_ids:
        return {"status": "nothing_to_reset", "reset": []}

    logging.getLogger(__name__).warning(
        "Reset stale historic-refresh progress rows: %s", reset_ids
    )
    return {"status": "reset", "reset": reset_ids}


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def metrics(
    db: AsyncSession = Depends(get_db),
):
    """Return per-job execution metrics + system info + alerting flag.

    Each per-job metrics dict is cached in Redis for 30s under
    ``admin_metrics:<job_name>``.  Without the cache, this endpoint
    issues 7 SQL queries per job per request (28 queries total for
    the 4 known jobs), which does not scale.  With the cache, a
    repeat request within the TTL window only pays the cost of the
    cache hit + the system-info dict.

    A new composite index ``ix_job_executions_job_name_started_at``
    on ``job_executions`` backs the last-run / last-success /
    last-failure ORDER BY ... LIMIT 1 lookups, so even cache-miss
    requests stay fast once the table grows past a few thousand rows.
    """
    job_names = sorted(ALLOWED_JOB_NAMES)
    execution_crud = JobExecutionCRUD(db)

    metrics_payload: dict[str, dict[str, Any]] = {}
    for job_name in job_names:
        cache_key = f"{_METRICS_CACHE_KEY_PREFIX}{job_name}"
        cached_value: Optional[dict[str, Any]] = None

        # Try the cache first.  ``short_cache.get`` returns ``None``
        # for both a miss and a disabled Redis (see HI-005), so we
        # treat both as "fall through to the CRUD".
        try:
            cached_value = await short_cache.get(cache_key)
        except Exception as exc:  # defensive: never let the cache break us
            logger.warning(
                "admin metrics cache read failed for %s: %s", job_name, exc
            )
            cached_value = None

        if cached_value is not None:
            metrics_payload[job_name] = cached_value
            continue

        fresh = await execution_crud.get_job_metrics(job_name)
        metrics_payload[job_name] = fresh

        # Best-effort write.  Failures are logged but never bubble up
        # â€” the response is still correct.
        try:
            await short_cache.set(
                cache_key, fresh, ttl=_METRICS_CACHE_TTL_S
            )
        except Exception as exc:
            logger.warning(
                "admin metrics cache write failed for %s: %s", job_name, exc
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


# ---------------------------------------------------------------------------
# POST /match-report/regenerate
# ---------------------------------------------------------------------------


@router.post("/match-report/regenerate")
async def regenerate_match_report(
    slug: Annotated[
        str,
        Query(min_length=1, max_length=12, description="Slug of the grand-final game"),
    ],
    db: AsyncSession = Depends(get_db),
):
    """Delete and regenerate the stored grand-final pre-match report.

    Any existing report row is deleted first so the generation below
    cannot hit the service's skip-if-exists path.  The service still
    enforces its own gates (grand final only, pre-match only, teams
    known, OpenRouter key configured), so a non-GF slug returns
    ``{"status": "skipped", ...}`` rather than an error.
    """
    game = await GameCRUD.get_by_slug(db, slug)
    if not game:
        raise http_error(404, "not_found", "Game not found")

    await MatchReportCRUD.delete_for_game(db, game.id)

    service = MatchReportService()
    try:
        report = await service.generate_and_store_report(db, game)
    finally:
        await service.close()

    if report is None:
        reason = (
            "Report not generated: the game may not be an upcoming "
            "grand final with known teams, the OpenRouter key may be "
            "missing, or generation failed"
        )
        # OPS: surface the agent's own failure reason so a skipped
        # regeneration is diagnosable without container logs.
        if getattr(service, "last_error", None):
            reason += f" â€” last error: {service.last_error}"
        return {
            "status": "skipped",
            "reason": reason,
        }
    return {"status": "generated"}


# ---------------------------------------------------------------------------
# POST /games/{slug}/void-fixture  (DUP-GUARD operator tool)
# ---------------------------------------------------------------------------


@router.post("/games/{slug}/void-fixture")
async def void_fixture(
    # LO-005: slug column is VARCHAR(12); reject over-long at routing.
    slug: Annotated[str, Path(min_length=1, max_length=12)],
    db: AsyncSession = Depends(get_db),
):
    """Soft-void a duplicated fixture row (TBC-style).

    Squiggle re-publishing a fixture under a new id used to leave two
    identical rows for one game (see the DUP-GUARD in
    ``GameCRUD.create_or_update_with_tracking``).  The orphan row is
    invisible to every consumer once its teams are NULL: the round
    locator's game_count filters blank-team rows out, tips grids and
    TBC placeholders already exclude them, and the sync will never
    feed the orphan a final score anyway.

    Team columns are set to NULL rather than deleting the row â€” tips,
    predictions and analyses reference it and there is no cascade.
    Refuses (409) for completed games: history is never rewritten.
    """
    from datetime import datetime, timezone

    from packages.shared.crud.games import _invalidate_game_cache

    game = await GameCRUD.get_by_slug(db, slug)
    if not game:
        raise http_error(404, "not_found", "Game not found")

    if game.completed:
        raise http_error(
            409,
            "game_completed",
            "Refusing to void a completed game â€” scores are historical fact",
        )

    game.home_team = None
    game.away_team = None
    game.last_synced_at = datetime.now(timezone.utc)
    game.sync_version = (game.sync_version or 0) + 1
    await db.commit()
    await db.refresh(game)

    try:
        await _invalidate_game_cache(game)
    except Exception:  # noqa: BLE001 â€” cache cleanup is best-effort
        logging.getLogger(__name__).exception(
            "Cache invalidation after voiding %s failed (non-fatal)", slug
        )

    logging.getLogger(__name__).warning(
        "Voided duplicate fixture row %s (game_id=%s, squiggle_id=%s)",
        slug,
        game.id,
        game.squiggle_id,
    )
    return {
        "status": "voided",
        "slug": slug,
        "game_id": game.id,
        "squiggle_id": game.squiggle_id,
    }
