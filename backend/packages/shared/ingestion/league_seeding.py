"""Rugby-league identity seeding (Phase 5.2, ADR 0001).

The one module that owns WHO rugby-league is: the ``rugby-league``
sport row, its three competitions (NRL, NRLW, State of Origin), the 17
NRL clubs plus the two Origin representative teams as team
``Participant`` rows with full-name aliases, the supported season range
(FixtureDownload serves 2017+, live-verified 2026-10-09), and per-ground
timezones built on the canonical venue alias table
(:mod:`.venue_aliases`).

Design contracts:

* **Canonical participant names are the feed nicknames** (``Broncos``,
  ``Blues``, …).  The fixture feed sends exactly these names, so the
  sync path resolves participants at the exact-name step and never
  reaches ``ParticipantResolver``'s transitional AFL ``canonical_team()``
  fallback — which owns ``"Bulldogs" → "Western Bulldogs"`` and would
  otherwise hijack the club under its AFL name (a silent cross-sport
  mismatch).  Full club names are registered as ``TeamAlias`` rows so
  human-facing inputs resolve too.

* **Loud failures, no silent mismatches.  An unknown or ambiguous team
  name raises** :class:`UnknownTeamError` (a ``ValueError``); the alias
  and venue-timezone tables validate themselves at import so a
  contradictory definition cannot even land.

* **Idempotent by construction.**  Seeding composes get-or-create
  primitives only — ``ensure_sport`` here, ``CompetitionCRUD``'s
  ``ensure_competition`` / ``ensure_season``, and
  ``ParticipantResolver.ensure_team`` — so re-running is a no-op.

* **No new migration.**  The generic schema (migration 0010) stores the
  venue as a string on ``events`` — there is no venues table.  Venue
  identity therefore lives HERE as validated declarative data: canonical
  ground names come from ``venue_aliases.CANONICAL_VENUES`` (the same
  table the provider normalizes ``Location`` strings through), each with
  its IANA timezone — ``Mount Smart Stadium`` is ``Pacific/Auckland``,
  which is exactly why per-competition timezone alone is insufficient
  for the Warriors.  Nothing in this module edits that table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..crud.competitions import CompetitionCRUD
from ..crud.multisport import ParticipantResolver
from ..logger import get_logger
from ..models import Competition, Sport
from .venue_aliases import CANONICAL_VENUES, resolve_venue

logger = get_logger(__name__)

#: The sport row id (matches ``sport_context.RUGBY_LEAGUE.sport_id``).
SPORT_ID = "rugby-league"
SPORT_DISPLAY_NAME = "Rugby League"

#: First FixtureDownload season with full NRL coverage (2017–2026
#: verified live 2026-10-09); everything earlier is out of scope.
MIN_SEASON = 2017


class UnknownTeamError(ValueError):
    """A feed/participant name matches no seeded rugby-league team.

    Raised instead of guessing: a silent mismatch would corrupt results
    and backtests far downstream of the ingestion boundary.
    """


@dataclass(frozen=True)
class CompetitionIdentity:
    """Declarative competition row (see ``models.Competition``)."""

    key: str
    name: str
    tier: str
    format: str
    timezone: str


@dataclass(frozen=True)
class TeamIdentity:
    """Declarative team participant: canonical name == feed nickname,
    plus the full/alternate club names registered as ``TeamAlias`` rows."""

    name: str
    aliases: Tuple[str, ...] = ()


#: Competition identity per the session-bundle decisions.  Timezone is
#: the competition-level display/scheduler default (the sport's cron
#: anchor, NRL HQ Brisbane); per-ground truth lives in VENUE_TIMEZONES.
COMPETITIONS: Dict[str, CompetitionIdentity] = {
    "nrl": CompetitionIdentity(
        key="nrl",
        name="National Rugby League",
        tier="national",
        format="rounds",
        timezone="Australia/Brisbane",
    ),
    "nrlw": CompetitionIdentity(
        key="nrlw",
        name="NRL Women's Premiership",
        tier="national",
        format="rounds",
        timezone="Australia/Brisbane",
    ),
    "origin": CompetitionIdentity(
        key="origin",
        name="State of Origin",
        tier="national",
        # A 3-match mid-year series, not a round-robin season — the
        # schema CheckConstraint allows 'rounds' | 'tournament' only.
        format="tournament",
        timezone="Australia/Brisbane",
    ),
}

#: The 17 NRL clubs (feed nicknames live-verified 2026-10-09).  NRLW
#: reuses these club identities — there are no women's-suffixed
#: duplicates; Origin teams are separate (ORIGIN_TEAMS below).
CLUBS: Tuple[TeamIdentity, ...] = (
    TeamIdentity("Broncos", ("Brisbane Broncos",)),
    TeamIdentity("Bulldogs", ("Canterbury-Bankstown Bulldogs", "Canterbury Bulldogs")),
    TeamIdentity("Cowboys", ("North Queensland Cowboys",)),
    TeamIdentity("Dolphins", ("The Dolphins", "Redcliffe Dolphins")),
    TeamIdentity("Dragons", ("St George Illawarra Dragons",)),
    TeamIdentity("Eels", ("Parramatta Eels",)),
    TeamIdentity("Knights", ("Newcastle Knights",)),
    TeamIdentity("Panthers", ("Penrith Panthers",)),
    TeamIdentity("Rabbitohs", ("South Sydney Rabbitohs",)),
    TeamIdentity("Raiders", ("Canberra Raiders",)),
    TeamIdentity("Roosters", ("Sydney Roosters",)),
    TeamIdentity("Sea Eagles", ("Manly Warringah Sea Eagles", "Manly Sea Eagles")),
    TeamIdentity("Sharks", ("Cronulla-Sutherland Sharks", "Cronulla Sharks")),
    TeamIdentity("Storm", ("Melbourne Storm",)),
    TeamIdentity("Titans", ("Gold Coast Titans",)),
    TeamIdentity("Warriors", ("New Zealand Warriors", "NZ Warriors")),
    # The merged club's official name IS "Wests Tigers"; the Wests/
    # Balmain predecessors are distinct historical entities, not aliases.
    TeamIdentity("Wests Tigers"),
)

#: State of Origin representative teams (fresh squads each series —
#: not club identities).
ORIGIN_TEAMS: Tuple[TeamIdentity, ...] = (
    TeamIdentity("Blues", ("NSW Blues", "New South Wales Blues", "New South Wales")),
    TeamIdentity("Maroons", ("QLD Maroons", "Queensland Maroons", "Queensland")),
)

#: Every seeded rugby-league team participant (17 clubs + 2 Origin).
TEAMS: Tuple[TeamIdentity, ...] = CLUBS + ORIGIN_TEAMS


def _normalize(name: str) -> str:
    """Case-, apostrophe- and whitespace-insensitive matching key
    (mirrors ``venue_aliases._normalize``; that helper is private)."""
    return re.sub(r"\s+", " ", name.replace("'", "").strip().lower())


def _build_alias_index(teams: Tuple[TeamIdentity, ...]) -> Dict[str, str]:
    """Normalized alias → canonical owner.  Two teams claiming one name
    is a definition bug that must fail at import, never at match time."""
    index: Dict[str, str] = {}
    for team in teams:
        for name in (team.name, *team.aliases):
            key = _normalize(name)
            owner = index.setdefault(key, team.name)
            if owner != team.name:
                raise ValueError(
                    f"Ambiguous rugby-league team alias {name!r}: "
                    f"claimed by both {owner!r} and {team.name!r}"
                )
    return index


#: Validated at import — an ambiguous definition aborts the module.
_NICKNAME_INDEX: Dict[str, str] = _build_alias_index(TEAMS)

#: Readable alias → canonical participant name (canonical names included).
NICKNAME_ALIASES: Dict[str, str] = {
    name: team.name for team in TEAMS for name in (team.name, *team.aliases)
}


def resolve_nickname(name: Optional[str]) -> str:
    """Resolve a feed/participant name to its canonical participant.

    Every provider team name must resolve to EXACTLY one participant;
    unknown, ambiguous or blank names raise :class:`UnknownTeamError`
    loudly — a silent mismatch would poison results and backtests.
    """
    if not name or not name.strip():
        raise UnknownTeamError(
            f"Blank participant name — expected one of {sorted(NICKNAME_ALIASES)}"
        )
    canonical = _NICKNAME_INDEX.get(_normalize(name))
    if canonical is None:
        raise UnknownTeamError(
            f"Unknown rugby-league team {name!r} — expected one of "
            f"{sorted(NICKNAME_ALIASES)}"
        )
    return canonical


#: Canonical ground → IANA timezone.  ``Mount Smart Stadium`` is the
#: point of the table: Warriors matches are Pacific/Auckland, which no
#: per-competition timezone can express.  Kept in lockstep with
#: ``venue_aliases.CANONICAL_VENUES`` (validated below, at import).
VENUE_TIMEZONES: Dict[str, str] = {
    # New Zealand (Warriors).
    "Mount Smart Stadium": "Pacific/Auckland",
    # New South Wales.
    "Shark Park": "Australia/Sydney",  # Cronulla
    "Jubilee Stadium": "Australia/Sydney",  # Kogarah
    "Newcastle Stadium": "Australia/Sydney",
    "Stadium Australia": "Australia/Sydney",  # Sydney Olympic Park
    "Sydney Football Stadium": "Australia/Sydney",  # Moore Park
    "Penrith Stadium": "Australia/Sydney",
    "Western Sydney Stadium": "Australia/Sydney",  # Parramatta
    "Brookvale Oval": "Australia/Sydney",  # Manly
    "Canberra Stadium": "Australia/Sydney",  # ACT shares NSW clock
    "Coffs International Stadium": "Australia/Sydney",
    "Central Coast Stadium": "Australia/Sydney",  # Gosford
    # Queensland.
    "Lang Park": "Australia/Brisbane",  # Brisbane
    "Dolphin Stadium": "Australia/Brisbane",  # Redcliffe
    "North Queensland Stadium": "Australia/Brisbane",  # Townsville
    "Robina Stadium": "Australia/Brisbane",  # Gold Coast
    # Victoria.
    "Melbourne Rectangular Stadium": "Australia/Melbourne",
    # Western Australia (State of Origin hosts).
    "Perth Rectangular Stadium": "Australia/Perth",
    "Perth Stadium": "Australia/Perth",
}


def _validate_venue_timezones() -> None:
    """The timezone table must cover EXACTLY the canonical grounds —
    a drifted table would silently mis-time venues, so it cannot import."""
    missing = sorted(CANONICAL_VENUES - set(VENUE_TIMEZONES))
    unknown = sorted(set(VENUE_TIMEZONES) - CANONICAL_VENUES)
    if missing or unknown:
        raise ValueError(
            "VENUE_TIMEZONES is out of sync with the canonical venue "
            f"table: missing timezone for {missing}, unknown venues {unknown}"
        )


_validate_venue_timezones()


def venue_timezone(name: Optional[str]) -> Optional[str]:
    """IANA timezone for a venue (canonical ground or sponsor alias).

    Unknown venues return ``None`` — matching ``resolve_venue``'s
    pass-through philosophy — and callers fall back to the competition
    timezone for display.  ``None``/blank likewise.
    """
    if name is None or not name.strip():
        return None
    canonical = resolve_venue(name)
    if canonical is None:
        return None
    return VENUE_TIMEZONES.get(canonical)


def season_years(through_year: int) -> List[int]:
    """The supported rugby-league seasons: ``MIN_SEASON`` … through_year
    (inclusive).  ``through_year`` earlier than ``MIN_SEASON`` is a
    configuration error and fails loudly."""
    if through_year < MIN_SEASON:
        raise ValueError(
            f"through_year {through_year} predates the first supported "
            f"rugby-league season ({MIN_SEASON})"
        )
    return list(range(MIN_SEASON, through_year + 1))


async def ensure_sport(db: AsyncSession) -> Sport:
    """Get-or-create the ``rugby-league`` sport row (idempotent).

    ``CompetitionCRUD.ensure_competition`` requires the sport to exist
    and refuses otherwise — this is the registrar it needs.
    """
    existing = (
        await db.execute(select(Sport).where(Sport.id == SPORT_ID))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    sport = Sport(id=SPORT_ID, display_name=SPORT_DISPLAY_NAME)
    db.add(sport)
    await db.flush()
    logger.info("Registered sport %s (%s)", SPORT_ID, SPORT_DISPLAY_NAME)
    return sport


async def _ensure_competitions_and_seasons(
    db: AsyncSession, years: List[int]
) -> Dict[str, Competition]:
    """Register the three competitions and, per competition, the season
    rows ``years[0]`` … ``years[-1]``.  Only the latest seeded year is
    marked current; older years are explicitly not (deterministic
    re-runs, and a re-seed with a lower through-year corrects the flag)."""
    competitions: Dict[str, Competition] = {}
    for key, identity in COMPETITIONS.items():
        competitions[key] = await CompetitionCRUD.ensure_competition(
            db,
            sport_id=SPORT_ID,
            name=identity.name,
            tier=identity.tier,
            format=identity.format,
            timezone=identity.timezone,
        )

    latest = years[-1]
    for competition in competitions.values():
        for year in years:
            await CompetitionCRUD.ensure_season(
                db,
                competition_id=int(competition.id),
                label=str(year),
                is_current=year == latest,
            )
    return competitions


async def seed_league_identity(
    db: AsyncSession, *, through_year: Optional[int] = None
) -> Dict[str, Any]:
    """Seed the full rugby-league identity set — idempotently.

    Order matters: sport → competitions+seasons → team participants.
    Every step is a get-or-create primitive, so re-running mutates
    nothing and duplicates nothing (teams resolve by canonical name or
    alias; ``ensure_team`` only backfills NULL identity fields).

    ``through_year`` defaults to ``settings.current_season``.  Returns a
    stats dict describing what the identity set covers.
    """
    if through_year is None:
        through_year = settings.current_season
    years = season_years(through_year)

    await ensure_sport(db)
    competitions = await _ensure_competitions_and_seasons(db, years)

    resolver = ParticipantResolver(db, SPORT_ID)
    for team in TEAMS:
        # Identity columns (logo/colours) stay NULL: no verified NRL
        # source supplies them yet, and inventing data is worse than
        # the frontend's colour fallback.
        await resolver.ensure_team(team.name, aliases=team.aliases)

    await db.commit()
    logger.info(
        "Rugby-league identity seeded: %d competitions, %d seasons, %d teams",
        len(competitions),
        len(COMPETITIONS) * len(years),
        len(TEAMS),
    )
    return {
        "status": "success",
        "sport_id": SPORT_ID,
        "competitions": list(COMPETITIONS),
        "season_years": years,
        "seasons_ensured": len(COMPETITIONS) * len(years),
        "participants_ensured": len(TEAMS),
    }


__all__ = [
    "CLUBS",
    "COMPETITIONS",
    "MIN_SEASON",
    "NICKNAME_ALIASES",
    "ORIGIN_TEAMS",
    "SPORT_DISPLAY_NAME",
    "SPORT_ID",
    "TEAMS",
    "VENUE_TIMEZONES",
    "CompetitionIdentity",
    "TeamIdentity",
    "UnknownTeamError",
    "ensure_sport",
    "resolve_nickname",
    "seed_league_identity",
    "season_years",
    "venue_timezone",
]
