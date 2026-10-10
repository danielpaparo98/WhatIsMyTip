"""National-league registry (Phase 5.2 — rugby-league expansion).

The national counterpart to the state rollout (:mod:`.state_leagues`):
the same frozen ``LeagueConfig`` shape, a new registry for the three
owner-approved rugby-league competitions (2026-10-09), and the facade
``get_league``/``run_league_sync`` pair that resolves across BOTH
registries — so callers (sync scripts, future API wiring) have one
resolution surface for every league regardless of tier.

Registered competitions (per the Phase 5.2 session bundle):

* ``nrl``   — National Rugby League, national/rounds.  Men's
  premiership, Mar–Oct (finals).
* ``nrlw``  — NRL Women's Premiership, national/rounds.  Same window.
* ``origin``— State of Origin, national/tournament.  A 3-match
  mid-year series; stored as ``'tournament'`` because the
  ``competitions.format`` CHECK constraint admits only
  ``'rounds'|'tournament'`` (models.multisport ``format_valid``).

Timezone: ``Australia/Brisbane`` for all three — the rugby-league
``SportContext`` cron timezone decision (2026-10-09).  Venue-level
sponsor drift is normalised by the provider's venue alias table; the
Warriors' Auckland grounds are a display/window concern (venue tz
table), not a competition timezone one.

Feed: every entry is live via the FixtureDownload-backed
:class:`.NrlProvider` (slugs ``nrl-{year}``, ``nrlw-{year}``,
``state-of-origin-{year}``; open JSON, daily-refresh politeness).

FORMAT NOTE (Phase 5.2): ``origin`` is a 3-match series, but the
``competitions.format`` CHECK constraint admits only
``'rounds'|'tournament'`` (models.multisport ``format_valid``), so it
is registered as ``'tournament'`` — matching the seeding identity in
:mod:`.league_seeding` so the first real sync cannot violate the DB
constraint.  Extending the CHECK is a possible follow-up migration.
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import update

from ..models import Season
from ..services.local_competition_sync import LocalCompetitionSyncService
from . import FeedProvider
from .league_config import LeagueConfig
from .state_leagues import STATE_LEAGUES, build_team_metadata_lookup

#: The rugby-league national competitions (Phase 5.2 approved scope).
NATIONAL_LEAGUES: Dict[str, LeagueConfig] = {
    "nrl": LeagueConfig(
        name="National Rugby League",
        timezone="Australia/Brisbane",
        provider_factory=lambda: _nrl("nrl"),
        status="live",
        source_note="fixturedownload.com/feed/json (open)",
        tier="national",
        format="rounds",
    ),
    "nrlw": LeagueConfig(
        name="NRL Women's Premiership",
        timezone="Australia/Brisbane",
        provider_factory=lambda: _nrl("nrlw"),
        status="live",
        source_note="fixturedownload.com/feed/json (open)",
        tier="national",
        format="rounds",
    ),
    "origin": LeagueConfig(
        name="State of Origin",
        timezone="Australia/Brisbane",
        provider_factory=lambda: _nrl("origin"),
        status="live",
        source_note="fixturedownload.com/feed/json (open)",
        tier="national",
        format="tournament",
    ),
}


def _nrl(competition: str) -> FeedProvider:
    from .nrl_provider import NrlProvider

    return NrlProvider(competition=competition)


def get_league(key: str) -> LeagueConfig:
    """Resolve a league key across BOTH registries — state first (the
    older registry keeps its own raise-for-unknown contract untouched),
    then national; keys unknown to both raise the repo-standard error."""
    if key in STATE_LEAGUES:
        return STATE_LEAGUES[key]
    if key in NATIONAL_LEAGUES:
        return NATIONAL_LEAGUES[key]
    raise ValueError(
        f"Unknown league {key!r} — available: "
        f"{sorted({**STATE_LEAGUES, **NATIONAL_LEAGUES})}"
    )


async def run_league_sync(
    session, league_key: str, season: int, *, mark_current: bool = True
) -> Dict[str, Any]:
    """Sync one league-season via either registry (mirror of the state
    runner, plus tier/format from the shared config).  Raises for
    leagues whose provider is not yet implemented."""
    config = get_league(league_key)
    if config.provider_factory is None:
        raise NotImplementedError(
            f"{config.name}: no provider yet — {config.source_note}"
        )
    provider = config.provider_factory()
    service = LocalCompetitionSyncService(
        session,
        provider=provider,
        competition_name=config.name,
        season=season,
        competition_tier=config.tier,
        competition_format=config.format,
        competition_timezone=config.timezone,
        team_metadata=await build_team_metadata_lookup(provider, season),
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


__all__ = ["NATIONAL_LEAGUES", "get_league", "run_league_sync"]
