"""PlayerFormModel — predicts game outcomes based on recent player form.

Aggregates player_advanced_stats to the team level to measure recent team
quality.  Teams whose players are collectively generating more metres gained,
score involvements, and contested possessions are playing better football.

Cold-start: abstains when there is no usable data.
"""

import json
from typing import Dict, List, Union

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import _get_client
from ..logger import get_logger
from ..models import Game, PlayerAdvancedStats, PlayerMatchStats
from .base import BaseModel
from .prediction import ABSTAINED, Abstained, Prediction

logger = get_logger(__name__)

_CACHE_PREFIX = "wimt:player_form:"
_CACHE_TTL = 1800  # 30 minutes — stats update after each round


class PlayerFormModel(BaseModel):
    """Predict game outcomes based on aggregated recent player form.

    Algorithm:
        1. Fetch last 5 completed games for each team
        2. Aggregate player_advanced_stats per team across those games
        3. Calculate weighted form score for each team
        4. Compare form scores to produce prediction
    """

    def get_name(self) -> str:
        return "player_form"

    # ------------------------------------------------------------------
    # Form score calculation
    # ------------------------------------------------------------------

    def _calculate_form_score(self, stats: dict) -> float:
        """Calculate a composite form score from aggregated advanced stats.

        Weighted composite (typical per-term magnitude):
            score_involvements * 3        (~9-18)
            + contested_possessions * 2   (~12-20)
            + metres_gained * 0.1         (~30-40)
            + pressure_acts * 1.5         (~12-22)
            + (tog_pct / 100) * 50        (~30-45)

        tog_pct is a 0-100 percentage: dividing by 100 normalizes it so
        its contribution (max 50) stays on the same scale as the other
        terms instead of the raw 0-100 value dominating the composite.
        """
        return (
            stats.get("avg_score_involvements", 0) * 3
            + stats.get("avg_contested_possessions", 0) * 2
            + stats.get("avg_metres_gained", 0) * 0.1
            + stats.get("avg_pressure_acts", 0) * 1.5
            + stats.get("avg_tog_pct", 0) / 100 * 50
        )

    # ------------------------------------------------------------------
    # Data access helpers
    # ------------------------------------------------------------------

    async def _get_recent_games(
        self,
        team: str,
        db: AsyncSession,
        before_date,
        limit: int = 5,
    ) -> List[int]:
        """Get last N completed game IDs for the team (home or away)."""
        result = await db.execute(
            select(Game.id)
            .where(
                and_(
                    Game.completed,
                    Game.date < before_date,
                    (Game.home_team == team) | (Game.away_team == team),
                )
            )
            .order_by(Game.date.desc())
            .limit(limit)
        )
        return [row[0] for row in result.all()]

    async def _get_team_advanced_stats(
        self,
        game_ids: List[int],
        team: str,
        db: AsyncSession,
    ) -> Dict[str, float]:
        """Aggregate player_advanced_stats for the team across given games.

        Joins through PlayerMatchStats to filter by team.
        Returns dict with average stats, or empty dict if no data.
        """
        if not game_ids:
            return {}

        result = await db.execute(
            select(
                func.avg(PlayerAdvancedStats.tog_pct).label("avg_tog_pct"),
                func.avg(PlayerAdvancedStats.metres_gained).label(
                    "avg_metres_gained"
                ),
                func.avg(PlayerAdvancedStats.score_involvements).label(
                    "avg_score_involvements"
                ),
                func.avg(PlayerAdvancedStats.contested_possessions).label(
                    "avg_contested_possessions"
                ),
                func.avg(PlayerAdvancedStats.pressure_acts).label(
                    "avg_pressure_acts"
                ),
            )
            .join(
                PlayerMatchStats,
                and_(
                    PlayerMatchStats.game_id == PlayerAdvancedStats.game_id,
                    PlayerMatchStats.player_id == PlayerAdvancedStats.player_id,
                ),
            )
            .where(
                and_(
                    PlayerAdvancedStats.game_id.in_(game_ids),
                    PlayerMatchStats.team == team,
                )
            )
        )

        row = result.one_or_none()
        if row is None:
            return {}

        # If all values are None, treat as no data
        if all(
            getattr(row, col) is None
            for col in [
                "avg_tog_pct",
                "avg_metres_gained",
                "avg_score_involvements",
                "avg_contested_possessions",
                "avg_pressure_acts",
            ]
        ):
            return {}

        return {
            "avg_tog_pct": float(row.avg_tog_pct or 0),
            "avg_metres_gained": float(row.avg_metres_gained or 0),
            "avg_score_involvements": float(row.avg_score_involvements or 0),
            "avg_contested_possessions": float(
                row.avg_contested_possessions or 0
            ),
            "avg_pressure_acts": float(row.avg_pressure_acts or 0),
        }

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    async def _store_cache(self, game: Game, data: dict) -> None:
        """Store computed prediction data in Redis."""
        try:
            client = _get_client()
            cache_key = (
                f"{_CACHE_PREFIX}"
                f"{game.home_team}:{game.away_team}:"
                f"{game.date.isoformat() if game.date else 'all'}"
            )
            await client.set(cache_key, json.dumps(data), ex=_CACHE_TTL)
        except Exception as e:
            logger.warning(f"PlayerFormModel: Redis cache write error: {e}")

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    async def predict(
        self, game: Game, db: AsyncSession
    ) -> Union[Prediction, Abstained]:
        """Predict winner based on recent player form.

        Returns:
            (winner_team, confidence, predicted_margin), or ABSTAINED
            when there is no usable data for either team.
        """
        try:
            # 1. Get recent games for both teams
            home_game_ids = await self._get_recent_games(
                game.home_team, db, game.date
            )
            away_game_ids = await self._get_recent_games(
                game.away_team, db, game.date
            )

            logger.info(
                f"PlayerFormModel: Recent games — "
                f"{game.home_team}={len(home_game_ids)}, "
                f"{game.away_team}={len(away_game_ids)}"
            )

            # 2. No games for either team → no usable data
            if not home_game_ids and not away_game_ids:
                logger.info(
                    "PlayerFormModel: No recent games for either team, "
                    "abstaining"
                )
                # P2-1: missing data carries no winner information —
                # never fabricate a home-team pick from it.
                return ABSTAINED

            # 3. Get advanced stats
            home_stats = (
                await self._get_team_advanced_stats(
                    home_game_ids, game.home_team, db
                )
                if home_game_ids
                else {}
            )
            away_stats = (
                await self._get_team_advanced_stats(
                    away_game_ids, game.away_team, db
                )
                if away_game_ids
                else {}
            )

            # 4. No stats for either team → no usable data
            if not home_stats and not away_stats:
                logger.info(
                    "PlayerFormModel: No advanced stats available, "
                    "abstaining"
                )
                # P2-1: missing data carries no winner information.
                return ABSTAINED

            # 5. Calculate form scores
            home_score = self._calculate_form_score(home_stats)
            away_score = self._calculate_form_score(away_stats)

            logger.info(
                f"PlayerFormModel: Form scores — "
                f"{game.home_team}={home_score:.1f}, "
                f"{game.away_team}={away_score:.1f}"
            )

            # 6. Determine winner (no home bump — home advantage lives
            #    only in elo.py / home_advantage.py).  Exact ties break to
            #    home via >= — a pre-existing convention; NOTE this is the
            #    opposite direction to form/value/weather/injury/matchup,
            #    whose strict > breaks ties to the away side.
            if home_score >= away_score:
                winner = game.home_team
            else:
                winner = game.away_team

            # 7. Confidence: baseline 0.50 + scaled differential
            diff = abs(home_score - away_score)
            confidence = 0.50 + min(diff * 0.02, 0.45)
            confidence = max(0.50, min(0.95, confidence))

            # 8. Margin: proportional to form differential
            margin = max(1, min(100, int(diff * 0.3 + 1)))

            logger.info(
                f"PlayerFormModel: Predicted {winner} with "
                f"confidence={confidence:.2f}, margin={margin} "
                f"(diff={diff:.1f})"
            )

            # 9. Cache the result
            await self._store_cache(game, {
                "winner": winner,
                "confidence": confidence,
                "margin": margin,
                "home_score": home_score,
                "away_score": away_score,
                "home_games": len(home_game_ids),
                "away_games": len(away_game_ids),
            })

            return Prediction(winner, confidence, margin)

        except Exception:
            # P0-3: never convert an internal failure into a confident-
            # looking home-team vote — re-raise so the orchestrator
            # records an abstention (ORCH-M7).
            raise
