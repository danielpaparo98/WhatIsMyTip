"""Service for syncing games from Squiggle API."""

from typing import Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import time

from app.squiggle import SquiggleClient
from app.crud.games import GameCRUD
from app.logger import get_logger


logger = get_logger(__name__)


class GameSyncService:
    """Service for syncing games from Squiggle API to database.
    
    This service handles:
    - Fetching games from Squiggle API for specified seasons
    - Creating or updating games in the database
    - Tracking sync statistics (created, updated, skipped)
    - Handling API errors gracefully
    """
    
    def __init__(
        self,
        squiggle_client: SquiggleClient,
        db_session: AsyncSession,
        season: Optional[int] = None
    ):
        """Initialize the GameSyncService.
        
        Args:
            squiggle_client: Squiggle API client
            db_session: Database session
            season: Season year to sync (defaults to current year)
        """
        self.client = squiggle_client
        self.db = db_session
        self.season = season or datetime.now().year
        self.logger = logger
        
    async def sync_games(self) -> Dict[str, Any]:
        """Sync games for the configured season.
        
        Returns:
            Dictionary with sync statistics:
            - games_created: Number of games created
            - games_updated: Number of games updated
            - games_skipped: Number of games skipped (no changes)
            - total_games: Total number of games processed
            - season: Season synced
            - duration_seconds: Time taken to sync
        """
        start_time = time.time()
        self.logger.info(f"Starting game sync for season {self.season}")
        
        stats = {
            "games_created": 0,
            "games_updated": 0,
            "games_skipped": 0,
            "total_games": 0,
            "season": self.season,
            "errors": []
        }
        
        try:
            # Fetch games from Squiggle API
            self.logger.info(f"Fetching games from Squiggle API for season {self.season}")
            games_data = await self.client.get_games(year=self.season)
            
            if not games_data:
                self.logger.warning(f"No games returned from Squiggle API for season {self.season}")
                stats["duration_seconds"] = time.time() - start_time
                return stats
            
            self.logger.info(f"Retrieved {len(games_data)} games from Squiggle API")
            
            # Process each game
            for game_data in games_data:
                try:
                    result = await GameCRUD.create_or_update_with_tracking(
                        self.db, game_data
                    )
                    
                    if result["action"] == "created":
                        stats["games_created"] += 1
                    elif result["action"] == "updated":
                        stats["games_updated"] += 1
                    else:
                        stats["games_skipped"] += 1
                    
                    stats["total_games"] += 1
                    
                except Exception as e:
                    error_msg = f"Error processing game {game_data.get('id', 'unknown')}: {str(e)}"
                    self.logger.error(error_msg)
                    stats["errors"].append(error_msg)
            
            duration = time.time() - start_time
            stats["duration_seconds"] = duration
            
            self.logger.info(
                f"Game sync completed: {stats['games_created']} created, "
                f"{stats['games_updated']} updated, {stats['games_skipped']} skipped "
                f"in {duration:.2f}s"
            )
            
        except Exception as e:
            error_msg = f"Error syncing games: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            stats["errors"].append(error_msg)
            stats["duration_seconds"] = time.time() - start_time
            raise
        
        return stats
    
    async def sync_multiple_seasons(self, seasons: List[int]) -> Dict[str, Any]:
        """Sync games for multiple seasons.
        
        Args:
            seasons: List of season years to sync
            
        Returns:
            Dictionary with combined sync statistics
        """
        self.logger.info(f"Starting game sync for {len(seasons)} seasons: {seasons}")
        
        combined_stats = {
            "games_created": 0,
            "games_updated": 0,
            "games_skipped": 0,
            "total_games": 0,
            "seasons": seasons,
            "season_stats": {},
            "errors": []
        }
        
        for season in seasons:
            try:
                self.season = season
                season_stats = await self.sync_games()
                combined_stats["season_stats"][season] = season_stats
                
                combined_stats["games_created"] += season_stats["games_created"]
                combined_stats["games_updated"] += season_stats["games_updated"]
                combined_stats["games_skipped"] += season_stats["games_skipped"]
                combined_stats["total_games"] += season_stats["total_games"]
                combined_stats["errors"].extend(season_stats.get("errors", []))
                
            except Exception as e:
                error_msg = f"Error syncing season {season}: {str(e)}"
                self.logger.error(error_msg, exc_info=True)
                combined_stats["errors"].append(error_msg)
        
        self.logger.info(
            f"Multi-season sync completed: {combined_stats['games_created']} created, "
            f"{combined_stats['games_updated']} updated, {combined_stats['games_skipped']} skipped"
        )
        
        return combined_stats
