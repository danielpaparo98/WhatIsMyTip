"""Service for generating AI match analysis talking points using OpenRouter."""

from sqlalchemy.ext.asyncio import AsyncSession
from app.openrouter.client import OpenRouterClient
from app.crud.match_analysis import MatchAnalysisCRUD
from app.orchestrator import ModelOrchestrator
from app.models import Game
from app.logger import get_logger

logger = get_logger(__name__)


class MatchAnalysisService:
    """Service for generating casual match analysis talking points."""

    def __init__(self):
        self.client = OpenRouterClient()
        self.orchestrator = ModelOrchestrator()

    async def generate_and_store_analysis(
        self, db: AsyncSession, game: Game
    ) -> str | None:
        """Generate match analysis talking points and store them.

        Args:
            db: Database session
            game: Game to generate analysis for

        Returns:
            Generated analysis text, or None on failure
        """
        try:
            # Check if analysis already exists
            existing = await MatchAnalysisCRUD.get_by_game_id(db, game.id)
            if existing:
                logger.info(f"Match analysis already exists for game {game.id}")
                return existing.analysis_text

            # Get model predictions for richer context
            model_predictions = {}
            try:
                for model in self.orchestrator.models:
                    winner, confidence, margin = await model.predict(game, db)
                    model_predictions[model.get_name()] = (winner, confidence, margin)
            except Exception as e:
                logger.warning(
                    f"Could not get model predictions for match analysis context: {e}"
                )

            # Build game dict
            game_dict = {
                "home_team": game.home_team,
                "away_team": game.away_team,
                "venue": game.venue,
                "date": str(game.date) if game.date else "TBD",
            }

            # Generate analysis
            analysis_text = await self.client.generate_match_analysis(
                game_dict, model_predictions if model_predictions else None
            )

            if analysis_text:
                await MatchAnalysisCRUD.create(db, game.id, analysis_text)
                logger.info(f"Generated match analysis for game {game.id}")

            return analysis_text

        except Exception as e:
            logger.error(
                f"Failed to generate match analysis for game {game.id}: {e}",
                exc_info=True,
            )
            return None

    async def close(self):
        """Close the service and release resources."""
        await self.client.close()
