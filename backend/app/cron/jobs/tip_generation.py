"""Tip generation cron job."""

from typing import Dict, Any, Optional
from datetime import datetime

from app.cron.base import BaseJob, TransientJobError, PermanentJobError
from app.services.tip_generation import TipGenerationService
from app.crud.games import GameCRUD
from app.logger import get_logger


logger = get_logger(__name__)


class TipGenerationJob(BaseJob):
    """Cron job for generating tips for upcoming games.
    
    This job:
    - Runs daily at configured time (default 3 AM, after daily sync)
    - Finds next upcoming round that needs tips
    - Generates tips for that round using ModelOrchestrator
    - Applies heuristics to get tip selections
    - Stores tips and model predictions in database
    - Tracks generation statistics
    - Handles errors and retry logic
    """
    
    def __init__(
        self,
        db_session,
        settings,
        instance_id: str = None,
        season: int = None,
        round_id: int = None,
        regenerate: bool = None
    ):
        """Initialize the TipGenerationJob.
        
        Args:
            db_session: Database session
            settings: Application settings
            instance_id: Optional instance identifier
            season: Optional season to generate tips for (defaults to finding next round)
            round_id: Optional round to generate tips for (defaults to finding next round)
            regenerate: Optional flag to regenerate existing tips (defaults to settings)
        """
        super().__init__(
            job_name="tip_generation",
            db_session=db_session,
            settings=settings,
            instance_id=instance_id
        )
        self.season = season
        self.round_id = round_id
        self.regenerate = regenerate if regenerate is not None else settings.tip_generation_regenerate_existing
        self.logger = logger
    
    async def execute(self) -> Dict[str, Any]:
        """Execute the tip generation job.
        
        Returns:
            Dictionary with execution results including:
            - items_processed: Number of games processed
            - items_succeeded: Number of tips created
            - items_failed: Number of games that failed
            - summary: Text summary of execution
        """
        self.logger.info("Starting TipGenerationJob")
        
        result = {
            "items_processed": 0,
            "items_succeeded": 0,
            "items_failed": 0,
            "summary": "",
            "season": None,
            "round_id": None,
            "games_processed": 0,
            "tips_created": 0,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 0,
            "model_predictions_updated": 0,
            "errors": []
        }
        
        try:
            # Determine which round to process
            if self.season and self.round_id:
                # Specific season and round provided
                season = self.season
                round_id = self.round_id
                self.logger.info(f"Generating tips for specified season {season}, round {round_id}")
            else:
                # Find next upcoming round that needs tips
                next_round = await GameCRUD.get_next_upcoming_round(self.db_session)
                
                if not next_round:
                    self.logger.info("No upcoming rounds found that need tips")
                    result["summary"] = "No upcoming rounds found that need tips"
                    return result
                
                season, round_id = next_round
                self.logger.info(f"Found next upcoming round: season {season}, round {round_id}")
            
            result["season"] = season
            result["round_id"] = round_id
            
            # Create tip generation service
            generation_service = TipGenerationService(
                db_session=self.db_session,
                season=season,
                round_id=round_id
            )
            
            # Generate tips for the round
            self.logger.info(
                f"Generating tips for season {season}, round {round_id}, "
                f"regenerate={self.regenerate}"
            )
            
            generation_stats = await generation_service.generate_for_round(
                season=season,
                round_id=round_id,
                regenerate=self.regenerate
            )
            
            # Populate result with generation statistics
            result["games_processed"] = generation_stats["games_processed"]
            result["tips_created"] = generation_stats["tips_created"]
            result["tips_skipped"] = generation_stats["tips_skipped"]
            result["tips_updated"] = generation_stats.get("tips_updated", 0)
            result["model_predictions_created"] = generation_stats["model_predictions_created"]
            result["model_predictions_updated"] = generation_stats.get("model_predictions_updated", 0)
            result["items_processed"] = generation_stats["games_processed"]
            result["items_succeeded"] = generation_stats["tips_created"]
            result["items_failed"] = len(generation_stats.get("errors", []))
            result["errors"] = generation_stats.get("errors", [])
            
            # Build summary
            summary_parts = [
                f"Generated tips for season {season}, round {round_id}",
                f"Processed {result['games_processed']} games",
                f"Created {result['tips_created']} tips",
                f"Skipped {result['tips_skipped']} existing tips"
            ]
            
            if result["tips_updated"] > 0:
                summary_parts.append(f"Updated {result['tips_updated']} tips")
            
            summary_parts.append(
                f"Created {result['model_predictions_created']} model predictions"
            )
            
            if result["model_predictions_updated"] > 0:
                summary_parts.append(
                    f"Updated {result['model_predictions_updated']} model predictions"
                )
            
            if result["items_failed"] > 0:
                summary_parts.append(f"Failed: {result['items_failed']}")
            
            result["summary"] = "; ".join(summary_parts)
            
            self.logger.info(f"TipGenerationJob completed: {result['summary']}")
            
        except Exception as e:
            error_msg = f"TipGenerationJob failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            
            result["items_failed"] = result["items_processed"]
            result["summary"] = f"Failed: {error_msg}"
            result["errors"].append(error_msg)
            
            # Classify error type
            if "timeout" in str(e).lower() or "network" in str(e).lower():
                raise TransientJobError(error_msg)
            else:
                raise PermanentJobError(error_msg)
        
        return result
