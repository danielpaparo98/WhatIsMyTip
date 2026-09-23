from typing import Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Game
from .base import BaseModel
from .prediction import Prediction
from .repository import GameHistoryRepository, SqlGameHistoryRepository


class FormModel(BaseModel):
    """Form-based model using recent team performance.

    P2-4: game history flows through a :class:`GameHistoryRepository` —
    the point-in-time (no-leakage) rule lives in the repository, not
    here.  When no repository is injected, one is built around the
    legacy ``db`` argument so the ABC signature stays stable during the
    cutover.
    """

    def __init__(
        self,
        games_to_consider: int = 5,
        repository: Optional[GameHistoryRepository] = None,
    ):
        self.games_to_consider = games_to_consider
        self._repository = repository

    def get_name(self) -> str:
        return "form"

    def _repository_for(self, db: AsyncSession) -> GameHistoryRepository:
        return self._repository or SqlGameHistoryRepository(db)

    async def _get_recent_form(
        self, db: AsyncSession, team: str, before_date
    ) -> Dict[str, float]:
        """Calculate recent form statistics for a team."""
        games = await self._repository_for(db).recent_games_for_participant(
            team, before=before_date, limit=self.games_to_consider
        )

        if not games:
            return {"wins": 0, "losses": 0, "draws": 0, "avg_score_diff": 0, "games": 0}

        wins = 0
        losses = 0
        draws = 0
        score_diffs = []

        for game in games:
            if game.home_team == team:
                score_diff = (game.home_score or 0) - (game.away_score or 0)
            else:
                score_diff = (game.away_score or 0) - (game.home_score or 0)

            if score_diff > 0:
                wins += 1
            elif score_diff < 0:
                losses += 1
            else:
                draws += 1

            score_diffs.append(abs(score_diff))

        return {
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "avg_score_diff": sum(score_diffs) / len(score_diffs) if score_diffs else 0,
            "games": len(games),
        }

    async def predict(self, game: Game, db: AsyncSession) -> Prediction:
        """Predict winner based on recent form."""
        home_form = await self._get_recent_form(db, game.home_team, game.date)
        away_form = await self._get_recent_form(db, game.away_team, game.date)

        # Calculate form scores (a draw is worth half a win, so it
        # contributes +1 under the ×2 win weighting)
        home_score = (
            home_form["wins"] * 2
            + home_form["draws"]
            - home_form["losses"]
            + home_form["avg_score_diff"] / 10
        )
        away_score = (
            away_form["wins"] * 2
            + away_form["draws"]
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

        return Prediction(winner, confidence, margin)
