"""Daily game sync cron job."""

from typing import Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo

from app.cron.base import BaseJob, classify_error
from app.squiggle import SquiggleClient
from app.services.game_sync import GameSyncService
from app.models_ml.elo import EloModel
from app.logger import get_logger


logger = get_logger(__name__)


class DailyGameSyncJob(BaseJob):
    """Cron job for daily game sync from Squiggle API.
    
    This job:
    - Syncs games for the current season from Squiggle API
    - Updates Elo ratings cache after successful sync
    - Tracks sync statistics
    - Handles errors and retry logic
    """
    
    def __init__(
        self,
        job_name: str,
        db_session,
        settings,
        instance_id: str = None,
        season: int = None
    ):
        """Initialize the DailyGameSyncJob.
        
        Args:
            job_name: Name of the job
            db_session: Database session
            settings: Application settings
            instance_id: Optional instance identifier
            season: Optional season to sync (defaults to current year)
        """
        super().__init__(
            job_name=job_name,
            db_session=db_session,
            settings=settings,
            instance_id=instance_id
        )
        self.season = season or datetime.now().year
        self.logger = logger
    
    async def execute(self) -> Dict[str, Any]:
        """Execute the daily game sync job.
        
        Returns:
            Dictionary with execution results including:
            - items_processed: Number of games synced
            - items_failed: Number of games that failed
            - summary: Text summary of execution
        """
        self.logger.info(f"Starting DailyGameSyncJob for season {self.season}")
        
        result = {
            "items_processed": 0,
            "items_failed": 0,
            "summary": "",
            "season": self.season,
            "games_created": 0,
            "games_updated": 0,
            "games_skipped": 0
        }
        
        # Off-season reduced frequency: Oct-Feb only sync once per day (2-4 AM)
        try:
            tz = ZoneInfo(self.settings.cron_timezone)
            now_local = datetime.now(tz)
            current_month = now_local.month
            current_hour = now_local.hour
            
            if current_month in (10, 11, 12, 1, 2):
                if current_hour < 2 or current_hour >= 4:
                    self.logger.info(
                        f"Skipping daily sync - off-season reduced frequency "
                        f"(month={current_month}, hour={current_hour})"
                    )
                    result["summary"] = "Skipped daily sync - off-season reduced frequency"
                    return result
                self.logger.info("Off-season: running once-daily sync in 2-4 AM window")
        except Exception as e:
            self.logger.warning(f"Could not determine off-season status, proceeding with sync: {e}")
        
        try:
            # Create Squiggle client
            squiggle_client = SquiggleClient()
            
            try:
                # Create game sync service
                sync_service = GameSyncService(
                    squiggle_client=squiggle_client,
                    db_session=self.db_session,
                    season=self.season
                )
                
                # Sync games
                self.logger.info("Syncing games from Squiggle API")
                sync_stats = await sync_service.sync_games()
                
                result["games_created"] = sync_stats["games_created"]
                result["games_updated"] = sync_stats["games_updated"]
                result["games_skipped"] = sync_stats["games_skipped"]
                result["items_processed"] = sync_stats["total_games"]
                result["items_failed"] = len(sync_stats.get("errors", []))
                
                if sync_stats.get("errors"):
                    error_msg = f"Game sync completed with {len(sync_stats['errors'])} errors"
                    self.logger.warning(error_msg)
                    # Don't raise error for partial failures
                    # Continue with Elo cache update
                
                # Update Elo ratings cache after successful sync
                self.logger.info("Updating Elo ratings cache")
                await EloModel.update_cache(self.db_session)
                
                # Build summary
                summary_parts = [
                    f"Synced {result['items_processed']} games for season {self.season}",
                    f"Created: {result['games_created']}, Updated: {result['games_updated']}, Skipped: {result['games_skipped']}",
                    f"Elo cache updated"
                ]
                
                if result["items_failed"] > 0:
                    summary_parts.append(f"Failed: {result['items_failed']}")
                
                result["summary"] = "; ".join(summary_parts)
                
                self.logger.info(f"DailyGameSyncJob completed: {result['summary']}")
                
            finally:
                # Close Squiggle client
                await squiggle_client.close()
        
        except Exception as e:
            error_msg = f"DailyGameSyncJob failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            
            result["items_failed"] = result["items_processed"]
            result["summary"] = f"Failed: {error_msg}"
            
            raise classify_error(e, "DailyGameSyncJob failed")
        
        return result
