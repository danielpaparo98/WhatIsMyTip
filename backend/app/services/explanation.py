"""Service for generating AI explanations for tips using OpenRouter."""

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.models import Game, Tip
from app.openrouter import OpenRouterClient
from app.orchestrator import ModelOrchestrator
from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)


class ExplanationService:
    """Service for generating AI explanations for tips."""
    
    def __init__(self):
        self.client = OpenRouterClient()
        self.orchestrator = ModelOrchestrator()
    
    async def generate_and_store_explanation(
        self, db: AsyncSession, tip: Tip, game: Game
    ) -> str:
        """Generate an AI explanation for a tip and store it in the database.
        
        Args:
            db: Database session
            tip: Tip to generate explanation for
            game: Associated game
            
        Returns:
            Generated explanation string
        """
        # Get model predictions for richer context
        model_predictions = {}
        try:
            for model in self.orchestrator.models:
                winner, confidence, margin = await model.predict(game, db)
                model_predictions[model.get_name()] = (winner, confidence, margin)
        except Exception as e:
            logger.warning(
                f"Could not get model predictions for explanation context: {e}"
            )
        
        # Build prediction dict
        prediction = {
            "winner": tip.selected_team,
            "confidence": tip.confidence,
            "margin": tip.margin,
        }
        
        # Build game dict
        game_dict = {
            "home_team": game.home_team,
            "away_team": game.away_team,
            "venue": game.venue,
            "date": game.date.isoformat(),
        }
        
        # Generate explanation via OpenRouter
        explanation = await self.client.generate_explanation(
            game=game_dict,
            prediction=prediction,
            heuristic=tip.heuristic,
            model_predictions=model_predictions if model_predictions else None,
        )
        
        # Update the tip in the database
        tip.explanation = explanation
        db.add(tip)
        await db.commit()
        
        return explanation
    
    async def generate_for_game_tips(
        self, db: AsyncSession, game_id: int
    ) -> int:
        """Generate explanations for all tips for a given game.
        
        Args:
            db: Database session
            game_id: Game ID to generate explanations for
            
        Returns:
            Number of explanations generated
        """
        from app.crud.tips import TipCRUD
        from app.crud.games import GameCRUD
        
        # Get the game
        game = await GameCRUD.get_by_id(db, game_id)
        if not game:
            logger.warning(f"Game {game_id} not found, skipping explanation generation")
            return 0
        
        # Get tips for this game
        tips = await TipCRUD.get_by_game(db, game_id)
        
        count = 0
        for tip in tips:
            # Only generate explanation if not already present
            if not tip.explanation:
                try:
                    await self.generate_and_store_explanation(db, tip, game)
                    count += 1
                    logger.debug(
                        f"Generated explanation for tip {tip.id} "
                        f"(game {game_id}, heuristic {tip.heuristic})"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to generate explanation for tip {tip.id}: {e}",
                        exc_info=True,
                    )
                    # Continue with other tips even if one fails
        
        if count > 0:
            logger.info(
                f"Generated {count} explanations for game {game_id} "
                f"({game.home_team} vs {game.away_team})"
            )
        
        return count
    
    async def generate_for_round(
        self, db: AsyncSession, season: int, round_id: int
    ) -> int:
        """Generate explanations for all tips in a round.
        
        Args:
            db: Database session
            season: Season year
            round_id: Round number
            
        Returns:
            Number of explanations generated
        """
        from app.crud.tips import TipCRUD
        from app.crud.games import GameCRUD
        
        # Get games for the round
        games = await GameCRUD.get_by_round(db, season, round_id)
        
        total_count = 0
        for game in games:
            count = await self.generate_for_game_tips(db, game.id)
            total_count += count
        
        if total_count > 0:
            logger.info(
                f"Generated {total_count} explanations for "
                f"season {season}, round {round_id}"
            )
        
        return total_count
    
    async def close(self):
        """Close the service and release resources."""
        await self.client.close()
