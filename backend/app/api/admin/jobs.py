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
