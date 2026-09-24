"""State-league rollout registry (Phase 5 — local AFL competitions).

The manifest for expanding across the state leagues: each entry names
the authoritative source and its integration status.  Adding a league
that is already supported = a config entry; a league on a NEW platform
needs a FeedProvider first (the QAFL/TSL pattern: probe, capture a
payload, write the provider).

Source findings (2026-09-24):

* **WAFL** — wafl.com.au runs on the Sportix platform; the site's own
  public client credentials expose a clean JSON API. LIVE.
* **WAFLW / Colts / Reserves** — same Sportix tenant as the WAFL
  (competition_name is the only difference). LIVE.
* **VFL / VFLW / SANFL / AFLW** — the AFL platform's open v2 match API
  (``aflapi.afl.com.au/afl/v2``) serves ``competitions/{id}/compseasons``
  and ``matches`` without auth (VFL=7, AFLW=3, VFLW=8, SANFL=4). LIVE
  via ``AflPlatformProvider`` (supersedes the earlier ``cfs/afl`` token
  dead-end noted below).
* **QAFL / QAFLW** — ``stats.isports.net.au/api`` exposes seasons,
  matches and teams without auth (QAFL=1, QAFLW=4). Scores arrive only
  in per-match ``teamReports``; completion is picked up by batch
  season re-sync. LIVE via ``ISportsProvider``.
* **TSL** — AFL Tasmania not yet probed.
* **VFL legacy note** — vfl.com.au no longer resolves; the VFL lives
  inside afl.com.au/vfl.

Historical note: the older ``api.afl.com.au/cfs/afl`` surface decoded
on 2026-09-24 (``POST /WMCTok`` → ``x-media-mis-token``) still 403s on
``/matches``; the open v2 API above made that path moot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from sqlalchemy import update

from ..logger import get_logger
from ..models import Season
from ..services.local_competition_sync import LocalCompetitionSyncService
from . import FeedProvider

logger = get_logger(__name__)


@dataclass(frozen=True)
class LeagueConfig:
    name: str
    timezone: str
    provider_factory: Optional[Callable[[], FeedProvider]]
    #: "live" = provider ready; "live-same-tenant" = another config on
    #: a live platform; "pending-source" = source identified but not yet
    #: reverse-engineered; "source-unknown" = nothing probed yet (see
    #: module docstring).
    status: str
    source_note: str


STATE_LEAGUES: Dict[str, LeagueConfig] = {
    "wafl": LeagueConfig(
        name="West Australian Football League",
        timezone="Australia/Perth",
        provider_factory=lambda: _sportix("sportix-wafl", "League"),
        status="live",
        source_note="wafl.com.au (Sportix public API)",
    ),
    "waflw": LeagueConfig(
        name="Western Australian Women's Football League",
        timezone="Australia/Perth",
        provider_factory=lambda: _sportix("sportix-waflw", "WAFLW"),
        status="live-same-tenant",
        source_note="Same Sportix tenant as the WAFL; competition_name='WAFLW'",
    ),
    "sanfl": LeagueConfig(
        name="South Australian National Football League",
        timezone="Australia/Adelaide",
        provider_factory=lambda: _afl_platform(4, "SANFL"),
        status="live",
        source_note="aflapi.afl.com.au/afl/v2 (open)",
    ),
    "vfl": LeagueConfig(
        name="Victorian Football League",
        timezone="Australia/Melbourne",
        provider_factory=lambda: _afl_platform(7, "VFL"),
        status="live",
        source_note="aflapi.afl.com.au/afl/v2 (open)",
    ),
    "vflw": LeagueConfig(
        name="Victorian Women's Football League",
        timezone="Australia/Melbourne",
        provider_factory=lambda: _afl_platform(8, "VFLW"),
        status="live",
        source_note="aflapi.afl.com.au/afl/v2 (open)",
    ),
    "aflw": LeagueConfig(
        name="AFL Women's",
        timezone="Australia/Melbourne",
        provider_factory=lambda: _afl_platform(3, "AFLW"),
        status="live",
        source_note="aflapi.afl.com.au/afl/v2 (open)",
    ),
    "qafl": LeagueConfig(
        name="Queensland Australian Football League",
        timezone="Australia/Brisbane",
        provider_factory=lambda: _isports(1),
        status="live",
        source_note="stats.isports.net.au/api (open)",
    ),
    "qaflw": LeagueConfig(
        name="Queensland Australian Football League Women's",
        timezone="Australia/Brisbane",
        provider_factory=lambda: _isports(4),
        status="live",
        source_note="stats.isports.net.au/api (open)",
    ),
    "tsl": LeagueConfig(
        name="Tasmanian State League",
        timezone="Australia/Hobart",
        provider_factory=None,
        status="source-unknown",
        source_note="AFL Tasmania — platform not yet probed",
    ),
}


def _sportix(source: str, competition_name: str) -> FeedProvider:
    from .sportix_provider import SportixProvider

    return SportixProvider(source=source, competition_name=competition_name)


def _afl_platform(competition_id: int, competition_name: str) -> FeedProvider:
    from .afl_platform_provider import AflPlatformProvider

    return AflPlatformProvider(
        competition_id=competition_id, competition_name=competition_name
    )


def _isports(league_id: int) -> FeedProvider:
    from .isports_provider import ISportsProvider

    return ISportsProvider(league_id=league_id)


def get_league(key: str) -> LeagueConfig:
    if key not in STATE_LEAGUES:
        raise ValueError(
            f"Unknown league {key!r} — available: {sorted(STATE_LEAGUES)}"
        )
    return STATE_LEAGUES[key]


async def run_league_sync(
    session, league_key: str, season: int, *, mark_current: bool = True
) -> Dict[str, Any]:
    """Sync one league-season via the registry. Raises for leagues
    whose provider is not yet implemented."""
    config = get_league(league_key)
    if config.provider_factory is None:
        raise NotImplementedError(
            f"{config.name}: no provider yet — {config.source_note}"
        )
    service = LocalCompetitionSyncService(
        session,
        provider=config.provider_factory(),
        competition_name=config.name,
        season=season,
        competition_timezone=config.timezone,
    )
    stats = await service.sync()
    if not mark_current:
        await session.execute(
            update(Season)
            .where(
                Season.competition_id == stats["competition_id"],
                Season.label == str(season),
            )
            .values(is_current=False)
        )
        await session.commit()
    stats["league"] = league_key
    stats["status"] = "success"
    return stats


__all__ = ["LeagueConfig", "STATE_LEAGUES", "get_league", "run_league_sync"]
