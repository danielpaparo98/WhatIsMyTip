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
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..crud.competitions import CompetitionCRUD
from ..crud.multisport import EventCRUD, ParticipantResolver
from ..ingestion import FeedProvider
from ..logger import get_logger
from ..sport_context import DEFAULT_CONTEXT
from .league_heuristics import generate_league_tips

logger = get_logger(__name__)

#: Optional per-team identity lookup (migration 0011): raw provider
#: team name → {logo_url, primary_color, secondary_color}.  ``None``
#: return / missing name ⇒ identity stays NULL (frontend falls back).
TeamMetadataFn = Callable[[str], Optional[Dict[str, Any]]]

#: Optional per-venue IANA-timezone resolver (rugby-league:
#: ``league_seeding.venue_timezone``).  When supplied AND the venue
#: resolves, the storage boundary converts kick-offs to the VENUE's
#: local zone (the Warriors' Mount Smart Stadium is Pacific/Auckland —
#: no single competition timezone can express that); venues the hook
#: cannot resolve keep the competition-timezone conversion.  ``None``
#: (the default) preserves the historical AFL/state behaviour exactly.
VenueTimezoneFn = Callable[[Optional[str]], Optional[str]]


class LocalCompetitionSyncService:
    """Sync one competition-season of fixtures onto the events tables,
    then refresh the competition's league tips (D3: deploy a league →
    tips happen by construction — no per-league code)."""

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
        team_metadata: Optional[TeamMetadataFn] = None,
        venue_timezone: Optional[VenueTimezoneFn] = None,
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
        # Team identity lookup (see TeamMetadataFn); built by the
        # caller from an optional provider ``get_team_metadata``.
        self.team_metadata = team_metadata
        # Per-GROUND timezone hook (see VenueTimezoneFn) — rugby-league
        # only; unset for every AFL/state competition.
        self.venue_timezone = venue_timezone
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
                    **self._identity_for(fixture.home_participant),
                ) if fixture.home_participant else None
                away = await resolver.ensure_team(
                    fixture.away_participant or "",
                    aliases=(fixture.away_participant or "",),
                    **self._identity_for(fixture.away_participant),
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
                try:
                    await self.db.rollback()  # clear the poisoned transaction
                except Exception:  # noqa: BLE001
                    pass

        await self.db.commit()

        # D3 generation hook: the events are committed — refresh this
        # competition's league tips (current + most recent past season,
        # per the heuristics service).  Best-effort by contract: the
        # sync is the critical path and tips are regenerable on the
        # next cycle, so a failure here never fails the sync.
        await self._refresh_league_tips(competition.id, stats)

        self.logger.info(
            "Local-competition sync completed for %s: %d fixtures (%d errors)",
            start_label,
            stats["fixtures_synced"],
            len(stats["errors"]),
        )
        return stats

    async def _refresh_league_tips(
        self, competition_id: int, stats: Dict[str, Any]
    ) -> None:
        """Refresh the competition's league tips (D3 generation hook).

        Never fails the sync: any generation error is logged, recorded
        on the stats, and swallowed — with a rollback to clear the
        poisoned transaction (mirrors the per-fixture guard above).
        """
        try:
            summary = await generate_league_tips(
                self.db, competition_id=competition_id
            )
        except Exception as e:  # noqa: BLE001 — tips never fail the sync
            self.logger.error(
                "league tip generation failed for %s: %s",
                self.competition_name,
                e,
                exc_info=True,
            )
            try:
                await self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            stats["league_tips"] = {"status": "failed", "error": str(e)}
            return
        # Defensive merge: the hook is annotated -> dict, but a
        # contract-violating None must still never fail the sync.
        stats["league_tips"] = {"status": "success", **(summary or {})}

    # ------------------------------------------------------------------

    def _identity_for(self, name: Optional[str]) -> Dict[str, Any]:
        """ensure_team identity kwargs for one fixture side: the
        callback's dict when it has an entry, all-None otherwise."""
        metadata = self.team_metadata(name) if (self.team_metadata and name) else None
        metadata = metadata or {}
        return {
            "logo_url": metadata.get("logo_url"),
            "primary_color": metadata.get("primary_color"),
            "secondary_color": metadata.get("secondary_color"),
        }

    @staticmethod
    def _competition_timezone(name: Optional[str]) -> ZoneInfo:
        try:
            return ZoneInfo(name or DEFAULT_CONTEXT.cron_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(DEFAULT_CONTEXT.cron_timezone)

    def _to_local(self, fixture, tz: ZoneInfo):
        """Convert the provider's UTC datetime to venue-local naive —
        the ``events.starts_at`` convention (see models.multisport).

        The venue's OWN zone (the ``venue_timezone`` hook) wins when it
        resolves one — a per-competition timezone alone cannot express
        the Warriors' Auckland grounds — and the competition timezone
        remains the fallback for unlisted venues (and the ONLY rule
        when no hook is set: every AFL/state competition)."""
        if fixture.starts_at is None:
            return fixture
        if fixture.starts_at.tzinfo is None:
            return fixture  # already venue-local (PlayHQ emits naive local)
        local = fixture.starts_at.astimezone(
            self._target_zone(fixture.venue, tz)
        ).replace(tzinfo=None)
        from dataclasses import replace

        return replace(fixture, starts_at=local)

    def _target_zone(
        self, venue: Optional[str], default_tz: ZoneInfo
    ) -> ZoneInfo:
        """The zone a fixture's kick-off converts to: the venue's own
        IANA zone when the hook resolves one, else the competition
        default.  An unresolvable or unknown venue degrades to the
        default — tz conversion never fails a sync."""
        if self.venue_timezone is not None and venue:
            zone_name = self.venue_timezone(venue)
            if zone_name:
                try:
                    return ZoneInfo(zone_name)
                except (ZoneInfoNotFoundError, ValueError):
                    pass
        return default_tz


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


async def run_all_leagues_sync(
    session: AsyncSession,
    season: Optional[int] = None,
    leagues: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Sync EVERY live state league for one season (cron/script core).

    The league list is derived from the ``STATE_LEAGUES`` registry —
    every entry whose ``status`` starts with ``"live"`` — so leagues
    come and go with the registry without touching this function (keys
    the registry no longer knows about, e.g. a renamed Tasmania entry,
    are simply not attempted).  An explicit ``leagues`` list overrides
    the derivation for single-league backfills.

    One league's failure never aborts the sweep: the error is recorded,
    the remaining leagues still sync, and the aggregate result reports
    ``status="partial"``.  ``season`` defaults to the current season
    from settings (the job always runs on the live season).

    Returns an aggregate dict::

        {
            "season": int,
            "leagues": [key, ...],          # attempted, in order
            "leagues_synced": [key, ...],
            "leagues_failed": [key, ...],
            "fixtures_synced": total,
            "errors": ["<key>: <reason>", ...],
            "results": {key: per-league stats},
            "status": "success" | "partial",
        }
    """
    # Deferred import — state_leagues imports this module's service
    # class at module level, so a top-level import here would cycle.
    from ..ingestion.state_leagues import STATE_LEAGUES, run_league_sync

    year = season or settings.current_season
    if leagues is None:
        keys: List[str] = [
            key
            for key, config in STATE_LEAGUES.items()
            if config.status.startswith("live")
        ]
    else:
        keys = list(leagues)

    aggregated: Dict[str, Any] = {
        "season": year,
        "leagues": keys,
        "leagues_synced": [],
        "leagues_failed": [],
        "fixtures_synced": 0,
        "errors": [],
        "results": {},
        "status": "success",
    }

    logger.info(
        "Starting all-leagues sync for %d (%d leagues)", year, len(keys)
    )

    for key in keys:
        if key not in STATE_LEAGUES:
            error = (
                f"{key}: unknown league — available: {sorted(STATE_LEAGUES)}"
            )
            logger.error("league sync skipped: %s", error)
            aggregated["leagues_failed"].append(key)
            aggregated["errors"].append(error)
            aggregated["status"] = "partial"
            continue
        try:
            stats = await run_league_sync(session, key, year)
        except Exception as e:  # noqa: BLE001 — one league never aborts the sweep
            error = f"{key}: {e}"
            logger.error("league sync failed: %s", error, exc_info=True)
            aggregated["leagues_failed"].append(key)
            aggregated["errors"].append(error)
            aggregated["status"] = "partial"
            continue
        aggregated["leagues_synced"].append(key)
        aggregated["fixtures_synced"] += stats.get("fixtures_synced", 0)
        aggregated["results"][key] = stats

    logger.info(
        "All-leagues sync completed for %d: %d synced, %d failed, "
        "%d fixtures (%d errors)",
        year,
        len(aggregated["leagues_synced"]),
        len(aggregated["leagues_failed"]),
        aggregated["fixtures_synced"],
        len(aggregated["errors"]),
    )
    return aggregated
