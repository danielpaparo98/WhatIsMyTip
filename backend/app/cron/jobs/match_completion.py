"""Match completion detection cron job."""

from typing import Dict, Any
from datetime import datetime

from app.cron.base import BaseJob, classify_error
from app.squiggle import SquiggleClient
from app.services.match_completion import MatchCompletionDetectorService
from app.models_ml.elo import EloModel
from app.logger import get_logger


logger = get_logger(__name__)


class MatchCompletionDetectionJob(BaseJob):
    """Cron job for detecting and processing completed matches.
    
    This job:
    - Runs every 15 minutes (configurable)
    - Detects games that have recently completed
    - Updates games with final scores from Squiggle API
    - Marks games as completed
    - Updates Elo ratings cache after games are completed
    - Tracks completion statistics
    - Handles errors and retry logic
    """
    
    def __init__(
        self,
        job_name: str,
        db_session,
        settings,
        instance_id: str = None,
        buffer_minutes: int = None
    ):
        """Initialize the MatchCompletionDetectionJob.
        
        Args:
            job_name: Name of the job
            db_session: Database session
            settings: Application settings
            instance_id: Optional instance identifier
            buffer_minutes: Optional buffer minutes override (defaults to settings)
        """
        super().__init__(
            job_name=job_name,
            db_session=db_session,
            settings=settings,
            instance_id=instance_id
        )
        self.buffer_minutes = buffer_minutes or settings.match_completion_buffer_minutes
        self.logger = logger
    
    async def execute(self) -> Dict[str, Any]:
        """Execute the match completion detection job.
        
        Returns:
            Dictionary with execution results including:
            - items_processed: Number of games checked
            - items_succeeded: Number of games marked complete
            - items_failed: Number of games that failed
            - summary: Text summary of execution
        """
        self.logger.info(
            f"Starting MatchCompletionDetectionJob with {self.buffer_minutes} minute buffer"
        )
        
        result = {
            "items_processed": 0,
            "items_succeeded": 0,
            "items_failed": 0,
            "summary": "",
            "games_checked": 0,
            "games_completed": 0,
            "games_already_completed": 0,
            "games_not_ready": 0,
            "elo_cache_updated": False
        }
        
        try:
            # Create Squiggle client
            squiggle_client = SquiggleClient()
            
            try:
                # Create match completion detector service
                detector_service = MatchCompletionDetectorService(
                    squiggle_client=squiggle_client,
                    db_session=self.db_session,
                    buffer_minutes=self.buffer_minutes
                )
                
                # Detect and process completed matches
                self.logger.info("Detecting completed matches")
                completion_stats = await detector_service.detect_and_process_completed_matches()
                
                result["games_checked"] = completion_stats["games_checked"]
                result["games_completed"] = completion_stats["games_completed"]
                result["games_already_completed"] = completion_stats["games_already_completed"]
                result["games_not_ready"] = completion_stats["games_not_ready"]
                result["items_processed"] = completion_stats["games_checked"]
                result["items_succeeded"] = completion_stats["games_completed"]
                result["items_failed"] = len(completion_stats.get("errors", []))
                
                # Update Elo ratings cache if games were completed
                if completion_stats["games_completed"] > 0:
                    self.logger.info(
                        f"Updating Elo ratings cache after {completion_stats['games_completed']} completed games"
                    )
                    try:
                        await EloModel.update_cache(self.db_session)
                        result["elo_cache_updated"] = True
                        self.logger.info("Elo ratings cache updated successfully")
                    except Exception as elo_error:
                        self.logger.error(
                            f"Failed to update Elo cache: {str(elo_error)}",
                            exc_info=True
                        )
                        # Don't fail the job if Elo cache update fails
                        # The game completion is still successful
                
                # Build summary
                summary_parts = [
                    f"Checked {result['games_checked']} games for completion",
                    f"Marked {result['games_completed']} games as complete",
                    f"{result['games_not_ready']} games not ready",
                    f"{result['games_already_completed']} already complete"
                ]
                
                if result["elo_cache_updated"]:
                    summary_parts.append("Elo cache updated")
                
                if result["items_failed"] > 0:
                    summary_parts.append(f"Failed: {result['items_failed']}")
                
                result["summary"] = "; ".join(summary_parts)
                
                self.logger.info(f"MatchCompletionDetectionJob completed: {result['summary']}")
                
            finally:
                # Close Squiggle client
                await squiggle_client.close()
        
        except Exception as e:
            error_msg = f"MatchCompletionDetectionJob failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            
            result["items_failed"] = result["items_processed"]
            result["summary"] = f"Failed: {error_msg}"
            
            raise classify_error(e, "MatchCompletionDetectionJob failed")
        
        return result
