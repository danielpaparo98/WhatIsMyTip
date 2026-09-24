"""Local-competition sync — provider → participants → events (Phase 5).

The orchestration that makes a NEW competition a data-entry task
(ADR 0001 / D3): register the competition + season, fetch canonical
fixtures from the competition's FeedProvider, resolve participants
(creating team rows + aliases as needed), convert kick-offs to the
competition's venue-local naive form, and upsert onto the events
tables.  First wired for WAFL (Sportix provider).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from ..crud.competitions import CompetitionCRUD
from ..crud.multisport import EventCRUD, ParticipantResolver
from ..ingestion import FeedProvider
from ..logger import get_logger
from ..sport_context import DEFAULT_CONTEXT

logger = get_logger(__name__)


class LocalCompetitionSyncService:
    """Sync one competition-season of fixtures onto the events tables."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        provider: FeedProvider,
        competition_name: str,
        season: int,
        competition_tier: str = "state",
        competition_format: str = "rounds",
        competition_timezone: Optional[str] = None,
    ):
        self.db = db
        self.provider = provider
        self.competition_name = competition_name
        self.season = season
        self.competition_tier = competition_tier
        self.competition_format = competition_format
        # Per-competition venue timezone (P5: SANFL=Adelaide,
        # VFL=Melbourne, …).  None ⇒ the sport default (AFL: Perth).
        self.competition_timezone = competition_timezone or DEFAULT_CONTEXT.cron_timezone
        self.logger = logger

    async def sync(self) -> Dict[str, Any]:
        start_label = f"{self.competition_name} {self.season}"
        self.logger.info("Starting local-competition sync for %s", start_label)

        stats: Dict[str, Any] = {
            "competition": self.competition_name,
            "season": self.season,
            "fixtures_synced": 0,
            "errors": [],
        }

        competition = await CompetitionCRUD.ensure_competition(
            self.db,
            sport_id=self.provider.sport_id,
            name=self.competition_name,
            tier=self.competition_tier,
            format=self.competition_format,
            timezone=self.competition_timezone,
        )
        season = await CompetitionCRUD.ensure_season(
            self.db,
            competition_id=competition.id,
            label=str(self.season),
            is_current=True,
        )
        stats["competition_id"] = competition.id
        stats["season_id"] = season.id

        tz = self._competition_timezone(self.competition_timezone)
        resolver = ParticipantResolver(self.db, sport_id=self.provider.sport_id)
        fixtures = await self.provider.get_fixtures(self.season)
        self.logger.info("Fetched %d fixtures for %s", len(fixtures), start_label)

        for fixture in fixtures:
            try:
                home = await resolver.ensure_team(
                    fixture.home_participant or "",
                    aliases=(fixture.home_participant or "",),
                ) if fixture.home_participant else None
                away = await resolver.ensure_team(
                    fixture.away_participant or "",
                    aliases=(fixture.away_participant or "",),
                ) if fixture.away_participant else None

                local_fixture = self._to_local(fixture, tz)
                await EventCRUD.upsert_fixture(
                    self.db,
                    season_id=season.id,
                    fixture=local_fixture,
                    home_participant_id=home.id if home else None,
                    away_participant_id=away.id if away else None,
                )
                stats["fixtures_synced"] += 1
            except Exception as e:  # noqa: BLE001 — one bad fixture never aborts the pass
                error = f"fixture {fixture.external_id}: {e}"
                self.logger.error(error, exc_info=True)
                stats["errors"].append(error)

        await self.db.commit()
        self.logger.info(
            "Local-competition sync completed for %s: %d fixtures (%d errors)",
            start_label,
            stats["fixtures_synced"],
            len(stats["errors"]),
        )
        return stats

    # ------------------------------------------------------------------

    @staticmethod
    def _competition_timezone(name: Optional[str]) -> ZoneInfo:
        try:
            return ZoneInfo(name or DEFAULT_CONTEXT.cron_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(DEFAULT_CONTEXT.cron_timezone)

    @staticmethod
    def _to_local(fixture, tz: ZoneInfo):
        """Convert the provider's UTC datetime to venue-local naive —
        the ``events.starts_at`` convention (see models.multisport)."""
        if fixture.starts_at is None:
            return fixture
        local = fixture.starts_at.astimezone(tz).replace(tzinfo=None)
        from dataclasses import replace

        return replace(fixture, starts_at=local)


async def run_wafl_sync(
    session: AsyncSession,
    season: Optional[int] = None,
    *,
    mark_current: bool = True,
) -> Dict[str, Any]:
    """Reusable job/script core: one WAFL competition-season sync pass.

    ``mark_current=False`` for historical backfills — only the live
    season should carry ``seasons.is_current``.
    """
    from ..ingestion.wafl_provider import WaflProvider

    year = season or datetime.now().year
    service = LocalCompetitionSyncService(
        session,
        provider=WaflProvider(),
        competition_name="West Australian Football League",
        season=year,
    )
    stats = await service.sync()
    if not mark_current:
        from sqlalchemy import update

        from ..models import Season

        await session.execute(
            update(Season)
            .where(
                Season.competition_id == stats["competition_id"],
                Season.label == str(year),
            )
            .values(is_current=False)
        )
        await session.commit()
    stats["status"] = "success"
    return stats
