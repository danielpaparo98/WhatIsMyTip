from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.models import Game, Tip
from app.openrouter import OpenRouterClient
from app.orchestrator import ModelOrchestrator


class ExplanationService:
    """Service for generating AI explanations for tips."""
    
    def __init__(self):
        self.client = OpenRouterClient()
        self.orchestrator = ModelOrchestrator()
    
    async def generate_for_tip(
        self, db: AsyncSession, tip: Tip, game: Game
    ) -> str:
        """Generate an explanation for an existing tip.
        
        Args:
            db: Database session
            tip: Tip to generate explanation for
            game: Associated game
            
        Returns:
            Generated explanation string
        """
        # Get model predictions for context
        model_predictions = {}
        for model in self.orchestrator.models:
            winner, confidence, margin = await model.predict(game)
            model_predictions[model.get_name()] = (winner, confidence, margin)
        
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
        
        # Generate explanation
        explanation = await self.client.generate_explanation(
            game=game_dict,
            prediction=prediction,
            heuristic=tip.heuristic,
            model_predictions=model_predictions,
        )
        
        return explanation
    
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
        from app.crud import TipCRUD, GameCRUD
        
        # Get games for the round
        games = await GameCRUD.get_by_round(db, season, round_id)
        
        count = 0
        for game in games:
            # Get tips for this game
            tips = await TipCRUD.get_by_game(db, game.id)
            
            for tip in tips:
                # Only generate explanation if not already present
                if not tip.explanation:
                    explanation = await self.generate_for_tip(db, tip, game)
                    
                    # Update tip with explanation
                    tip.explanation = explanation
                    count += 1
        
        await db.commit()
        return count
    
    async def close(self):
        """Close the service."""
        await self.client.close()
