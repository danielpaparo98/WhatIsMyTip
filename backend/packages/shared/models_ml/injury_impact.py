"""InjuryImpactModel — predicts game outcomes based on player injuries.

Assesses the impact of injured players on team performance by quantifying
each missing player's importance from their historical match stats and
calculating a team-level impact score.

Cold-start: returns (home_team, 0.55, 5) when no injury data is available.
"""

import json
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import _get_client
from ..logger import get_logger
from ..models import Game, Injury, Player, PlayerMatchStats
from .base import BaseModel

logger = get_logger(__name__)

_CACHE_PREFIX = "wimt:injury_impact:"
_CACHE_TTL = 900  # 15 minutes — injury reports update daily

# Default importance assigned to an injured player when no stats are available
_DEFAULT_IMPORTANCE = 3.0


class InjuryImpactModel(BaseModel):
    """Predict game outcomes based on the injury status of key players.

    Algorithm:
        1. Fetch active injuries for both teams
        2. Resolve injured players to their player_id
        3. Calculate each player's importance from average stats
        4. Sum importance per team, normalize by team average goals
        5. Team with fewer impactful injuries wins
    """

    def get_name(self) -> str:
        return "injury_impact"

    # ------------------------------------------------------------------
    # Importance calculation
    # ------------------------------------------------------------------

    def _calculate_importance(self, stats: dict) -> float:
        """Calculate a single player's importance from average stats.

        Weighted composite that rewards goal-kickers, ball-winners, and
        contested players more heavily.

        Scale: ~0-15 for a typical player, ~15-30+ for stars.
        """
        return (
            stats.get("avg_goals", 0) * 4.0
            + stats.get("avg_disposals", 0) * 0.3
            + stats.get("avg_tackles", 0) * 1.5
            + stats.get("avg_marks", 0) * 0.5
            + stats.get("avg_hitouts", 0) * 0.2
        )

    # ------------------------------------------------------------------
    # Data access helpers
    # ------------------------------------------------------------------

    async def _get_active_injuries(
        self, game: Game, db: AsyncSession
    ) -> List[Tuple[Injury, Optional[Player]]]:
        """Query active injuries for both teams.

        Filters out players marked 'Available' or 'Test'.
        Returns list of (Injury, Player | None) tuples.
        """
        result = await db.execute(
            select(Injury, Player)
            .outerjoin(Player, Player.name == Injury.player_name)
            .where(
                and_(
                    Injury.team.in_([game.home_team, game.away_team]),
                    Injury.return_timeline != "Available",
                    Injury.return_timeline != "Test",
                    # Point-in-time guard: only consider injuries that were
                    # already known (scraped) on or before the game's date.
                    # Without this, future-scraped injury reports leak into
                    # historical games and corrupt walk-forward predictions.
                    # Mirrors the ``Game.date < game.date`` filters used by the
                    # player-stat and team-average sub-queries below.
                    Injury.scraped_at <= game.date,
                )
            )
        )
        return result.all()

    async def _get_player_stats(
        self,
        player_ids: List[int],
        game: Game,
        db: AsyncSession,
    ) -> Dict[int, dict]:
        """Get average stats for the given player IDs from completed games
        before the prediction date.

        Returns dict of player_id → {avg_goals, avg_disposals, ...}.
        """
        if not player_ids:
            return {}

        result = await db.execute(
            select(
                PlayerMatchStats.player_id,
                func.avg(PlayerMatchStats.goals).label("avg_goals"),
                func.avg(PlayerMatchStats.disposals).label("avg_disposals"),
                func.avg(PlayerMatchStats.tackles).label("avg_tackles"),
                func.avg(PlayerMatchStats.marks).label("avg_marks"),
                func.avg(PlayerMatchStats.hitouts).label("avg_hitouts"),
            )
            .join(Game, Game.id == PlayerMatchStats.game_id)
            .where(
                and_(
                    PlayerMatchStats.player_id.in_(player_ids),
                    Game.completed,
                    Game.date < game.date,
                )
            )
            .group_by(PlayerMatchStats.player_id)
        )

        return {
            row.player_id: {
                "avg_goals": float(row.avg_goals or 0),
                "avg_disposals": float(row.avg_disposals or 0),
                "avg_tackles": float(row.avg_tackles or 0),
                "avg_marks": float(row.avg_marks or 0),
                "avg_hitouts": float(row.avg_hitouts or 0),
            }
            for row in result.all()
        }

    async def _get_team_averages(
        self, game: Game, db: AsyncSession
    ) -> Dict[str, dict]:
        """Get average team-level stats per game for normalization.

        Returns dict of team → {avg_goals, avg_disposals}.
        """
        result = await db.execute(
            select(
                PlayerMatchStats.team,
                func.avg(PlayerMatchStats.goals).label("avg_goals"),
                func.avg(PlayerMatchStats.disposals).label("avg_disposals"),
            )
            .join(Game, Game.id == PlayerMatchStats.game_id)
            .where(
                and_(
                    PlayerMatchStats.team.in_([game.home_team, game.away_team]),
                    Game.completed,
                    Game.date < game.date,
                )
            )
            .group_by(PlayerMatchStats.team)
        )

        return {
            row.team: {
                "avg_goals": float(row.avg_goals or 0),
                "avg_disposals": float(row.avg_disposals or 0),
            }
            for row in result.all()
        }

    # ------------------------------------------------------------------
    # Team impact calculation
    # ------------------------------------------------------------------

    def _calculate_team_impact(
        self,
        injuries: List[Tuple[Injury, Optional[Player]]],
        team: str,
        player_stats: Dict[int, dict],
    ) -> float:
        """Calculate total importance lost for a team due to injuries.

        Args:
            injuries: List of (Injury, Player|None) tuples for both teams.
            team: Team name to filter injuries for.
            player_stats: Dict of player_id → avg stats.

        Returns:
            Sum of importance scores for all injured players on the team.
        """
        total_impact = 0.0

        for injury, player in injuries:
            if injury.team != team:
                continue

            if player is not None and player.id is not None:
                # Player resolved — use actual stats if available
                stats = player_stats.get(player.id)
                if stats:
                    total_impact += self._calculate_importance(stats)
                else:
                    total_impact += _DEFAULT_IMPORTANCE
            else:
                # Player not resolved — use default importance
                total_impact += _DEFAULT_IMPORTANCE

        return total_impact

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    async def _check_cache(self, game: Game) -> Optional[dict]:
        """Check Redis cache for a previously computed prediction."""
        try:
            client = _get_client()
            cache_key = (
                f"{_CACHE_PREFIX}"
                f"{game.home_team}:{game.away_team}:"
                f"{game.date.isoformat() if game.date else 'all'}"
            )
            raw = await client.get(cache_key)
            if raw is not None:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"InjuryImpactModel: Redis cache read error: {e}")
        return None

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
            logger.warning(f"InjuryImpactModel: Redis cache write error: {e}")

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    async def predict(
        self, game: Game, db: AsyncSession
    ) -> Tuple[str, float, int]:
        """Predict winner based on injury impact.

        Returns:
            (winner_team, confidence, predicted_margin)
        """
        try:
            # 1. Fetch active injuries for both teams
            injuries = await self._get_active_injuries(game, db)

            if not injuries:
                logger.info(
                    f"InjuryImpactModel: No injuries for game {game.id}, "
                    "using cold-start default"
                )
                return game.home_team, 0.52, 8

            # 2. Resolve player IDs
            injured_player_ids = [
                player.id
                for _injury, player in injuries
                if player is not None and player.id is not None
            ]

            # 3. Get player stats for resolved players
            player_stats = await self._get_player_stats(
                injured_player_ids, game, db
            )

            # 4. Get team averages for normalization
            team_avgs = await self._get_team_averages(game, db)

            # 5. Calculate total impact per team
            home_impact = self._calculate_team_impact(
                injuries, game.home_team, player_stats
            )
            away_impact = self._calculate_team_impact(
                injuries, game.away_team, player_stats
            )

            logger.info(
                f"InjuryImpactModel: {game.home_team} impact={home_impact:.1f}, "
                f"{game.away_team} impact={away_impact:.1f}"
            )

            # 6. Normalize by team average goals
            home_avg_goals = team_avgs.get(game.home_team, {}).get(
                "avg_goals", 10.0
            )
            away_avg_goals = team_avgs.get(game.away_team, {}).get(
                "avg_goals", 10.0
            )

            home_impact_pct = home_impact / max(home_avg_goals * 4, 1)
            away_impact_pct = away_impact / max(away_avg_goals * 4, 1)

            # 7. Differential: positive means home is LESS impacted
            diff = away_impact_pct - home_impact_pct

            logger.info(
                f"InjuryImpactModel: diff={diff:.3f} "
                f"(home_pct={home_impact_pct:.3f}, away_pct={away_impact_pct:.3f})"
            )

            # 8. Determine winner
            if diff > 0:
                winner = game.home_team
            else:
                winner = game.away_team

            # 9. Confidence: baseline 0.50, scaled by injury differential
            confidence = 0.50 + min(abs(diff) * 0.3, 0.45)
            confidence = max(0.50, min(0.95, confidence))

            # 10. Margin: proportional to importance difference
            margin = max(1, min(100, int(abs(diff) * 25)))

            logger.info(
                f"InjuryImpactModel: Predicted {winner} with "
                f"confidence={confidence:.2f}, margin={margin}"
            )

            # 11. Cache the result
            await self._store_cache(game, {
                "winner": winner,
                "confidence": confidence,
                "margin": margin,
                "home_impact": home_impact,
                "away_impact": away_impact,
            })

            return winner, confidence, margin

        except Exception as e:
            logger.error(f"InjuryImpactModel: Prediction failed: {e}")
            return game.home_team, 0.55, 5
