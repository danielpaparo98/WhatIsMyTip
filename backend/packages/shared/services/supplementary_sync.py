"""Scheduled refresh for the supplementary data feeds (P3-3).

Injuries (FootyWire) and match-day weather (Open-Meteo) were previously
refreshed only by MANUAL scripts — the ``InjuryImpactModel``'s
point-in-time guard (``scraped_at <= game.date``) silently starved as
scraped data aged, and predictions degraded to default importance
weights with nobody alerted.  This service closes that gap:

* injuries: fetch the FootyWire injury list and upsert (same
  constraint + stale-delete semantics as ``scripts/seed_player_data.py``);
* weather: fetch forecasts for upcoming games that have no
  ``match_weather`` row yet;
* alerting: alert when either feed fails or injury data is stale.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..alerting import AlertingService
from ..config import settings
from ..logger import get_logger
from ..models import Game, Injury, MatchWeather

logger = get_logger(__name__)

_JOB_NAME = "supplementary-sync"

# How far ahead to look for weather-less upcoming games.
_WEATHER_LOOKAHEAD_DAYS = 8


class SupplementaryDataSyncService:
    """Refresh injuries + weather on a schedule, alerting on staleness."""

    def __init__(
        self,
        db_session: AsyncSession,
        footywire_client: Any = None,
        weather_client: Any = None,
        alerting: Optional[AlertingService] = None,
        staleness_days: Optional[int] = None,
    ):
        self.db = db_session
        self._footywire_client = footywire_client
        self._weather_client = weather_client
        self.alerting = alerting or AlertingService()
        self.staleness_days = (
            staleness_days
            if staleness_days is not None
            else settings.supplementary_sync_staleness_days
        )

    async def sync(self) -> Dict[str, Any]:
        """Run one refresh pass.  Feed failures never raise — they
        alert and land in ``stats["errors"]``."""
        start_time = time.time()
        stats: Dict[str, Any] = {
            "injuries_upserted": 0,
            "weather_stored": 0,
            "stale": False,
            "alerts_sent": 0,
            "errors": [],
        }

        # ---- injuries ---------------------------------------------------
        try:
            stats["injuries_upserted"] = await self._sync_injuries()
        except Exception as e:  # noqa: BLE001 — alert, don't fail the job
            logger.error(f"Injury sync failed: {e}", exc_info=True)
            stats["errors"].append(f"injury sync: {e}")
            await self._alert(f"injury sync failed: {e}", stats)

        # ---- weather ----------------------------------------------------
        try:
            stats["weather_stored"] = await self._sync_weather()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Weather sync failed: {e}", exc_info=True)
            stats["errors"].append(f"weather sync: {e}")

        # ---- staleness --------------------------------------------------
        try:
            stats["stale"] = await self._is_injury_data_stale()
            if stats["stale"]:
                await self._alert(
                    f"injury data is older than {self.staleness_days} days",
                    stats,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Staleness probe failed: {e}")

        stats["duration_seconds"] = time.time() - start_time
        return stats

    # ------------------------------------------------------------------

    async def _sync_injuries(self) -> int:
        from ..afl_data import FootyWireClient

        now = datetime.now(timezone.utc)
        if self._footywire_client is not None:
            injuries = await self._footywire_client.get_injury_list()
        else:
            async with FootyWireClient() as client:
                injuries = await client.get_injury_list()

        if not injuries:
            logger.info("Supplementary sync: no injury data returned")
            return 0

        # Player name → id lookup (rows without a player row keep
        # player_id NULL — same as the seed script).
        result = await self.db.execute(select(Injury.player_name).limit(0))
        from sqlalchemy import text

        result = await self.db.execute(text("SELECT id, name FROM players"))
        player_map = {row[1].strip().lower(): row[0] for row in result.fetchall()}

        upserted = 0
        for injury in injuries:
            player_name = (injury.get("player") or "").strip()
            team = (injury.get("team") or "").strip()
            injury_type = (injury.get("injury") or "").strip()
            return_timeline = (injury.get("return_timeline") or "").strip()

            if not player_name or not injury_type:
                continue

            player_id = player_map.get(player_name.lower())

            stmt = pg_insert(Injury).values(
                player_id=player_id,
                player_name=player_name,
                team=team,
                injury_type=injury_type,
                return_timeline=return_timeline,
                source="footywire",
                scraped_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_injuries_player_injury",
                set_={
                    "return_timeline": stmt.excluded.return_timeline,
                    "team": stmt.excluded.team,
                    "player_id": stmt.excluded.player_id,
                    "scraped_at": stmt.excluded.scraped_at,
                    "updated_at": now,
                },
            )
            await self.db.execute(stmt)
            upserted += 1

        # Remove stale injuries not refreshed by this pass (identical
        # semantics to the seed script).
        await self.db.execute(
            text("DELETE FROM injuries WHERE scraped_at < :now"),
            {"now": now},
        )
        await self.db.commit()

        logger.info(f"Supplementary sync: upserted {upserted} injury records")
        return upserted

    async def _sync_weather(self) -> int:
        from ..weather.client import WeatherClient

        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=_WEATHER_LOOKAHEAD_DAYS)

        result = await self.db.execute(
            select(Game)
            .where(
                Game.completed.is_(False),
                Game.venue.isnot(None),
                Game.venue != "",
                Game.date.isnot(None),
                Game.date >= now,
                Game.date <= horizon,
            )
            .order_by(Game.date)
            .limit(50)
        )
        upcoming = result.scalars().all()

        stored = 0
        for game in upcoming:
            existing = await self.db.execute(
                select(func.count(MatchWeather.id)).where(
                    MatchWeather.game_id == game.id
                )
            )
            if (existing.scalar() or 0) > 0:
                continue

            if self._weather_client is not None:
                forecast = await self._weather_client.get_forecast(
                    game.venue, days=_WEATHER_LOOKAHEAD_DAYS
                )
            else:
                async with WeatherClient() as client:
                    forecast = await client.get_forecast(
                        game.venue, days=_WEATHER_LOOKAHEAD_DAYS
                    )

            hourly = (forecast or {}).get("hourly") or {}
            times = hourly.get("time") or []
            if not times:
                continue

            kickoff = (
                game.date.replace(tzinfo=None) if game.date.tzinfo else game.date
            )
            idx = self._nearest_hour_index(times, kickoff)

            self.db.add(
                MatchWeather(
                    game_id=game.id,
                    venue=game.venue,
                    match_date=kickoff.date(),
                    temperature=_pick_hourly(hourly, "temperature_2m", idx),
                    precipitation=_pick_hourly(hourly, "precipitation", idx),
                    wind_speed=_pick_hourly(hourly, "windspeed_10m", idx),
                    wind_direction=_pick_hourly(hourly, "winddirection_10m", idx),
                    wind_gusts=_pick_hourly(hourly, "windgusts", idx),
                    humidity=_pick_hourly(hourly, "relative_humidity_2m", idx),
                    weather_code=_pick_hourly(hourly, "weathercode", idx),
                    data_type="forecast",
                    raw_hourly=hourly,
                )
            )
            stored += 1

        if stored:
            await self.db.commit()
        logger.info(f"Supplementary sync: stored {stored} weather forecasts")
        return stored

    async def _is_injury_data_stale(self) -> bool:
        latest = await self.db.execute(select(func.max(Injury.scraped_at)))
        latest_scraped = latest.scalar()
        if latest_scraped is None:
            return False  # nothing scraped yet — staleness N/A
        if latest_scraped.tzinfo is None:
            latest_scraped = latest_scraped.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - latest_scraped
        return age > timedelta(days=self.staleness_days)

    async def _alert(self, error: str, stats: Dict[str, Any]) -> None:
        try:
            sent = await self.alerting.send_failure_alert(
                job_name=_JOB_NAME, error=error
            )
            if sent:
                stats["alerts_sent"] += 1
        except Exception:  # noqa: BLE001 — alerting must not break the job
            logger.exception("Failed to send supplementary-sync alert")

    @staticmethod
    def _nearest_hour_index(times: list, kickoff: datetime) -> int:
        try:
            return min(
                range(len(times)),
                key=lambda i: abs(
                    datetime.fromisoformat(times[i]).replace(tzinfo=None) - kickoff
                ),
            )
        except (ValueError, TypeError):
            return len(times) // 2


def _pick_hourly(hourly: Dict[str, Any], key: str, idx: int):
    values = hourly.get(key) or []
    if idx >= len(values):
        return None
    return values[idx]


async def run_supplementary_sync(session: Session) -> Dict[str, Any]:
    """Reusable cron-job core: one supplementary-data refresh pass."""
    service = SupplementaryDataSyncService(session)
    stats = await service.sync()
    stats["status"] = "success"
    stats["message"] = (
        f"Injuries upserted: {stats['injuries_upserted']}, "
        f"weather stored: {stats['weather_stored']}, "
        f"stale: {stats['stale']}, errors: {len(stats['errors'])}"
    )
    logger.info("supplementary-sync completed: %s", stats["message"])
    return stats
