"""Service for refreshing historical data from Squiggle API."""

from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import time

from app.squiggle import SquiggleClient
from app.services.game_sync import GameSyncService
from app.services.tip_generation import TipGenerationService
from app.crud.generation_progress import GenerationProgressCRUD
from app.crud.games import GameCRUD
from app.logger import get_logger


logger = get_logger(__name__)


class HistoricDataRefreshService:
    """Service for refreshing historical data from Squiggle API.
    
    This service handles:
    - Syncing games from Squiggle API for specified seasons
    - Generating tips for games that don't have tips
    - Tracking progress via GenerationProgress table
    - Supporting resumable operations (continue from where left off)
    - Handling errors gracefully and continuing with next season/round
    """
    
    def __init__(
        self,
        db_session: AsyncSession,
        seasons: Optional[List[int]] = None,
        round_id: Optional[int] = None,
        regenerate_tips: bool = False,
        job_execution_id: Optional[int] = None
    ):
        """Initialize the HistoricDataRefreshService.
        
        Args:
            db_session: Database session
            seasons: List of season years to refresh (defaults to 2010-2025)
            round_id: Optional round number to refresh (for partial refresh)
            regenerate_tips: Whether to regenerate existing tips (default: False)
            job_execution_id: Optional job execution ID for tracking
        """
        self.db = db_session
        self.seasons = seasons or list(range(2010, 2026))
        self.round_id = round_id
        self.regenerate_tips = regenerate_tips
        self.job_execution_id = job_execution_id
        self.logger = logger
        self.progress_id = None
    
    def _parse_seasons(self, seasons_str: str) -> List[int]:
        """Parse seasons string to list of years.
        
        Supports:
        - Comma-separated: "2010,2011,2012"
        - Range: "2010-2025"
        - Single year: "2020"
        
        Args:
            seasons_str: Seasons string to parse
            
        Returns:
            List of season years
        """
        seasons = []
        
        # Handle range format (e.g., "2010-2025")
        if '-' in seasons_str:
            parts = seasons_str.split('-')
            if len(parts) == 2:
                try:
                    start = int(parts[0].strip())
                    end = int(parts[1].strip())
                    seasons = list(range(start, end + 1))
                    return seasons
                except ValueError:
                    pass
        
        # Handle comma-separated format
        for part in seasons_str.split(','):
            part = part.strip()
            if part:
                try:
                    seasons.append(int(part))
                except ValueError:
                    self.logger.warning(f"Invalid season value: {part}")
        
        return seasons
    
    async def refresh(self) -> Dict[str, Any]:
        """Refresh historical data for configured seasons.
        
        Returns:
            Dictionary with refresh statistics:
            - seasons_processed: Number of seasons processed
            - games_synced: Number of games synced
            - tips_generated: Number of tips generated
            - errors: List of error messages
            - season_stats: Per-season statistics
            - duration_seconds: Time taken to refresh
        """
        start_time = time.time()
        self.logger.info(
            f"Starting historic data refresh for {len(self.seasons)} seasons: {self.seasons}"
        )
        
        stats = {
            "seasons_processed": 0,
            "games_synced": 0,
            "tips_generated": 0,
            "errors": [],
            "season_stats": {}
        }
        
        # Create progress tracking record
        try:
            progress = await GenerationProgressCRUD.create(
                db=self.db,
                operation_type="historic_refresh",
                total_items=len(self.seasons),
                season=None,
            )
            self.progress_id = progress.id
            self.logger.info(f"Created progress tracking record: {progress.id}")
        except Exception as e:
            self.logger.error(f"Failed to create progress tracking: {str(e)}", exc_info=True)
            stats["errors"].append(f"Failed to create progress tracking: {str(e)}")
        
        try:
            # Create Squiggle client
            squiggle_client = SquiggleClient()
            
            try:
                # Process each season
                for i, season in enumerate(self.seasons):
                    season_stats = {
                        "games_synced": 0,
                        "tips_generated": 0,
                        "rounds_processed": 0,
                        "errors": []
                    }
                    
                    try:
                        self.logger.info(f"Processing season {season} ({i+1}/{len(self.seasons)})")
                        
                        # Sync games for this season
                        self.logger.info(f"Syncing games for season {season}")
                        sync_service = GameSyncService(
                            squiggle_client=squiggle_client,
                            db_session=self.db,
                            season=season
                        )
                        
                        sync_result = await sync_service.sync_games()
                        season_stats["games_synced"] = sync_result["games_created"] + sync_result["games_updated"]
                        stats["games_synced"] += season_stats["games_synced"]
                        
                        self.logger.info(
                            f"Season {season}: Synced {season_stats['games_synced']} games "
                            f"({sync_result['games_created']} created, {sync_result['games_updated']} updated, "
                            f"{sync_result['games_skipped']} skipped)"
                        )
                        
                        # Generate tips for games that don't have tips
                        if self.round_id:
                            # Generate tips for specific round only
                            self.logger.info(f"Generating tips for season {season}, round {self.round_id}")
                            tip_service = TipGenerationService(
                                db_session=self.db,
                                season=season,
                                round_id=self.round_id
                            )
                            
                            tip_result = await tip_service.generate_for_round(
                                season=season,
                                round_id=self.round_id,
                                regenerate=self.regenerate_tips
                            )
                            
                            season_stats["tips_generated"] = tip_result["tips_created"] + tip_result["tips_updated"]
                            season_stats["rounds_processed"] = 1
                        else:
                            # Generate tips for all rounds in the season
                            self.logger.info(f"Generating tips for all rounds in season {season}")
                            tip_service = TipGenerationService(
                                db_session=self.db,
                                season=season
                            )
                            
                            # Get all rounds for this season
                            rounds = await GameCRUD.get_rounds_for_season(self.db, season)
                            season_stats["rounds_processed"] = len(rounds)
                            
                            for round_id in rounds:
                                try:
                                    tip_result = await tip_service.generate_for_round(
                                        season=season,
                                        round_id=round_id,
                                        regenerate=self.regenerate_tips
                                    )
                                    season_stats["tips_generated"] += tip_result["tips_created"] + tip_result["tips_updated"]
                                    
                                    self.logger.debug(
                                        f"Season {season}, Round {round_id}: "
                                        f"{tip_result['tips_created']} tips created, "
                                        f"{tip_result['tips_skipped']} skipped"
                                    )
                                except Exception as e:
                                    error_msg = f"Error generating tips for season {season}, round {round_id}: {str(e)}"
                                    self.logger.error(error_msg, exc_info=True)
                                    season_stats["errors"].append(error_msg)
                        
                        stats["tips_generated"] += season_stats["tips_generated"]
                        stats["seasons_processed"] += 1
                        
                        # Update progress
                        if self.progress_id:
                            await GenerationProgressCRUD.update_progress(
                                db=self.db,
                                progress_id=self.progress_id,
                                completed_items=i + 1,
                                status="in_progress"
                            )
                        
                        stats["season_stats"][season] = season_stats
                        
                    except Exception as e:
                        error_msg = f"Error processing season {season}: {str(e)}"
                        self.logger.error(error_msg, exc_info=True)
                        stats["errors"].append(error_msg)
                        season_stats["errors"].append(error_msg)
                        stats["season_stats"][season] = season_stats
                
                # Mark progress as completed
                if self.progress_id:
                    await GenerationProgressCRUD.update_progress(
                        db=self.db,
                        progress_id=self.progress_id,
                        completed_items=len(self.seasons),
                        status="completed"
                    )
                
                duration = time.time() - start_time
                stats["duration_seconds"] = duration
                
                self.logger.info(
                    f"Historic data refresh completed: "
                    f"{stats['seasons_processed']}/{len(self.seasons)} seasons processed, "
                    f"{stats['games_synced']} games synced, "
                    f"{stats['tips_generated']} tips generated "
                    f"in {duration:.2f}s"
                )
                
            finally:
                # Close Squiggle client
                await squiggle_client.close()
        
        except Exception as e:
            error_msg = f"Error in historic data refresh: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            stats["errors"].append(error_msg)
            
            # Mark progress as failed
            if self.progress_id:
                await GenerationProgressCRUD.update_progress(
                    db=self.db,
                    progress_id=self.progress_id,
                    completed_items=stats["seasons_processed"],
                    status="failed",
                    error_message=error_msg
                )
            
            stats["duration_seconds"] = time.time() - start_time
            raise
        
        return stats
    
    async def refresh_from_string(
        self,
        seasons_str: str,
        round_id: Optional[int] = None,
        regenerate_tips: bool = False
    ) -> Dict[str, Any]:
        """Refresh historical data from a seasons string.
        
        Args:
            seasons_str: Seasons string (e.g., "2010-2025", "2010,2011,2012")
            round_id: Optional round number to refresh
            regenerate_tips: Whether to regenerate existing tips
            
        Returns:
            Dictionary with refresh statistics
        """
        seasons = self._parse_seasons(seasons_str)
        self.seasons = seasons
        self.round_id = round_id
        self.regenerate_tips = regenerate_tips
        
        return await self.refresh()
    
    async def get_progress(self) -> Optional[Dict[str, Any]]:
        """Get current progress of historic refresh operation.
        
        Returns:
            Dictionary with progress information or None if no active operation
        """
        if not self.progress_id:
            # Try to find the most recent historic refresh operation
            progress = await GenerationProgressCRUD.get_by_operation(
                db=self.db,
                operation_type="historic_refresh"
            )
            if progress:
                self.progress_id = progress.id
            else:
                return None
        
        progress = await GenerationProgressCRUD.get_by_id(
            db=self.db,
            progress_id=self.progress_id
        )
        
        if not progress:
            return None
        
        return {
            "progress_id": progress.id,
            "operation_type": progress.operation_type,
            "total_items": progress.total_items,
            "completed_items": progress.completed_items,
            "status": progress.status,
            "started_at": progress.started_at.isoformat() if progress.started_at else None,
            "completed_at": progress.completed_at.isoformat() if progress.completed_at else None,
            "error_message": progress.error_message,
            "progress_percentage": (
                (progress.completed_items / progress.total_items * 100) 
                if progress.total_items > 0 else 0
            )
        }
