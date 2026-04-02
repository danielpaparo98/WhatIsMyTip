"""Admin API endpoints for cron job management."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional
from pydantic import BaseModel

from app.db import get_db
from app.config import settings
from app.logger import get_logger


router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = get_logger(__name__)


class DailySyncTriggerRequest(BaseModel):
    """Request model for triggering daily sync."""
    season: Optional[int] = None


class DailySyncTriggerResponse(BaseModel):
    """Response model for daily sync trigger."""
    success: bool
    message: str
    season: int
    games_created: int = 0
    games_updated: int = 0
    games_skipped: int = 0
    games_failed: int = 0
    duration_seconds: float = 0.0


class MatchCompletionTriggerRequest(BaseModel):
    """Request model for triggering match completion check."""
    buffer_minutes: Optional[int] = None


@router.post("/daily-sync/trigger", response_model=DailySyncTriggerResponse)
@limiter.limit("10/minute")
async def trigger_daily_sync(
    request: Request,
    trigger_request: DailySyncTriggerRequest,
    db: AsyncSession = Depends(get_db)
):
    """Manually trigger the daily game sync job.
    
    This endpoint allows admins to manually trigger the daily game sync
    for a specific season or the current season.
    
    Args:
        request: FastAPI request object
        trigger_request: Request with optional season parameter
        
    Returns:
        Sync results with statistics
    """
    from app.squiggle import SquiggleClient
    from app.services.game_sync import GameSyncService
    from app.models_ml.elo import EloModel
    
    season = trigger_request.season or settings.current_season
    
    logger.info(f"Manual daily sync triggered for season {season}")
    
    try:
        # Create Squiggle client
        squiggle_client = SquiggleClient()
        
        try:
            # Create game sync service
            sync_service = GameSyncService(
                squiggle_client=squiggle_client,
                db_session=db,
                season=season
            )
            
            # Sync games
            sync_stats = await sync_service.sync_games()
            
            # Update Elo ratings cache
            await EloModel.update_cache(db)
            
            # Build response
            response = DailySyncTriggerResponse(
                success=True,
                message=f"Successfully synced {sync_stats['total_games']} games for season {season}",
                season=season,
                games_created=sync_stats["games_created"],
                games_updated=sync_stats["games_updated"],
                games_skipped=sync_stats["games_skipped"],
                games_failed=len(sync_stats.get("errors", [])),
                duration_seconds=sync_stats.get("duration_seconds", 0.0)
            )
            
            logger.info(f"Manual daily sync completed: {response.message}")
            
            return response
            
        finally:
            # Close Squiggle client
            await squiggle_client.close()
    
    except Exception as e:
        logger.error(f"Manual daily sync failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Daily sync failed: {str(e)}"
        )


class MatchCompletionTriggerResponse(BaseModel):
    """Response model for match completion trigger."""
    success: bool
    message: str
    games_checked: int = 0
    games_completed: int = 0
    games_already_completed: int = 0
    games_not_ready: int = 0
    games_failed: int = 0
    duration_seconds: float = 0.0
    elo_cache_updated: bool = False


@router.post("/match-completion/trigger", response_model=MatchCompletionTriggerResponse)
@limiter.limit("10/minute")
async def trigger_match_completion(
    request: Request,
    trigger_request: MatchCompletionTriggerRequest,
    db: AsyncSession = Depends(get_db)
):
    """Manually trigger match completion detection job.
    
    This endpoint allows admins to manually trigger match completion detection
    with an optional buffer_minutes override.
    
    Args:
        request: FastAPI request object
        trigger_request: Request with optional buffer_minutes parameter
        
    Returns:
        Completion detection results with statistics
    """
    from app.squiggle import SquiggleClient
    from app.services.match_completion import MatchCompletionDetectorService
    from app.models_ml.elo import EloModel
    
    buffer_minutes = trigger_request.buffer_minutes or settings.match_completion_buffer_minutes
    
    logger.info(f"Manual match completion detection triggered with {buffer_minutes} minute buffer")
    
    try:
        # Create Squiggle client
        squiggle_client = SquiggleClient()
        
        try:
            # Create match completion detector service
            detector_service = MatchCompletionDetectorService(
                squiggle_client=squiggle_client,
                db_session=db,
                buffer_minutes=buffer_minutes
            )
            
            # Detect and process completed matches
            completion_stats = await detector_service.detect_and_process_completed_matches()
            
            # Update Elo ratings cache if games were completed
            elo_cache_updated = False
            if completion_stats["games_completed"] > 0:
                try:
                    await EloModel.update_cache(db)
                    elo_cache_updated = True
                except Exception as elo_error:
                    logger.error(f"Failed to update Elo cache: {str(elo_error)}", exc_info=True)
            
            # Build response
            response = MatchCompletionTriggerResponse(
                success=True,
                message=f"Checked {completion_stats['games_checked']} games, "
                        f"marked {completion_stats['games_completed']} as complete",
                games_checked=completion_stats["games_checked"],
                games_completed=completion_stats["games_completed"],
                games_already_completed=completion_stats["games_already_completed"],
                games_not_ready=completion_stats["games_not_ready"],
                games_failed=len(completion_stats.get("errors", [])),
                duration_seconds=completion_stats.get("duration_seconds", 0.0),
                elo_cache_updated=elo_cache_updated
            )
            
            logger.info(f"Manual match completion detection completed: {response.message}")
            
            return response
            
        finally:
            # Close Squiggle client
            await squiggle_client.close()
    
    except Exception as e:
        logger.error(f"Manual match completion detection failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Match completion detection failed: {str(e)}"
        )


class TipGenerationTriggerRequest(BaseModel):
    """Request model for triggering tip generation."""
    season: Optional[int] = None
    round_id: Optional[int] = None
    regenerate: bool = False


class TipGenerationTriggerResponse(BaseModel):
    """Response model for tip generation trigger."""
    success: bool
    message: str
    season: Optional[int] = None
    round_id: Optional[int] = None
    games_processed: int = 0
    tips_created: int = 0
    tips_skipped: int = 0
    tips_updated: int = 0
    model_predictions_created: int = 0
    model_predictions_updated: int = 0
    errors: list = []
    duration_seconds: float = 0.0


@router.post("/tip-generation/trigger", response_model=TipGenerationTriggerResponse)
@limiter.limit("10/minute")
async def trigger_tip_generation(
    request: Request,
    trigger_request: TipGenerationTriggerRequest,
    db: AsyncSession = Depends(get_db)
):
    """Manually trigger tip generation job.
    
    This endpoint allows admins to manually trigger tip generation
    for a specific season/round or the next upcoming round.
    
    Args:
        request: FastAPI request object
        trigger_request: Request with optional season, round_id, and regenerate parameters
        
    Returns:
        Tip generation results with statistics
    """
    from app.services.tip_generation import TipGenerationService
    
    season = trigger_request.season
    round_id = trigger_request.round_id
    regenerate = trigger_request.regenerate
    
    logger.info(
        f"Manual tip generation triggered for "
        f"season={season}, round_id={round_id}, regenerate={regenerate}"
    )
    
    try:
        # Create tip generation service
        generation_service = TipGenerationService(
            db_session=db,
            season=season,
            round_id=round_id
        )
        
        # Generate tips
        if season and round_id:
            # Generate for specific round
            generation_stats = await generation_service.generate_for_round(
                season=season,
                round_id=round_id,
                regenerate=regenerate
            )
        else:
            # Generate for next upcoming round
            generation_stats = await generation_service.generate_for_next_upcoming_round(
                regenerate=regenerate
            )
        
        # Build response
        response = TipGenerationTriggerResponse(
            success=True,
            message=generation_stats.get("message", "Tip generation completed"),
            season=generation_stats.get("season"),
            round_id=generation_stats.get("round_id"),
            games_processed=generation_stats.get("games_processed", 0),
            tips_created=generation_stats.get("tips_created", 0),
            tips_skipped=generation_stats.get("tips_skipped", 0),
            tips_updated=generation_stats.get("tips_updated", 0),
            model_predictions_created=generation_stats.get("model_predictions_created", 0),
            model_predictions_updated=generation_stats.get("model_predictions_updated", 0),
            errors=generation_stats.get("errors", []),
            duration_seconds=generation_stats.get("duration_seconds", 0.0)
        )
        
        logger.info(f"Manual tip generation completed: {response.message}")
        
        return response
    
    except Exception as e:
        logger.error(f"Manual tip generation failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Tip generation failed: {str(e)}"
        )


class HistoricRefreshTriggerRequest(BaseModel):
    """Request model for triggering historic refresh."""
    seasons: Optional[str] = None
    round_id: Optional[int] = None
    regenerate_tips: bool = False


class HistoricRefreshTriggerResponse(BaseModel):
    """Response model for historic refresh trigger."""
    success: bool
    message: str
    seasons_processed: int = 0
    games_synced: int = 0
    tips_generated: int = 0
    errors: list = []
    duration_seconds: float = 0.0
    season_stats: dict = {}


@router.post("/historic-refresh/trigger", response_model=HistoricRefreshTriggerResponse)
@limiter.limit("10/minute")
async def trigger_historic_refresh(
    request: Request,
    trigger_request: HistoricRefreshTriggerRequest,
    db: AsyncSession = Depends(get_db)
):
    """Manually trigger the historic data refresh job.
    
    This endpoint allows admins to manually trigger historic data refresh
    for specific seasons or rounds.
    
    Args:
        request: FastAPI request object
        trigger_request: Request with optional seasons, round_id, and regenerate_tips parameters
        
    Returns:
        Historic refresh results with statistics
    """
    from app.services.historic_data_refresh import HistoricDataRefreshService
    
    seasons_str = trigger_request.seasons or settings.historic_refresh_seasons
    round_id = trigger_request.round_id
    regenerate_tips = trigger_request.regenerate_tips
    
    logger.info(
        f"Manual historic refresh triggered for "
        f"seasons={seasons_str}, round_id={round_id}, regenerate_tips={regenerate_tips}"
    )
    
    try:
        # Create historic data refresh service
        refresh_service = HistoricDataRefreshService(
            db_session=db,
            seasons=None,  # Will be parsed from string
            round_id=round_id,
            regenerate_tips=regenerate_tips
        )
        
        # Refresh from string
        refresh_stats = await refresh_service.refresh_from_string(
            seasons_str=seasons_str,
            round_id=round_id,
            regenerate_tips=regenerate_tips
        )
        
        # Build response
        response = HistoricRefreshTriggerResponse(
            success=True,
            message=f"Successfully refreshed {refresh_stats['seasons_processed']} seasons",
            seasons_processed=refresh_stats["seasons_processed"],
            games_synced=refresh_stats["games_synced"],
            tips_generated=refresh_stats["tips_generated"],
            errors=refresh_stats.get("errors", []),
            duration_seconds=refresh_stats.get("duration_seconds", 0.0),
            season_stats=refresh_stats.get("season_stats", {})
        )
        
        logger.info(f"Manual historic refresh completed: {response.message}")
        
        return response
    
    except Exception as e:
        logger.error(f"Manual historic refresh failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Historic refresh failed: {str(e)}"
        )


class HistoricRefreshProgressResponse(BaseModel):
    """Response model for historic refresh progress."""
    progress_id: Optional[int] = None
    operation_type: Optional[str] = None
    total_items: Optional[int] = None
    completed_items: Optional[int] = None
    status: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    progress_percentage: Optional[float] = None


@router.get("/historic-refresh/progress", response_model=HistoricRefreshProgressResponse)
@limiter.limit("30/minute")
async def get_historic_refresh_progress(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Get current progress of historic refresh operations.
    
    This endpoint returns the current progress of the most recent
    historic refresh operation.
    
    Args:
        request: FastAPI request object
        
    Returns:
        Historic refresh progress information
    """
    from app.services.historic_data_refresh import HistoricDataRefreshService
    
    logger.info("Fetching historic refresh progress")
    
    try:
        # Create a temporary service instance to get progress
        refresh_service = HistoricDataRefreshService(
            db_session=db,
            seasons=[],
            round_id=None,
            regenerate_tips=False
        )
        
        # Get progress
        progress = await refresh_service.get_progress()
        
        if progress:
            response = HistoricRefreshProgressResponse(**progress)
            logger.info(f"Historic refresh progress: {progress['status']}")
        else:
            response = HistoricRefreshProgressResponse(
                message="No active historic refresh operation found"
            )
            logger.info("No active historic refresh operation found")
        
        return response
    
    except Exception as e:
        logger.error(f"Failed to fetch historic refresh progress: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch progress: {str(e)}"
        )
