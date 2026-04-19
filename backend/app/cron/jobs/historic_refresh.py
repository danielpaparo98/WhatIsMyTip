"""Historical data refresh cron job."""

from typing import Dict, Any, List, Optional
from datetime import datetime

from app.cron.base import BaseJob, classify_error
from app.services.historic_data_refresh import HistoricDataRefreshService
from app.logger import get_logger


logger = get_logger(__name__)


class HistoricDataRefreshJob(BaseJob):
    """Cron job for refreshing historical data from Squiggle API.
    
    This job:
    - Syncs games for historical seasons (2010-2025) from Squiggle API
    - Generates tips for games that don't have tips
    - Skips data that already exists to avoid duplication
    - Tracks sync statistics
    - Handles errors and retry logic
    """
    
    def __init__(
        self,
        job_name: str,
        db_session,
        settings,
        instance_id: Optional[str] = None,
        seasons: Optional[List[int]] = None,
        round_id: Optional[int] = None,
        regenerate_tips: bool = False
    ):
        """Initialize the HistoricDataRefreshJob.
        
        Args:
            job_name: Name of the job
            db_session: Database session
            settings: Application settings
            instance_id: Optional instance identifier
            seasons: Optional list of seasons to refresh (defaults to 2010-2025)
            round_id: Optional round number to refresh (for partial refresh)
            regenerate_tips: Whether to regenerate existing tips (default: False)
        """
        super().__init__(
            job_name=job_name,
            db_session=db_session,
            settings=settings,
            instance_id=instance_id
        )
        
        # Determine seasons to refresh
        if seasons:
            self.seasons = seasons
        else:
            # Use settings or default range
            start_year = getattr(settings, 'historic_refresh_start_year', 2010)
            end_year = getattr(settings, 'current_season', 2025)
            self.seasons = list(range(start_year, end_year + 1))
        
        self.round_id = round_id
        self.regenerate_tips = regenerate_tips
        self.logger = logger
    
    async def execute(self) -> Dict[str, Any]:
        """Execute the historic data refresh job.
        
        Returns:
            Dictionary with execution results including:
            - items_processed: Number of seasons processed
            - items_failed: Number of seasons that failed
            - summary: Text summary of execution
        """
        self.logger.info(
            f"Starting HistoricDataRefreshJob for {len(self.seasons)} seasons: {self.seasons}"
        )
        
        result = {
            "items_processed": 0,
            "items_failed": 0,
            "summary": "",
            "seasons": self.seasons,
            "games_synced": 0,
            "tips_generated": 0,
            "season_stats": {}
        }
        
        try:
            # Create historic data refresh service
            refresh_service = HistoricDataRefreshService(
                db_session=self.db_session,
                seasons=self.seasons,
                round_id=self.round_id,
                regenerate_tips=self.regenerate_tips,
                job_execution_id=None  # Will be set by base job's run() method
            )
            
            # Refresh historical data
            self.logger.info("Refreshing historical data from Squiggle API")
            refresh_stats = await refresh_service.refresh()
            
            result["games_synced"] = refresh_stats["games_synced"]
            result["tips_generated"] = refresh_stats["tips_generated"]
            result["items_processed"] = refresh_stats["seasons_processed"]
            result["items_failed"] = len(refresh_stats.get("errors", []))
            result["season_stats"] = refresh_stats.get("season_stats", {})
            
            # Build summary
            summary_parts = [
                f"Processed {result['items_processed']}/{len(self.seasons)} seasons",
                f"Synced {result['games_synced']} games",
                f"Generated {result['tips_generated']} tips"
            ]
            
            if self.round_id:
                summary_parts.append(f"Round {self.round_id} only")
            
            if result["items_failed"] > 0:
                summary_parts.append(f"Failed: {result['items_failed']} season(s)")
            
            result["summary"] = "; ".join(summary_parts)
            
            self.logger.info(f"HistoricDataRefreshJob completed: {result['summary']}")
        
        except Exception as e:
            error_msg = f"HistoricDataRefreshJob failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            
            result["items_failed"] = len(self.seasons)
            result["summary"] = f"Failed: {error_msg}"
            
            raise classify_error(e, "HistoricDataRefreshJob failed")
        
        return result
    
    async def execute_from_string(
        self,
        seasons_str: str,
        round_id: Optional[int] = None,
        regenerate_tips: bool = False
    ) -> Dict[str, Any]:
        """Execute the historic data refresh job from a seasons string.
        
        Args:
            seasons_str: Seasons string (e.g., "2010-2025", "2010,2011,2012")
            round_id: Optional round number to refresh
            regenerate_tips: Whether to regenerate existing tips
            
        Returns:
            Dictionary with execution results
        """
        self.logger.info(f"Executing HistoricDataRefreshJob with seasons string: {seasons_str}")
        
        try:
            # Create historic data refresh service
            refresh_service = HistoricDataRefreshService(
                db_session=self.db_session,
                seasons=None,  # Will be parsed from string
                round_id=round_id,
                regenerate_tips=regenerate_tips,
                job_execution_id=None
            )
            
            # Refresh from string
            refresh_stats = await refresh_service.refresh_from_string(
                seasons_str=seasons_str,
                round_id=round_id,
                regenerate_tips=regenerate_tips
            )
            
            result = {
                "items_processed": refresh_stats["seasons_processed"],
                "items_failed": len(refresh_stats.get("errors", [])),
                "summary": "",
                "games_synced": refresh_stats["games_synced"],
                "tips_generated": refresh_stats["tips_generated"],
                "season_stats": refresh_stats.get("season_stats", {})
            }
            
            # Build summary
            summary_parts = [
                f"Processed {result['items_processed']} seasons",
                f"Synced {result['games_synced']} games",
                f"Generated {result['tips_generated']} tips"
            ]
            
            if round_id:
                summary_parts.append(f"Round {round_id} only")
            
            if result["items_failed"] > 0:
                summary_parts.append(f"Failed: {result['items_failed']} season(s)")
            
            result["summary"] = "; ".join(summary_parts)
            
            self.logger.info(f"HistoricDataRefreshJob completed: {result['summary']}")
            
            return result
        
        except Exception as e:
            error_msg = f"HistoricDataRefreshJob failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            
            raise classify_error(e, "HistoricDataRefreshJob failed")
