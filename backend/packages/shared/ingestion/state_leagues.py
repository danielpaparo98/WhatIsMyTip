"""State-league rollout registry (Phase 5 — local AFL competitions).

The manifest for expanding across the state leagues: each entry names
the authoritative source and its integration status.  Adding a league
that is already supported = a config entry; a league on a NEW platform
needs a FeedProvider first (see SANFL/VFL notes).

Source findings (2026-09-24):

* **WAFL** — wafl.com.au runs on the Sportix platform; the site's own
  public client credentials expose a clean JSON API. LIVE.
* **WAFLW / Colts / Reserves** — same Sportix tenant as the WAFL
  (competition_name is the only difference). LIVE.
* **VFL** — the competition is AFL-run and its authoritative source is
  the AFL platform API (``api.afl.com.au/cfs/afl``, seen referenced
  from ``afl.com.au/vfl``).  The API key/endpoints are embedded in the
  site's JS bundles and need dedicated reverse-engineering before a
  provider can be written. PENDING SOURCE.
* **SANFL** — sanfl.com.au is WordPress; fixture/ladder data loads via
  ``admin-ajax.php`` actions from a custom plugin.  The action names
  and payload shapes still need mapping. PENDING SOURCE.
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
    #: "live" = provider ready; "pending-source" = authoritative source
    #: identified but not yet reverse-engineered (see module docstring).
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
        provider_factory=None,
        status="pending-source",
        source_note=(
            "sanfl.com.au (WordPress admin-ajax) — action names and "
            "payload shapes need mapping"
        ),
    ),
    "vfl": LeagueConfig(
        name="Victorian Football League",
        timezone="Australia/Melbourne",
        provider_factory=None,
        status="pending-source",
        source_note=(
            "Authoritative: api.afl.com.au/cfs/afl (AFL-run). Key + "
            "endpoints embedded in afl.com.au JS bundles — needs a "
            "dedicated reverse-engineering pass"
        ),
    ),
}


def _sportix(source: str, competition_name: str) -> FeedProvider:
    from .sportix_provider import SportixProvider

    return SportixProvider(source=source, competition_name=competition_name)


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
