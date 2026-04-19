"""Service for generating tips using ML models and heuristics."""

from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import time

from app.orchestrator import ModelOrchestrator
from app.crud.games import GameCRUD
from app.crud.tips import TipCRUD
from app.crud.model_predictions import ModelPredictionCRUD
from app.models import Game
from app.logger import get_logger
from app.services.explanation import ExplanationService


logger = get_logger(__name__)


class TipGenerationService:
    """Service for generating tips using ML models and heuristics.
    
    This service handles:
    - Finding games needing tips (upcoming, not yet generated)
    - Running ModelOrchestrator for predictions
    - Applying heuristics to generate final tips
    - Storing tips and model predictions
    - Handling batch generation for efficiency
    - Supporting regeneration of existing tips
    """
    
    def __init__(
        self,
        db_session: AsyncSession,
        season: Optional[int] = None,
        round_id: Optional[int] = None
    ):
        """Initialize the TipGenerationService.
        
        Args:
            db_session: Database session
            season: Season year to generate tips for (defaults to current year)
            round_id: Round number to generate tips for (optional)
        """
        self.db = db_session
        self.season = season or datetime.now().year
        self.round_id = round_id
        self.logger = logger
        self.orchestrator = ModelOrchestrator()
    
    async def generate_for_round(
        self,
        season: int,
        round_id: int,
        regenerate: bool = False,
        skip_nlp: bool = False
    ) -> Dict[str, Any]:
        """Generate tips for a specific round.
        
        Args:
            season: Season year
            round_id: Round number
            regenerate: Whether to regenerate existing tips (default: False)
            skip_nlp: Skip AI explanation and match analysis generation (default: False)
            
        Returns:
            Dictionary with generation statistics:
            - games_processed: Number of games processed
            - tips_created: Number of tips created
            - tips_skipped: Number of tips skipped (already exist)
            - tips_updated: Number of tips updated (when regenerate=True)
            - model_predictions_created: Number of model predictions created
            - model_predictions_updated: Number of model predictions updated
            - errors: List of error messages
            - duration_seconds: Time taken to generate
            - heuristics_used: List of heuristics used
            - season: Season processed
            - round_id: Round processed
        """
        start_time = time.time()
        self.logger.info(f"Starting tip generation for season {season}, round {round_id}, regenerate={regenerate}, skip_nlp={skip_nlp}")
        
        stats = {
            "games_processed": 0,
            "tips_created": 0,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 0,
            "model_predictions_updated": 0,
            "errors": [],
            "heuristics_used": self.orchestrator.get_available_heuristics(),
            "season": season,
            "round_id": round_id
        }
        
        try:
            # Get games for the specified round
            games = await GameCRUD.get_by_round(self.db, season, round_id)
            
            if not games:
                self.logger.warning(f"No games found for round {round_id}, season {season}")
                stats["duration_seconds"] = time.time() - start_time
                return stats
            
            self.logger.info(f"Found {len(games)} games for round {round_id}, season {season}")
            
            # Process each game
            for game in games:
                try:
                    game_stats = await self._generate_for_game(game, regenerate, skip_nlp=skip_nlp)
                    
                    stats["games_processed"] += 1
                    stats["tips_created"] += game_stats.get("tips_created", 0)
                    stats["tips_skipped"] += game_stats.get("tips_skipped", 0)
                    stats["tips_updated"] += game_stats.get("tips_updated", 0)
                    stats["model_predictions_created"] += game_stats.get("model_predictions_created", 0)
                    stats["model_predictions_updated"] += game_stats.get("model_predictions_updated", 0)
                    
                    self.logger.debug(
                        f"Processed game {game.id}: {game.home_team} vs {game.away_team} - "
                        f"Tips: {game_stats.get('tips_created', 0)} created, "
                        f"{game_stats.get('tips_skipped', 0)} skipped, "
                        f"{game_stats.get('tips_updated', 0)} updated"
                    )
                    
                except Exception as e:
                    error_msg = f"Error processing game {game.id} ({game.home_team} vs {game.away_team}): {str(e)}"
                    self.logger.error(error_msg, exc_info=True)
                    stats["errors"].append(error_msg)
            
            duration = time.time() - start_time
            stats["duration_seconds"] = duration
            
            self.logger.info(
                f"Tip generation completed for season {season}, round {round_id}: "
                f"{stats['games_processed']} games processed, "
                f"{stats['tips_created']} tips created, "
                f"{stats['tips_skipped']} tips skipped, "
                f"{stats['tips_updated']} tips updated, "
                f"{stats['model_predictions_created']} model predictions created "
                f"in {duration:.2f}s"
            )
            
        except Exception as e:
            error_msg = f"Error generating tips for season {season}, round {round_id}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            stats["errors"].append(error_msg)
            stats["duration_seconds"] = time.time() - start_time
            raise
        
        return stats
    
    async def generate_for_next_upcoming_round(self, regenerate: bool = False) -> Dict[str, Any]:
        """Generate tips for the next upcoming round that needs tips.
        
        Args:
            regenerate: Whether to regenerate existing tips (default: False)
            
        Returns:
            Dictionary with generation statistics or empty if no round found
        """
        # Find the next upcoming round that needs tips
        next_round = await GameCRUD.get_next_upcoming_round(self.db)
        
        if not next_round:
            self.logger.info("No upcoming rounds found that need tips")
            return {
                "games_processed": 0,
                "tips_created": 0,
                "tips_skipped": 0,
                "tips_updated": 0,
                "model_predictions_created": 0,
                "model_predictions_updated": 0,
                "errors": [],
                "heuristics_used": [],
                "season": None,
                "round_id": None,
                "duration_seconds": 0.0,
                "message": "No upcoming rounds found that need tips"
            }
        
        season, round_id = next_round
        self.logger.info(f"Found next upcoming round: season {season}, round {round_id}")
        
        return await self.generate_for_round(season, round_id, regenerate)
    
    async def _generate_for_game(
        self,
        game: Game,
        regenerate: bool = False,
        skip_nlp: bool = False
    ) -> Dict[str, Any]:
        """Generate tips and predictions for a single game.
        
        Args:
            game: Game to generate tips for
            regenerate: Whether to regenerate existing tips
            skip_nlp: Skip AI explanation and match analysis generation (default: False)
            
        Returns:
            Dictionary with game-level statistics
        """
        game_stats = {
            "tips_created": 0,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 0,
            "model_predictions_updated": 0
        }
        
        # Check if tips already exist for this game
        existing_tips = await TipCRUD.get_by_game(self.db, game.id)
        existing_heuristics = {tip.heuristic for tip in existing_tips}
        
        # Get available heuristics
        heuristics_to_use = self.orchestrator.get_available_heuristics()
        
        # Generate tips for each heuristic
        for heuristic in heuristics_to_use:
            # Check if tip already exists
            if heuristic in existing_heuristics:
                if regenerate:
                    # Delete existing tips for this heuristic
                    await TipCRUD.delete_for_game(self.db, game.id)
                    existing_heuristics.discard(heuristic)
                    # Re-fetch to get remaining tips
                    remaining_tips = await TipCRUD.get_by_game(self.db, game.id)
                    existing_heuristics = {tip.heuristic for tip in remaining_tips}
                else:
                    game_stats["tips_skipped"] += 1
                    continue
            
            # Generate prediction using the heuristic
            try:
                winner, confidence, margin = await self.orchestrator.predict(
                    game, heuristic, self.db
                )
                
                # Create the tip
                await TipCRUD.create(
                    db=self.db,
                    game_id=game.id,
                    heuristic=heuristic,
                    selected_team=winner,
                    margin=margin,
                    confidence=confidence,
                    explanation=""  # Explanations can be generated separately
                )
                
                game_stats["tips_created"] += 1
                
            except Exception as e:
                self.logger.error(
                    f"Error generating {heuristic} tip for game {game.id}: {str(e)}",
                    exc_info=True
                )
                raise
        
        # Generate and store model predictions for this game
        # Fetch all existing predictions once (N+1 fix)
        existing_predictions = await ModelPredictionCRUD.get_by_game(self.db, game.id)
        existing_by_model = {p.model_name: p for p in existing_predictions}
        
        for model in self.orchestrator.models:
            try:
                winner, confidence, margin = await model.predict(game, self.db)
                
                if model.get_name() in existing_by_model:
                    if regenerate:
                        # Update existing prediction
                        await ModelPredictionCRUD.create_or_update(
                            db=self.db,
                            game_id=game.id,
                            model_name=model.get_name(),
                            winner=winner,
                            confidence=confidence,
                            margin=margin,
                        )
                        game_stats["model_predictions_updated"] += 1
                    else:
                        # Skip existing prediction
                        pass
                else:
                    # Create new prediction
                    await ModelPredictionCRUD.create(
                        db=self.db,
                        game_id=game.id,
                        model_name=model.get_name(),
                        winner=winner,
                        confidence=confidence,
                        margin=margin,
                    )
                    game_stats["model_predictions_created"] += 1
                    
            except Exception as e:
                self.logger.error(
                    f"Error generating prediction for model {model.get_name()} "
                    f"for game {game.id}: {str(e)}",
                    exc_info=True
                )
                # Continue with other models even if one fails
        
        # Generate AI explanations for newly created tips
        if not skip_nlp and game_stats["tips_created"] > 0:
            try:
                explanation_service = ExplanationService()
                explanation_count = await explanation_service.generate_for_game_tips(
                    self.db, game.id
                )
                if explanation_count > 0:
                    self.logger.info(
                        f"Generated {explanation_count} AI explanations for game {game.id}"
                    )
                await explanation_service.close()
            except Exception as e:
                # Explanation failure should not break tip generation
                self.logger.warning(
                    f"Explanation generation failed for game {game.id}: {e}",
                    exc_info=True,
                )

        # Generate match analysis talking points
        if not skip_nlp and game_stats["tips_created"] > 0:
            try:
                from .match_analysis import MatchAnalysisService

                match_analysis_service = MatchAnalysisService()
                analysis = await match_analysis_service.generate_and_store_analysis(
                    self.db, game
                )
                if analysis:
                    self.logger.info(
                        f"Generated match analysis for game {game.id}"
                    )
                await match_analysis_service.close()
            except Exception as e:
                # Match analysis failure should not break tip generation
                self.logger.warning(
                    f"Match analysis generation failed for game {game.id}: {e}",
                    exc_info=True,
                )

        return game_stats
    
    async def generate_batch(
        self,
        games: List[Game],
        regenerate: bool = False
    ) -> Dict[str, Any]:
        """Generate tips for multiple games in batch.
        
        Args:
            games: List of games to generate tips for
            regenerate: Whether to regenerate existing tips
            
        Returns:
            Dictionary with aggregated generation statistics
        """
        start_time = time.time()
        self.logger.info(f"Starting batch tip generation for {len(games)} games")
        
        stats = {
            "games_processed": 0,
            "tips_created": 0,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 0,
            "model_predictions_updated": 0,
            "errors": [],
            "heuristics_used": self.orchestrator.get_available_heuristics(),
            "duration_seconds": 0.0
        }
        
        for game in games:
            try:
                game_stats = await self._generate_for_game(game, regenerate)
                
                stats["games_processed"] += 1
                stats["tips_created"] += game_stats.get("tips_created", 0)
                stats["tips_skipped"] += game_stats.get("tips_skipped", 0)
                stats["tips_updated"] += game_stats.get("tips_updated", 0)
                stats["model_predictions_created"] += game_stats.get("model_predictions_created", 0)
                stats["model_predictions_updated"] += game_stats.get("model_predictions_updated", 0)
                
            except Exception as e:
                error_msg = f"Error processing game {game.id}: {str(e)}"
                self.logger.error(error_msg, exc_info=True)
                stats["errors"].append(error_msg)
        
        stats["duration_seconds"] = time.time() - start_time
        
        self.logger.info(
            f"Batch tip generation completed: "
            f"{stats['games_processed']}/{len(games)} games processed, "
            f"{stats['tips_created']} tips created, "
            f"{stats['tips_skipped']} tips skipped, "
            f"{stats['tips_updated']} tips updated "
            f"in {stats['duration_seconds']:.2f}s"
        )
        
        return stats
