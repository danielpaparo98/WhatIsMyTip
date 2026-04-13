from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, Tuple, List
from app.models_ml.base import BaseModel
from app.models import Game


class FormModel(BaseModel):
    """Form-based model using recent team performance."""
    
    def __init__(self, games_to_consider: int = 5):
        self.games_to_consider = games_to_consider
    
    def get_name(self) -> str:
        return "Form"
    
    async def _get_recent_form(
        self, db: AsyncSession, team: str, before_date
    ) -> Dict[str, float]:
        """Calculate recent form statistics for a team."""
        result = await db.execute(
            select(Game)
            .where(
                and_(
                    Game.completed == True,
                    (Game.home_team == team) | (Game.away_team == team),
                    Game.date < before_date,
                )
            )
            .order_by(Game.date.desc())
            .limit(self.games_to_consider)
        )
        games = result.scalars().all()
        
        if not games:
            return {"wins": 0, "losses": 0, "avg_score_diff": 0, "games": 0}
        
        wins = 0
        losses = 0
        score_diffs = []
        
        for game in games:
            if game.home_team == team:
                score_diff = (game.home_score or 0) - (game.away_score or 0)
                if score_diff > 0:
                    wins += 1
                else:
                    losses += 1
            else:
                score_diff = (game.away_score or 0) - (game.home_score or 0)
                if score_diff > 0:
                    wins += 1
                else:
                    losses += 1
            
            score_diffs.append(abs(score_diff))
        
        return {
            "wins": wins,
            "losses": losses,
            "avg_score_diff": sum(score_diffs) / len(score_diffs) if score_diffs else 0,
            "games": len(games),
        }
    
    async def predict(self, game: Game, db: AsyncSession) -> Tuple[str, float, int]:
        """Predict winner based on recent form."""
        home_form = await self._get_recent_form(db, game.home_team, game.date)
        away_form = await self._get_recent_form(db, game.away_team, game.date)
        
        # Calculate form scores
        home_score = (
            home_form["wins"] * 2
            - home_form["losses"]
            + home_form["avg_score_diff"] / 10
        )
        away_score = (
            away_form["wins"] * 2
            - away_form["losses"]
            + away_form["avg_score_diff"] / 10
        )
        
        # Apply home advantage
        home_score += 1.0
        
        # Calculate confidence
        total_score = abs(home_score) + abs(away_score)
        if total_score > 0:
            confidence = abs(home_score - away_score) / (total_score + 1)
        else:
            confidence = 0.5
        
        # Predict winner
        if home_score > away_score:
            winner = game.home_team
            margin = int(abs(home_score - away_score) * 5)
        else:
            winner = game.away_team
            margin = int(abs(home_score - away_score) * 5)
        
        # Clamp values
        confidence = max(0.5, min(0.95, confidence))
        margin = max(1, min(100, margin))
        
        return winner, confidence, margin
