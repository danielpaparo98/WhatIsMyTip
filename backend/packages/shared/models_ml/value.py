from datetime import date
from typing import Dict, Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Game
from .base import BaseModel
from .prediction import Prediction


class ValueModel(BaseModel):
    """Value-based model identifying undervalued teams."""

    def __init__(self):
        self.team_win_rates: Dict[str, float] = {}

    def get_name(self) -> str:
        return "value"

    async def _calculate_win_rates(self, db: AsyncSession, before_date: Optional[date] = None):
        """Calculate historical win rates for each team.

        Only uses games that occurred BEFORE the given date
        to ensure no data leakage in backtesting.

        Uses aggregated queries instead of per-team queries to avoid N+1.

        Args:
            db: Database session
            before_date: Only consider games before this date (None = use all games)
        """
        # Build base filter
        base_filter = [Game.completed]
        if before_date is not None:
            base_filter.append(Game.date < before_date)

        # Single query for home games
        home_query = (
            select(
                Game.home_team.label("team"),
                func.count().label("total"),
                func.sum(case((Game.home_score > Game.away_score, 1), else_=0)).label("wins"),
            )
            .where(*base_filter)
            .group_by(Game.home_team)
        )
        home_result = await db.execute(home_query)
        home_stats = {
            row.team: {"total": row.total, "wins": row.wins or 0} for row in home_result.all()
        }

        # Single query for away games
        away_query = (
            select(
                Game.away_team.label("team"),
                func.count().label("total"),
                func.sum(case((Game.away_score > Game.home_score, 1), else_=0)).label("wins"),
            )
            .where(*base_filter)
            .group_by(Game.away_team)
        )
        away_result = await db.execute(away_query)
        away_stats = {
            row.team: {"total": row.total, "wins": row.wins or 0} for row in away_result.all()
        }

        # Combine home and away stats
        all_teams = set(home_stats.keys()) | set(away_stats.keys())
        self.team_win_rates = {}
        for team in all_teams:
            home = home_stats.get(team, {"total": 0, "wins": 0})
            away = away_stats.get(team, {"total": 0, "wins": 0})
            total = home["total"] + away["total"]
            wins = home["wins"] + away["wins"]
            self.team_win_rates[team] = (wins / total) if total > 0 else 0.5

    async def predict(self, game: Game, db: AsyncSession) -> Prediction:
        """Predict winner based on value (undervalued teams).

        Uses only historical data before the prediction game's date.
        """
        await self._calculate_win_rates(db, before_date=game.date)

        home_rate = self.team_win_rates.get(game.home_team, 0.5)
        away_rate = self.team_win_rates.get(game.away_team, 0.5)

        # Predict team with better historical performance.
        # Raw rates only — home advantage is owned by the elo /
        # home_advantage models; a hardcoded bump here double-counted it
        # and flipped genuine away-team edges (e.g. 0.50 vs 0.53).
        if home_rate > away_rate:
            winner = game.home_team
            confidence = (home_rate - away_rate) + 0.5
            margin = int((home_rate - away_rate) * 150)
        else:
            winner = game.away_team
            confidence = (away_rate - home_rate) + 0.5
            margin = int((away_rate - home_rate) * 150)

        # Clamp values
        confidence = max(0.5, min(0.9, confidence))
        margin = max(1, min(80, margin))

        return Prediction(winner, confidence, margin)
