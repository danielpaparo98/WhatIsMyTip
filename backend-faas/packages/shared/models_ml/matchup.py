"""MatchupModel — predicts game outcomes based on head-to-head history.

Uses historical head-to-head performance between specific team pairs with
exponential time decay weighting.  Combines H2H win rate (60%) with
venue-specific records (40%) to produce predictions.

Cold-start: returns (home_team, 0.55, 8) when insufficient historical data.
"""

import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Tuple, Optional

from .base import BaseModel
from ..models import Game
from ..cache import _get_client
from ..logger import get_logger

logger = get_logger(__name__)

_CACHE_PREFIX = "wimt:matchup:"
_CACHE_TTL = 3600  # 1 hour — H2H data only changes after games complete

# Minimum H2H games required before trusting the model
_MIN_H2H_GAMES = 3


class MatchupModel(BaseModel):
    """Predict game outcomes based on head-to-head historical performance.

    Algorithm:
        1. Query all historical H2H games between the two teams
        2. Apply exponential time decay (half-life: 1 year)
        3. Calculate weighted win rate for home_team
        4. Query venue-specific records for both teams
        5. Combine H2H (60%) + venue record (40%) to pick winner
    """

    def get_name(self) -> str:
        return "matchup"

    # ------------------------------------------------------------------
    # Time decay
    # ------------------------------------------------------------------

    def _apply_time_decay(self, game_date, prediction_date) -> float:
        """Exponential decay: recent games matter more.

        weight = 0.5 ** (years_ago)

        A game from 1 year ago gets 50% weight.
        A game from 2 years ago gets 25% weight.
        """
        if game_date is None or prediction_date is None:
            return 0.1  # Minimal weight for unknown dates

        years_ago = (prediction_date - game_date).days / 365

        if years_ago < 0:
            return 1.0  # Future game (shouldn't happen, but safe default)

        return 0.5 ** years_ago

    # ------------------------------------------------------------------
    # Data access helpers
    # ------------------------------------------------------------------

    async def _get_head_to_head(
        self,
        home_team: str,
        away_team: str,
        db: AsyncSession,
        before_date,
    ) -> Tuple[float, int, float]:
        """Query all historical games between these two teams.

        Returns:
            (weighted_win_rate, game_count, avg_weighted_margin)
            - weighted_win_rate: home_team's win rate vs away_team (0.0–1.0)
            - game_count: number of H2H games found
            - avg_weighted_margin: average margin (positive = home_team favoured)
        """
        result = await db.execute(
            select(Game)
            .where(
                and_(
                    Game.completed == True,
                    Game.date < before_date,
                    (
                        (Game.home_team == home_team) & (Game.away_team == away_team)
                        | (Game.home_team == away_team) & (Game.away_team == home_team)
                    ),
                )
            )
            .order_by(Game.date.desc())
            .limit(30)
        )
        games = result.scalars().all()

        if not games:
            return 0.5, 0, 0.0

        weighted_wins = 0.0
        total_weight = 0.0
        weighted_margin = 0.0

        for g in games:
            weight = self._apply_time_decay(g.date, before_date)

            # Determine scores from home_team's perspective
            if g.home_team == home_team:
                team_score = g.home_score or 0
                opp_score = g.away_score or 0
            else:
                team_score = g.away_score or 0
                opp_score = g.home_score or 0

            if team_score > opp_score:
                weighted_wins += weight

            margin = team_score - opp_score
            weighted_margin += margin * weight
            total_weight += weight

        win_rate = weighted_wins / max(total_weight, 1)
        avg_margin = weighted_margin / max(total_weight, 1)

        return win_rate, len(games), avg_margin

    async def _get_venue_record(
        self,
        team: str,
        venue: str,
        db: AsyncSession,
        before_date,
    ) -> float:
        """Query how this team performs at this specific venue.

        Returns:
            Weighted win rate at venue (0.0–1.0), or 0.5 if no data.
        """
        result = await db.execute(
            select(Game)
            .where(
                and_(
                    Game.completed == True,
                    Game.date < before_date,
                    Game.venue == venue,
                    (Game.home_team == team) | (Game.away_team == team),
                )
            )
            .order_by(Game.date.desc())
            .limit(20)
        )
        games = result.scalars().all()

        if not games:
            return 0.5

        weighted_wins = 0.0
        total_weight = 0.0

        for g in games:
            weight = self._apply_time_decay(g.date, before_date)

            if g.home_team == team:
                score = g.home_score or 0
                opp_score = g.away_score or 0
            else:
                score = g.away_score or 0
                opp_score = g.home_score or 0

            if score > opp_score:
                weighted_wins += weight

            total_weight += weight

        return weighted_wins / max(total_weight, 1)

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    async def _check_cache(self, game: Game) -> Optional[dict]:
        """Check Redis cache for a previously computed prediction."""
        try:
            client = _get_client()
            teams_sorted = sorted([game.home_team, game.away_team])
            cache_key = (
                f"{_CACHE_PREFIX}{teams_sorted[0]}:{teams_sorted[1]}:"
                f"{game.venue}:"
                f"{game.date.isoformat() if game.date else 'all'}"
            )
            raw = await client.get(cache_key)
            if raw is not None:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"MatchupModel: Redis cache read error: {e}")
        return None

    async def _store_cache(self, game: Game, data: dict) -> None:
        """Store computed prediction data in Redis."""
        try:
            client = _get_client()
            teams_sorted = sorted([game.home_team, game.away_team])
            cache_key = (
                f"{_CACHE_PREFIX}{teams_sorted[0]}:{teams_sorted[1]}:"
                f"{game.venue}:"
                f"{game.date.isoformat() if game.date else 'all'}"
            )
            await client.set(cache_key, json.dumps(data), ex=_CACHE_TTL)
        except Exception as e:
            logger.warning(f"MatchupModel: Redis cache write error: {e}")

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    async def predict(
        self, game: Game, db: AsyncSession
    ) -> Tuple[str, float, int]:
        """Predict winner based on head-to-head history and venue records.

        Returns:
            (winner_team, confidence, predicted_margin)
        """
        try:
            # 1. Get H2H data
            h2h_wr, game_count, avg_h2h_margin = await self._get_head_to_head(
                game.home_team, game.away_team, db, game.date
            )

            logger.info(
                f"MatchupModel: H2H {game.home_team} vs {game.away_team} "
                f"WR={h2h_wr:.2f} over {game_count} games "
                f"(avg margin={avg_h2h_margin:.1f})"
            )

            # 2. Cold start if fewer than 3 H2H games
            if game_count < _MIN_H2H_GAMES:
                logger.info(
                    f"MatchupModel: Only {game_count} H2H games, "
                    "using cold-start default"
                )
                return game.home_team, 0.55, 8

            # 3. Get venue records for both teams
            home_venue_wr = await self._get_venue_record(
                game.home_team, game.venue, db, game.date
            )
            away_venue_wr = await self._get_venue_record(
                game.away_team, game.venue, db, game.date
            )

            logger.info(
                f"MatchupModel: Venue records at {game.venue} — "
                f"{game.home_team}={home_venue_wr:.2f}, "
                f"{game.away_team}={away_venue_wr:.2f}"
            )

            # 4. Combine signals: 60% H2H + 40% venue
            h2h_signal = h2h_wr - 0.5
            venue_signal = home_venue_wr - away_venue_wr
            combined = 0.6 * h2h_signal + 0.4 * venue_signal

            # 5. Determine winner
            if combined > 0:
                winner = game.home_team
            else:
                winner = game.away_team

            # 6. Confidence: baseline 0.50 + scaled combined signal
            confidence = 0.50 + min(abs(combined) * 0.8, 0.45)
            confidence = max(0.50, min(0.95, confidence))

            # 7. Margin: based on historical H2H margin, dampened
            margin = max(1, min(100, int(abs(avg_h2h_margin) * 0.6)))

            logger.info(
                f"MatchupModel: Predicted {winner} with "
                f"confidence={confidence:.2f}, margin={margin} "
                f"(combined={combined:.3f})"
            )

            # 8. Cache the result
            await self._store_cache(game, {
                "winner": winner,
                "confidence": confidence,
                "margin": margin,
                "h2h_wr": h2h_wr,
                "home_venue_wr": home_venue_wr,
                "away_venue_wr": away_venue_wr,
                "game_count": game_count,
            })

            return winner, confidence, margin

        except Exception as e:
            logger.error(f"MatchupModel: Prediction failed: {e}")
            return game.home_team, 0.55, 8
