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
``SportContext`` cron timezone decision (2026-10-09) and the
competition-level FALLBACK zone.  The storage boundary converts the
provider's tz-aware UTC kick-offs to venue-local naive
(``events.starts_at`` convention, ADR 0001) using each venue's OWN
IANA zone from :data:`.league_seeding.VENUE_TIMEZONES` — Mount Smart
Stadium is ``Pacific/Auckland`` and Perth's Optus Stadium is
``Australia/Perth``, which no single competition timezone can express —
and falls back to the competition timezone only for unlisted grounds.
Display and backtest windows interpret the stored naive value through
the same table (:func:`.league_seeding.venue_timezone`).

Feed: every entry is live via the FixtureDownload-backed
:class:`.NrlProvider` (slugs ``nrl-{year}``, ``nrlw-{year}``,
``state-of-origin-{year}``; open JSON, daily-refresh politeness).

Series semantics (origin): the feed's ``RoundNumber`` is the series
GAME NUMBER (1–3) and maps to ``events.round_id`` unchanged — Game N of
the series is queryable through the same round-scoped surfaces a rounds
competition uses.  The feed's ``Group`` is the constant series label
(``"State of Origin"``) with no per-match information beyond the
competition identity the rows already carry, so it is deliberately NOT
mapped (the events schema has no group column).

FORMAT NOTE (Phase 5.2): ``origin`` is a 3-match series, but the
``competitions.format`` CHECK constraint admits only
``'rounds'|'tournament'`` (models.multisport ``format_valid``), so it
is registered as ``'tournament'`` — matching the seeding identity in
:mod:`.league_seeding` so the first real sync cannot violate the DB
constraint.  Extending the CHECK is a possible follow-up migration.

Failure contract: unknown league keys raise ``BackendServiceError``
(400 ``unknown_league``); operational sync failures (feed unreachable,
DB fault) raise ``BackendServiceError`` (502 ``league_sync_failed``)
with the repo-standard ``status_code``/``code``/``message``/``details``
shape.  Per-fixture failures are NOT failures of the pass: the service
logs them and returns them on ``stats["errors"]``.  A league registered
without a provider keeps the pinned ``NotImplementedError`` guidance.
The app-layer import is deferred so this shared module stays app-free
at import time.
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import update

from ..crud.multisport import ParticipantResolver
from ..logger import get_logger
from ..models import Season
from ..services.local_competition_sync import LocalCompetitionSyncService
from . import FeedProvider
from .league_config import LeagueConfig
from .league_seeding import SPORT_ID, TEAMS, ensure_sport, venue_timezone
from .state_leagues import STATE_LEAGUES, build_team_metadata_lookup

logger = get_logger(__name__)

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


async def _ensure_rugby_league_identity(session) -> None:
    """National-runner prerequisite (idempotent get-or-create):
    the ``rugby-league`` sport row — ``CompetitionCRUD.ensure_competition``
    refuses to run without it — and the 19 canonical team participants,
    so feed nicknames resolve at ``ParticipantResolver``'s exact-name
    step and its transitional AFL ``canonical_team()`` fallback can
    never hijack a club (``league_seeding``'s documented contract:
    'Bulldogs' must not become 'Western Bulldogs' in this sport).
    Composes the same primitives ``seed_league_identity`` does — a
    re-run is a no-op."""
    await ensure_sport(session)
    resolver = ParticipantResolver(session, sport_id=SPORT_ID)
    for team in TEAMS:
        await resolver.ensure_team(team.name, aliases=team.aliases)


def _raise_sync_error(
    league_key: str,
    season: int,
    *,
    status_code: int,
    code: str,
    message: str,
    cause: BaseException | None = None,
) -> None:
    """Raise ``BackendServiceError`` with the repo-standard shape.

    The app-layer import is deferred: ``packages.shared`` stays free of
    ``app.*`` imports at module load time (the app imports shared, never
    the reverse); only the error-mapping call site reaches into it.
    """
    from app.core.exceptions import BackendServiceError

    raise BackendServiceError(
        status_code=status_code,
        code=code,
        message=message,
        details={"league": league_key, "season": season},
    ) from cause


async def run_league_sync(
    session, league_key: str, season: int, *, mark_current: bool = True
) -> Dict[str, Any]:
    """Sync one league-season via either registry (mirror of the state
    runner, plus tier/format from the shared config).

    Rugby-league specifics wired here:

    * identity prerequisites (sport row + canonical team participants)
      run before the pass, so participant resolution never reaches the
      transitional AFL fallback;
    * kick-offs convert to venue-local naive at the storage boundary
      via the venue-timezone table, competition timezone as fallback;
    * failures raise ``BackendServiceError`` (400 unknown league / 502
      failed pass); per-fixture failures stay on ``stats["errors"]``
      (logged, never swallowed, never raised).

    Raises ``NotImplementedError`` (pinned contract) for leagues whose
    provider is not yet implemented."""
    try:
        config = get_league(league_key)
    except ValueError as exc:
        _raise_sync_error(
            league_key,
            season,
            status_code=400,
            code="unknown_league",
            message=str(exc),
            cause=exc,
        )
    if config.provider_factory is None:
        raise NotImplementedError(
            f"{config.name}: no provider yet — {config.source_note}"
        )

    is_national = league_key in NATIONAL_LEAGUES
    if is_national:
        await _ensure_rugby_league_identity(session)

    provider = config.provider_factory()
    service = LocalCompetitionSyncService(
        session,
        provider=provider,
        competition_name=config.name,
        season=season,
        competition_tier=config.tier,
        competition_format=config.format,
        competition_timezone=config.timezone,
        # Venue-level tz truth at the storage boundary (Warriors =
        # Pacific/Auckland; Perth Origin hosts = Australia/Perth).
        # State/AFL keys keep the competition-timezone-only behaviour.
        venue_timezone=venue_timezone if is_national else None,
        team_metadata=await build_team_metadata_lookup(provider, season),
    )
    try:
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
    except Exception as exc:
        # Operational failure of the PASS (feed unreachable, DB fault) —
        # surfaced with the repo-standard shape, cause preserved.
        # Per-fixture failures never reach this: the service logs them
        # and reports them on stats["errors"].
        logger.error(
            "league sync failed: %s %s: %s", league_key, season, exc, exc_info=True
        )
        _raise_sync_error(
            league_key,
            season,
            status_code=502,
            code="league_sync_failed",
            message=f"{config.name} {season} sync failed: {exc}",
            cause=exc,
        )
    stats["league"] = league_key
    stats["status"] = "success"
    return stats


__all__ = ["NATIONAL_LEAGUES", "get_league", "run_league_sync"]
