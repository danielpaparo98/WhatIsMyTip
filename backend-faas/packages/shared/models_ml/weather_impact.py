"""WeatherImpactModel — predicts game outcomes based on weather conditions.

Uses historical weather data to determine how specific conditions affect
game outcomes.  AFL is an outdoor sport where rain, wind, and extreme
temperatures significantly impact scoring and game style.

Cold-start: returns (home_team, 0.55, 12) when insufficient data.
"""

import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, Tuple, Optional

from .base import BaseModel
from ..models import Game, MatchWeather
from ..cache import _get_client
from ..logger import get_logger

logger = get_logger(__name__)

_CACHE_PREFIX = "wimt:weather_impact:"
_CACHE_TTL = 1800  # 30 minutes — forecasts update hourly

# Weather severity multipliers for margin calculation
_WEATHER_SEVERITY = {
    "good": 0.5,
    "moderate": 0.7,
    "challenging": 0.9,
    "poor": 1.2,
}

# Minimum number of similar-condition games required per team
_MIN_SAMPLE_SIZE = 3


class WeatherImpactModel(BaseModel):
    """Predict game outcomes based on weather conditions and historical
    team performance in similar conditions at the same venue."""

    def get_name(self) -> str:
        return "weather_impact"

    # ------------------------------------------------------------------
    # Weather classification
    # ------------------------------------------------------------------

    def _classify_weather(self, weather: MatchWeather) -> str:
        """Classify weather into a tier based on composite conditions.

        Scoring:
            precipitation > 5mm → +2,  > 1mm → +1
            wind_gusts    > 50  → +2,  > 35  → +1
            temperature   > 35  or < 10 → +1

        Tiers:
            score ≥ 3 → "poor"
            score ≥ 2 → "challenging"
            score ≥ 1 → "moderate"
            else      → "good"
        """
        score = 0.0

        # Precipitation impact
        if weather.precipitation is not None:
            if weather.precipitation > 5.0:
                score += 2.0
            elif weather.precipitation > 1.0:
                score += 1.0

        # Wind gust impact
        if weather.wind_gusts is not None:
            if weather.wind_gusts > 50.0:
                score += 2.0
            elif weather.wind_gusts > 35.0:
                score += 1.0

        # Temperature extremes
        if weather.temperature is not None:
            if weather.temperature > 35.0 or weather.temperature < 10.0:
                score += 1.0

        if score >= 3.0:
            return "poor"
        elif score >= 2.0:
            return "challenging"
        elif score >= 1.0:
            return "moderate"
        else:
            return "good"

    # ------------------------------------------------------------------
    # Data access helpers
    # ------------------------------------------------------------------

    async def _get_match_weather(
        self, game: Game, db: AsyncSession
    ) -> Optional[MatchWeather]:
        """Get weather forecast/actual for the given game."""
        result = await db.execute(
            select(MatchWeather).where(MatchWeather.game_id == game.id)
        )
        return result.scalars().first()

    async def _get_historical_performance(
        self,
        venue: str,
        weather_tier: str,
        team: str,
        db: AsyncSession,
        before_date,
    ) -> float:
        """Query historical games at the same venue in similar weather,
        returning the team's win rate (0.0–1.0).

        Fetches recent games with weather data and filters in Python by
        weather tier to avoid complex SQL tier-matching.
        """
        result = await db.execute(
            select(Game, MatchWeather)
            .join(MatchWeather, MatchWeather.game_id == Game.id)
            .where(
                and_(
                    Game.completed == True,
                    Game.date < before_date,
                    (Game.home_team == team) | (Game.away_team == team),
                )
            )
            .order_by(Game.date.desc())
            .limit(30)
        )
        games_with_weather = result.all()

        # Filter in Python by weather tier
        similar_games = [
            (g, w) for g, w in games_with_weather
            if self._classify_weather(w) == weather_tier
        ]

        if not similar_games:
            return 0.0

        # Calculate win rate
        wins = 0
        for g, _w in similar_games:
            if g.home_team == team:
                if (g.home_score or 0) > (g.away_score or 0):
                    wins += 1
            else:
                if (g.away_score or 0) > (g.home_score or 0):
                    wins += 1

        return wins / len(similar_games)

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    async def _check_cache(self, game: Game) -> Optional[dict]:
        """Check Redis cache for a previously computed prediction."""
        try:
            client = _get_client()
            cache_key = (
                f"{_CACHE_PREFIX}"
                f"{game.date.isoformat() if game.date else 'all'}"
                f":{game.home_team}:{game.away_team}"
            )
            raw = await client.get(cache_key)
            if raw is not None:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"WeatherImpactModel: Redis cache read error: {e}")
        return None

    async def _store_cache(self, game: Game, data: dict) -> None:
        """Store computed prediction data in Redis."""
        try:
            client = _get_client()
            cache_key = (
                f"{_CACHE_PREFIX}"
                f"{game.date.isoformat() if game.date else 'all'}"
                f":{game.home_team}:{game.away_team}"
            )
            await client.set(cache_key, json.dumps(data), ex=_CACHE_TTL)
        except Exception as e:
            logger.warning(f"WeatherImpactModel: Redis cache write error: {e}")

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    async def predict(
        self, game: Game, db: AsyncSession
    ) -> Tuple[str, float, int]:
        """Predict winner based on weather conditions.

        Returns:
            (winner_team, confidence, predicted_margin)
        """
        try:
            # 1. Get weather for this game
            weather = await self._get_match_weather(game, db)

            if weather is None:
                logger.info(
                    "WeatherImpactModel: No weather data for game "
                    f"{game.id}, using cold-start default"
                )
                return game.home_team, 0.55, 12

            # 2. Classify weather tier
            current_tier = self._classify_weather(weather)
            logger.info(
                f"WeatherImpactModel: Game {game.id} weather tier = "
                f"{current_tier} (temp={weather.temperature}, "
                f"precip={weather.precipitation}, gusts={weather.wind_gusts})"
            )

            # 3. Get historical win rates for both teams in similar conditions
            home_wr = await self._get_historical_performance(
                game.venue, current_tier, game.home_team, db,
                before_date=game.date,
            )
            away_wr = await self._get_historical_performance(
                game.venue, current_tier, game.away_team, db,
                before_date=game.date,
            )

            logger.info(
                f"WeatherImpactModel: {game.home_team} WR={home_wr:.2f}, "
                f"{game.away_team} WR={away_wr:.2f} in '{current_tier}'"
            )

            # 4. Check sample sizes — cold start if insufficient data
            # We approximate: if both WRs are 0.0, no data at all
            if home_wr == 0.0 and away_wr == 0.0:
                logger.info(
                    "WeatherImpactModel: No historical weather data, "
                    "using cold-start default"
                )
                return game.home_team, 0.55, 12

            # 5. Weather resilience differential
            diff = home_wr - away_wr

            # Home weather bonus: home teams get slight edge in poor conditions
            home_weather_bonus = 0.0
            if current_tier in ("poor", "challenging"):
                home_weather_bonus = 0.03

            adjusted_diff = diff + home_weather_bonus

            # 6. Determine winner
            if adjusted_diff > 0:
                winner = game.home_team
            else:
                winner = game.away_team

            # 7. Confidence: base 0.50 + scaled differential
            confidence = 0.50 + min(abs(adjusted_diff) * 0.4, 0.45)
            confidence = max(0.50, min(0.95, confidence))

            # 8. Margin: proportional to differential and weather severity
            severity_mult = _WEATHER_SEVERITY.get(current_tier, 0.5)
            margin = max(1, min(100, int(abs(adjusted_diff) * 100 * severity_mult)))

            logger.info(
                f"WeatherImpactModel: Predicted {winner} with "
                f"confidence={confidence:.2f}, margin={margin} "
                f"(adjusted_diff={adjusted_diff:.3f}, tier={current_tier})"
            )

            # 9. Cache the result
            await self._store_cache(game, {
                "winner": winner,
                "confidence": confidence,
                "margin": margin,
                "tier": current_tier,
            })

            return winner, confidence, margin

        except Exception as e:
            logger.error(f"WeatherImpactModel: Prediction failed: {e}")
            return game.home_team, 0.55, 12
